"""source_decision / source_preset 模型路由变量的图完整性回归测试。

直接加载仓库根目录的真实 config.toml，确保：
- 把模型路由变量并入依赖图后，整图无环（拓扑排序不抛异常）
- source_preset 的依赖闭包确实拉到了 source_decision 和 final_prompt
- source_preset 的 dict 映射目标都是真实存在的预设名（gpt / nai / fallback）
"""
import tomllib
from pathlib import Path

import pytest

from services.builtin_variable_provider import BuiltinVariableProvider
from services.custom_variable_registry import CustomVariableRegistry
from services.variable_dependency_resolver import VariableDependencyResolver

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"
ACTION_PARAMETER_NAMES = {"image_intent", "aspect_ratio", "resolution"}


@pytest.fixture(scope="module")
def config() -> dict:
    return tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def custom_variables(config: dict) -> list:
    return config["custom_variables_config"]["custom_variables"]


def _union_bindings(config: dict) -> list:
    union: list = []
    for client_key, mapping_key in (
        ("bizyair_client", "openapi_parameter_mappings"),
        ("nai_chat_client", "parameter_mappings"),
    ):
        bindings = config.get(client_key, {}).get(mapping_key, [])
        if isinstance(bindings, list):
            union.extend(item for item in bindings if isinstance(item, dict))
    return union


def _build_resolver(config: dict, custom_variables: list) -> tuple[VariableDependencyResolver, set[str]]:
    registry = CustomVariableRegistry(
        raw_variables=custom_variables,
        action_parameter_names=ACTION_PARAMETER_NAMES,
    )
    union_bindings = _union_bindings(config)
    direct_keys = set(registry.collect_required_variable_keys(union_bindings))
    direct_keys.add("source_preset")
    builtin_names = BuiltinVariableProvider.get_default_variable_names()
    required_keys = VariableDependencyResolver.compute_required_variable_keys(
        direct_keys=direct_keys,
        action_inputs={"image_intent": "画一只趴在窗台的猫", "aspect_ratio": "1:1", "resolution": "2K"},
        custom_variable_definitions=registry.variable_definitions,
        action_parameter_names=ACTION_PARAMETER_NAMES,
        builtin_names=builtin_names,
    )
    resolver = VariableDependencyResolver(
        action_inputs={"image_intent": "画一只趴在窗台的猫", "aspect_ratio": "1:1", "resolution": "2K"},
        custom_variable_definitions=registry.variable_definitions,
        action_parameter_names=ACTION_PARAMETER_NAMES,
        builtin_names=builtin_names,
        required_custom_variable_keys=required_keys,
    )
    return resolver, required_keys


def test_routing_variables_defined(custom_variables: list):
    keys = {v["key"] for v in custom_variables}
    assert "source_decision" in keys
    assert "source_preset" in keys
    # NAI 独立 LLM 翻译器路径
    assert "nai_prompt_builder" in keys
    assert "final_prompt_nai" in keys


def test_dependency_graph_has_no_cycle(config: dict, custom_variables: list):
    resolver, _ = _build_resolver(config, custom_variables)
    # 有环会抛 ValueError("检测到循环引用: ...")
    order = resolver.topological_sort()
    assert order  # 非空即说明成功排序


def test_source_preset_closure_pulls_final_prompt(config: dict, custom_variables: list):
    _, required_keys = _build_resolver(config, custom_variables)
    assert "source_preset" in required_keys
    assert "source_decision" in required_keys
    assert "final_prompt" in required_keys


def test_source_decision_consumes_final_prompt(custom_variables: list):
    sd = next(v for v in custom_variables if v["key"] == "source_decision")
    assert sd["mode"] == "llm"
    prompt = sd["values"][0] if isinstance(sd["values"], list) else sd["values"]
    # 启发式所需的最终提示词
    assert "{final_prompt}" in prompt
    # 显式模型指令覆盖：路由器要能读最近 10 条上下文（builtin recent_chat_context_10）
    assert "{recent_chat_context_10}" in prompt


def test_source_decision_uses_sfw_nsfw_standard(custom_variables: list):
    """第二步启发式已从「三件事（文字/写实/真人）」改为「SFW/NSFW 内容尺度」判定。"""
    sd = next(v for v in custom_variables if v["key"] == "source_decision")
    prompt = sd["values"][0] if isinstance(sd["values"], list) else sd["values"]
    # 新标准：SFW/NSFW 二分
    assert "SFW" in prompt and "NSFW" in prompt
    # NSFW 信号词样例在场（确认第二步落到了内容尺度判定）
    assert "lingerie" in prompt
    assert "micro bikini" in prompt
    # 旧的三条启发式措辞已彻底移除
    assert "三条启发式" not in prompt


