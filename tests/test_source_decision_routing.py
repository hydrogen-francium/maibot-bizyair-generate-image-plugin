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
    # NAI 专用画风路径变量
    assert "nai_style_block" in keys
    assert "selfie_content" in keys
    assert "normal_content" in keys
    assert "selfie_assembly_nai" in keys
    assert "normal_assembly_nai" in keys
    assert "final_prompt_nai" in keys
    # 群聊/私聊 SFW 守卫变量（坍缩后只剩一个：直接由 chat_type 决定 general/sensitive）
    assert "nai_safety_rating" in keys


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


def test_nai_style_block_has_painterly_artist_string(custom_variables: list):
    """nai_style_block 承载厚涂画师串 + V4.5 反扁平负向杠杆。"""
    nsb = next(v for v in custom_variables if v["key"] == "nai_style_block")
    raw = nsb["values"]
    assert "artist:ciloranko" in raw  # 主导画师，无括号最高权重
    assert "artist:" in raw  # 至少一个 artist: 前缀的画师标签
    assert "flat color" in raw  # -2.5::flat color:: 反 cel-shade 杠杆
    assert "masterpiece" in raw


def test_final_prompt_nai_closure(config: dict, custom_variables: list):
    """NAI 绑定 {final_prompt_nai} 的依赖闭包应拉到画师串块与共享内容变量。"""
    _, required_keys = _build_resolver(config, custom_variables)
    for key in (
        "final_prompt_nai",
        "selfie_assembly_nai",
        "normal_assembly_nai",
        "nai_style_block",
        "selfie_content",
        "normal_content",
        # 尾部 SFW 守卫坍缩为单变量：final_prompt_nai → nai_safety_rating（直接读 chat_type）
        "nai_safety_rating",
    ):
        assert key in required_keys, f"{key} 不在依赖闭包内"


def test_gpt_and_nai_paths_share_content_not_style(custom_variables: list):
    """GPT 走 style_base、NAI 走 nai_style_block，但两者共用同一份内容（selfie_content/normal_content）。"""
    by_key = {v["key"]: v for v in custom_variables}
    # selfie：GPT 版前缀 style_base，NAI 版前缀 nai_style_block，内容同为 selfie_content
    assert "{style_base}, {selfie_content}" in by_key["selfie_assembly"]["values"]
    assert "{nai_style_block}, {selfie_content}" in by_key["selfie_assembly_nai"]["values"]
    # normal：同理，内容同为 normal_content
    assert "{normal_style}, {normal_content}" in by_key["normal_assembly"]["values"]
    assert "{nai_style_block}, {normal_content}" in by_key["normal_assembly_nai"]["values"]


def test_final_prompt_nai_appends_safety_rating(custom_variables: list):
    """final_prompt_nai 两个分支都必须在尾部带上 SFW 守卫 rating。"""
    fp = next(v for v in custom_variables if v["key"] == "final_prompt_nai")
    assert "{nai_safety_rating}" in fp["values"]
    assert "{nai_safety_rating}" in fp["values_else"]


def test_chat_type_builtin_reflects_is_group():
    """chat_type 内置变量：群聊 group / 私聊 private，且已在默认内置名集合中。"""
    assert "chat_type" in BuiltinVariableProvider.get_default_variable_names()
    group = BuiltinVariableProvider(chat_id="t", is_group=True)
    private = BuiltinVariableProvider(chat_id="t", is_group=False)
    assert group.build_placeholder_values({"chat_type"}) == {"{chat_type}": "group"}
    assert private.build_placeholder_values({"chat_type"}) == {"{chat_type}": "private"}


@pytest.mark.asyncio
async def test_nai_safety_rating_matrix(custom_variables: list):
    """守卫坍缩后的矩阵：只看 chat_type，群聊 → general，私聊 → sensitive。

    钠不再焊死 SFW（私聊允许 sensitive）；NSFW 仍由 source_decision 与 NAI 自身把关。
    """
    from unittest.mock import AsyncMock

    registry = CustomVariableRegistry(
        raw_variables=custom_variables,
        action_parameter_names=ACTION_PARAMETER_NAMES,
    )
    all_defs = registry.variable_definitions
    safety_defs = {"nai_safety_rating": all_defs["nai_safety_rating"]}

    async def rating(*, is_group: bool) -> str:
        resolver = VariableDependencyResolver(
            action_inputs={},
            custom_variable_definitions=safety_defs,
            action_parameter_names=set(),
            builtin_names=BuiltinVariableProvider.get_default_variable_names(),
            required_custom_variable_keys={"nai_safety_rating"},
        )
        provider = BuiltinVariableProvider(chat_id="t", is_group=is_group)
        _, cv = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=provider,
        )
        return cv["nai_safety_rating"]

    assert await rating(is_group=True) == "rating:general"
    assert await rating(is_group=False) == "rating:sensitive"
