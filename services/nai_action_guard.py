# -*- coding: utf-8 -*-
"""出图 Action 的最小护栏（Action Guard）。

仅作用于 ALWAYS 激活的 generate_image Action——它由 planner LLM 决定何时调用，护栏只在
planner 误触 / 连发时兜底；显式命令（/nai0、/nai 随机、/dr）是用户主动请求，**不**走护栏。

两项最小护栏（均纯逻辑 + 可注入时钟，便于单测）：
1. 强否定否决：用户消息明确叫停出图（「别画了」等）时跳过。保守词表，低误伤。
2. 频率节流：距上次成功出图不足 min_interval 秒时跳过（防 ALWAYS+parallel 连发 / 双触）。

throttle 状态是进程内、按 chat_id 维度，重启即清空（节流场景足够，无需持久化）。
完整的 explicit/proactive 分档 + 弱否定 TTL（nai_draw 的 action_guard）未移植——本插件的
Action 由 planner 自调控，最小护栏即可，符合「让内置系统自调控」。
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.common.logger import get_logger

logger = get_logger("bizyair_generate_image_plugin")

# 强否定关键词：只收**明确叫停出图**的短语，刻意避开「别画得太丑 / 别画成那样」这类仍想出图的表达，
# 以压低误伤率（护栏宁可漏判也不该把用户真想要的图拦掉）。
STRONG_NEGATIVE_KEYWORDS: tuple[str, ...] = (
    "别画了", "别画图", "不要画了", "不要画图", "不用画了", "不用画图",
    "别再画", "别发图", "不要发图", "别生成图", "不要生成图", "停止画图", "先别画",
)

# 进程内、按 chat_id 的上次出图时刻（time.monotonic() 值；重启清空）
_last_draw_at: dict[str, float] = {}


def has_negative_signal(text: Optional[str], keywords: Iterable[str] = STRONG_NEGATIVE_KEYWORDS) -> bool:
    """文本是否含「明确叫停出图」信号。"""
    if not text:
        return False
    return any(kw in text for kw in keywords)


def should_throttle(chat_id, now: float, min_interval: float) -> bool:
    """距上次出图不足 min_interval 秒 → True（应节流跳过）。min_interval<=0 关闭节流。"""
    if not chat_id or min_interval <= 0:
        return False
    last = _last_draw_at.get(str(chat_id))
    if last is None:
        return False
    return (now - last) < min_interval


def record_draw(chat_id, now: float) -> None:
    """记录一次成功出图的时刻（用于后续节流判定）。"""
    if chat_id:
        _last_draw_at[str(chat_id)] = now


def seconds_until_unthrottled(chat_id, now: float, min_interval: float) -> float:
    """还需等待多少秒才解除节流（仅用于日志 / 提示）；未节流返回 0。"""
    if not chat_id or min_interval <= 0:
        return 0.0
    last = _last_draw_at.get(str(chat_id))
    if last is None:
        return 0.0
    remaining = min_interval - (now - last)
    return remaining if remaining > 0 else 0.0


def reset() -> None:
    """清空节流状态（测试隔离用）。"""
    _last_draw_at.clear()
