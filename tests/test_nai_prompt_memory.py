# -*- coding: utf-8 -*-
"""nai_prompt_memory 单测：render 继承规则块（默认全新）+ per-chat 内存存取 + TTL 过期。

不依赖框架/网络；TTL 用直接注入旧 timestamp 的方式测（不 monkeypatch time，靠真实 now 远大于旧 ts）。
"""

import pytest

from services.nai_prompt_memory import (
    _last_nai_context,
    get_last_nai_context,
    render_previous_prompt_block,
    reset_prompt_memory,
    set_last_nai_context,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_prompt_memory()
    yield
    reset_prompt_memory()


class TestRenderPreviousPromptBlock:
    def test_no_previous_returns_placeholder(self):
        for val in (None, "", "   "):
            block = render_previous_prompt_block(val)
            assert "<previous_prompt_context>" in block
            assert "</previous_prompt_context>" in block
            assert "无上一轮提示词" in block

    def test_with_previous_contains_prompt_and_default_new_rule(self):
        block = render_previous_prompt_block("solo, 1girl, smile")
        assert "solo, 1girl, smile" in block
        # 收紧后：默认全新，仅明确续画词才继承
        assert "默认全新" in block
        assert "续画" in block
        assert "完全忽略上方提示词" in block
        # 旧三档关键词已移除（避免大脑误判续承）
        assert "换角色保场景" not in block

    def test_with_last_request_includes_it(self):
        block = render_previous_prompt_block("solo, 1girl", last_request="画一个女孩")
        assert "上一轮用户请求" in block
        assert "画一个女孩" in block

    def test_without_last_request_omits_section(self):
        block = render_previous_prompt_block("solo, 1girl")
        assert "上一轮用户请求" not in block


class TestSetGetContext:
    def test_set_then_get(self):
        set_last_nai_context("c1", "solo, 1girl", "画女孩")
        p, r = get_last_nai_context("c1")
        assert p == "solo, 1girl"
        assert r == "画女孩"

    def test_blank_prompt_not_stored(self):
        set_last_nai_context("c1", "   ")
        assert get_last_nai_context("c1") == (None, None)

    def test_empty_chat_id_noop(self):
        set_last_nai_context("", "solo")
        assert get_last_nai_context("") == (None, None)

    def test_per_chat_isolation(self):
        set_last_nai_context("c1", "tag_a")
        set_last_nai_context("c2", "tag_b")
        assert get_last_nai_context("c1")[0] == "tag_a"
        assert get_last_nai_context("c2")[0] == "tag_b"

    def test_missing_returns_none(self):
        assert get_last_nai_context("never") == (None, None)

    def test_empty_request_returns_none(self):
        set_last_nai_context("c1", "solo")  # request 默认 ""
        p, r = get_last_nai_context("c1")
        assert p == "solo"
        assert r is None


class TestTTL:
    def test_expired_entry_cleared(self):
        # 直接注入很旧的 timestamp（真实 now - 1000 远大于 ttl=10）→ 过期
        _last_nai_context["c1"] = ("old prompt", "old req", 1000.0)
        p, r = get_last_nai_context("c1", ttl=10)
        assert (p, r) == (None, None)
        assert "c1" not in _last_nai_context  # 过期顺手清除

    def test_ttl_zero_never_expires(self):
        _last_nai_context["c2"] = ("p", "r", 1000.0)
        p, _ = get_last_nai_context("c2", ttl=0)
        assert p == "p"

    def test_fresh_entry_within_ttl(self):
        set_last_nai_context("c3", "fresh")  # ts = now
        p, _ = get_last_nai_context("c3", ttl=3600)
        assert p == "fresh"
