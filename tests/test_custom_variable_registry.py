import pytest

from services.custom_variable_registry import CustomVariableRegistry


def _make_registry(
        raw_variables=None,
        action_parameter_names=None,
) -> CustomVariableRegistry:
    return CustomVariableRegistry(
        raw_variables=raw_variables or [],
        action_parameter_names=action_parameter_names or set(),
    )


class TestParseVariableDefinitions:
    def test_normal(self):
        raw = [
            {"key": "style", "mode": "literal", "values": '["anime"]', "probability": 1.0},
        ]
        registry = _make_registry(raw_variables=raw, action_parameter_names={"prompt"})
        assert "style" in registry.variable_definitions
        assert registry.variable_definitions["style"].mode == "literal"
        assert registry.variable_definitions["style"].values == ["anime"]

    def test_reserved_name_conflict_raises(self):
        raw = [{"key": "prompt", "mode": "literal", "values": '["x"]'}]
        with pytest.raises(ValueError, match="冲突"):
            _make_registry(raw_variables=raw, action_parameter_names={"prompt"})

    def test_builtin_name_conflict_raises(self):
        raw = [{"key": "random_seed", "mode": "literal", "values": '["x"]'}]
        with pytest.raises(ValueError, match="冲突"):
            _make_registry(raw_variables=raw)

    def test_duplicate_key_raises(self):
        raw = [
            {"key": "style", "mode": "literal", "values": '["a"]'},
            {"key": "style", "mode": "literal", "values": '["b"]'},
        ]
        with pytest.raises(ValueError, match="重复"):
            _make_registry(raw_variables=raw)

    def test_invalid_mode_raises(self):
        raw = [{"key": "style", "mode": "invalid", "values": '["x"]'}]
        with pytest.raises(ValueError, match="只能是"):
            _make_registry(raw_variables=raw)

    def test_probability_out_of_range_raises(self):
        raw = [{"key": "style", "mode": "literal", "values": '["x"]', "probability": 1.5}]
        with pytest.raises(ValueError, match="0 到 1"):
            _make_registry(raw_variables=raw)

    def test_none_raw_variables(self):
        registry = _make_registry(raw_variables=None)
        assert registry.variable_definitions == {}

    def test_empty_list(self):
        registry = _make_registry(raw_variables=[])
        assert registry.variable_definitions == {}


class TestParseVariableValues:
    def test_json_list_string(self):
        raw = [{"key": "s", "mode": "literal", "values": '["a", "b"]'}]
        registry = _make_registry(raw_variables=raw)
        assert registry.variable_definitions["s"].values == ["a", "b"]

    def test_native_list(self):
        raw = [{"key": "s", "mode": "literal", "values": ["x", "y"]}]
        registry = _make_registry(raw_variables=raw)
        assert registry.variable_definitions["s"].values == ["x", "y"]

    def test_multiline_text(self):
        raw = [{"key": "s", "mode": "literal", "values": "line1\nline2\n"}]
        registry = _make_registry(raw_variables=raw)
        assert registry.variable_definitions["s"].values == ["line1", "line2"]

    def test_none_values(self):
        raw = [{"key": "s", "mode": "literal", "values": None}]
        registry = _make_registry(raw_variables=raw)
        assert registry.variable_definitions["s"].values == []


class TestCollectRequiredVariableKeys:
    def test_extracts_custom_vars_from_bindings(self):
        raw = [{"key": "ep", "mode": "literal", "values": '["x"]'}]
        registry = _make_registry(
            raw_variables=raw,
            action_parameter_names={"prompt"},
        )
        bindings = [
            {"field": "f1", "value": "{ep}", "value_type": "string"},
        ]
        keys = registry.collect_required_variable_keys(bindings)
        assert keys == {"ep"}

    def test_ignores_action_params_and_builtins(self):
        raw = [{"key": "ep", "mode": "literal", "values": '["x"]'}]
        registry = _make_registry(
            raw_variables=raw,
            action_parameter_names={"prompt"},
        )
        bindings = [
            {"field": "f1", "value": "{prompt} {random_seed} {ep}", "value_type": "string"},
        ]
        keys = registry.collect_required_variable_keys(bindings)
        assert keys == {"ep"}

    def test_none_bindings(self):
        registry = _make_registry()
        assert registry.collect_required_variable_keys(None) == set()