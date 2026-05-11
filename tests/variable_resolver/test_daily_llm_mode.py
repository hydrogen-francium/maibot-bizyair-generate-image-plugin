"""daily_llm 模式在 VariableDependencyResolver 中的端到端测试。

覆盖：
- 模板插值后才调 LLM
- 命中缓存跳过 LLM
- min_length / required_markers 不通过时不写盘并抛 DailyLlmValidationError
- 校验通过正常落盘并返回
"""
import json
from datetime import date
from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from services import llm_value_cache
from services.llm_value_cache import DailyLlmCache, DailyLlmValidationError

from .fixtures import make_definition, make_resolver, mock_builtin_provider


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch):
    """把 daily_llm 的全局单例换成隔离到 tmp_path 的实例。"""
    instance = DailyLlmCache(tmp_path / "var_cache")
    monkeypatch.setattr(llm_value_cache, "_singleton", instance)
    return instance


class TestDailyLlmResolveAll:
    @pytest.mark.asyncio
    async def test_daily_llm_calls_factory_with_resolved_template(self, isolated_cache: DailyLlmCache):
        factory = AsyncMock(return_value="今日：2026-05-05 周二\n整体心情：平静\n日程时间表：略")
        defs = {
            "today_state": make_definition(
                "today_state",
                mode="daily_llm",
                values=["请基于 {image_intent} 生成日程"],
            ),
        }
        resolver = make_resolver(
            action_inputs={"image_intent": "随便画"},
            definitions=defs,
            action_parameter_names={"image_intent"},
        )
        _, cv = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=factory,
            builtin_variable_provider=mock_builtin_provider(),
        )

        factory.assert_awaited_once_with("请基于 随便画 生成日程")
        assert cv["today_state"].startswith("今日：")
        assert isolated_cache._cache_path("today_state").exists()

    @pytest.mark.asyncio
    async def test_daily_llm_cache_hit_skips_factory(self, isolated_cache: DailyLlmCache):
        cache_file = isolated_cache._cache_path("today_state")
        cache_file.write_text(
            json.dumps({"value": "cached body", "date_key": date.today().isoformat()}),
            encoding="utf-8",
        )

        factory = AsyncMock(return_value="should not run")
        defs = {
            "today_state": make_definition(
                "today_state",
                mode="daily_llm",
                values=["prompt body"],
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=factory,
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["today_state"] == "cached body"
        factory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_min_length_validator_blocks_truncated_output(self, isolated_cache: DailyLlmCache):
        """模拟 LLM 截断输出，min_length 校验失败应抛错且不落盘。"""
        factory = AsyncMock(return_value="今日：2026-05-05 周二\n整体心情：慵懒\n日程时间表（24")
        defs = {
            "today_state": make_definition(
                "today_state",
                mode="daily_llm",
                values=["prompt body"],
                min_length=200,
            ),
        }
        with pytest.raises(DailyLlmValidationError, match="长度"):
            await make_resolver(
                action_inputs={},
                definitions=defs,
                action_parameter_names=set(),
            ).resolve_all(
                builtin_placeholder_values={},
                llm_value_factory=factory,
                builtin_variable_provider=mock_builtin_provider(),
            )

        assert not isolated_cache._cache_path("today_state").exists()

    @pytest.mark.asyncio
    async def test_required_markers_validator_blocks_missing_marker(self, isolated_cache: DailyLlmCache):
        factory = AsyncMock(return_value="今日：2026-05-05 周二\n整体心情：慵懒")
        defs = {
            "today_state": make_definition(
                "today_state",
                mode="daily_llm",
                values=["prompt body"],
                required_markers=("今日：", "整体心情：", "日程时间表"),
            ),
        }
        with pytest.raises(DailyLlmValidationError, match="日程时间表"):
            await make_resolver(
                action_inputs={},
                definitions=defs,
                action_parameter_names=set(),
            ).resolve_all(
                builtin_placeholder_values={},
                llm_value_factory=factory,
                builtin_variable_provider=mock_builtin_provider(),
            )

        assert not isolated_cache._cache_path("today_state").exists()

    @pytest.mark.asyncio
    async def test_validator_pass_writes_cache(self, isolated_cache: DailyLlmCache):
        full_output = (
            "今日：2026-05-05 周二\n"
            "整体心情：慵懒平静\n"
            "日程时间表（24h）：\n"
            "00:00-08:00 自己的房间 睡觉\n"
            "08:00-12:00 客厅 吃布丁打游戏\n"
            "12:00-18:00 实验室 调试离子炮\n"
            "18:00-23:00 自己的房间 看番\n"
            "23:00-24:00 浴室 冲澡\n"
        )
        factory = AsyncMock(return_value=full_output)
        defs = {
            "today_state": make_definition(
                "today_state",
                mode="daily_llm",
                values=["prompt body"],
                min_length=50,
                required_markers=("今日：", "整体心情：", "日程时间表"),
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=factory,
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["today_state"] == full_output
        assert isolated_cache._cache_path("today_state").exists()

    @pytest.mark.asyncio
    async def test_no_validation_fields_runs_without_validator(self, isolated_cache: DailyLlmCache):
        """min_length=0 且 required_markers 为空时，任意输出都应通过。"""
        factory = AsyncMock(return_value="x")
        defs = {
            "today_state": make_definition(
                "today_state",
                mode="daily_llm",
                values=["prompt"],
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=factory,
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["today_state"] == "x"
