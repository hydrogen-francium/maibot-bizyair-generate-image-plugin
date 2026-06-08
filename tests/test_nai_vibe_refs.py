# -*- coding: utf-8 -*-
"""nai_vibe_refs 单测：画风图库的多选解析 / 读图 / 存图 / help。"""

import base64
import os

import pytest

from services import nai_vibe_refs as vr


_PRESETS = [
    {"name": "梦幻", "path": "reference_images/a.png", "info_extracted": 0.8},
    {"name": "厚涂", "path": "reference_images/b.jpg"},
    {"name": "胶片", "path": "reference_images/c.png", "strength": 0.5},
]


class TestNormalize:
    def test_skips_invalid(self):
        raw = [
            {"name": "ok", "path": "x.png"},
            {"name": "", "path": "y.png"},       # 无名跳过
            {"name": "z", "path": ""},           # 无路径跳过
            "not_a_dict",
        ]
        out = vr.normalize_vibe_presets(raw)
        assert [p["name"] for p in out] == ["ok"]

    def test_non_list(self):
        assert vr.normalize_vibe_presets(None) == []


class TestResolvePhotoSelection:
    def test_empty(self):
        assert vr.resolve_photo_selection([], _PRESETS)[0] == "empty"

    def test_clear(self):
        assert vr.resolve_photo_selection(["off"], _PRESETS)[0] == "clear"
        assert vr.resolve_photo_selection(["取消"], _PRESETS)[0] == "clear"

    def test_single_by_index(self):
        status, chosen, unknown = vr.resolve_photo_selection(["1"], _PRESETS)
        assert status == "set"
        assert [c["name"] for c in chosen] == ["梦幻"]

    def test_multi_by_index(self):
        status, chosen, _ = vr.resolve_photo_selection(["1", "2"], _PRESETS)
        assert status == "set"
        assert [c["name"] for c in chosen] == ["梦幻", "厚涂"]

    def test_by_name(self):
        status, chosen, _ = vr.resolve_photo_selection(["胶片"], _PRESETS)
        assert status == "set" and chosen[0]["name"] == "胶片"

    def test_dedup(self):
        status, chosen, _ = vr.resolve_photo_selection(["1", "1", "梦幻"], _PRESETS)
        assert status == "set" and len(chosen) == 1  # 同一张去重

    def test_truncate_to_max(self):
        # 超过 4 张截断
        many = [{"name": f"p{i}", "path": f"{i}.png"} for i in range(6)]
        status, chosen, _ = vr.resolve_photo_selection(["1", "2", "3", "4", "5", "6"], many)
        assert status == "set" and len(chosen) == vr.MAX_VIBE_IMAGES

    def test_unknown(self):
        status, chosen, unknown = vr.resolve_photo_selection(["99", "不存在"], _PRESETS)
        assert status == "unknown"
        assert "99" in unknown and "不存在" in unknown

    def test_partial_unknown_is_unknown(self):
        # 有一个识别不了 → 整体 unknown（提示用户）
        status, chosen, unknown = vr.resolve_photo_selection(["1", "99"], _PRESETS)
        assert status == "unknown" and "99" in unknown


class TestSelectedNames:
    def test_split(self):
        assert vr.selected_names("梦幻,厚涂") == ["梦幻", "厚涂"]
        assert vr.selected_names("") == []
        assert vr.selected_names(" a , , b ") == ["a", "b"]


class TestLoadImages:
    def test_load_reads_files(self, tmp_path, monkeypatch):
        # 造两张假图文件，让 _plugin_root 指向 tmp
        root = tmp_path
        refdir = root / "reference_images"
        refdir.mkdir()
        (refdir / "a.png").write_bytes(b"AAA")
        (refdir / "b.jpg").write_bytes(b"BBB")
        monkeypatch.setattr(vr, "_plugin_root", lambda: str(root))
        presets = [
            {"name": "梦幻", "path": "reference_images/a.png", "info_extracted": 0.8},
            {"name": "厚涂", "path": "reference_images/b.jpg", "strength": 0.5},
        ]
        out = vr.load_selected_images_base64("梦幻,厚涂", presets)
        assert len(out) == 2
        assert out[0]["image"] == base64.b64encode(b"AAA").decode()
        assert out[0]["info_extracted"] == 0.8
        assert out[1]["strength"] == 0.5

    def test_missing_file_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vr, "_plugin_root", lambda: str(tmp_path))
        presets = [{"name": "梦幻", "path": "reference_images/nope.png"}]
        assert vr.load_selected_images_base64("梦幻", presets) == []

    def test_preset_not_found_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vr, "_plugin_root", lambda: str(tmp_path))
        assert vr.load_selected_images_base64("不存在", _PRESETS) == []

    def test_empty_selection(self):
        assert vr.load_selected_images_base64("", _PRESETS) == []


class TestSaveImage:
    def test_save_落盘(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vr, "_plugin_root", lambda: str(tmp_path))
        b64 = base64.b64encode(b"IMGDATA").decode()
        rel = vr.save_reference_image("我的画风", b64)
        assert rel == "reference_images/我的画风.png"
        saved = tmp_path / rel
        assert saved.read_bytes() == b"IMGDATA"

    def test_save_sanitizes_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vr, "_plugin_root", lambda: str(tmp_path))
        b64 = base64.b64encode(b"X").decode()
        rel = vr.save_reference_image("a/b:c*d", b64)
        # 非法文件名字符替换为下划线
        assert "/" not in os.path.basename(rel)
        assert rel.startswith("reference_images/")


class TestHelp:
    def test_help_lists(self):
        h = vr.vibe_presets_help(_PRESETS)
        assert "1. 梦幻" in h and "3. 胶片" in h

    def test_help_empty(self):
        assert "未配置" in vr.vibe_presets_help([])