def test_source_preset_maps_to_real_presets(config: dict, custom_variables: list):
    import json

    sp = next(v for v in custom_variables if v["key"] == "source_preset")
    assert sp["mode"] == "dict"
    assert sp["source"] == "source_decision"
    entries = json.loads(sp["values"])

    bizyair_presets = {p["preset_name"] for p in config["bizyair_client"]["app_presets"]}
    nai_presets = {p["preset_name"] for p in config["nai_chat_client"]["presets"]}
    all_presets = bizyair_presets | nai_presets

    # gpt / nai 两个关键字都在；映射目标都是真实预设
    assert set(entries.keys()) == {"gpt", "nai"}
    for target in entries.values():
        assert target in all_presets, f"映射目标 {target!r} 不是已配置的预设名"
    assert entries["nai"] in nai_presets
    assert entries["gpt"] in bizyair_presets
    # 兜底也必须是真实预设
    assert sp["fallback_value"] in all_presets


def test_nai_binding_uses_painterly_prompt(config: dict):
    """NAI prompt 绑定必须走画师串版 final_prompt_nai，而不是 GPT 用的裸 final_prompt。"""
    mappings = config["nai_chat_client"]["parameter_mappings"]
    prompt_binding = next(m for m in mappings if m.get("field") == "prompt")
    assert prompt_binding["value"] == "{final_prompt_nai}"
    # 确认没有任何 NAI 绑定还指向裸 final_prompt（避免漏改）
    assert all(m.get("value") != "{final_prompt}" for m in mappings)


def test_nai_prompt_builder_is_llm_with_required_inputs(custom_variables: list):
    """nai_prompt_builder 是独立 LLM 变量，prompt 中引用了所有必要输入字段。"""
    npb = next(v for v in custom_variables if v["key"] == "nai_prompt_builder")
    assert npb["mode"] == "llm"
    prompt = npb["values"][0] if isinstance(npb["values"], list) else npb["values"]
    for field in ("{free_prompt}", "{scene_type}", "{includes_natrium}", "{chat_type}",
                  "{outfit_brief}", "{emotion_key}", "{pose_key}"):
        assert field in prompt, f"nai_prompt_builder prompt 缺少 {field}"
    # 禁止输出 artist（系统自动添加）
    assert "禁止" in prompt and "artist" in prompt


def test_final_prompt_nai_closure(config: dict, custom_variables: list):
    """NAI 绑定 {final_prompt_nai} 的依赖闭包应拉到 nai_prompt_builder。"""
    _, required_keys = _build_resolver(config, custom_variables)
    for key in (
        "final_prompt_nai",
        "nai_prompt_builder",
    ):
        assert key in required_keys, f"{key} 不在依赖闭包内"


def test_nai_path_independent_from_gpt(custom_variables: list):
    """NAI 路径(nai_prompt_builder)不再依赖 GPT 的 selfie_content/normal_content/style_base。"""
    npb = next(v for v in custom_variables if v["key"] == "nai_prompt_builder")
    prompt = npb["values"][0] if isinstance(npb["values"], list) else npb["values"]
    for gpt_var in ("{selfie_content}", "{normal_content}", "{style_base}",
                    "{normal_style}", "{character_base}", "{hand_discipline}"):
        assert gpt_var not in prompt, f"nai_prompt_builder 不应引用 GPT 变量 {gpt_var}"


def test_final_prompt_nai_group_appends_rating(custom_variables: list):
    """final_prompt_nai 群聊分支硬追加 rating:general；私聊分支只用 nai_prompt_builder。"""
    fp = next(v for v in custom_variables if v["key"] == "final_prompt_nai")
    assert "rating:general" in fp["values"]
    assert "{nai_prompt_builder}" in fp["values"]
    assert "{nai_prompt_builder}" in fp["values_else"]
    assert "rating:general" not in fp["values_else"]


def test_chat_type_builtin_reflects_is_group():
    """chat_type 内置变量：群聊 group / 私聊 private，且已在默认内置名集合中。"""
    assert "chat_type" in BuiltinVariableProvider.get_default_variable_names()
    group = BuiltinVariableProvider(chat_id="t", is_group=True)
    private = BuiltinVariableProvider(chat_id="t", is_group=False)
    assert group.build_placeholder_values({"chat_type"}) == {"{chat_type}": "group"}
    assert private.build_placeholder_values({"chat_type"}) == {"{chat_type}": "private"}


def test_style_hint_safe_reads_includes_natrium(custom_variables: list):
    """style_hint_safe 的 condition_source 必须是 includes_natrium（而非已删的 natrium_in_normal）。"""
    shs = next(v for v in custom_variables if v["key"] == "style_hint_safe")
    assert shs["condition_source"] == "includes_natrium"
    assert shs["condition_type"] == "regex_match"
    assert "yes" in shs["condition_value"].lower()
