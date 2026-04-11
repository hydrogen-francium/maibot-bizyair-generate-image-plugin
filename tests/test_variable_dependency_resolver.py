import pytest
from unittest.mock import AsyncMock, MagicMock

from services.custom_variable_registry import CustomVariableDefinition
from services.variable_dependency_resolver import VariableDependencyResolver
from services.builtin_variable_provider import BuiltinVariableProvider


BUILTIN_NAMES = frozenset({"random_seed", "current_datetime"})


def _make_definition(key: str, mode: str = "literal", values: list[str] | None = None,
                     probability: float = 1.0, index: int = 0) -> CustomVariableDefinition:
    return CustomVariableDefinition(
        key=key,
        mode=mode,
        values=values or [],
        probability=probability,
        index=index,
    )


def _make_resolver(
        action_inputs: dict,
        definitions: dict[str, CustomVariableDefinition],
        action_parameter_names: set[str] | None = None,
        required_keys: set[str] | None = None,
) -> VariableDependencyResolver:
    if action_parameter_names is None:
        action_parameter_names = set(action_inputs.keys())
    if required_keys is None:
        required_keys = set(definitions.keys())
    return VariableDependencyResolver(
        action_inputs=action_inputs,
        custom_variable_definitions=definitions,
        action_parameter_names=action_parameter_names,
        builtin_names=BUILTIN_NAMES,
        required_custom_variable_keys=required_keys,
    )


def _mock_builtin_provider() -> BuiltinVariableProvider:
    provider = MagicMock(spec=BuiltinVariableProvider)
    provider.build_placeholder_values.return_value = {}
    provider.variable_names = frozenset()
    return provider


# ─── compute_required_variable_keys ───


class TestComputeRequiredVariableKeys:
    def test_direct_keys_only(self):
        defs = {"ep": _make_definition("ep", values=["{prompt}"])}
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"ep"},
            action_inputs={"prompt": "a cat"},
            custom_variable_definitions=defs,
            action_parameter_names={"prompt"},
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"ep"}

    def test_transitive_closure(self):
        defs = {
            "a": _make_definition("a", values=["{b}"]),
            "b": _make_definition("b", values=["hello"]),
            "c": _make_definition("c", values=["unused"]),
        }
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"a"},
            action_inputs={},
            custom_variable_definitions=defs,
            action_parameter_names=set(),
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"a", "b"}

    def test_action_input_references(self):
        defs = {"style": _make_definition("style", values=["anime"])}
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys=set(),
            action_inputs={"prompt": "{style} cat"},
            custom_variable_definitions=defs,
            action_parameter_names={"prompt"},
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"style"}

    def test_deep_chain(self):
        defs = {
            "a": _make_definition("a", values=["{b}"]),
            "b": _make_definition("b", values=["{c}"]),
            "c": _make_definition("c", values=["{d}"]),
            "d": _make_definition("d", values=["leaf"]),
        }
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"a"},
            action_inputs={},
            custom_variable_definitions=defs,
            action_parameter_names=set(),
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"a", "b", "c", "d"}


# ─── topological_sort ───


