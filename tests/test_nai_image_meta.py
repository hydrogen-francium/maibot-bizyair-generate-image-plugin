# -*- coding: utf-8 -*-
"""nai_image_meta 单测（移植 nai_draw image_meta，纯 stdlib 解 PNG / JPEG / WebP 头）。

i2i 尺寸对齐/校验依赖 read_image_dimensions；本仓无 Pillow，故只解文件头拿宽高。
"""

import base64
import struct

from services import nai_image_meta as meta


def _png(w: int, h: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x00" * 8


def _jpeg(w: int, h: int) -> bytes:
    # SOI + SOF0：段长(2) precision(1) height(2) width(2)
    return b"\xff\xd8\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w) + b"\x00" * 10


def _webp_vp8x(w: int, h: int) -> bytes:
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class TestReadImageDimensions:
    def test_png(self):
        assert meta.read_image_dimensions(_b64(_png(832, 1216))) == (832, 1216)

    def test_jpeg(self):
        assert meta.read_image_dimensions(_b64(_jpeg(1216, 832))) == (1216, 832)

    def test_webp_vp8x(self):
        assert meta.read_image_dimensions(_b64(_webp_vp8x(1024, 1024))) == (1024, 1024)

    def test_accepts_raw_bytes(self):
        assert meta.read_image_dimensions(_png(64, 128)) == (64, 128)

    def test_accepts_data_uri_prefix(self):
        assert meta.read_image_dimensions("data:image/png;base64," + _b64(_png(512, 768))) == (512, 768)

    def test_non_image_returns_none(self):
        assert meta.read_image_dimensions(_b64(b"hello world, definitely not an image")) is None

    def test_truncated_png_returns_none(self):
        assert meta.read_image_dimensions(_b64(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4)) is None

    def test_empty_returns_none(self):
        assert meta.read_image_dimensions("") is None


class TestNormalizeImageBase64:
    def test_strips_data_uri_prefix_and_newlines(self):
        assert meta.normalize_image_base64("data:image/png;base64,AAAA\nBBBB\r\n") == "AAAABBBB"

    def test_plain_base64_unchanged(self):
        assert meta.normalize_image_base64("AAAABBBB") == "AAAABBBB"

    def test_empty_and_none(self):
        assert meta.normalize_image_base64("") == ""
        assert meta.normalize_image_base64(None) == ""
