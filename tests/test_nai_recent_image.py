# -*- coding: utf-8 -*-
"""nai_recent_image 单测：抓群里最近一张图 base64。

通过 monkeypatch 三个框架依赖 helper（_fetch_recent_messages / _query_image_path /
_read_image_base64）隔离框架，不触真实 DB / 文件 / sys.modules。
"""

import pytest

import services.nai_recent_image as mod
from services.nai_recent_image import get_recent_chat_image_base64, _extract_image_id


class _Msg:
    def __init__(self, text="", is_picid=False, is_emoji=False):
        self.processed_plain_text = text
        self.is_picid = is_picid
        self.is_emoji = is_emoji


def _setup(monkeypatch, *, messages, images_map, b64_map):
    monkeypatch.setattr(mod, "_fetch_recent_messages", lambda chat_id, lookback, hours: messages)
    monkeypatch.setattr(mod, "_query_image_path", lambda image_id: images_map.get(image_id))
    monkeypatch.setattr(mod, "_read_image_base64", lambda path: b64_map.get(path))


class TestExtractImageId:
    def test_extract(self):
        assert _extract_image_id("看 [picid:1a2b-3c4d] 这图") == "1a2b-3c4d"

    def test_none(self):
        assert _extract_image_id("纯文字") is None
        assert _extract_image_id("") is None


class TestGetRecentChatImage:
    def test_finds_latest_image(self, monkeypatch):
        msgs = [
            _Msg("早些的文字"),
            _Msg("一张图 [picid:img-001]", is_picid=True),
            _Msg("更近的文字"),
            _Msg("最近的图 [picid:img-002]", is_picid=True),
        ]
        _setup(monkeypatch, messages=msgs,
               images_map={"img-002": "/p/2.png", "img-001": "/p/1.png"},
               b64_map={"/p/2.png": "B64_TWO", "/p/1.png": "B64_ONE"})
        assert get_recent_chat_image_base64("chat1") == "B64_TWO"  # 逆序取最近 img-002

    def test_skips_emoji(self, monkeypatch):
        msgs = [
            _Msg("普通图 [picid:img-001]", is_picid=True),
            _Msg("表情包 [picid:emoji-9]", is_picid=True, is_emoji=True),
        ]
        _setup(monkeypatch, messages=msgs,
               images_map={"img-001": "/p/1.png"},
               b64_map={"/p/1.png": "B64_ONE"})
        assert get_recent_chat_image_base64("chat1") == "B64_ONE"  # 最近是表情包→跳过

    def test_no_image_returns_none(self, monkeypatch):
        _setup(monkeypatch, messages=[_Msg("纯文字1"), _Msg("纯文字2")], images_map={}, b64_map={})
        assert get_recent_chat_image_base64("chat1") is None

    def test_no_messages_returns_none(self, monkeypatch):
        _setup(monkeypatch, messages=[], images_map={}, b64_map={})
        assert get_recent_chat_image_base64("chat1") is None

    def test_picid_not_in_images_skips(self, monkeypatch):
        msgs = [
            _Msg("能查到 [picid:img-001]", is_picid=True),
            _Msg("查不到 [picid:img-missing]", is_picid=True),
        ]
        _setup(monkeypatch, messages=msgs,
               images_map={"img-001": "/p/1.png"},  # img-missing 不在
               b64_map={"/p/1.png": "B64_ONE"})
        assert get_recent_chat_image_base64("chat1") == "B64_ONE"  # 最近的查不到→退而取更早的

    def test_empty_chat_id_returns_none(self):
        assert get_recent_chat_image_base64("") is None

    def test_picid_true_but_no_picid_in_text_skips(self, monkeypatch):
        msgs = [
            _Msg("有标记但文本无占位", is_picid=True),
            _Msg("正常图 [picid:img-001]", is_picid=True),
        ]
        _setup(monkeypatch, messages=msgs,
               images_map={"img-001": "/p/1.png"},
               b64_map={"/p/1.png": "B64_ONE"})
        assert get_recent_chat_image_base64("chat1") == "B64_ONE"

    def test_fetch_exception_returns_none(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(mod, "_fetch_recent_messages", _boom)
        assert get_recent_chat_image_base64("chat1") is None  # 失败安全
