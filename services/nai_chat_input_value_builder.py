from __future__ import annotations

import json
from typing import Any

from src.common.logger import get_logger
from .action_parameter_utils import ActionParameterDefinition
from .builtin_variable_provider import BuiltinVariableProvider
from .openapi_input_value_builder import BizyAirOpenApiInputValueBuilder
from ..clients import BizyAirOpenApiParameterBinding

logger = get_logger("bizyair_generate_image_plugin")


class NaiChatInputValueBuilder:
    """负责将 action/context/config 组装为 NAI Chat user message JSON"""

    BUILTIN_PLACEHOLDER_NAMES = BuiltinVariableProvider.get_default_variable_names()

    @classmethod
    def parse_parameter_bindings(cls, raw_bindings: Any) -> list[BizyAirOpenApiParameterBinding]:
        """解析并校验 NAI 参数映射配置"""
        return BizyAirOpenApiInputValueBuilder.parse_parameter_bindings(raw_bindings)

    @classmethod
    def build_message_content_json(
            cls,
            parameter_bindings: list[BizyAirOpenApiParameterBinding],
            template_context: dict[str, Any],
            action_inputs: dict[str, Any],
            action_parameter_names: set[str],
            required_action_parameters: set[str],
            action_parameter_definitions: dict[str, ActionParameterDefinition] | None = None,
            builtin_placeholder_values: dict[str, Any] | None = None,
    ) -> str:
        """构造 messages[0].content 对应的 JSON 字符串"""
        payload = BizyAirOpenApiInputValueBuilder.build_input_values(
            parameter_bindings=parameter_bindings,
            template_context=template_context,
            action_inputs=action_inputs,
            action_parameter_names=action_parameter_names,
            required_action_parameters=required_action_parameters,
            action_parameter_definitions=action_parameter_definitions,
            builtin_placeholder_values=builtin_placeholder_values,
        )
        if not payload:
            raise ValueError("NAI parameter_mappings 解析结果为空，无法构造 user message content")
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def collect_builtin_placeholder_names_from_bindings(cls, raw_bindings: Any) -> set[str]:
        """从 NAI 参数映射配置中提取被引用到的内置变量名"""
        return BizyAirOpenApiInputValueBuilder.collect_builtin_placeholder_names_from_bindings(raw_bindings)