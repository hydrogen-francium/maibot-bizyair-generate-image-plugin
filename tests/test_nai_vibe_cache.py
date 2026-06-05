# -*- coding: utf-8 -*-
"""nai_vibe_cache 单测（SQLite vibe cache_id 持久化 + 图 hash + info_extracted 量化）。

用临时 db（VibeCacheService(tmp_path/...)）隔离，不碰插件 data/ 真实库。
"""

import base64
import hashlib

import pytest

from services.nai_vibe_cache import (
    VibeCacheService,
    compute_image_hash,
    iter_image_hashes,
    quantize_info_extracted,
)


@pytest.fixture
def service(tmp_path):
    return VibeCacheService(tmp_path / "vibe_cache.db")


class TestComputeImageHash:
    def test_bytes_sha256(self):
        assert compute_image_hash(b"hello") == hashlib.sha256(b"hello").hexdigest()

    def test_base64_str(self):
        raw = b"some image bytes"
        b64 = base64.b64encode(raw).decode()
        assert compute_image_hash(b64) == hashlib.sha256(raw).hexdigest()

    def test_data_uri_prefix_stripped(self):
        raw = b"img-bytes"
        b64 = base64.b64encode(raw).decode()
        assert compute_image_hash(f"data:image/png;base64,{b64}") == hashlib.sha256(raw).hexdigest()

    def test_empty_returns_empty(self):
        assert compute_image_hash("") == ""

    def test_garbage_returns_empty(self):
        # 解不出字节（全非 base64 字符）→ 空串，不抛错
        assert compute_image_hash("!!!!") == ""


class TestQuantizeInfoExtracted:
    def test_none_default(self):
        assert quantize_info_extracted(None) == 0.70

    def test_non_numeric_default(self):
        assert quantize_info_extracted("abc") == 0.70

    def test_below_or_zero_floor(self):
        assert quantize_info_extracted(0) == 0.01
        assert quantize_info_extracted(-5) == 0.01

    def test_above_one_ceil(self):
        assert quantize_info_extracted(5.0) == 1.00

    def test_quantized_to_step(self):
        assert quantize_info_extracted(0.7) == 0.70
        assert quantize_info_extracted(0.734) == 0.73
        assert quantize_info_extracted(0.736) == 0.74


class TestVibeCacheService:
    def test_lookup_miss_returns_none(self, service):
        assert service.lookup(image_hash="h1", model_id="m1", info_extracted=0.7) is None

    def test_persist_then_lookup(self, service):
        assert service.persist(image_hash="h1", model_id="m1", info_extracted=0.7, cache_id="cid1") is True
        assert service.lookup(image_hash="h1", model_id="m1", info_extracted=0.7) == "cid1"

    def test_lookup_quantization_tolerant(self, service):
        service.persist(image_hash="h1", model_id="m1", info_extracted=0.70, cache_id="cid1")
        # 0.704 量化到 0.70 → 命中（小数微差不应错位 key）
        assert service.lookup(image_hash="h1", model_id="m1", info_extracted=0.704) == "cid1"

    def test_lookup_model_isolation(self, service):
        service.persist(image_hash="h1", model_id="m1", info_extracted=0.7, cache_id="cid1")
        assert service.lookup(image_hash="h1", model_id="m2", info_extracted=0.7) is None

    def test_persist_replace(self, service):
        service.persist(image_hash="h1", model_id="m1", info_extracted=0.7, cache_id="cid1")
        service.persist(image_hash="h1", model_id="m1", info_extracted=0.7, cache_id="cid2")
        assert service.lookup(image_hash="h1", model_id="m1", info_extracted=0.7) == "cid2"
        assert service.count() == 1

    def test_persist_empty_cache_id_rejected(self, service):
        assert service.persist(image_hash="h1", model_id="m1", info_extracted=0.7, cache_id="") is False
        assert service.count() == 0

    def test_persist_missing_key_rejected(self, service):
        assert service.persist(image_hash="", model_id="m1", info_extracted=0.7, cache_id="cid") is False

    def test_delete(self, service):
        service.persist(image_hash="h1", model_id="m1", info_extracted=0.7, cache_id="cid1")
        assert service.delete(image_hash="h1", model_id="m1", info_extracted=0.7) is True
        assert service.lookup(image_hash="h1", model_id="m1", info_extracted=0.7) is None
        assert service.delete(image_hash="h1", model_id="m1", info_extracted=0.7) is False  # 已无

    def test_purge_and_count(self, service):
        service.persist(image_hash="h1", model_id="m1", info_extracted=0.7, cache_id="c1")
        service.persist(image_hash="h2", model_id="m1", info_extracted=0.5, cache_id="c2")
        assert service.count() == 2
        assert service.purge() == 2
        assert service.count() == 0

    def test_persist_across_reopen(self, tmp_path):
        # 跨实例（重启）持久化
        db = tmp_path / "vibe.db"
        VibeCacheService(db).persist(image_hash="h1", model_id="m1", info_extracted=0.7, cache_id="cid1")
        assert VibeCacheService(db).lookup(image_hash="h1", model_id="m1", info_extracted=0.7) == "cid1"


class TestIterImageHashes:
    def test_iter(self):
        b64 = base64.b64encode(b"x").decode()
        out = iter_image_hashes([{"image": b64}, {"cache_id": "c"}, "notdict"])
        assert out[0] == hashlib.sha256(b"x").hexdigest()
        assert out[1] == ""  # 无 image
        assert out[2] == ""  # 非 dict
