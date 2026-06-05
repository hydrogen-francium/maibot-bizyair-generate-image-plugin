# -*- coding: utf-8 -*-
"""NAI 包装层变量端到端解析测试（P0 包装层 + P1 画师/尺寸注入 + nai_director/直发旁路）。

用真实的 VariableDependencyResolver 跑 NAI 包装层那几个变量的定义（与 config.toml 对齐），
在不真正调 LLM 的前提下验证：
- nai_final_prompt 的 length_gt 条件（空画师串走 else 不注入、非空注入画师段）
- 主体来源 nai_subject：注入 nai_raw_tags → 直发（/nai0），否则 → nai_director 大脑
- **director 惰性旁路**：/nai0 提供 raw tags 时 nai_director 一次都不跑（llm_value_factory 零调用）
- nai_size 按 Action 注入的 nai_size_code（v/h/s）映射到 NAI 规范尺寸字面量

P1 关键点：nai_artist / nai_size_code / nai_raw_tags 不是自定义变量，而是 Action 注入的
**伪 action_input**，且**不登记进 action_parameter_names**。本测试精确复刻这一点。
改 config 时本测试应同步更新。
"""

import pytest
from unittest.mock import AsyncMock

from variable_resolver.fixtures import (
    make_definition,
    make_resolver,
    mock_builtin_provider,
)


# 与 config.toml 逐字一致
NAI_QUALITY = "very aesthetic, masterpiece, best quality, amazing quality, very detailed, absurdres"
NAI_NEGATIVE = (
    "lowres, worst quality, low quality, bad anatomy, bad hands, missing fingers, "
    "extra digits, fewer digits, jpeg artifacts, signature, watermark, username, "
    "blurry, artistic error, scan, abstract"
)
NAI_SIZE_ENTRIES = {
    "v": "[832, 1216]",
    "h": "[1216, 832]",
    "s": "[1024, 1024]",
}


def _nai_wrapper_defs() -> dict:
    """构造 NAI 包装层自定义变量链（nai_director 打桩成 llm，便于探测是否被调用）。

    nai_artist / nai_size_code / nai_raw_tags 不在这里（它们是 Action 注入的 action_input）。
    """
    return {
        "nai_quality": make_definition("nai_quality", values=[NAI_QUALITY]),
        "nai_negative": make_definition("nai_negative", values=[NAI_NEGATIVE]),
        "nai_size": make_definition(
            "nai_size",
            mode="dict",
            source="nai_size_code",
            entries=NAI_SIZE_ENTRIES,
            missing_behavior="use_default",
            fallback_value="[832, 1216]",
        ),
        "nai_director": make_definition("nai_director", mode="llm", values=["brain prompt: {image_intent}"]),
        "nai_subject": make_definition(
            "nai_subject",
            values=["{nai_raw_tags}"],
            condition_type="length_gt",
            condition_source="nai_raw_tags",
            condition_value="0",
            use_raw_condition_value=True,
            values_else=["{nai_director}"],
        ),
        "nai_final_prompt": make_definition(
            "nai_final_prompt",
            values=["{nai_quality}, {nai_artist}, {nai_subject}"],
            condition_type="length_gt",
            condition_source="nai_artist",
            condition_value="0",
            use_raw_condition_value=True,
            values_else=["{nai_quality}, {nai_subject}"],
        ),
    }


def _make(action_inputs: dict):
    """构造 resolver；action_parameter_names 只含真实 action 参数，复刻"注入但不登记"。"""
    return make_resolver(
        action_inputs=action_inputs,
        definitions=_nai_wrapper_defs(),
        action_parameter_names={"aspect_ratio", "image_intent"},
    )


async def _resolve(action_inputs: dict, factory: AsyncMock) -> dict:
    resolver = _make(action_inputs)
    _, resolved = await resolver.resolve_all(
        builtin_placeholder_values={},
        llm_value_factory=factory,
        builtin_variable_provider=mock_builtin_provider(),
    )
    return resolved


class TestNaiDirectorPath:
    @pytest.mark.asyncio
    async def test_director_runs_when_no_raw_tags(self):
        # 正常出图：无 nai_raw_tags → 走 nai_director（LLM 调用一次），输出作主体
        factory = AsyncMock(return_value="1girl, masterpiece scene")
        resolved = await _resolve(
            {"aspect_ratio": "9:16", "nai_artist": "", "nai_size_code": "v", "image_intent": "画钠"},
            factory,
        )
        assert resolved["nai_final_prompt"] == f"{NAI_QUALITY}, 1girl, masterpiece scene"
        assert factory.await_count == 1  # director 跑了

    @pytest.mark.asyncio
    async def test_raw_tags_bypasses_director(self):
        # /nai0：注入 nai_raw_tags → 直接用，nai_director 一次都不跑（惰性旁路命脉）
        factory = AsyncMock(return_value="SHOULD_NOT_APPEAR")
        resolved = await _resolve(
            {
                "aspect_ratio": "9:16",
                "nai_artist": "",
                "nai_size_code": "v",
                "nai_raw_tags": "1girl, cat ears, smile",
                "image_intent": "x",
            },
            factory,
        )
        assert resolved["nai_final_prompt"] == f"{NAI_QUALITY}, 1girl, cat ears, smile"
        assert "SHOULD_NOT_APPEAR" not in resolved["nai_final_prompt"]
        assert factory.await_count == 0  # director 被惰性旁路，零调用

    @pytest.mark.asyncio
    async def test_raw_tags_with_artist_order(self):
        # /nai0 + 画师串：质量词 → 画师 → 原始 tag，director 仍不跑
        factory = AsyncMock(return_value="X")
        resolved = await _resolve(
            {
                "aspect_ratio": "9:16",
                "nai_artist": "artist:foo",
                "nai_size_code": "v",
                "nai_raw_tags": "1girl, smile",
            },
            factory,
        )
        assert resolved["nai_final_prompt"] == f"{NAI_QUALITY}, artist:foo, 1girl, smile"
        assert factory.await_count == 0


class TestNaiSizeAndNegative:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "size_code, expected",
        [
            ("v", "[832, 1216]"),
            ("h", "[1216, 832]"),
            ("s", "[1024, 1024]"),
            ("x", "[832, 1216]"),  # 未知代号走 fallback
        ],
    )
    async def test_nai_size_code_mapping(self, size_code, expected):
        resolved = await _resolve(
            {"aspect_ratio": "9:16", "nai_artist": "", "nai_size_code": size_code, "image_intent": "x"},
            AsyncMock(return_value="dir"),
        )
        assert resolved["nai_size"] == expected

    @pytest.mark.asyncio
    async def test_negative_passthrough(self):
        resolved = await _resolve(
            {"aspect_ratio": "9:16", "nai_artist": "", "nai_size_code": "v", "image_intent": "x"},
            AsyncMock(return_value="dir"),
        )
        assert resolved["nai_negative"] == NAI_NEGATIVE
