# -*- coding: utf-8 -*-
"""出图 Action 最小护栏（services/nai_action_guard.py）单测：强否定否决 + 频率节流。

节流用注入的 monotonic 时钟（不依赖真实时间），并在每个用例前 reset 进程内状态。
"""

import pytest

from services import nai_action_guard


@pytest.fixture(autouse=True)
def _reset_guard_state():
    nai_action_guard.reset()
    yield
    nai_action_guard.reset()


class TestHasNegativeSignal:
    @pytest.mark.parametrize(
        "text",
        ["别画了", "你别画了喵", "不要画图", "先别画", "停止画图", "别发图了", "不用画图"],
    )
    def test_hits(self, text):
        assert nai_action_guard.has_negative_signal(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "画一张猫",
            "帮我画个夕阳",
            "别画得太丑",      # 仍想出图，刻意不收入词表，不应误伤
            "画风别太写实",
            "",
            None,
        ],
    )
    def test_misses(self, text):
        assert nai_action_guard.has_negative_signal(text) is False

    def test_custom_keywords(self):
        assert nai_action_guard.has_negative_signal("stop drawing", keywords=["stop drawing"]) is True


class TestThrottle:
    def test_disabled_when_interval_zero(self):
        nai_action_guard.record_draw("c1", 100.0)
        assert nai_action_guard.should_throttle("c1", 100.5, 0) is False
        assert nai_action_guard.should_throttle("c1", 100.5, -5) is False

    def test_no_prior_draw_never_throttles(self):
        assert nai_action_guard.should_throttle("c1", 100.0, 30) is False

    def test_within_window_throttles(self):
        nai_action_guard.record_draw("c1", 100.0)
        assert nai_action_guard.should_throttle("c1", 120.0, 30) is True   # 20s < 30s

    def test_after_window_passes(self):
        nai_action_guard.record_draw("c1", 100.0)
        assert nai_action_guard.should_throttle("c1", 131.0, 30) is False  # 31s >= 30s

    def test_per_chat_isolation(self):
        nai_action_guard.record_draw("c1", 100.0)
        # 另一个聊天没出过图，不受 c1 影响
        assert nai_action_guard.should_throttle("c2", 110.0, 30) is False

    def test_empty_chat_id_never_throttles(self):
        nai_action_guard.record_draw("", 100.0)
        assert nai_action_guard.should_throttle("", 100.1, 30) is False

    def test_seconds_until_unthrottled(self):
        nai_action_guard.record_draw("c1", 100.0)
        assert nai_action_guard.seconds_until_unthrottled("c1", 120.0, 30) == pytest.approx(10.0)
        assert nai_action_guard.seconds_until_unthrottled("c1", 131.0, 30) == 0.0
        assert nai_action_guard.seconds_until_unthrottled("c1", 120.0, 0) == 0.0

    def test_record_overwrites(self):
        nai_action_guard.record_draw("c1", 100.0)
        nai_action_guard.record_draw("c1", 200.0)
        assert nai_action_guard.should_throttle("c1", 210.0, 30) is True    # 距 200 仅 10s
        assert nai_action_guard.should_throttle("c1", 235.0, 30) is False   # 距 200 已 35s
