# -*- coding: utf-8 -*-
"""NAI P0 配置自检：直接加载真实 config.toml，验证新增的 NAI 包装层变量与
nai_default 预设/映射能被既有解析器正确接受。

只断言结构与逻辑（变量存在、mode、条件分支、映射字段、预设可唯一解析、被引用的变量），
不断言画师串 / 负面词 / 质量词的具体文本（这些是用户可编辑的审美内容），避免脆性。
"""

import tomllib
from pathlib import Path

import pytest

from services.builtin_variable_provider import BuiltinVariableProvider
from services.custom_variable_registry import CustomVariableRegistry
from services.nai_chat_input_value_builder import NaiChatInputValueBuilder
from services import nai_settings
from services.preset_resolution import resolve_active_preset
from services.variable_dependency_resolver import VariableDependencyResolver

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"

# GPT 大脑链（director→final_prompt 那套）——NAI 预设激活时这些一个都不该进闭包
GPT_BRAIN_VARS = {
    "final_prompt", "director", "english_free", "english_outfit", "english_bg",
    "english_extra", "selfie_assembly", "normal_assembly", "scene_type",
    "style_hint", "emotion_dynamic", "emotion_prompt", "pose_prompt", "free_prompt",
}
# NAI 大脑链——GPT 预设激活时这些一个都不该进闭包
NAI_BRAIN_VARS = {"nai_director", "nai_subject", "nai_final_prompt", "nai_quality", "nai_size", "nai_negative"}


@pytest.fixture(scope="module")
def config() -> dict:
    with open(CONFIG_PATH, "rb") as fp:
        return tomllib.load(fp)


@pytest.fixture(scope="module")
def action_param_names(config) -> set[str]:
    params = config["bizyair_generate_image_plugin"]["action_parameters"]
    return {p["name"] for p in params}


@pytest.fixture(scope="module")
def registry(config, action_param_names) -> CustomVariableRegistry:
    raw_vars = config["custom_variables_config"]["custom_variables"]
    # 构造成功本身即验证所有定义（含新增 NAI 变量）通过校验
    return CustomVariableRegistry(raw_vars, action_param_names)


def _nai_default_mappings(config) -> list[dict]:
    return [
        m for m in config["nai_chat_client"]["parameter_mappings"]
        if m["preset_name"] == "nai_default"
    ]


def _preset_closure(registry, mappings, preset, action_param_names) -> set[str]:
    """复刻 Action 的闭包计算：按 preset 过滤映射 → 收集直接引用 → 算传递闭包。"""
    filtered = [
        m for m in mappings
        if preset in {p.strip() for p in str(m.get("preset_name", "")).split(",")}
    ]
    direct = registry.collect_required_variable_keys(filtered)
    return VariableDependencyResolver.compute_required_variable_keys(
        direct_keys=direct,
        action_inputs={"image_intent": "x", "aspect_ratio": "9:16", "resolution": "2K"},
        custom_variable_definitions=registry.variable_definitions,
        action_parameter_names=set(action_param_names),
        builtin_names=BuiltinVariableProvider.get_default_variable_names(),
    )


