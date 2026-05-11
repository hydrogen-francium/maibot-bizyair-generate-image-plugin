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

    def test_condition_fields_are_parsed(self):
        raw = [
            {
                "key": "style_hint",
                "mode": "literal",
                "condition_type": "length_gt",
                "condition_source": "prompt",
                "condition_value": "50",
                "values": '["long"]',
                "values_else": '["short"]',
            }
        ]
        registry = _make_registry(raw_variables=raw, action_parameter_names={"prompt"})
        definition = registry.variable_definitions["style_hint"]
        assert definition.condition_type == "length_gt"
        assert definition.condition_source == "prompt"
        assert definition.condition_value == "50"
        assert definition.values_else == ["short"]

    def test_invalid_condition_type_raises(self):
        raw = [
            {
                "key": "style_hint",
                "mode": "literal",
                "condition_type": "invalid",
                "condition_source": "prompt",
                "condition_value": "50",
                "values": '["x"]',
            }
        ]
        with pytest.raises(ValueError, match="condition_type"):
            _make_registry(raw_variables=raw, action_parameter_names={"prompt"})

    def test_fixed_true_does_not_require_source_or_value(self):
        raw = [
            {
                "key": "style_hint",
                "mode": "literal",
                "condition_type": "fixed_true",
                "values": '["x"]',
            }
        ]
        registry = _make_registry(raw_variables=raw)
        definition = registry.variable_definitions["style_hint"]
        assert definition.condition_type == "fixed_true"
        assert definition.condition_source is None
        assert definition.condition_value is None

    def test_fixed_false_does_not_require_source_or_value(self):
        raw = [
            {
                "key": "style_hint",
                "mode": "literal",
                "condition_type": "fixed_false",
                "values": '["x"]',
                "values_else": '["y"]',
            }
        ]
        registry = _make_registry(raw_variables=raw)
        definition = registry.variable_definitions["style_hint"]
        assert definition.condition_type == "fixed_false"
        assert definition.condition_source is None
        assert definition.condition_value is None

    def test_dict_mode_parses_entries_source_and_fallback(self):
        raw = [
            {
                "key": "emotion_prompt",
                "mode": "dict",
                "source": "emotion",
                "values": '{"joy": "smile", "cool": "glasses"}',
                "missing_behavior": "use_default",
                "fallback_value": "neutral",
            }
        ]
        registry = _make_registry(raw_variables=raw, action_parameter_names={"emotion"})
        definition = registry.variable_definitions["emotion_prompt"]
        assert definition.mode == "dict"
        assert definition.source == "emotion"
        assert definition.entries == {"joy": "smile", "cool": "glasses"}
        assert definition.missing_behavior == "use_default"
        assert definition.fallback_value == "neutral"
        assert definition.values == []

    def test_dict_mode_missing_source_raises(self):
        raw = [
            {
                "key": "emotion_prompt",
                "mode": "dict",
                "values": '{"joy": "smile"}',
            }
        ]
        with pytest.raises(ValueError, match="source 不能为空"):
            _make_registry(raw_variables=raw)

    def test_invalid_missing_behavior_raises(self):
        raw = [
            {
                "key": "emotion_prompt",
                "mode": "dict",
                "source": "emotion",
                "values": '{"joy": "smile"}',
                "missing_behavior": "invalid",
            }
        ]
        with pytest.raises(ValueError, match="missing_behavior"):
            _make_registry(raw_variables=raw, action_parameter_names={"emotion"})


class TestExtractMode:
    def test_extract_mode_parses_pattern_and_group(self):
        raw = [
            {
                "key": "scene_type",
                "mode": "extract",
                "source": "director",
                "pattern": r"SCENE_TYPE[:：]\s*(\S+)",
                "group": 1,
                "missing_behavior": "use_default",
                "fallback_value": "normal",
            }
        ]
        registry = _make_registry(raw_variables=raw)
        definition = registry.variable_definitions["scene_type"]
        assert definition.mode == "extract"
        assert definition.source == "director"
        assert definition.pattern == r"SCENE_TYPE[:：]\s*(\S+)"
        assert definition.group == 1
        assert definition.missing_behavior == "use_default"
        assert definition.fallback_value == "normal"
        assert definition.values == []

    def test_extract_mode_default_group_is_one(self):
        raw = [
            {
                "key": "v",
                "mode": "extract",
                "source": "src",
                "pattern": r"(\S+)",
            }
        ]
        registry = _make_registry(raw_variables=raw)
        assert registry.variable_definitions["v"].group == 1

    def test_extract_mode_supports_group_zero(self):
        raw = [
            {
                "key": "v",
                "mode": "extract",
                "source": "src",
                "pattern": r"\S+",
                "group": 0,
            }
        ]
        registry = _make_registry(raw_variables=raw)
        assert registry.variable_definitions["v"].group == 0

    def test_extract_mode_missing_pattern_raises(self):
        raw = [
            {
                "key": "v",
                "mode": "extract",
                "source": "src",
            }
        ]
        with pytest.raises(ValueError, match="pattern 不能为空"):
            _make_registry(raw_variables=raw)

    def test_extract_mode_missing_source_raises(self):
        raw = [
            {
                "key": "v",
                "mode": "extract",
                "pattern": r"(\S+)",
            }
        ]
        with pytest.raises(ValueError, match="source 不能为空"):
            _make_registry(raw_variables=raw)

    def test_extract_mode_invalid_regex_raises(self):
        raw = [
            {
                "key": "v",
                "mode": "extract",
                "source": "src",
                "pattern": r"(unclosed",
            }
        ]
        with pytest.raises(ValueError, match="不是合法的正则"):
            _make_registry(raw_variables=raw)

    def test_extract_mode_negative_group_raises(self):
        raw = [
            {
                "key": "v",
                "mode": "extract",
                "source": "src",
                "pattern": r"(\S+)",
                "group": -1,
            }
        ]
        with pytest.raises(ValueError, match=">= 0"):
            _make_registry(raw_variables=raw)

    def test_extract_mode_non_integer_group_raises(self):
        raw = [
            {
                "key": "v",
                "mode": "extract",
                "source": "src",
                "pattern": r"(\S+)",
                "group": "abc",
            }
        ]
        with pytest.raises(ValueError, match="必须是整数"):
            _make_registry(raw_variables=raw)

    def test_extract_mode_invalid_missing_behavior_raises(self):
        raw = [
            {
                "key": "v",
                "mode": "extract",
                "source": "src",
                "pattern": r"(\S+)",
                "missing_behavior": "invalid",
            }
        ]
        with pytest.raises(ValueError, match="missing_behavior"):
            _make_registry(raw_variables=raw)


