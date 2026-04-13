import pytest
from unittest.mock import AsyncMock

from .fixtures import make_definition, make_resolver, mock_builtin_provider


class TestLazyEvaluation:
    @pytest.mark.asyncio
    async def test_dict_hit_returns_entry_not_fallback(self):
        defs = {
            "default_text": make_definition("default_text", values=["neutral smile"]),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="use_default",
                fallback_value="{default_text}",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"emotion": "joy"},
            definitions=defs,
            action_parameter_names={"emotion"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["emotion_prompt"] == "big smile"

    @pytest.mark.asyncio
    async def test_dict_miss_uses_fallback(self):
        defs = {
            "default_text": make_definition("default_text", values=["neutral smile"]),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="use_default",
                fallback_value="{default_text}",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"emotion": "sad"},
            definitions=defs,
            action_parameter_names={"emotion"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["emotion_prompt"] == "neutral smile"

    @pytest.mark.asyncio
    async def test_probability_zero_returns_empty(self):
        defs = {
            "threshold": make_definition("threshold", values=["5"]),
            "style_hint": make_definition(
                "style_hint",
                probability=0.0,
                values=["long mode"],
                condition_type="length_gt",
                condition_source="prompt",
                condition_value="{threshold}",
                values_else=["short mode"],
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"prompt": "123456"},
            definitions=defs,
            action_parameter_names={"prompt"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["style_hint"] == ""

    @pytest.mark.asyncio
    async def test_no_condition_type_ignores_condition_value(self):
        defs = {
            "simple_var": make_definition(
                "simple_var",
                values=["hello"],
                condition_value="{nonexistent}",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["simple_var"] == "hello"