class TestNaiConfigIntegration:
    def test_config_is_valid_toml(self, config):
        assert "nai_chat_client" in config
        assert "custom_variables_config" in config

    def test_action_parameters_expose_image_op(self, config):
        # 问题2 回归锁：image_op 必须暴露给 planner，否则 bot 无法感知/控制 i2i 改图能力
        params = config["bizyair_generate_image_plugin"]["action_parameters"]
        by_name = {p["name"]: p for p in params}
        assert "image_op" in by_name, "action_parameters 缺 image_op，bot 无法自主 i2i 改群里的图"
        op = by_name["image_op"]
        assert op.get("required", "选填") == "选填"
        assert op.get("missing_behavior") == "keep_placeholder"

    def test_action_require_mentions_i2i(self, config):
        # action_require 必须讲清 i2i 改图能力（硬触发 + image_op 主动），否则 bot 不知情
        require = config["bizyair_generate_image_plugin"]["action_require"]
        assert "image_op" in require and "i2i" in require

    def test_registry_accepts_nai_wrapper_vars(self, registry):
        defs = registry.variable_definitions
        assert defs["nai_quality"].mode == "literal"
        assert defs["nai_negative"].mode == "literal"
        assert defs["nai_size"].mode == "dict"
        # 尺寸来源改为 Action 注入的 nai_size_code（不再直接读 aspect_ratio）
        assert defs["nai_size"].source == "nai_size_code"

    def test_artist_and_size_code_are_injected_not_custom_vars(self, registry):
        # 画师串 / 尺寸代号都改为 Action 注入的伪 action_input，不应再是自定义变量
        defs = registry.variable_definitions
        assert "nai_artist" not in defs
        assert "nai_size_code" not in defs

    def test_nai_size_dict_keyed_by_size_code(self, registry):
        # dict 重构为 code→像素映射
        entries = registry.variable_definitions["nai_size"].entries
        assert set(entries) == {"v", "h", "s"}
        assert entries["v"] == "[832, 1216]"
        assert entries["h"] == "[1216, 832]"
        assert entries["s"] == "[1024, 1024]"

    def test_nai_final_prompt_is_conditional_on_artist(self, registry):
        d = registry.variable_definitions["nai_final_prompt"]
        assert d.mode == "literal"
        assert d.condition_type == "length_gt"
        assert d.condition_source == "nai_artist"
        # 主分支拼画师串，else 分支不拼
        assert "{nai_artist}" in d.values[0]
        assert all("{nai_artist}" not in v for v in d.values_else)

    def test_nai_artist_seg_removed(self, registry):
        # 重构后不应再有 nai_artist_seg（避免回潮）
        assert "nai_artist_seg" not in registry.variable_definitions

    def test_no_legacy_final_prompt_artist_double_quality(self, config):
        # 既有共享 final_prompt 未被改动（GPT 路径不受影响）
        defs = {v["key"]: v for v in config["custom_variables_config"]["custom_variables"]}
        assert "final_prompt" in defs
        assert "{nai_" not in defs["final_prompt"]["values"]

    def test_nai_default_mappings_parse(self, config):
        raw = _nai_default_mappings(config)
        # parse 不抛错即通过校验
        NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        fields = {m["field"]: m for m in raw}
        # 基础字段 + 采样参数（scale/sampler/noise_schedule/cfg_rescale；seed 默认不配=留空随机）
        assert {"prompt", "negative_prompt", "size", "steps", "n_samples"} <= set(fields)
        assert {"scale", "sampler", "noise_schedule", "cfg_rescale"} <= set(fields)
        assert fields["prompt"]["value"] == "{nai_final_prompt}"
        assert fields["negative_prompt"]["value"] == "{nai_negative}"
        assert fields["size"]["value"] == "{nai_size}"
        # 浮点参数用 json 类型表达（builder value_type 不支持 float）
        assert fields["scale"]["value_type"] == "json"
        assert fields["cfg_rescale"]["value_type"] == "json"
        assert fields["size"]["value_type"] == "json"

    def test_nai_default_preset_resolves_to_nai_chat(self, config):
        active = "nai_default"
        bizyair_presets = config["bizyair_client"]["app_presets"]
        nai_presets = config["nai_chat_client"]["presets"]
        resolved = resolve_active_preset(active, bizyair_presets, nai_presets)
        assert resolved["provider"] == "nai_chat"
        assert resolved["preset"]["preset_name"] == "nai_default"

    def test_active_preset_is_globally_unique(self, config):
        # 当前激活预设也应能唯一解析（回归保护）
        active = config["bizyair_generate_image_plugin"]["active_preset"]
        resolved = resolve_active_preset(
            active,
            config["bizyair_client"]["app_presets"],
            config["nai_chat_client"]["presets"],
        )
        assert resolved["preset"]["preset_name"] == active

    def test_required_keys_for_nai_default(self, config, registry):
        raw = _nai_default_mappings(config)
        required = registry.collect_required_variable_keys(raw)
        # 映射直接引用的 NAI 变量都应被收集
        assert {"nai_final_prompt", "nai_negative", "nai_size"} <= required

    def test_nai_artist_presets_normalize(self, config):
        # 画师串预设应存在且能被 nai_settings 正常规范化（命令读取的就是这份）
        raw = config["nai_chat_client"]["nai_artist_presets"]
        presets = nai_settings.normalize_artist_presets(raw)
        assert len(presets) >= 1
        assert all(p["name"] and p["prompt"] for p in presets)
        # 按 1 基序号可命中第一个预设
        status, name, prompt = nai_settings.resolve_artist_choice("1", raw)
        assert status == "set"
        assert name == presets[0]["name"]
        assert prompt == presets[0]["prompt"]

    def test_nai_director_template_placeholders_and_role_logic(self, registry, config):
        # nai_director 模板（移植 prompt_rules）的回归锁：没有任何测试跑 director 路径，
        # 占位符若被改坏会静默失效，这里盯死关键不变量。
        d = registry.variable_definitions["nai_director"]
        assert d.mode == "llm"
        template = d.values[0]
        # 注入占位符必须在场（被替换的就是这几个；拼错会导致 director 拿不到上下文/状态/尺度指令）
        # 注意：意图由上游 translater 提炼后以 action_input 注入，故 director 引用 {nai_intent} 而非原始 {image_intent}
        #      nai_nsfw_directive 由 inject_nai_nsfw_directive 按 /nai nsfw 开关注入（off=容忍所有 nsfw / on=SFW）
        for ph in (
            "{nai_intent}", "{today_state}", "{current_datetime}",
            "{recent_chat_context_30}", "{nai_nsfw_directive}",
        ):
            assert ph in template, f"nai_director 模板缺少占位符 {ph}"
        # nai_intent / nai_nsfw_directive 不是 custom variable（改由代码注入），故不应出现在变量定义里
        assert "nai_intent" not in registry.variable_definitions
        assert "nai_nsfw_directive" not in registry.variable_definitions
        # translater 模板存在于 [nai_chat_client].intent_refine_template，且吃原始 image_intent + 聊天上下文
        refine_tmpl = config["nai_chat_client"]["intent_refine_template"]
        assert "{image_intent}" in refine_tmpl
        assert "{recent_chat_context_30}" in refine_tmpl
        # 主体识别（支撑「画指定角色靠 director 自判」决策）：已知角色写 (作品)、不补外貌
        assert "(作品)" in template
        assert "主体识别" in template
        # 钠人设锚点（画钠时套）
        assert "light brown cat ears" in template
        # 单花括号注入占位符之外，不应残留会被误当变量替换的真实变量名（防回潮）
        assert "{final_prompt}" not in template
        assert "{nai_director}" not in template


