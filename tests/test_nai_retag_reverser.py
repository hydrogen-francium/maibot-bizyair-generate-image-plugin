# -*- coding: utf-8 -*-
"""nai_retag_reverser 单测：mock png_meta + mock WD14Client，验两级编排。

不触网。验证：
- 元数据命中 → source=metadata，不调 WD14
- 元数据未命中 → 走 WD14 → source=wd14
- 空 image_bytes → failed
- WD14 禁用 / 客户端 None → failed
- WD14 抛 WD14ClientError / 超时 / 其它异常 → failed（不向上抛）
- _flatten_wd14_tags 去重 + 保序
"""

import asyncio

import pytest

from services import nai_retag_reverser
from services.nai_retag_reverser import ReverseService, ReverseResult, _flatten_wd14_tags
from services.nai_retag_png_meta import PngMetaResult
from clients.wd14_client import WD14ClientError


class _FakeWD14:
    """假 WD14Client：返回预设 tag 结构或抛异常。"""

    command_timeout = 360.0

    def __init__(self, result=None, raise_exc=None):
        self._result = result if result is not None else {"tags": []}
        self._raise = raise_exc
        self.calls = []

    async def tag_image(self, image_base64, threshold=0.35, character_threshold=None):
        self.calls.append((threshold, character_threshold))
        if self._raise is not None:
            raise self._raise
        return self._result


def _patch_png(monkeypatch, return_value):
    monkeypatch.setattr(
        "services.nai_retag_reverser.extract_prompt_from_png",
        lambda image_bytes: return_value,
    )


class TestMetadataLevel:
    @pytest.mark.asyncio
    async def test_metadata_hit_skips_wd14(self, monkeypatch):
        _patch_png(monkeypatch, PngMetaResult(prompt="1girl, solo", tags=["1girl", "solo"]))
        wd14 = _FakeWD14()
        svc = ReverseService(wd14_client=wd14, wd14_enabled=True)
        result = await svc.reverse(b"\x89PNG fake bytes")
        assert result.source == "metadata"
        assert result.prompt == "1girl, solo"
        assert result.tags == ["1girl", "solo"]
        assert wd14.calls == []  # 元数据命中不调 WD14

    @pytest.mark.asyncio
    async def test_metadata_none_falls_to_wd14(self, monkeypatch):
        _patch_png(monkeypatch, None)
        wd14 = _FakeWD14(result={"tags": [{"label": "cat_girl", "score": 0.9}, {"label": "smile", "score": 0.8}]})
        svc = ReverseService(wd14_client=wd14, wd14_enabled=True)
        result = await svc.reverse(b"fake")
        assert result.source == "wd14"
        assert result.tags == ["cat_girl", "smile"]
        assert result.prompt == "cat_girl, smile"
        assert len(wd14.calls) == 1

    @pytest.mark.asyncio
    async def test_metadata_empty_tags_falls_to_wd14(self, monkeypatch):
        # 元数据解析出对象但 tags 为空 → 视为未命中，走 WD14
        _patch_png(monkeypatch, PngMetaResult(prompt="", tags=[]))
        wd14 = _FakeWD14(result={"tags": [{"label": "x", "score": 0.5}]})
        svc = ReverseService(wd14_client=wd14, wd14_enabled=True)
        result = await svc.reverse(b"fake")
        assert result.source == "wd14"

    @pytest.mark.asyncio
    async def test_metadata_exception_falls_to_wd14(self, monkeypatch):
        # png_meta 抛异常 → 跳过，走 WD14（不崩）
        def _boom(image_bytes):
            raise ValueError("corrupt png")
        monkeypatch.setattr("services.nai_retag_reverser.extract_prompt_from_png", _boom)
        wd14 = _FakeWD14(result={"tags": [{"label": "y", "score": 0.6}]})
        svc = ReverseService(wd14_client=wd14, wd14_enabled=True)
        result = await svc.reverse(b"fake")
        assert result.source == "wd14"


