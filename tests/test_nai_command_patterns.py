# -*- coding: utf-8 -*-
"""命令 pattern 互斥回归锁。

框架命令分发（component_registry.find_command_by_text）对多个命中的 command_pattern
只取**首个匹配**（按注册顺序），多匹配仅告警。故 /nai 反推 与 /nai 反推 重绘 的两个
pattern 必须互斥，否则行为依赖注册顺序、脆弱。这里从真实 command_pattern 读取断言，
pattern 一旦被改坏导致双命中，立刻红。
"""

import re

import pytest

# 从模块级常量读 pattern（命令类继承被 conftest mock 的 BaseCommand，类属性读出来是 Mock，
# 故 pattern 提为模块常量供这里 import；命令类自身用 command_pattern = 这些常量）。
from _bizyair_plugin.components.nai_commands import NAI_RETAG_REDRAW_PATTERN
from _bizyair_plugin.components.nai_retag_command import NAI_RETAG_PATTERN

RETAG = NAI_RETAG_PATTERN
REDRAW = NAI_RETAG_REDRAW_PATTERN


@pytest.mark.parametrize(
    "text, retag_hit, redraw_hit",
    [
        ("/nai 反推", True, False),                     # 纯反推
        ("/nai 反推 重绘", False, True),                 # 反推重绘
        ("/nai 反推 [图片]", True, False),               # 带图占位符（processed_plain_text 污染）
        ("/nai 反推 重绘 [图片]", False, True),           # 反推重绘 + 图占位符
        ("[回复 xx 的消息] /nai 反推 重绘", False, True),  # 引用前缀 + 反推重绘
        ("/nai 反推重绘", False, False),                 # 粘连无空格：两者都不匹配（防误触，与原防粘连一致）
    ],
)
def test_retag_redraw_mutually_exclusive(text, retag_hit, redraw_hit):
    assert bool(re.match(RETAG, text)) is retag_hit, f"retag pattern 对 {text!r} 判定错误"
    assert bool(re.match(REDRAW, text)) is redraw_hit, f"redraw pattern 对 {text!r} 判定错误"


@pytest.mark.parametrize(
    "text",
    ["/nai 反推", "/nai 反推 重绘", "/nai 反推 重绘 [图片]", "[回复] /nai 反推 重绘 [picid:xx]"],
)
def test_never_double_match(text):
    # 关键不变量：任何文本都不会同时命中两个 pattern（否则分发取首个、依赖注册顺序）
    assert not (re.match(RETAG, text) and re.match(REDRAW, text)), f"{text!r} 同时命中两 pattern"
