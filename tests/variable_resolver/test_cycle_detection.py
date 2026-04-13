import pytest

from .fixtures import make_definition, make_resolver


class TestCycleDetection:
    def test_cycle_detection_simple(self):
        defs = {
            "a": make_definition("a", values=["{b}"]),
            "b": make_definition("b", values=["{a}"]),
        }
        resolver = make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        with pytest.raises(ValueError, match="a|b"):
            resolver.topological_sort()

    def test_cycle_detection_with_action_input(self):
        defs = {"style": make_definition("style", values=["{prompt}"])}
        resolver = make_resolver(
            action_inputs={"prompt": "{style}"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        with pytest.raises(ValueError, match="style|prompt"):
            resolver.topological_sort()

    def test_soft_dependency_cycle_is_detected(self):
        defs = {
            "a": make_definition(
                "a",
                values=["main"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="x",
                values_else=["{b}"],
            ),
            "b": make_definition(
                "b",
                values=["main"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="y",
                values_else=["{a}"],
            ),
        }
        resolver = make_resolver(
            action_inputs={"prompt": "z"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        with pytest.raises(ValueError, match="a|b"):
            resolver.topological_sort()

    def test_cycle_through_fallback_value(self):
        defs = {
            "var_a": make_definition(
                "var_a",
                mode="dict",
                source="x",
                entries={"hit": "ok"},
                missing_behavior="use_default",
                fallback_value="{var_b}",
            ),
            "var_b": make_definition(
                "var_b",
                mode="dict",
                source="x",
                entries={"hit": "ok"},
                missing_behavior="use_default",
                fallback_value="{var_a}",
            ),
        }
        with pytest.raises(ValueError, match=".*"):
            make_resolver(
                action_inputs={"x": "miss"},
                definitions=defs,
                action_parameter_names={"x"},
            ).topological_sort()

    def test_cycle_through_condition_value(self):
        defs = {
            "var_a": make_definition(
                "var_a",
                values=["a"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="{var_b}",
                values_else=["a_else"],
            ),
            "var_b": make_definition(
                "var_b",
                values=["b"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="{var_a}",
                values_else=["b_else"],
            ),
        }
        with pytest.raises(ValueError, match=".*"):
            make_resolver(
                action_inputs={"prompt": "test"},
                definitions=defs,
                action_parameter_names={"prompt"},
            ).topological_sort()

    def test_no_cycle_when_acyclic(self):
        defs = {
            "base": make_definition("base", values=["base_val"]),
            "dict_var": make_definition(
                "dict_var",
                mode="dict",
                source="x",
                entries={"hit": "ok"},
                missing_behavior="use_default",
                fallback_value="{base}",
            ),
            "cond_var": make_definition(
                "cond_var",
                values=["yes"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="{base}",
                values_else=["no"],
            ),
        }
        order = make_resolver(
            action_inputs={"x": "miss", "prompt": "test"},
            definitions=defs,
            action_parameter_names={"x", "prompt"},
        ).topological_sort()
        assert "base" in order
        assert "dict_var" in order
        assert "cond_var" in order
