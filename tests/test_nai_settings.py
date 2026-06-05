# -*- coding: utf-8 -*-
"""NAI 运行时设置（services/nai_settings.py）单测：模型代号解析 + 标量写回。"""

import pytest

import src.common.toml_utils as toml_utils

from services import nai_settings


class TestResolveModelAlias:
    def test_code_to_full(self):
        assert nai_settings.resolve_model_alias("4.5") == "nai-diffusion-4-5-full"
        assert nai_settings.resolve_model_alias("4") == "nai-diffusion-4-full"
        assert nai_settings.resolve_model_alias("3") == "nai-diffusion-3"
        assert nai_settings.resolve_model_alias("f3") == "nai-diffusion-furry-3"
        assert nai_settings.resolve_model_alias("4c") == "nai-diffusion-4-curated"
        assert nai_settings.resolve_model_alias("4.5c") == "nai-diffusion-4-5-curated"

    def test_full_name_passthrough(self):
        assert nai_settings.resolve_model_alias("nai-diffusion-4-5-full") == "nai-diffusion-4-5-full"

    def test_unknown_returns_none(self):
        assert nai_settings.resolve_model_alias("xyz") is None
        assert nai_settings.resolve_model_alias("") is None
        assert nai_settings.resolve_model_alias("   ") is None

    def test_whitespace_trimmed(self):
        assert nai_settings.resolve_model_alias("  4.5  ") == "nai-diffusion-4-5-full"

    def test_alias_table_complete(self):
        assert set(nai_settings.MODEL_ALIASES) == {"3", "f3", "4c", "4", "4.5c", "4.5"}
        # help 行包含每个全名
        help_text = nai_settings.model_alias_help()
        for full in nai_settings.MODEL_ALIASES.values():
            assert full in help_text


class TestSaveSetting:
    def test_save_success_passes_section_scalar(self):
        toml_utils.save_toml_with_format.reset_mock()
        toml_utils.save_toml_with_format.side_effect = None
        ok = nai_settings.save_setting(nai_settings.NAI_MODEL_KEY, "nai-diffusion-4-5-full")
        assert ok is True
        toml_utils.save_toml_with_format.assert_called_once()
        args, _ = toml_utils.save_toml_with_format.call_args
        # 第一个位置参数必须是 {section: {key: value}}，确保只动顶层标量、不碰数组
        assert args[0] == {
            nai_settings.SETTINGS_SECTION: {
                nai_settings.NAI_MODEL_KEY: "nai-diffusion-4-5-full"
            }
        }

    def test_save_failure_returns_false(self):
        toml_utils.save_toml_with_format.reset_mock()
        toml_utils.save_toml_with_format.side_effect = RuntimeError("disk full")
        try:
            ok = nai_settings.save_setting(nai_settings.NAI_SFW_FILTER_KEY, True)
            assert ok is False
        finally:
            toml_utils.save_toml_with_format.side_effect = None  # 复位，避免污染其它用例


class TestResolveSizeAlias:
    def test_canonical_passthrough(self):
        for code in ("v", "h", "s", "auto"):
            assert nai_settings.resolve_size_alias(code) == code

    def test_chinese_and_english_aliases(self):
        assert nai_settings.resolve_size_alias("竖") == "v"
        assert nai_settings.resolve_size_alias("vertical") == "v"
        assert nai_settings.resolve_size_alias("横") == "h"
        assert nai_settings.resolve_size_alias("landscape") == "h"
        assert nai_settings.resolve_size_alias("方") == "s"
        assert nai_settings.resolve_size_alias("自动") == "auto"

    def test_case_insensitive_and_trim(self):
        assert nai_settings.resolve_size_alias("  V  ") == "v"
        assert nai_settings.resolve_size_alias("AUTO") == "auto"

    def test_unknown_returns_none(self):
        assert nai_settings.resolve_size_alias("x") is None
        assert nai_settings.resolve_size_alias("") is None
        assert nai_settings.resolve_size_alias("   ") is None


class TestAspectRatioToSizeCode:
    @pytest.mark.parametrize(
        "aspect, code",
        [
            ("1:1", "s"),
            ("4:3", "h"),
            ("16:9", "h"),
            ("9:16", "v"),
            ("auto", "v"),
            ("3:4", "v"),  # 未知比例缺省走竖图
            ("", "v"),
            (None, "v"),
        ],
    )
    def test_mapping(self, aspect, code):
        assert nai_settings.aspect_ratio_to_size_code(aspect) == code


