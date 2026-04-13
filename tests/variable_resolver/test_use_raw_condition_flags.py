import pytest
from unittest.mock import AsyncMock

from services.variable_dependency_resolver import VariableDependencyResolver

from .fixtures import BUILTIN_NAMES, make_definition, make_resolver, mock_builtin_provider


class TestUseRawConditionSourceGraphBehavior:
    def test_collect_node_dependencies_excludes_condition_source_from_hard_dependencies(self):
        defs = {
            "selector": make_definition("selector", values=["selfie"]),
            "result": make_definition(
                "result",
                values=["hit"],
                condition_type="equals",
                condition_source="selector",
                condition_value="selfie",
                values_else=["miss"],
                use_raw_condition_source=True,
            ),
        }

        resolver = make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
            required_keys={"selector", "result"},
        )

        result_node = resolver._nodes["result"]

        assert result_node.hard_dependencies == frozenset()
        assert result_node.soft_dependencies == frozenset()

    def test_compute_required_variable_keys_excludes_condition_source_closure_when_raw_enabled(self):
        defs = {
            "selector": make_definition("selector", values=["selfie"]),
            "result": make_definition(
                "result",
                values=["hit"],
                condition_type="equals",
                condition_source="selector",
                condition_value="selfie",
                values_else=["miss"],
                use_raw_condition_source=True,
            ),
        }

        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"result"},
            action_inputs={},
            custom_variable_definitions=defs,
            action_parameter_names=set(),
            builtin_names=BUILTIN_NAMES,
        )

        assert result == {"result"}


class TestUseRawConditionValueGraphBehavior:
    def test_collect_node_dependencies_excludes_condition_value_placeholder_dependencies(self):
        defs = {
            "threshold": make_definition("threshold", values=["5"]),
            "result": make_definition(
                "result",
                values=["hit"],
                condition_type="length_gt",
                condition_source="prompt",
                condition_value="{threshold}",
                values_else=["miss"],
                use_raw_condition_value=True,
            ),
        }

        resolver = make_resolver(
            action_inputs={"prompt": "123456"},
            definitions=defs,
            action_parameter_names={"prompt"},
            required_keys={"threshold", "result"},
        )

        result_node = resolver._nodes["result"]

        assert result_node.hard_dependencies == frozenset({"prompt"})
        assert result_node.soft_dependencies == frozenset()

    def test_compute_required_variable_keys_excludes_condition_value_closure_when_raw_enabled(self):
        defs = {
            "threshold": make_definition("threshold", values=["5"]),
            "result": make_definition(
                "result",
                values=["hit"],
                condition_type="length_gt",
                condition_source="prompt",
                condition_value="{threshold}",
                values_else=["miss"],
                use_raw_condition_value=True,
            ),
        }

        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"result"},
            action_inputs={"prompt": "123456"},
            custom_variable_definitions=defs,
            action_parameter_names={"prompt"},
            builtin_names=BUILTIN_NAMES,
        )

        assert result == {"result"}


class TestUseRawConditionSourceResolveAll:
    @pytest.mark.asyncio
    async def test_reads_raw_value_from_action_inputs_without_resolving_custom_variable(self):
        llm_factory = AsyncMock(return_value="generated")
        defs = {
            "selector": make_definition("selector", mode="llm", values=["call llm"]),
            "result": make_definition(
                "result",
                values=["hit"],
                condition_type="equals",
                condition_source="selector",
                condition_value="selfie",
                values_else=["miss"],
                use_raw_condition_source=True,
            ),
        }

        _, cv = await make_resolver(
            action_inputs={"selector": "selfie"},
            definitions=defs,
            action_parameter_names={"selector"},
            required_keys={"result"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=llm_factory,
            builtin_variable_provider=mock_builtin_provider(),
        )

        assert cv["result"] == "hit"
        llm_factory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reads_resolved_context_value_when_action_input_depends_on_custom_variable(self):
        defs = {
            "selector": make_definition("selector", values=["selfie"]),
            "result": make_definition(
                "result",
                values=["hit"],
                condition_type="equals",
                condition_source="prompt",
                condition_value="selfie",
                values_else=["miss"],
                use_raw_condition_source=True,
            ),
        }

        resolved_inputs, cv = await make_resolver(
            action_inputs={"prompt": "{selector}"},
            definitions=defs,
            action_parameter_names={"prompt"},
            required_keys={"selector", "result"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )

        assert resolved_inputs["prompt"] == "selfie"
        assert cv["selector"] == "selfie"
        assert cv["result"] == "miss"

    @pytest.mark.asyncio
    async def test_missing_unresolved_custom_variable_falls_back_to_empty_string(self):
        defs = {
            "selector": make_definition("selector", values=["selfie"]),
            "result": make_definition(
                "result",
                values=["hit"],
                condition_type="equals",
                condition_source="selector",
                condition_value="selfie",
                values_else=["miss"],
                use_raw_condition_source=True,
            ),
        }

        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
            required_keys={"result"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )

        assert cv["result"] == "miss"
        assert "selector" not in cv


class TestUseRawConditionValueResolveAll:
    @pytest.mark.asyncio
    async def test_condition_value_literal_text_is_used_directly_for_equals(self):
        defs = {
            "threshold": make_definition("threshold", values=["selfie"]),
            "result": make_definition(
                "result",
                values=["hit"],
                condition_type="equals",
                condition_source="prompt",
                condition_value="{threshold}",
                values_else=["miss"],
                use_raw_condition_value=True,
            ),
        }

        _, cv = await make_resolver(
            action_inputs={"prompt": "{threshold}"},
            definitions=defs,
            action_parameter_names={"prompt"},
            required_keys={"result"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )

        assert cv["result"] == "hit"
        assert "threshold" not in cv

    @pytest.mark.asyncio
    async def test_condition_value_can_compare_literal_placeholder_text(self):
        defs = {
            "result": make_definition(
                "result",
                values=["hit"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="{random_seed}",
                values_else=["miss"],
                use_raw_condition_value=True,
            ),
        }

        _, cv = await make_resolver(
            action_inputs={"prompt": "prefix {random_seed} suffix"},
            definitions=defs,
            action_parameter_names={"prompt"},
            required_keys={"result"},
        ).resolve_all(
            builtin_placeholder_values={"{random_seed}": "42"},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )

        assert cv["result"] == "hit"