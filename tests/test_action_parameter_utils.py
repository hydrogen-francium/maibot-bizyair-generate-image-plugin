import pytest

from services.action_parameter_utils import (
    ActionParameterDefinition,
    build_action_parameters,
    is_parameter_required,
    normalize_parameter,
)


class TestNormalizeParameter:
    def test_normal(self):
        assert normalize_parameter("prompt", "f") == "prompt"

    def test_strips_whitespace(self):
        assert normalize_parameter("  prompt  ", "f") == "prompt"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            normalize_parameter("", "field")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            normalize_parameter(None, "field")


class TestIsParameterRequired:
    def test_required(self):
        assert is_parameter_required("必填", "f") is True

    def test_optional_explicit(self):
        assert is_parameter_required("选填", "f") is False

    def test_optional_empty(self):
        assert is_parameter_required("", "f") is False

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="只能是"):
            is_parameter_required("maybe", "f")


class TestBuildActionParameters:
    def test_normal(self):
        raw = [
            {"name": "prompt", "description": "描述词", "required": "必填"},
            {"name": "style", "description": "风格", "required": "选填"},
        ]
        params = build_action_parameters(raw)
        assert params == {
            "prompt": ActionParameterDefinition(name="prompt", description="描述词", required=True),
            "style": ActionParameterDefinition(name="style", description="风格", required=False),
        }
        assert {name for name, definition in params.items() if definition.required} == {"prompt"}

    def test_parses_missing_behavior_and_default_value(self):
        raw = [
            {
                "name": "aspect_ratio",
                "description": "比例",
                "required": "选填",
                "missing_behavior": "use_default",
                "default_value": "1:1",
            }
        ]
        params = build_action_parameters(raw)
        assert params["aspect_ratio"] == ActionParameterDefinition(
            name="aspect_ratio",
            description="比例",
            required=False,
            missing_behavior="use_default",
            default_value="1:1",
        )

    def test_invalid_missing_behavior_raises(self):
        raw = [
            {
                "name": "aspect_ratio",
                "description": "比例",
                "missing_behavior": "invalid",
            }
        ]
        with pytest.raises(ValueError, match="missing_behavior"):
            build_action_parameters(raw)

    def test_blank_missing_behavior_raises(self):
        raw = [
            {
                "name": "aspect_ratio",
                "description": "比例",
                "missing_behavior": "   ",
            }
        ]
        with pytest.raises(ValueError, match="missing_behavior"):
            build_action_parameters(raw)

    def test_default_value_none_becomes_empty_string(self):
        raw = [
            {
                "name": "aspect_ratio",
                "description": "比例",
                "missing_behavior": "use_default",
                "default_value": None,
            }
        ]
        params = build_action_parameters(raw)
        assert params["aspect_ratio"].default_value == ""

    def test_default_value_strips_whitespace(self):
        raw = [
            {
                "name": "aspect_ratio",
                "description": "比例",
                "missing_behavior": "use_default",
                "default_value": "  16:9  ",
            }
        ]
        params = build_action_parameters(raw)
        assert params["aspect_ratio"].default_value == "16:9"

    def test_required_parameter_keeps_missing_behavior_metadata(self):
        raw = [
            {
                "name": "prompt",
                "description": "描述词",
                "required": "必填",
                "missing_behavior": "raise_error",
                "default_value": "ignored",
            }
        ]
        params = build_action_parameters(raw)
        assert params["prompt"] == ActionParameterDefinition(
            name="prompt",
            description="描述词",
            required=True,
            missing_behavior="raise_error",
            default_value="ignored",
        )

    def test_description_is_required(self):
        with pytest.raises(ValueError, match="description 不能为空"):
            build_action_parameters([{"name": "prompt"}])

    def test_name_is_stripped_before_duplicate_check(self):
        raw = [
            {"name": " prompt ", "description": "d1"},
            {"name": "prompt", "description": "d2"},
        ]
        with pytest.raises(ValueError, match="重复"):
            build_action_parameters(raw)

    def test_default_required_is_optional(self):
        raw = [{"name": "x", "description": "d"}]
        params = build_action_parameters(raw)
        assert "x" in params
        assert {name for name, definition in params.items() if definition.required} == set()

    def test_optional_empty_required_value_is_optional(self):
        raw = [{"name": "x", "description": "d", "required": ""}]
        params = build_action_parameters(raw)
        assert params["x"].required is False

    def test_non_string_name_and_description_are_normalized(self):
        raw = [{"name": 123, "description": 456, "required": "选填"}]
        params = build_action_parameters(raw)
        assert params["123"] == ActionParameterDefinition(
            name="123",
            description="456",
            required=False,
        )

    def test_missing_required_defaults_to_optional(self):
        raw = [{"name": "style", "description": "风格"}]
        params = build_action_parameters(raw)
        assert params["style"].required is False

    def test_invalid_required_value_raises(self):
        raw = [{"name": "style", "description": "风格", "required": "maybe"}]
        with pytest.raises(ValueError, match="只能是"):
            build_action_parameters(raw)

    def test_accepts_keep_placeholder_and_raise_error(self):
        raw = [
            {"name": "a", "description": "A", "missing_behavior": "keep_placeholder"},
            {"name": "b", "description": "B", "missing_behavior": "raise_error"},
        ]
        params = build_action_parameters(raw)
        assert params["a"].missing_behavior == "keep_placeholder"
        assert params["b"].missing_behavior == "raise_error"

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            build_action_parameters([{"description": "d"}])

    def test_duplicate_name_raises(self):
        raw = [
            {"name": "prompt", "description": "d1"},
            {"name": "prompt", "description": "d2"},
        ]
        with pytest.raises(ValueError, match="重复"):
            build_action_parameters(raw)

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="必须是非空列表"):
            build_action_parameters([])

    def test_non_list_raises(self):
        with pytest.raises(ValueError, match="必须是非空列表"):
            build_action_parameters("not a list")

    def test_non_dict_item_raises(self):
        with pytest.raises(ValueError, match="必须是对象"):
            build_action_parameters(["not a dict"])
