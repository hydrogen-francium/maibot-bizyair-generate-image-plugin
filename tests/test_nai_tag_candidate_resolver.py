# -*- coding: utf-8 -*-
"""nai_tag_candidate_resolver 单测：仅 online 调度 + 失败安全（绝不回退 local）。

mock get_online_retriever，不触网。验证：
- 未启用 / 配置非 dict / query 空 → 空串（且不取检索器）
- online 命中 → 返回 format_candidates 文本
- 无结果 / 检索器 None / retrieve 抛异常 → 空串（失败安全）
- mode 非 online 也按 online 跑（本插件只实现 online）
"""

import pytest

from services.nai_tag_candidate_resolver import resolve_tag_candidates

_ENABLED = {"enabled": True, "mode": "online"}


class _FakeRetriever:
    def __init__(self, results=None, raise_exc=None, formatted="<formatted-block>"):
        self._results = results if results is not None else {"search": [], "related": []}
        self._raise = raise_exc
        self._formatted = formatted
        self.retrieve_calls = []

    async def retrieve(self, query):
        self.retrieve_calls.append(query)
        if self._raise is not None:
            raise self._raise
        return self._results

    def format_candidates(self, results):
        return self._formatted


def _patch_retriever(monkeypatch, retriever):
    monkeypatch.setattr(
        "services.nai_tag_candidate_resolver.get_online_retriever",
        lambda **kwargs: retriever,
    )


class TestResolveTagCandidates:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty_without_touching_retriever(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            "services.nai_tag_candidate_resolver.get_online_retriever",
            lambda **kw: called.append(1),
        )
        assert await resolve_tag_candidates({"enabled": False}, "画猫娘") == ""
        assert called == []  # 未启用：连检索器单例都不取

    @pytest.mark.asyncio
    async def test_non_dict_config_returns_empty(self):
        assert await resolve_tag_candidates(None, "画猫娘") == ""
        assert await resolve_tag_candidates("nope", "画猫娘") == ""

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self, monkeypatch):
        fake = _FakeRetriever(results={"search": [{"tag": "x"}], "related": []})
        _patch_retriever(monkeypatch, fake)
        assert await resolve_tag_candidates(_ENABLED, "") == ""
        assert await resolve_tag_candidates(_ENABLED, "   ") == ""
        assert fake.retrieve_calls == []  # query 空：不触发检索

    @pytest.mark.asyncio
    async def test_online_hit_returns_formatted(self, monkeypatch):
        fake = _FakeRetriever(
            results={"search": [{"tag": "cat_girl"}], "related": []},
            formatted="<tag_candidates>...</tag_candidates>",
        )
        _patch_retriever(monkeypatch, fake)
        out = await resolve_tag_candidates(_ENABLED, "画猫娘")
        assert out == "<tag_candidates>...</tag_candidates>"
        assert fake.retrieve_calls == ["画猫娘"]

    @pytest.mark.asyncio
    async def test_no_results_returns_empty(self, monkeypatch):
        fake = _FakeRetriever(results={"search": [], "related": []})
        _patch_retriever(monkeypatch, fake)
        assert await resolve_tag_candidates(_ENABLED, "冷门概念") == ""

    @pytest.mark.asyncio
    async def test_retrieve_exception_returns_empty(self, monkeypatch):
        # 失败安全：retrieve 抛异常 → 空串（不回退 local、不向上抛）
        fake = _FakeRetriever(raise_exc=RuntimeError("network down"))
        _patch_retriever(monkeypatch, fake)
        assert await resolve_tag_candidates(_ENABLED, "画猫娘") == ""

    @pytest.mark.asyncio
    async def test_retriever_none_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "services.nai_tag_candidate_resolver.get_online_retriever",
            lambda **kw: None,
        )
        assert await resolve_tag_candidates(_ENABLED, "画猫娘") == ""

    @pytest.mark.asyncio
    async def test_non_online_mode_still_runs_online(self, monkeypatch):
        # mode=local 也按 online 处理（本插件只实现 online，不存在 local 分支）
        fake = _FakeRetriever(
            results={"search": [{"tag": "x"}], "related": []},
            formatted="<block>",
        )
        _patch_retriever(monkeypatch, fake)
        out = await resolve_tag_candidates({"enabled": True, "mode": "local"}, "画猫娘")
        assert out == "<block>"
        assert fake.retrieve_calls == ["画猫娘"]