class TestNaiBrainIsolation:
    """惰性隔离的命脉测试：NAI 预设激活时只跑 nai_director，GPT 链整条不进闭包，反之亦然。

    这是"单次 nai_director 替代 final_prompt 复用"方案的命脉——一旦哪天有人把
    nai_final_prompt 又指回 final_prompt，或 GPT 映射误引 nai_*，这里立刻红。
    """

    def test_nai_preset_runs_only_nai_director(self, config, registry, action_param_names):
        closure = _preset_closure(
            registry, config["nai_chat_client"]["parameter_mappings"], "nai_default", action_param_names
        )
        # NAI 大脑及包装层在闭包内
        assert {"nai_director", "nai_subject"} <= closure
        assert {"nai_final_prompt", "nai_negative", "nai_size", "nai_quality"} <= closure
        # GPT 大脑整链一个都不许进（零双跑/零空转的命脉）
        leaked = GPT_BRAIN_VARS & closure
        assert not leaked, f"GPT 链泄漏进 NAI 闭包: {sorted(leaked)}"

    def test_gpt_preset_does_not_run_nai_brain(self, config, registry, action_param_names):
        # 明确取一个 GPT 预设名（openapi_parameter_mappings 里的 preset_name），
        # 不依赖用户运行时设置的 active_preset（用户切到 NAI 预设时它会是 nai_default，
        # 拿 GPT mappings 按 NAI 预设过滤会得到空集，与本测试意图无关）。
        gpt_mappings = config["bizyair_client"]["openapi_parameter_mappings"]
        active = gpt_mappings[0]["preset_name"]
        closure = _preset_closure(
            registry, gpt_mappings, active, action_param_names
        )
        # GPT 大脑在闭包内
        assert "final_prompt" in closure
        # NAI 大脑/包装层一个都不许进
        leaked = NAI_BRAIN_VARS & closure
        assert not leaked, f"NAI 链泄漏进 GPT 闭包: {sorted(leaked)}"

    def test_nai_subject_wiring(self, registry):
        # nai_final_prompt 两个分支都引用 {nai_subject}（不再直接引用 nai_director / final_prompt）
        fp = registry.variable_definitions["nai_final_prompt"]
        assert all("{nai_subject}" in v for v in [*fp.values, *fp.values_else])
        assert all("{final_prompt}" not in v for v in [*fp.values, *fp.values_else])
        # nai_subject：命中分支用 {nai_raw_tags}（/nai0 直发），否则 {nai_director}（大脑）
        subj = registry.variable_definitions["nai_subject"]
        assert subj.condition_type == "length_gt"
        assert subj.condition_source == "nai_raw_tags"
        assert all("{nai_raw_tags}" in v for v in subj.values)
        assert all("{nai_director}" in v for v in subj.values_else)
