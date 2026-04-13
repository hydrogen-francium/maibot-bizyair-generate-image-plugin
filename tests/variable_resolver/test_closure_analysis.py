import pytest

from services.variable_dependency_resolver import VariableDependencyResolver

from .fixtures import BUILTIN_NAMES, make_definition


class TestComputeRequiredVariableKeys:
    def test_direct_keys_only(self):
        defs = {"ep": make_definition("ep", values=["{prompt}"])}
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
            "a": make_definition("a", values=["{b}"]),
            "b": make_definition("b", values=["hello"]),
            "c": make_definition("c", values=["unused"]),
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
        defs = {"style": make_definition("style", values=["anime"])}
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
            "a": make_definition("a", values=["{b}"]),
            "b": make_definition("b", values=["{c}"]),
            "c": make_definition("c", values=["{d}"]),
            "d": make_definition("d", values=["leaf"]),
        }
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"a"},
            action_inputs={},
            custom_variable_definitions=defs,
            action_parameter_names=set(),
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"a", "b", "c", "d"}

    def test_dict_source_included_in_closure(self):
        defs = {
            "selector": make_definition("selector", values=["joy"]),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="selector",
                entries={"joy": "smile"},
            ),
        }
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"emotion_prompt"},
            action_inputs={},
            custom_variable_definitions=defs,
            action_parameter_names=set(),
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"emotion_prompt", "selector"}

    def test_values_else_references_included_in_closure(self):
        defs = {
            "fallback": make_definition("fallback", values=["leaf"]),
            "style_hint": make_definition(
                "style_hint",
                values=["main"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="cat",
                values_else=["{fallback}"],
            ),
        }
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"style_hint"},
            action_inputs={"prompt": "dog"},
            custom_variable_definitions=defs,
            action_parameter_names={"prompt"},
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"style_hint", "fallback"}

    def test_soft_dependency_downstream_kept_in_closure(self):
        defs = {
            "base": make_definition("base", values=["leaf"]),
            "branch": make_definition("branch", values=["{base}"]),
            "selector": make_definition(
                "selector",
                values=["main"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="cat",
                values_else=["{branch}"],
            ),
        }
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"selector"},
            action_inputs={"prompt": "dog"},
            custom_variable_definitions=defs,
            action_parameter_names={"prompt"},
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"selector", "branch", "base"}


class TestComputeRequiredKeysSoftDeps:
    def test_fallback_value_ref_included(self):
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
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"emotion_prompt"},
            action_inputs={},
            custom_variable_definitions=defs,
            action_parameter_names=set(),
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"emotion_prompt", "default_text"}

    def test_condition_value_ref_included(self):
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
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"style_hint"},
            action_inputs={},
            custom_variable_definitions=defs,
            action_parameter_names=set(),
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"style_hint", "threshold"}

    def test_fallback_builtin_ref_not_included_as_custom(self):
        defs = {
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "smile"},
                missing_behavior="use_default",
                fallback_value="seed={random_seed}",
            ),
        }
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"emotion_prompt"},
            action_inputs={},
            custom_variable_definitions=defs,
            action_parameter_names=set(),
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"emotion_prompt"}

    def test_transitive_through_fallback(self):
        defs = {
            "base": make_definition("base", values=["base_val"]),
            "mid": make_definition("mid", values=["{base}"]),
            "top": make_definition(
                "top",
                mode="dict",
                source="x",
                entries={"hit": "ok"},
                missing_behavior="use_default",
                fallback_value="{mid}",
            ),
        }
        result = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"top"},
            action_inputs={},
            custom_variable_definitions=defs,
            action_parameter_names=set(),
            builtin_names=BUILTIN_NAMES,
        )
        assert result == {"top", "mid", "base"}
