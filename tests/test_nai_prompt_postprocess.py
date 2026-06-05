# -*- coding: utf-8 -*-
"""NAI 提示词后处理（services/nai_prompt_postprocess.py）单测。

重点覆盖 P0 唯一接线的 strip_cjk_and_fullwidth（§8 硬约束），并对一并移植但 P0 未接线的
sanitize_sfw_prompt / normalize_prompt_order / remove_selfie_appearance_tags 做回归保护。
"""

from services.nai_prompt_postprocess import (
    normalize_prompt_order,
    remove_selfie_appearance_tags,
    sanitize_sfw_prompt,
    strip_cjk_and_fullwidth,
)


class TestStripCjkAndFullwidth:
    def test_clean_english_unchanged(self):
        assert strip_cjk_and_fullwidth("1girl, blue sky, smile") == "1girl, blue sky, smile"

    def test_empty_and_blank_passthrough(self):
        assert strip_cjk_and_fullwidth("") == ""
        assert strip_cjk_and_fullwidth("   ") == "   "

    def test_chinese_inside_tag_replaced_with_space_then_trimmed(self):
        # "红色" 整段替换为空格后合并空白、trim
        assert strip_cjk_and_fullwidth("1girl, 红色 dress, smile") == "1girl, dress, smile"

    def test_fullwidth_comma_becomes_separator(self):
        # 全角逗号 ，与顿号 、都当英文逗号分隔符
        assert strip_cjk_and_fullwidth("1girl，blue sky、smile") == "1girl, blue sky, smile"

    def test_pure_cjk_tag_dropped(self):
        assert strip_cjk_and_fullwidth("1girl, 全中文标签, blue sky") == "1girl, blue sky"

    def test_all_cjk_line_becomes_empty(self):
        assert strip_cjk_and_fullwidth("你好世界") == ""

    def test_weight_syntax_tag_with_cjk_core(self):
        # NAI4.5 权重语法 X::tag:: 内含中文时，中文被剔除、权重包装保留
        assert strip_cjk_and_fullwidth("1girl, 1.2::红 hair::, smile") == "1girl, 1.2:: hair::, smile"

    def test_char_prefix_preserved(self):
        assert strip_cjk_and_fullwidth("char1: 红色 hair") == "char1:hair"

    def test_japanese_and_korean_removed(self):
        assert strip_cjk_and_fullwidth("1girl, こんにちは, 안녕, sky") == "1girl, sky"

    def test_fullwidth_alphanumeric_removed(self):
        # 全角字母数字（U+FF00–U+FFEF）也属 §8 禁区
        assert strip_cjk_and_fullwidth("1girl, ＡＢＣ, sky") == "1girl, sky"


class TestSanitizeSfwPrompt:
    def test_removes_exact_and_substring_banned(self):
        assert sanitize_sfw_prompt("1girl, nude, bikini, blue sky, cleavage") == "1girl, blue sky"

    def test_keeps_safe_tags(self):
        assert sanitize_sfw_prompt("1girl, smile, blue sky") == "1girl, smile, blue sky"

    def test_strips_interaction_prefix_before_match(self):
        # source#/target#/mutual# 前缀剥离后再判定
        assert sanitize_sfw_prompt("source#nude, 1girl") == "1girl"


class TestNormalizePromptOrder:
    def test_camera_count_rest_year_ordering(self):
        result = normalize_prompt_order("blue sky, 1girl, looking at viewer, year 2023, tree")
        assert result == "looking at viewer, 1girl, blue sky, tree, year 2023"

    def test_stable_within_groups(self):
        result = normalize_prompt_order("tree, grass, solo, 1girl")
        # solo 与 1girl 都是 count，按原相对顺序；rest 在后
        assert result == "solo, 1girl, tree, grass"


class TestRemoveSelfieAppearanceTags:
    def test_removes_hair_and_eye_colors(self):
        result = remove_selfie_appearance_tags("1girl, black hair, blue eyes, smile")
        assert result == "1girl, smile"

    def test_keeps_hair_accessories(self):
        result = remove_selfie_appearance_tags("1girl, hair ribbon, smile")
        assert result == "1girl, hair ribbon, smile"