class TestFailedCases:
    @pytest.mark.asyncio
    async def test_empty_bytes(self):
        svc = ReverseService(wd14_client=_FakeWD14(), wd14_enabled=True)
        result = await svc.reverse(b"")
        assert result.source == "failed"
        assert "为空" in (result.detail or "")

    @pytest.mark.asyncio
    async def test_wd14_disabled(self, monkeypatch):
        _patch_png(monkeypatch, None)
        svc = ReverseService(wd14_client=_FakeWD14(), wd14_enabled=False)
        result = await svc.reverse(b"fake")
        assert result.source == "failed"
        assert "未启用 WD14" in (result.detail or "")

    @pytest.mark.asyncio
    async def test_wd14_client_none(self, monkeypatch):
        _patch_png(monkeypatch, None)
        svc = ReverseService(wd14_client=None, wd14_enabled=True)
        result = await svc.reverse(b"fake")
        assert result.source == "failed"
        assert "未初始化" in (result.detail or "")

    @pytest.mark.asyncio
    async def test_wd14_client_error(self, monkeypatch):
        _patch_png(monkeypatch, None)
        wd14 = _FakeWD14(raise_exc=WD14ClientError("所有 Spaces 都无法使用"))
        svc = ReverseService(wd14_client=wd14, wd14_enabled=True)
        result = await svc.reverse(b"fake")
        assert result.source == "failed"
        assert "WD14" in (result.detail or "")

    @pytest.mark.asyncio
    async def test_wd14_timeout(self, monkeypatch):
        _patch_png(monkeypatch, None)
        wd14 = _FakeWD14(raise_exc=asyncio.TimeoutError())
        svc = ReverseService(wd14_client=wd14, wd14_enabled=True)
        result = await svc.reverse(b"fake")
        assert result.source == "failed"
        assert "超时" in (result.detail or "")

    @pytest.mark.asyncio
    async def test_wd14_generic_exception(self, monkeypatch):
        _patch_png(monkeypatch, None)
        wd14 = _FakeWD14(raise_exc=RuntimeError("unexpected"))
        svc = ReverseService(wd14_client=wd14, wd14_enabled=True)
        result = await svc.reverse(b"fake")
        assert result.source == "failed"

    @pytest.mark.asyncio
    async def test_wd14_empty_tags(self, monkeypatch):
        _patch_png(monkeypatch, None)
        wd14 = _FakeWD14(result={"tags": []})
        svc = ReverseService(wd14_client=wd14, wd14_enabled=True)
        result = await svc.reverse(b"fake")
        assert result.source == "failed"
        assert "未识别到任何标签" in (result.detail or "")


class TestFlattenWd14Tags:
    def test_dedup_keeps_order(self):
        wd14_result = {"tags": [
            {"label": "1girl", "score": 0.9},
            {"label": "solo", "score": 0.8},
            {"label": "1girl", "score": 0.7},  # 重复 → 去掉
            {"label": "smile", "score": 0.6},
        ]}
        assert _flatten_wd14_tags(wd14_result) == ["1girl", "solo", "smile"]

    def test_skips_empty_and_non_dict(self):
        wd14_result = {"tags": [
            {"label": "", "score": 0.9},
            "not_a_dict",
            {"label": "  ", "score": 0.5},
            {"label": "valid", "score": 0.4},
        ]}
        assert _flatten_wd14_tags(wd14_result) == ["valid"]

    def test_non_list_returns_empty(self):
        assert _flatten_wd14_tags({"tags": None}) == []
        assert _flatten_wd14_tags({}) == []
        assert _flatten_wd14_tags("not a dict") == []


class TestRuntimeUpdate:
    def test_update_thresholds_and_client(self):
        svc = ReverseService(wd14_enabled=False)
        new_client = _FakeWD14()
        svc.update_wd14_client(new_client)
        svc.update_wd14_thresholds(threshold=0.5, character_threshold=0.9, enabled=True)
        assert svc._wd14_client is new_client
        assert svc._wd14_threshold == 0.5
        assert svc._wd14_character_threshold == 0.9
        assert svc._wd14_enabled is True