class TestTopologicalSort:
    def test_no_nodes(self):
        resolver = _make_resolver(
            action_inputs={"prompt": "cat"},
            definitions={},
            required_keys=set(),
        )
        assert resolver.topological_sort() == []

    def test_independent_variables(self):
        defs = {
            "a": _make_definition("a", values=["x"]),
            "b": _make_definition("b", values=["y"]),
        }
        resolver = _make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        order = resolver.topological_sort()
        assert set(order) == {"a", "b"}

    def test_linear_chain(self):
        defs = {
            "a": _make_definition("a", values=["{b}"]),
            "b": _make_definition("b", values=["leaf"]),
        }
        resolver = _make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        order = resolver.topological_sort()
        assert order.index("b") < order.index("a")

    def test_action_input_depends_on_custom_var(self):
        defs = {"style": _make_definition("style", values=["anime"])}
        resolver = _make_resolver(
            action_inputs={"prompt": "{style} cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        order = resolver.topological_sort()
        assert order.index("style") < order.index("prompt")

    def test_cycle_detection_simple(self):
        defs = {
            "a": _make_definition("a", values=["{b}"]),
            "b": _make_definition("b", values=["{a}"]),
        }
        resolver = _make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        with pytest.raises(ValueError, match="a|b"):
            resolver.topological_sort()

    def test_cycle_detection_with_action_input(self):
        defs = {"style": _make_definition("style", values=["{prompt}"])}
        resolver = _make_resolver(
            action_inputs={"prompt": "{style}"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        with pytest.raises(ValueError, match="style|prompt"):
            resolver.topological_sort()

    def test_diamond_dag(self):
        defs = {
            "a": _make_definition("a", values=["{c}"]),
            "b": _make_definition("b", values=["{c}"]),
            "c": _make_definition("c", values=["leaf"]),
        }
        resolver = _make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        order = resolver.topological_sort()
        assert order.index("c") < order.index("a")
        assert order.index("c") < order.index("b")


# ─── resolve_all ───


class TestResolveAll:
    @pytest.mark.asyncio
    async def test_no_cross_references(self):
        """无交叉引用时退化为当前行为"""
        defs = {"ep": _make_definition("ep", values=["translated"])}
        resolver = _make_resolver(
            action_inputs={"prompt": "a cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        resolved_inputs, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(return_value="ignored"),
            builtin_variable_provider=_mock_builtin_provider(),
        )
        assert resolved_inputs["prompt"] == "a cat"
        assert resolved_vars["ep"] == "translated"

    @pytest.mark.asyncio
    async def test_custom_var_references_action_input(self):
        """自定义变量引用 action_input"""
        defs = {"ep": _make_definition("ep", values=["translate: {prompt}"])}
        resolver = _make_resolver(
            action_inputs={"prompt": "a cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        resolved_inputs, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=_mock_builtin_provider(),
        )
        assert resolved_vars["ep"] == "translate: a cat"

    @pytest.mark.asyncio
    async def test_action_input_references_custom_var(self):
        """action_input 引用自定义变量"""
        defs = {"style": _make_definition("style", values=["anime"])}
        resolver = _make_resolver(
            action_inputs={"prompt": "{style} cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        resolved_inputs, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=_mock_builtin_provider(),
        )
        assert resolved_inputs["prompt"] == "anime cat"
        assert resolved_vars["style"] == "anime"

    @pytest.mark.asyncio
    async def test_chain_a_depends_b(self):
        """A → B 链式引用"""
        defs = {
            "a": _make_definition("a", values=["prefix_{b}"]),
            "b": _make_definition("b", values=["leaf"]),
        }
        resolver = _make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=_mock_builtin_provider(),
        )
        assert resolved_vars["b"] == "leaf"
        assert resolved_vars["a"] == "prefix_leaf"

    @pytest.mark.asyncio
    async def test_action_input_resolved_before_downstream(self):
        """action_input 引用 custom_var → 另一个 custom_var 引用 resolved action_input"""
        defs = {
            "style": _make_definition("style", values=["anime"]),
            "ep": _make_definition("ep", values=["translate: {prompt}"]),
        }
        resolver = _make_resolver(
            action_inputs={"prompt": "{style} cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        resolved_inputs, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=_mock_builtin_provider(),
        )
        assert resolved_inputs["prompt"] == "anime cat"
        assert resolved_vars["ep"] == "translate: anime cat"

    @pytest.mark.asyncio
    async def test_llm_mode_called(self):
        """llm 模式变量会调用 llm_value_factory"""
        factory = AsyncMock(return_value="english tags")
        defs = {"ep": _make_definition("ep", mode="llm", values=["translate: {prompt}"])}
        resolver = _make_resolver(
            action_inputs={"prompt": "a cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=factory,
            builtin_variable_provider=_mock_builtin_provider(),
        )
        assert resolved_vars["ep"] == "english tags"
        factory.assert_called_once_with("translate: a cat")

    @pytest.mark.asyncio
    async def test_probability_zero_returns_empty(self):
        """probability=0 的变量返回空字符串"""
        defs = {"style": _make_definition("style", values=["anime"], probability=0.0)}
        resolver = _make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=_mock_builtin_provider(),
        )
        assert resolved_vars["style"] == ""

    @pytest.mark.asyncio
    async def test_cycle_raises_on_resolve(self):
        """循环引用在 resolve_all 中也会抛错"""
        defs = {
            "a": _make_definition("a", values=["{b}"]),
            "b": _make_definition("b", values=["{a}"]),
        }
        resolver = _make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        with pytest.raises(ValueError, match="循环引用"):
            await resolver.resolve_all(
                builtin_placeholder_values={},
                llm_value_factory=AsyncMock(),
                builtin_variable_provider=_mock_builtin_provider(),
            )

    @pytest.mark.asyncio
    async def test_builtin_placeholder_substituted(self):
        """内置变量在自定义变量模板中被替换"""
        defs = {"ep": _make_definition("ep", values=["seed={random_seed}"])}
        resolver = _make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        )
        _, resolved_vars = await resolver.resolve_all(
            builtin_placeholder_values={"{random_seed}": 42},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=_mock_builtin_provider(),
        )
        assert resolved_vars["ep"] == "seed=42"
