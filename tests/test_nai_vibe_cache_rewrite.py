# -*- coding: utf-8 -*-
"""nai_vibe_cache_rewrite 单测（content_json vibe cache 协同纯逻辑）。

用真实 VibeCacheService(临时 db) 注入，验证查改写 / 落库 / stale 清理 / 注释解析。
"""

import base64
import json

import pytest

from services.nai_vibe_cache import VibeCacheService, compute_image_hash
from services.nai_vibe_cache_rewrite import (
    extract_vibe_cache_ids,
    looks_like_stale_vibe_cache_error,
    persist_vibe_cache_ids,
    purge_vibe_cache_hits,
    rewrite_content_json_for_vibe_cache,
)


def _img_b64(tag: bytes) -> str:
    return base64.b64encode(b"IMG-" + tag).decode()


@pytest.fixture
def service(tmp_path):
    return VibeCacheService(tmp_path / "vibe.db")


class TestExtractVibeCacheIds:
    def test_parses_comment(self):
        content = '![img](data:image/png;base64,AAA)\n<!-- vibe_cache_ids:[{"index":0,"cache_id":"abc"}] -->'
        assert extract_vibe_cache_ids(content) == [{"index": 0, "cache_id": "abc"}]

    def test_multiple(self):
        content = '<!-- vibe_cache_ids:[{"index":0,"cache_id":"a"},{"index":1,"cache_id":"b"}] -->'
        assert extract_vibe_cache_ids(content) == [{"index": 0, "cache_id": "a"}, {"index": 1, "cache_id": "b"}]

    def test_no_comment_returns_empty(self):
        assert extract_vibe_cache_ids("![img](data:image/png;base64,AAA)") == []

    def test_malformed_json_returns_empty(self):
        assert extract_vibe_cache_ids("<!-- vibe_cache_ids:[not json -->") == []

    def test_drops_invalid_entries(self):
        # 空 cache_id / 缺 index 的条目被剔除
        content = '<!-- vibe_cache_ids:[{"index":0,"cache_id":""},{"index":1,"cache_id":"b"},{"cache_id":"c"}] -->'
        assert extract_vibe_cache_ids(content) == [{"index": 1, "cache_id": "b"}]

    def test_empty_content(self):
        assert extract_vibe_cache_ids("") == []


class TestRewriteContentJson:
    def test_no_controlnet_returns_original(self, service):
        cj = json.dumps({"prompt": "1girl", "size": [832, 1216]})
        out, persist, hit = rewrite_content_json_for_vibe_cache(cj, model="m1", service=service)
        assert out == cj
        assert persist == [] and hit == []

    def test_invalid_json_returns_original(self, service):
        out, persist, hit = rewrite_content_json_for_vibe_cache("not json", model="m1", service=service)
        assert out == "not json"
        assert persist == [] and hit == []

    def test_miss_records_persist_plan_no_rewrite(self, service):
        img = _img_b64(b"a")
        cj = json.dumps({"prompt": "x", "controlnet": {"images": [{"image": img, "info_extracted": 0.7}], "strength": 1.0}})
        out, persist, hit = rewrite_content_json_for_vibe_cache(cj, model="m1", service=service)
        # 未命中：persist_plan 一条，hit 空，content_json 仍含原图字节（未改写成 cache_id）
        assert len(persist) == 1
        assert persist[0][0] == 0                       # index
        assert persist[0][1] == compute_image_hash(img) # image_hash
        assert hit == []
        assert json.loads(out)["controlnet"]["images"][0]["image"]

    def test_hit_rewrites_to_cache_id(self, service):
        img = _img_b64(b"a")
        h = compute_image_hash(img)
        service.persist(image_hash=h, model_id="m1", info_extracted=0.7, cache_id="CID")
        cj = json.dumps({"controlnet": {"images": [{"image": img, "info_extracted": 0.7, "strength": 0.6}], "strength": 1.0}})
        out, persist, hit = rewrite_content_json_for_vibe_cache(cj, model="m1", service=service)
        img0 = json.loads(out)["controlnet"]["images"][0]
        assert img0 == {"cache_id": "CID", "strength": 0.6}  # 改写成复用态，保留 strength，去掉 image
        assert "image" not in img0
        assert hit == [(h, 0.7)]
        assert persist == []

    def test_existing_cache_id_passthrough(self, service):
        cj = json.dumps({"controlnet": {"images": [{"cache_id": "X", "strength": 0.5}], "strength": 1.0}})
        out, persist, hit = rewrite_content_json_for_vibe_cache(cj, model="m1", service=service)
        # 已是复用态：无 persist/hit，原样返回
        assert persist == [] and hit == []
        assert json.loads(out)["controlnet"]["images"][0] == {"cache_id": "X", "strength": 0.5}

    def test_model_isolation_no_hit(self, service):
        img = _img_b64(b"a")
        h = compute_image_hash(img)
        service.persist(image_hash=h, model_id="m1", info_extracted=0.7, cache_id="CID")
        cj = json.dumps({"controlnet": {"images": [{"image": img, "info_extracted": 0.7}], "strength": 1.0}})
        out, persist, hit = rewrite_content_json_for_vibe_cache(cj, model="m2", service=service)  # 不同 model
        assert hit == []
        assert len(persist) == 1   # 未命中 → 待落库


class TestPersistVibeCacheIds:
    def test_persists_by_index(self, service):
        img = _img_b64(b"a")
        h = compute_image_hash(img)
        content = '<!-- vibe_cache_ids:[{"index":0,"cache_id":"NEWCID"}] -->'
        n = persist_vibe_cache_ids(content, model="m1", persist_plan=[(0, h, 0.7)], service=service)
        assert n == 1
        assert service.lookup(image_hash=h, model_id="m1", info_extracted=0.7) == "NEWCID"

    def test_empty_plan(self, service):
        content = '<!-- vibe_cache_ids:[{"index":0,"cache_id":"X"}] -->'
        assert persist_vibe_cache_ids(content, model="m1", persist_plan=[], service=service) == 0

    def test_no_comment(self, service):
        assert persist_vibe_cache_ids("no comment", model="m1", persist_plan=[(0, "h", 0.7)], service=service) == 0


class TestLooksLikeStale:
    def test_positive(self):
        assert looks_like_stale_vibe_cache_error("Error: cache_id not found") is True
        assert looks_like_stale_vibe_cache_error("cache_id 已过期") is True

    def test_negative_no_cache_id_keyword(self):
        assert looks_like_stale_vibe_cache_error("some other 400 error") is False

    def test_negative_cache_id_but_no_stale_marker(self):
        assert looks_like_stale_vibe_cache_error("cache_id accepted ok") is False

    def test_empty(self):
        assert looks_like_stale_vibe_cache_error("") is False


class TestPurgeHits:
    def test_purge(self, service):
        service.persist(image_hash="h1", model_id="m1", info_extracted=0.7, cache_id="c1")
        assert purge_vibe_cache_hits("m1", [("h1", 0.7)], service=service) == 1
        assert service.lookup(image_hash="h1", model_id="m1", info_extracted=0.7) is None

    def test_empty_plan(self, service):
        assert purge_vibe_cache_hits("m1", [], service=service) == 0
