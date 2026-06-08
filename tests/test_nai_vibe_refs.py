# -*- coding: utf-8 -*-
"""nai_vibe_refs 单测：画风图库（扫文件夹版）——扫描 / 多选解析 / 读图 / 存图 / help。"""

import base64
import os

import pytest

from services import nai_vibe_refs as vr


@pytest.fixture
def refs_root(tmp_path, monkeypatch):
    """造一个临时插件根 + reference_images/，放几张假图，让 vr 扫它。"""
    refdir = tmp_path / "reference_images"
    refdir.mkdir()
    (refdir / "梦幻.png").write_bytes(b"AAA")
    (refdir / "厚涂.jpg").write_bytes(b"BBB")
    (refdir / "胶片.webp").write_bytes(b"CCC")
    (refdir / ".gitkeep").write_bytes(b"")        # 非图，应忽略
    (refdir / "readme.txt").write_bytes(b"x")      # 非图扩展名，应忽略
    monkeypatch.setattr(vr, "_plugin_root", lambda: str(tmp_path))
    return tmp_path


class TestListVibeImages:
    def test_scans_images_sorted(self, refs_root):
        imgs = vr.list_vibe_images()
        names = [i["name"] for i in imgs]
        # 只认图片扩展名，.gitkeep/.txt 被忽略；按文件名排序
        assert set(names) == {"梦幻", "厚涂", "胶片"}
        assert names == sorted(names)  # 排序稳定

    def test_empty_when_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vr, "_plugin_root", lambda: str(tmp_path))  # 无 reference_images/
        assert vr.list_vibe_images() == []


class TestResolvePhotoSelection:
    def test_empty(self, refs_root):
        assert vr.resolve_photo_selection([])[0] == "empty"

    def test_clear(self, refs_root):
        assert vr.resolve_photo_selection(["off"])[0] == "clear"
        assert vr.resolve_photo_selection(["取消"])[0] == "clear"

    def test_single_by_index(self, refs_root):
        status, chosen, _ = vr.resolve_photo_selection(["1"])
        assert status == "set" and len(chosen) == 1

    def test_multi_by_index(self, refs_root):
        status, chosen, _ = vr.resolve_photo_selection(["1", "2"])
        assert status == "set" and len(chosen) == 2

    def test_by_name(self, refs_root):
        status, chosen, _ = vr.resolve_photo_selection(["胶片"])
        assert status == "set" and chosen[0]["name"] == "胶片"

    def test_dedup(self, refs_root):
        status, chosen, _ = vr.resolve_photo_selection(["1", "1"])
        assert status == "set" and len(chosen) == 1

    def test_unknown(self, refs_root):
        status, _, unknown = vr.resolve_photo_selection(["99", "不存在"])
        assert status == "unknown" and "99" in unknown and "不存在" in unknown

    def test_partial_unknown_is_unknown(self, refs_root):
        status, _, unknown = vr.resolve_photo_selection(["1", "99"])
        assert status == "unknown" and "99" in unknown


class TestSelectedNames:
    def test_split(self):
        assert vr.selected_names("梦幻,厚涂") == ["梦幻", "厚涂"]
        assert vr.selected_names("") == []
        assert vr.selected_names(" a , , b ") == ["a", "b"]


class TestLoadImages:
    def test_load_reads_files_with_global_defaults(self, refs_root):
        out = vr.load_selected_images_base64("梦幻,厚涂", default_info_extracted=0.8, default_strength=0.55)
        assert len(out) == 2
        assert out[0]["image"] == base64.b64encode(b"AAA").decode()
        # 全局默认强度应用到每张
        assert out[0]["info_extracted"] == 0.8 and out[0]["strength"] == 0.55
        assert out[1]["info_extracted"] == 0.8 and out[1]["strength"] == 0.55

    def test_load_falls_back_to_const_defaults(self, refs_root):
        out = vr.load_selected_images_base64("梦幻")
        assert out[0]["info_extracted"] == 0.7 and out[0]["strength"] == 0.6

    def test_name_not_in_folder_skipped(self, refs_root):
        assert vr.load_selected_images_base64("不存在") == []

    def test_empty_selection(self, refs_root):
        assert vr.load_selected_images_base64("") == []


class TestSaveImage:
    def test_save_落盘_returns_name(self, refs_root):
        b64 = base64.b64encode(b"IMGDATA").decode()
        name = vr.save_reference_image("我的画风", b64)
        assert name == "我的画风"
        saved = refs_root / "reference_images" / "我的画风.png"
        assert saved.read_bytes() == b"IMGDATA"
        # 存完立即能扫到
        assert "我的画风" in [i["name"] for i in vr.list_vibe_images()]

    def test_save_sanitizes_name(self, refs_root):
        b64 = base64.b64encode(b"X").decode()
        name = vr.save_reference_image("a/b:c*d", b64)
        assert "/" not in name and ":" not in name and "*" not in name


class TestHelp:
    def test_help_lists(self, refs_root):
        h = vr.vibe_images_help()
        assert "梦幻" in h and "胶片" in h

    def test_help_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vr, "_plugin_root", lambda: str(tmp_path))
        assert "图库为空" in vr.vibe_images_help()
