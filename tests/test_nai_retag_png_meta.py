# -*- coding: utf-8 -*-
"""nai_retag_png_meta 单测：纯标准库、完全离线。

构造带 tEXt/iTXt/zTXt chunk 的最小 PNG 字节，验证：
- NAI Comment(JSON) 命中、取 prompt 丢 uc
- SD parameters 取正向、丢 Negative prompt、遇 Steps 截断
- 通用字段（prompt/Description/UserComment）兜底
- 无元数据 / 非 PNG / 空 → None
- 负面绝不串入正向
"""

import json
import struct
import zlib

from services.nai_retag_png_meta import extract_prompt_from_png, PngMetaResult

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    """组一个合法 PNG chunk：length(4) + type(4) + data + crc(4)。"""
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _text_chunk(keyword: str, value: str) -> bytes:
    """tEXt chunk：keyword\\0value（latin-1）。"""
    data = keyword.encode("latin-1") + b"\x00" + value.encode("latin-1")
    return _chunk(b"tEXt", data)


def _itxt_chunk(keyword: str, value: str, *, compressed: bool = False) -> bytes:
    """iTXt chunk：keyword\\0 comp_flag comp_method lang\\0 translated\\0 text(utf-8)。"""
    text_bytes = value.encode("utf-8")
    if compressed:
        comp_flag = b"\x01"
        text_bytes = zlib.compress(text_bytes)
    else:
        comp_flag = b"\x00"
    data = (
        keyword.encode("latin-1") + b"\x00"
        + comp_flag + b"\x00"   # comp_flag + comp_method
        + b"\x00"               # language\0
        + b"\x00"               # translated_keyword\0
        + text_bytes
    )
    return _chunk(b"iTXt", data)


def _ztxt_chunk(keyword: str, value: str) -> bytes:
    """zTXt chunk：keyword\\0 comp_method compressed(latin-1)。"""
    compressed = zlib.compress(value.encode("latin-1"))
    data = keyword.encode("latin-1") + b"\x00" + b"\x00" + compressed
    return _chunk(b"zTXt", data)


def _make_png(*chunks: bytes) -> bytes:
    """拼一个最小 PNG：签名 + 给定 chunk + IEND。"""
    return _PNG_SIG + b"".join(chunks) + _chunk(b"IEND", b"")


class TestNaiComment:
    def test_json_comment_takes_prompt_drops_uc(self):
        comment = json.dumps({"prompt": "1girl, solo, smile", "uc": "lowres, bad anatomy"})
        png = _make_png(_text_chunk("Comment", comment))
        result = extract_prompt_from_png(png)
        assert isinstance(result, PngMetaResult)
        assert result.tags == ["1girl", "solo", "smile"]
        assert result.prompt == "1girl, solo, smile"
        # uc（负面）绝不串入
        assert "lowres" not in result.prompt
        assert "bad anatomy" not in result.prompt

    def test_json_comment_via_itxt(self):
        comment = json.dumps({"prompt": "cat_girl, cat ears"})
        png = _make_png(_itxt_chunk("Comment", comment))
        result = extract_prompt_from_png(png)
        assert result is not None
        assert result.tags == ["cat_girl", "cat ears"]

    def test_json_comment_compressed_itxt(self):
        comment = json.dumps({"prompt": "scenery, mountain, sky"})
        png = _make_png(_itxt_chunk("Comment", comment, compressed=True))
        result = extract_prompt_from_png(png)
        assert result is not None
        assert result.tags == ["scenery", "mountain", "sky"]

    def test_plain_text_comment(self):
        # Comment 不是 JSON：当纯 prompt 处理
        png = _make_png(_text_chunk("Comment", "1girl, masterpiece"))
        result = extract_prompt_from_png(png)
        assert result is not None
        assert result.tags == ["1girl", "masterpiece"]

    def test_comment_description_fallback(self):
        comment = json.dumps({"Description": "1boy, blue eyes"})
        png = _make_png(_text_chunk("Comment", comment))
        result = extract_prompt_from_png(png)
        assert result is not None
        assert result.tags == ["1boy", "blue eyes"]


class TestSdParameters:
    def test_takes_positive_drops_negative_and_params(self):
        params = (
            "masterpiece, 1girl, garden\n"
            "Negative prompt: lowres, worst quality, bad hands\n"
            "Steps: 28, Sampler: Euler a, CFG scale: 7"
        )
        png = _make_png(_text_chunk("parameters", params))
        result = extract_prompt_from_png(png)
        assert result is not None
        assert result.tags == ["masterpiece", "1girl", "garden"]
        assert "lowres" not in result.prompt
        assert "worst quality" not in result.prompt
        assert "Steps" not in result.prompt
        assert "Euler" not in result.prompt

    def test_multiline_positive_before_negative(self):
        params = (
            "best quality, 1girl,\n"
            "long hair, school uniform\n"
            "Negative prompt: nsfw\n"
            "Steps: 20"
        )
        png = _make_png(_text_chunk("parameters", params))
        result = extract_prompt_from_png(png)
        assert result is not None
        assert "best quality" in result.tags
        assert "school uniform" in result.tags
        assert "nsfw" not in result.prompt


class TestGenericFallback:
    def test_prompt_field(self):
        png = _make_png(_text_chunk("prompt", "solo, looking at viewer"))
        result = extract_prompt_from_png(png)
        assert result is not None
        assert result.tags == ["solo", "looking at viewer"]

    def test_priority_comment_over_generic(self):
        # Comment 优先于通用字段
        png = _make_png(
            _text_chunk("prompt", "should_not_win"),
            _text_chunk("Comment", json.dumps({"prompt": "comment_wins"})),
        )
        result = extract_prompt_from_png(png)
        assert result is not None
        assert result.tags == ["comment_wins"]


class TestNoMetadata:
    def test_png_without_text_chunks_returns_none(self):
        png = _make_png()  # 仅签名 + IEND
        assert extract_prompt_from_png(png) is None

    def test_empty_bytes_returns_none(self):
        assert extract_prompt_from_png(b"") is None

    def test_non_png_returns_none(self):
        assert extract_prompt_from_png(b"this is not a png file at all") is None

    def test_empty_comment_returns_none(self):
        png = _make_png(_text_chunk("Comment", ""))
        assert extract_prompt_from_png(png) is None

    def test_comment_json_without_prompt_falls_through_to_none(self):
        # Comment 是 JSON 但无 prompt/Description，且无其它字段 → None
        png = _make_png(_text_chunk("Comment", json.dumps({"uc": "only negative"})))
        assert extract_prompt_from_png(png) is None
