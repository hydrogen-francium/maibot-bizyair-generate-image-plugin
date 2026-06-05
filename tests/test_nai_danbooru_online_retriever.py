# -*- coding: utf-8 -*-
"""nai_danbooru_online_retriever 单测：mock client，验 search+related 合并/去重、format、空结果。

不触网：替换 retriever.client 为假 client，注入预设响应。
"""

import pytest

from services.nai_danbooru_online_retriever import (
    DanbooruOnlineRetriever,
    get_online_retriever,
    reset_online_retriever,
)


class _FakeClient:
    """假 DanbooruOnlineClient：返回预设响应，记录调用。"""

    def __init__(self, search_resp=None, related_resp=None):
        self._search_resp = search_resp
        self._related_resp = related_resp
        self.search_calls = []
        self.related_calls = []

    async def search(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        return self._search_resp

    async def related(self, tags, **kwargs):
        self.related_calls.append((list(tags), kwargs))
        return self._related_resp


def _retriever_with(search_resp=None, related_resp=None) -> DanbooruOnlineRetriever:
    r = DanbooruOnlineRetriever()
    r.client = _FakeClient(search_resp=search_resp, related_resp=related_resp)
    return r


class TestRetrieve:
    @pytest.mark.asyncio
    async def test_merges_search_and_related_with_dedup(self):
        r = _retriever_with(
            search_resp={"results": [
                {"tag": "cat_girl", "cn_name": "猫娘", "final_score": 0.9, "category": "General"},
                {"tag": "cat_ears", "cn_name": "猫耳", "final_score": 0.8, "category": "General"},
            ]},
            related_resp=[
                {"tag": "tail", "cn_name": "尾巴", "cooc_score": 0.5, "category": "General"},
                {"tag": "cat_girl", "cn_name": "猫娘", "cooc_score": 0.4},  # 与 search 重复 → 去重
            ],
        )
        out = await r.retrieve("画猫娘")
        assert [x["tag"] for x in out["search"]] == ["cat_girl", "cat_ears"]
        # related 去重：已在 search 的 cat_girl 被排除
        assert [x["tag"] for x in out["related"]] == ["tail"]
        # related 用 search 命中的 tag 作种子
        assert r.client.related_calls[0][0] == ["cat_girl", "cat_ears"]
        # score 字段从 final_score 映射
        assert out["search"][0]["score"] == 0.9

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        r = _retriever_with()
        assert await r.retrieve("") == {"search": [], "related": []}
        assert await r.retrieve("   ") == {"search": [], "related": []}

    @pytest.mark.asyncio
    async def test_search_none_returns_empty(self):
        # client.search 返回 None（失败/超时）→ 空结构，不调 related
        r = _retriever_with(search_resp=None)
        out = await r.retrieve("画猫娘")
        assert out == {"search": [], "related": []}
        assert r.client.related_calls == []

    @pytest.mark.asyncio
    async def test_search_no_results_returns_empty(self):
        r = _retriever_with(search_resp={"results": []})
        out = await r.retrieve("xyz")
        assert out == {"search": [], "related": []}

    @pytest.mark.asyncio
    async def test_related_none_keeps_search(self):
        # related 失败返回 None → related 为空，但 search 保留
        r = _retriever_with(
            search_resp={"results": [{"tag": "a", "cn_name": "", "final_score": 0.9}]},
            related_resp=None,
        )
        out = await r.retrieve("x")
        assert [x["tag"] for x in out["search"]] == ["a"]
        assert out["related"] == []

    @pytest.mark.asyncio
    async def test_related_seed_count_limits_seeds(self):
        # related_seed_count 限定取前 N 个 search 结果作种子
        r = DanbooruOnlineRetriever(related_seed_count=2)
        r.client = _FakeClient(
            search_resp={"results": [
                {"tag": f"t{i}", "cn_name": "", "final_score": 0.9 - i * 0.1} for i in range(5)
            ]},
            related_resp=[],
        )
        await r.retrieve("x")
        assert r.client.related_calls[0][0] == ["t0", "t1"]

    @pytest.mark.asyncio
    async def test_search_results_hard_truncated_to_search_limit(self):
        # API 不严格遵守 limit（实测传 30 仍返回更多），retriever 按 search_limit 硬截断兜底
        r = DanbooruOnlineRetriever(search_limit=3, related_seed_count=2)
        r.client = _FakeClient(
            search_resp={"results": [
                {"tag": f"s{i}", "cn_name": "", "final_score": 0.9 - i * 0.05} for i in range(20)
            ]},
            related_resp=[],
        )
        out = await r.retrieve("x")
        assert len(out["search"]) == 3                      # 截到 search_limit
        assert [x["tag"] for x in out["search"]] == ["s0", "s1", "s2"]
        # 种子仍从截断后的前 related_seed_count 取
        assert r.client.related_calls[0][0] == ["s0", "s1"]

    @pytest.mark.asyncio
    async def test_related_results_hard_truncated_to_related_limit(self):
        # related 去重后按 related_limit 硬截断
        r = DanbooruOnlineRetriever(search_limit=50, related_limit=2, related_seed_count=1)
        r.client = _FakeClient(
            search_resp={"results": [{"tag": "seed", "final_score": 0.9}]},
            related_resp=[
                {"tag": f"r{i}", "cooc_score": 0.5 - i * 0.01} for i in range(10)
            ],
        )
        out = await r.retrieve("x")
        assert len(out["related"]) == 2                      # 截到 related_limit
        assert [x["tag"] for x in out["related"]] == ["r0", "r1"]


class TestFormatCandidates:
    def test_two_sections(self):
        r = DanbooruOnlineRetriever()
        text = r.format_candidates({
            "search": [{"tag": "cat_girl", "cn_name": "猫娘", "score": 0.9, "category": "General"}],
            "related": [{"tag": "tail", "cn_name": "尾巴", "cooc_score": 0.5, "category": "General"}],
        })
        assert text.startswith("<tag_candidates>")
        assert text.endswith("</tag_candidates>")
        assert "语义匹配" in text
        assert "共现推荐" in text
        assert "猫娘 → cat_girl" in text
        assert "尾巴 → tail" in text

    def test_search_only(self):
        r = DanbooruOnlineRetriever()
        text = r.format_candidates({
            "search": [{"tag": "solo", "cn_name": "", "score": 0.7, "category": "General"}],
            "related": [],
        })
        assert "语义匹配" in text
        assert "共现推荐" not in text
        assert "solo" in text

    def test_empty_returns_empty_string(self):
        r = DanbooruOnlineRetriever()
        assert r.format_candidates({"search": [], "related": []}) == ""


class TestSingleton:
    def test_disabled_returns_none(self):
        reset_online_retriever()
        assert get_online_retriever(enabled=False) is None

    def test_singleton_and_runtime_update(self):
        reset_online_retriever()
        r1 = get_online_retriever(enabled=True, search_limit=10)
        r2 = get_online_retriever(enabled=True, search_limit=99)
        assert r1 is r2
        assert r2.search_limit == 99  # update_runtime_config 生效
        reset_online_retriever()
