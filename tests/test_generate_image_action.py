# -*- coding: utf-8 -*-
"""generate_image_action 的 i2i 自动选择路由单测。

只测纯函数 resolve_i2i_choice（Action 在测试环境是框架 mock 无法实例化 execute）。
两路径：引用图硬触发 / 无引用大脑主动抓最近图。
"""

from _bizyair_plugin.components.generate_image_action import resolve_i2i_choice


class TestResolveI2iChoice:
    def test_quoted_image_hard_triggers(self):
        # 路径1：有引用图 → 必定 i2i 用 quoted，不看 image_op
        assert resolve_i2i_choice("nai_default", "", has_quoted_image=True, has_recent_image=False) == ("nai_i2i", "quoted")
        # 即便大脑没填 image_op，引用图也硬触发
        assert resolve_i2i_choice("nai_default", "", has_quoted_image=True, has_recent_image=True) == ("nai_i2i", "quoted")

    def test_brain_op_with_recent_image(self):
        # 路径2：无引用图 + 大脑填 i2i + 有最近图 → i2i 用 recent
        assert resolve_i2i_choice("nai_default", "i2i", has_quoted_image=False, has_recent_image=True) == ("nai_i2i", "recent")

    def test_brain_op_but_no_recent_image(self):
        # 大脑想 i2i 但群里没图 → 回退普通文生图
        assert resolve_i2i_choice("nai_default", "i2i", has_quoted_image=False, has_recent_image=False) == ("nai_default", None)

    def test_no_op_no_quoted_stays_t2i(self):
        # 没引用图、大脑也没填 → 普通文生图（即便群里有最近图也不主动抓）
        assert resolve_i2i_choice("nai_default", "", has_quoted_image=False, has_recent_image=True) == ("nai_default", None)

    def test_only_nai_default_eligible(self):
        # 仅 nai_default 基础预设生效，不覆盖手动 vibe/charref/GPT
        assert resolve_i2i_choice("nai_vibe", "i2i", has_quoted_image=True, has_recent_image=True) == ("nai_vibe", None)
        assert resolve_i2i_choice("nai_charref", "", has_quoted_image=True, has_recent_image=False) == ("nai_charref", None)
        assert resolve_i2i_choice("default", "i2i", has_quoted_image=True, has_recent_image=True) == ("default", None)

    def test_case_insensitive_op(self):
        assert resolve_i2i_choice("nai_default", "I2I", has_quoted_image=False, has_recent_image=True) == ("nai_i2i", "recent")
        assert resolve_i2i_choice("nai_default", " i2i ", has_quoted_image=False, has_recent_image=True) == ("nai_i2i", "recent")

    def test_unknown_op_ignored(self):
        assert resolve_i2i_choice("nai_default", "vibe", has_quoted_image=False, has_recent_image=True) == ("nai_default", None)
