# -*- coding: utf-8 -*-
"""nai_director / 变量 LLM「假成功」防御单测。

针对真机事故：框架 llm_api 偶发返回 success=True 但 content='Token count: 0'（空响应透传），
被当 tag 送去出图 → 画出「只有质量词+画师串、无主体」的乱图。
本测试锁住防御：废输出被识别 → 重试 → 仍废则抛错（绝不返回废串当 prompt）。
"""

import pytest
from unittest.mock import AsyncMock

import services.nai_draw_core as core
from services.nai_draw_core import _is_degenerate_llm_output, generate_variable_with_llm


def _get_config(key, default=None):
    return default


class TestIsDegenerateOutput:
    def test_empty_is_degenerate(self):
        assert _is_degenerate_llm_output("")
        assert _is_degenerate_llm_output("   ")

    def test_token_count_placeholder_is_degenerate(self):
        assert _is_degenerate_llm_output("Token count: 0")
        assert _is_degenerate_llm_output("token count: 123")
        assert _is_degenerate_llm_output("  Token count: 0  ")

    def test_normal_tags_not_degenerate(self):
        assert not _is_degenerate_llm_output("1girl, solo, cat ears, down jacket")
        # 正常 tag 串里即便后面出现 token 字样也不误伤（只拦开头占位）
        assert not _is_degenerate_llm_output("1girl, holding token, smile")


class TestGenerateVariableDefense:
    @pytest.mark.asyncio
    async def test_good_output_returned(self, monkeypatch):
        monkeypatch.setattr(
            core.llm_api, "generate_with_model",
            AsyncMock(return_value=(True, "1girl, solo, smile", "", "g3f")),
        )
        out = await generate_variable_with_llm(_get_config, "prompt")
        assert out == "1girl, solo, smile"

    @pytest.mark.asyncio
    async def test_token_count_retried_then_good(self, monkeypatch):
        # 第一次废，第二次正常 → 返回第二次
        mock = AsyncMock(side_effect=[
            (True, "Token count: 0", "", "g3f"),
            (True, "1girl, solo, down jacket", "", "g3f"),
        ])
        monkeypatch.setattr(core.llm_api, "generate_with_model", mock)
        out = await generate_variable_with_llm(_get_config, "prompt")
        assert out == "1girl, solo, down jacket"
        assert mock.await_count == 2

    @pytest.mark.asyncio
    async def test_token_count_twice_raises(self, monkeypatch):
        # 连续两次废 → 抛错，绝不返回废串
        monkeypatch.setattr(
            core.llm_api, "generate_with_model",
            AsyncMock(return_value=(True, "Token count: 0", "", "g3f")),
        )
        with pytest.raises(RuntimeError, match="无效输出"):
            await generate_variable_with_llm(_get_config, "prompt")

    @pytest.mark.asyncio
    async def test_empty_twice_raises(self, monkeypatch):
        monkeypatch.setattr(
            core.llm_api, "generate_with_model",
            AsyncMock(return_value=(True, "", "", "g3f")),
        )
        with pytest.raises(RuntimeError, match="无效输出"):
            await generate_variable_with_llm(_get_config, "prompt")

    @pytest.mark.asyncio
    async def test_framework_failure_raises(self, monkeypatch):
        # success=False → 直接抛（原有行为保留）
        monkeypatch.setattr(
            core.llm_api, "generate_with_model",
            AsyncMock(return_value=(False, "生成内容时出错: timeout", "", "")),
        )
        with pytest.raises(RuntimeError, match="失败"):
            await generate_variable_with_llm(_get_config, "prompt")
