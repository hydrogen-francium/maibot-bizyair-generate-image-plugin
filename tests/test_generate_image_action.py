# -*- coding: utf-8 -*-
"""generate_image_action 的 i2i 自动选择路由单测。

只测纯函数 resolve_image_op_preset（Action 在测试环境是框架 mock 无法实例化 execute）。
"""

from _bizyair_plugin.components.generate_image_action import resolve_image_op_preset


class TestResolveImageOpPreset:
    def test_i2i_with_image_switches_to_nai_i2i(self):
        assert resolve_image_op_preset("i2i", "nai_default", has_image=True) == "nai_i2i"

    def test_i2i_without_image_stays(self):
        # 决策器判 i2i 但没取到图 → 回退普通文生图
        assert resolve_image_op_preset("i2i", "nai_default", has_image=False) == "nai_default"

    def test_no_image_op_stays(self):
        # 决策器没填 image_op → 普通文生图
        assert resolve_image_op_preset("", "nai_default", has_image=True) == "nai_default"
        assert resolve_image_op_preset(None, "nai_default", has_image=True) == "nai_default"

    def test_i2i_only_from_nai_default(self):
        # 仅 nai_default 基础预设才自动切；其它预设（vibe/charref/GPT）不被 i2i 覆盖
        assert resolve_image_op_preset("i2i", "nai_vibe", has_image=True) == "nai_vibe"
        assert resolve_image_op_preset("i2i", "nai_charref", has_image=True) == "nai_charref"
        assert resolve_image_op_preset("i2i", "default", has_image=True) == "default"

    def test_unknown_op_stays(self):
        assert resolve_image_op_preset("vibe", "nai_default", has_image=True) == "nai_default"
        assert resolve_image_op_preset("xyz", "nai_default", has_image=True) == "nai_default"

    def test_case_insensitive(self):
        assert resolve_image_op_preset("I2I", "nai_default", has_image=True) == "nai_i2i"
        assert resolve_image_op_preset(" i2i ", "nai_default", has_image=True) == "nai_i2i"