class TestResolveSizeCode:
    def test_override_wins(self):
        # 显式 v/h/s 覆盖优先，忽略 aspect_ratio
        assert nai_settings.resolve_size_code("v", "1:1") == "v"
        assert nai_settings.resolve_size_code("h", "9:16") == "h"
        assert nai_settings.resolve_size_code("s", "16:9") == "s"

    def test_auto_follows_aspect_ratio(self):
        # auto / 空 / 不可识别 override → 跟随画面比例（精确复刻旧 nai_size 行为）
        assert nai_settings.resolve_size_code("auto", "1:1") == "s"
        assert nai_settings.resolve_size_code("auto", "9:16") == "v"
        assert nai_settings.resolve_size_code("", "16:9") == "h"
        assert nai_settings.resolve_size_code("garbage", "4:3") == "h"

    def test_aliases_normalized_in_override(self):
        assert nai_settings.resolve_size_code("竖", "1:1") == "v"
        assert nai_settings.resolve_size_code("横", "1:1") == "h"


class TestArtistPresets:
    PRESETS = [
        {"name": "通透厚涂", "prompt": "artist:wlop, artist:as109"},
        {"name": "清新二次元", "prompt": "artist:ciloranko"},
        {"name": "", "prompt": "缺名应被过滤"},  # 非法项
        {"prompt": "缺名2"},                      # 非法项
        "不是字典",                                # 非法项
    ]

    def test_normalize_filters_invalid(self):
        out = nai_settings.normalize_artist_presets(self.PRESETS)
        assert [p["name"] for p in out] == ["通透厚涂", "清新二次元"]

    def test_normalize_non_list(self):
        assert nai_settings.normalize_artist_presets(None) == []
        assert nai_settings.normalize_artist_presets("x") == []

    def test_resolve_by_index(self):
        status, name, prompt = nai_settings.resolve_artist_choice("1", self.PRESETS)
        assert (status, name) == ("set", "通透厚涂")
        assert prompt == "artist:wlop, artist:as109"
        status, name, _ = nai_settings.resolve_artist_choice("2", self.PRESETS)
        assert (status, name) == ("set", "清新二次元")

    def test_resolve_by_name_case_insensitive(self):
        status, name, prompt = nai_settings.resolve_artist_choice("清新二次元", self.PRESETS)
        assert (status, name) == ("set", "清新二次元")
        assert prompt == "artist:ciloranko"

    def test_resolve_clear_tokens(self):
        for token in ("off", "OFF", "clear", "none", "取消", "关闭", "无"):
            status, _, _ = nai_settings.resolve_artist_choice(token, self.PRESETS)
            assert status == "clear", token

    def test_resolve_unknown(self):
        for arg in ("99", "0", "不存在的画师", ""):
            status, _, _ = nai_settings.resolve_artist_choice(arg, self.PRESETS)
            assert status == "unknown", arg

    def test_help_lists_with_index(self):
        text = nai_settings.artist_presets_help(self.PRESETS)
        assert "1. 通透厚涂" in text
        assert "2. 清新二次元" in text

    def test_help_empty(self):
        assert "未配置" in nai_settings.artist_presets_help([])


class TestResolveNaiPresetName:
    PRESETS = [
        {"preset_name": "nai_default", "model": "nai-diffusion-4-5-full"},
        {"preset_name": "nai_old", "model": "nai-diffusion-3"},
    ]

    def test_active_is_nai_preset_uses_it(self):
        # 当前已在某 NAI 预设 → 直接用它（尊重用户选择）
        assert nai_settings.resolve_nai_preset_name("nai_old", self.PRESETS) == "nai_old"

    def test_active_is_gpt_falls_back_to_first_nai(self):
        # 当前在 GPT 预设 → 回落首个 NAI 预设，使命令始终是 NAI 专属语义
        assert nai_settings.resolve_nai_preset_name("自己找的二百一次", self.PRESETS) == "nai_default"

    def test_empty_active_falls_back_to_first(self):
        assert nai_settings.resolve_nai_preset_name("", self.PRESETS) == "nai_default"

    def test_no_nai_presets_returns_none(self):
        assert nai_settings.resolve_nai_preset_name("whatever", []) is None
        assert nai_settings.resolve_nai_preset_name("whatever", None) is None

    def test_skips_malformed_entries(self):
        raw = ["not a dict", {"no_name": 1}, {"preset_name": "  "}, {"preset_name": "nai_real"}]
        assert nai_settings.resolve_nai_preset_name("x", raw) == "nai_real"
