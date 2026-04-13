import pytest

from .fixtures import make_definition, make_resolver


class TestTopologicalSort:
    def test_no_nodes(self):
        resolver = make_resolver(
            action_inputs={"prompt": "cat"},
            definitions={},
            required_keys=set(),
        )
        assert resolver.topological_sort() == []

    def test_independent_variables(self):
        defs = {
            "a": make_definition("a", values=["x"]),
            "b": make_definition("b", values=["y"]),
        }
        resolver = make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        order = resolver.topological_sort()
        assert set(order) == {"a", "b"}

    def test_linear_chain(self):
        defs = {
            "a": make_definition("a", values=["{b}"]),
            "b": make_definition("b", values=["leaf"]),
        }
        resolver = make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        order = resolver.topological_sort()
        assert order.index("b") < order.index("a")

    def test_action_input_depends_on_custom_var(self):
        defs = {"style": make_definition("style", values=["anime"])}
        resolver = make_resolver(
            action_inputs={"prompt": "{style} cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
        )
        order = resolver.topological_sort()
        assert order.index("style") < order.index("prompt")

    def test_diamond_dag(self):
        defs = {
            "a": make_definition("a", values=["{c}"]),
            "b": make_definition("b", values=["{c}"]),
            "c": make_definition("c", values=["leaf"]),
        }
        resolver = make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        order = resolver.topological_sort()
        assert order.index("c") < order.index("a")
        assert order.index("c") < order.index("b")

    def test_dict_source_dependency_sorted_first(self):
        defs = {
            "selector": make_definition("selector", values=["joy"]),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="selector",
                entries={"joy": "smile"},
            ),
        }
        resolver = make_resolver(action_inputs={}, definitions=defs, action_parameter_names=set())
        order = resolver.topological_sort()
        assert order.index("selector") < order.index("emotion_prompt")


class TestTopologicalSortSoftDeps:
    def test_fallback_dep_sorted_before_consumer(self):
        defs = {
            "default_text": make_definition("default_text", values=["neutral"]),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "smile"},
                missing_behavior="use_default",
                fallback_value="{default_text}",
            ),
        }
        order = make_resolver(
            action_inputs={"emotion": "sad"},
            definitions=defs,
            action_parameter_names={"emotion"},
        ).topological_sort()
        assert order.index("default_text") < order.index("emotion_prompt")

    def test_condition_value_dep_sorted_before_consumer(self):
        defs = {
            "threshold": make_definition("threshold", values=["5"]),
            "style_hint": make_definition(
                "style_hint",
                values=["long mode"],
                condition_type="length_gt",
                condition_source="prompt",
                condition_value="{threshold}",
                values_else=["short mode"],
            ),
        }
        order = make_resolver(
            action_inputs={"prompt": "123456"},
            definitions=defs,
            action_parameter_names={"prompt"},
        ).topological_sort()
        assert order.index("threshold") < order.index("style_hint")
