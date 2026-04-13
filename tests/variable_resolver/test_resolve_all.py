import pytest
from unittest.mock import AsyncMock

from .fixtures import make_definition, make_resolver, mock_builtin_provider


class TestResolveAll:
    @pytest.mark.asyncio
    async def test_no_cross_references(self):
        defs = {"ep": make_definition("ep", values=["translated"])}
        resolver = make_resolver(
            action_inputs={"prompt": "a cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        resolved_inputs, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(return_value="ignored"),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_inputs["prompt"] == "a cat"
        assert resolved_vars["ep"] == "translated"

    @pytest.mark.asyncio
    async def test_custom_var_references_action_input(self):
        defs = {"ep": make_definition("ep", values=["translate: {prompt}"])}
        resolver = make_resolver(
            action_inputs={"prompt": "a cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["ep"] == "translate: a cat"

    @pytest.mark.asyncio
    async def test_action_input_references_custom_var(self):
        defs = {"style": make_definition("style", values=["anime"])}
        resolver = make_resolver(
            action_inputs={"prompt": "{style} cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        resolved_inputs, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_inputs["prompt"] == "anime cat"
        assert resolved_vars["style"] == "anime"

    @pytest.mark.asyncio
    async def test_chain_a_depends_b(self):
        defs = {
            "a": make_definition("a", values=["prefix_{b}"]),
            "b": make_definition("b", values=["leaf"]),
        }
        resolver = make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["b"] == "leaf"
        assert resolved_vars["a"] == "prefix_leaf"

    @pytest.mark.asyncio
    async def test_action_input_resolved_before_downstream(self):
        defs = {
            "style": make_definition("style", values=["anime"]),
            "ep": make_definition("ep", values=["translate: {prompt}"]),
        }
        resolver = make_resolver(
            action_inputs={"prompt": "{style} cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        resolved_inputs, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_inputs["prompt"] == "anime cat"
        assert resolved_vars["ep"] == "translate: anime cat"

    @pytest.mark.asyncio
    async def test_llm_mode_called(self):
        factory = AsyncMock(return_value="english tags")
        defs = {"ep": make_definition("ep", mode="llm", values=["translate: {prompt}"])}
        resolver = make_resolver(
            action_inputs={"prompt": "a cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=factory,
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["ep"] == "english tags"
        factory.assert_called_once_with("translate: a cat")

    @pytest.mark.asyncio
    async def test_probability_zero_returns_empty(self):
        defs = {"style": make_definition("style", values=["anime"], probability=0.0)}
        resolver = make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["style"] == ""

    @pytest.mark.asyncio
    async def test_cycle_raises_on_resolve(self):
        defs = {
            "a": make_definition("a", values=["{b}"]),
            "b": make_definition("b", values=["{a}"]),
        }
        resolver = make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        with pytest.raises(ValueError, match="循环引用"):
            await resolver.resolve_all(
                builtin_placeholder_values={},
                llm_value_factory=AsyncMock(),
                builtin_variable_provider=mock_builtin_provider(),
            )

    @pytest.mark.asyncio
    async def test_builtin_placeholder_substituted(self):
        defs = {"ep": make_definition("ep", values=["seed={random_seed}"])}
        resolver = make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={"{random_seed}": 42},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["ep"] == "seed=42"

    @pytest.mark.asyncio
    async def test_dict_lookup_hit(self):
        defs = {
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile", "cool": "sunglasses"},
            )
        }
        resolver = make_resolver(
            action_inputs={"emotion": "joy"},
            definitions=defs,
            action_parameter_names={"emotion"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["emotion_prompt"] == "big smile"

    @pytest.mark.asyncio
    async def test_dict_lookup_miss_keep_placeholder(self):
        defs = {
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="keep_placeholder",
            )
        }
        resolver = make_resolver(
            action_inputs={"emotion": "sad"},
            definitions=defs,
            action_parameter_names={"emotion"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["emotion_prompt"] == "{emotion_prompt}"

    @pytest.mark.asyncio
    async def test_dict_lookup_miss_raise_error(self):
        defs = {
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="raise_error",
            )
        }
        resolver = make_resolver(
            action_inputs={"emotion": "sad"},
            definitions=defs,
            action_parameter_names={"emotion"},
        )
        with pytest.raises(ValueError, match="emotion_prompt"):
            await resolver.resolve_all(
                builtin_placeholder_values={},
                llm_value_factory=AsyncMock(),
                builtin_variable_provider=mock_builtin_provider(),
            )

    @pytest.mark.asyncio
    async def test_dict_lookup_miss_use_default(self):
        defs = {
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="use_default",
                fallback_value="neutral smile",
            )
        }
        resolver = make_resolver(
            action_inputs={"emotion": "sad"},
            definitions=defs,
            action_parameter_names={"emotion"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["emotion_prompt"] == "neutral smile"

    @pytest.mark.asyncio
    async def test_literal_condition_true_uses_values(self):
        defs = {
            "style_hint": make_definition(
                "style_hint",
                values=["long mode"],
                condition_type="length_gt",
                condition_source="prompt",
                condition_value="5",
                values_else=["short mode"],
            )
        }
        resolver = make_resolver(
            action_inputs={"prompt": "123456"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["style_hint"] == "long mode"

    @pytest.mark.asyncio
    async def test_fixed_true_uses_values(self):
        defs = {
            "style_hint": make_definition(
                "style_hint",
                values=["main"],
                condition_type="fixed_true",
                values_else=["fallback"],
            )
        }
        resolver = make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["style_hint"] == "main"

    @pytest.mark.asyncio
    async def test_fixed_false_uses_values_else(self):
        defs = {
            "style_hint": make_definition(
                "style_hint",
                values=["main"],
                condition_type="fixed_false",
                values_else=["fallback"],
            )
        }
        resolver = make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["style_hint"] == "fallback"

    @pytest.mark.asyncio
    async def test_literal_condition_false_uses_values_else(self):
        defs = {
            "style_hint": make_definition(
                "style_hint",
                values=["long mode"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="cat",
                values_else=["short mode"],
            )
        }
        resolver = make_resolver(
            action_inputs={"prompt": "a dog"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["style_hint"] == "short mode"

    @pytest.mark.asyncio
    async def test_literal_condition_false_without_values_else_returns_empty(self):
        defs = {
            "style_hint": make_definition(
                "style_hint",
                values=["long mode"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="cat",
                values_else=[],
            )
        }
        resolver = make_resolver(
            action_inputs={"prompt": "a dog"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["style_hint"] == ""

    @pytest.mark.asyncio
    async def test_probability_zero_skips_condition_evaluation(self):
        defs = {
            "style_hint": make_definition(
                "style_hint",
                values=["long mode"],
                probability=0.0,
                condition_type="regex_match",
                condition_source="prompt",
                condition_value="(",
                values_else=["short mode"],
            )
        }
        resolver = make_resolver(
            action_inputs={"prompt": "anything"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["style_hint"] == ""

    @pytest.mark.asyncio
    async def test_condition_source_can_use_builtin_value(self):
        defs = {
            "style_hint": make_definition(
                "style_hint",
                values=["has seed"],
                condition_type="contains",
                condition_source="random_seed",
                condition_value="42",
                values_else=["no seed"],
            )
        }
        resolver = make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={"{random_seed}": 42},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["style_hint"] == "has seed"

    @pytest.mark.asyncio
    async def test_values_else_can_reference_other_custom_variable(self):
        defs = {
            "fallback": make_definition("fallback", values=["fallback-text"]),
            "style_hint": make_definition(
                "style_hint",
                values=["main"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="cat",
                values_else=["{fallback}"],
            ),
        }
        resolver = make_resolver(
            action_inputs={"prompt": "dog"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_vars["style_hint"] == "fallback-text"
