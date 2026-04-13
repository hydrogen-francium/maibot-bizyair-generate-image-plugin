import pytest
from unittest.mock import AsyncMock

from .fixtures import make_definition, make_resolver, mock_builtin_provider


class TestConditionEquals:
    @pytest.mark.asyncio
    async def test_equals_true(self):
        defs = {
            "hint": make_definition(
                "hint",
                values=["matched"],
                condition_type="equals",
                condition_source="prompt",
                condition_value="cat",
                values_else=["not matched"],
            ),
        }
        resolver = make_resolver(
            action_inputs={"prompt": "cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, cv = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["hint"] == "matched"

    @pytest.mark.asyncio
    async def test_equals_false(self):
        defs = {
            "hint": make_definition(
                "hint",
                values=["matched"],
                condition_type="equals",
                condition_source="prompt",
                condition_value="cat",
                values_else=["not matched"],
            ),
        }
        resolver = make_resolver(
            action_inputs={"prompt": "dog"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, cv = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["hint"] == "not matched"

    @pytest.mark.asyncio
    async def test_equals_substring_is_not_equal(self):
        defs = {
            "hint": make_definition(
                "hint",
                values=["matched"],
                condition_type="equals",
                condition_source="prompt",
                condition_value="cat",
                values_else=["not matched"],
            ),
        }
        resolver = make_resolver(
            action_inputs={"prompt": "a cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, cv = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["hint"] == "not matched"

    @pytest.mark.asyncio
    async def test_not_equals_true(self):
        defs = {
            "hint": make_definition(
                "hint",
                values=["different"],
                condition_type="not_equals",
                condition_source="prompt",
                condition_value="cat",
                values_else=["same"],
            ),
        }
        resolver = make_resolver(
            action_inputs={"prompt": "dog"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, cv = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["hint"] == "different"

    @pytest.mark.asyncio
    async def test_not_equals_false(self):
        defs = {
            "hint": make_definition(
                "hint",
                values=["different"],
                condition_type="not_equals",
                condition_source="prompt",
                condition_value="cat",
                values_else=["same"],
            ),
        }
        resolver = make_resolver(
            action_inputs={"prompt": "cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, cv = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["hint"] == "same"