class TestDailyLlmMode:
    def test_daily_llm_default_no_validation(self):
        raw = [
            {
                "key": "today_state",
                "mode": "daily_llm",
                "values": '["prompt body"]',
            }
        ]
        registry = _make_registry(raw_variables=raw)
        definition = registry.variable_definitions["today_state"]
        assert definition.mode == "daily_llm"
        assert definition.values == ["prompt body"]
        assert definition.min_length == 0
        assert definition.required_markers == ()

    def test_daily_llm_parses_min_length_and_required_markers_json(self):
        raw = [
            {
                "key": "today_state",
                "mode": "daily_llm",
                "values": '["prompt"]',
                "min_length": 200,
                "required_markers": '["今日：", "整体心情：", "日程时间表"]',
            }
        ]
        registry = _make_registry(raw_variables=raw)
        definition = registry.variable_definitions["today_state"]
        assert definition.min_length == 200
        assert definition.required_markers == ("今日：", "整体心情：", "日程时间表")

    def test_daily_llm_required_markers_native_list(self):
        raw = [
            {
                "key": "v",
                "mode": "daily_llm",
                "values": '["prompt"]',
                "required_markers": ["a", "b"],
            }
        ]
        registry = _make_registry(raw_variables=raw)
        assert registry.variable_definitions["v"].required_markers == ("a", "b")

    def test_daily_llm_required_markers_dedupes_and_skips_empty(self):
        raw = [
            {
                "key": "v",
                "mode": "daily_llm",
                "values": '["prompt"]',
                "required_markers": ["a", "", "a", "b", "  "],
            }
        ]
        registry = _make_registry(raw_variables=raw)
        assert registry.variable_definitions["v"].required_markers == ("a", "b")

    def test_daily_llm_required_markers_single_string(self):
        raw = [
            {
                "key": "v",
                "mode": "daily_llm",
                "values": '["prompt"]',
                "required_markers": "今日：",
            }
        ]
        registry = _make_registry(raw_variables=raw)
        assert registry.variable_definitions["v"].required_markers == ("今日：",)

    def test_daily_llm_required_markers_invalid_json_raises(self):
        raw = [
            {
                "key": "v",
                "mode": "daily_llm",
                "values": '["prompt"]',
                "required_markers": "[unclosed",
            }
        ]
        with pytest.raises(ValueError, match="JSON"):
            _make_registry(raw_variables=raw)

    def test_daily_llm_required_markers_non_list_json_raises(self):
        raw = [
            {
                "key": "v",
                "mode": "daily_llm",
                "values": '["prompt"]',
                "required_markers": '{"a": "b"}',
            }
        ]
        with pytest.raises(ValueError, match="JSON 数组"):
            _make_registry(raw_variables=raw)

    def test_daily_llm_negative_min_length_raises(self):
        raw = [
            {
                "key": "v",
                "mode": "daily_llm",
                "values": '["prompt"]',
                "min_length": -1,
            }
        ]
        with pytest.raises(ValueError, match="非负整数"):
            _make_registry(raw_variables=raw)

    def test_daily_llm_non_integer_min_length_raises(self):
        raw = [
            {
                "key": "v",
                "mode": "daily_llm",
                "values": '["prompt"]',
                "min_length": "abc",
            }
        ]
        with pytest.raises(ValueError, match="非负整数"):
            _make_registry(raw_variables=raw)


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

    def test_dict_values_json_object_string(self):
        raw = [
            {
                "key": "emotion_prompt",
                "mode": "dict",
                "source": "emotion",
                "values": '{"joy": "smile", "cool": "glasses"}',
            }
        ]
        registry = _make_registry(raw_variables=raw, action_parameter_names={"emotion"})
        assert registry.variable_definitions["emotion_prompt"].entries == {"joy": "smile", "cool": "glasses"}

    def test_dict_values_non_object_raises(self):
        raw = [
            {
                "key": "emotion_prompt",
                "mode": "dict",
                "source": "emotion",
                "values": '["not", "object"]',
            }
        ]
        with pytest.raises(ValueError, match="必须是 JSON 对象"):
            _make_registry(raw_variables=raw, action_parameter_names={"emotion"})


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
