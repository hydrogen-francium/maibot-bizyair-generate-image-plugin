import json

import pytest

from services.action_parameter_utils import ActionParameterDefinition
from services.nai_chat_input_value_builder import NaiChatInputValueBuilder


class TestNaiChatInputValueBuilder:
    def test_build_message_content_json_normal(self):
        raw = [
            {"field": "prompt", "value_type": "string", "value": "{prompt}"},
            {"field": "steps", "value_type": "int", "value": "23"},
            {"field": "size", "value_type": "json", "value": "[832, 1216]"},
        ]
        bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        result = NaiChatInputValueBuilder.build_message_content_json(
            parameter_bindings=bindings,
            template_context={"prompt": "a cat"},
            action_inputs={"prompt": "a cat"},
            action_parameter_names={"prompt"},
            required_action_parameters={"prompt"},
            action_parameter_definitions={
                "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True)
            },
            builtin_placeholder_values={},
        )
        assert json.loads(result) == {
            "prompt": "a cat",
            "steps": 23,
            "size": [832, 1216],
        }

    def test_empty_payload_raises(self):
        raw = [
            {"field": "negative_prompt", "value_type": "string", "value": "{missing}"},
        ]
        bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        with pytest.raises(ValueError, match="解析结果为空"):
            NaiChatInputValueBuilder.build_message_content_json(
                parameter_bindings=bindings,
                template_context={"prompt": "a cat"},
                action_inputs={"prompt": "a cat"},
                action_parameter_names={"prompt", "missing"},
                required_action_parameters={"prompt"},
                action_parameter_definitions={
                    "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True),
                    "missing": ActionParameterDefinition(name="missing", description="缺失", required=False),
                },
                builtin_placeholder_values={},
            )