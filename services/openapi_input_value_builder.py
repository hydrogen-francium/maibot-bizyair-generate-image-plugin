from __future__ import annotations

import json
from typing import Any

from ..clients import BizyAirOpenApiParameterBinding
from .builtin_variable_provider import BuiltinVariableProvider
from .template_placeholder_utils import TemplatePlaceholderUtils

VALID_VALUE_TYPES = {"string", "int", "boolean", "json"}

class BizyAirOpenApiInputValueBuilder:
    """负责将 action/context/config 组装为 OpenAPI input_values"""

    BUILTIN_PLACEHOLDER_NAMES = BuiltinVariableProvider.get_default_variable_names()

    @classmethod
    def parse_parameter_bindings(cls, raw_bindings: Any) -> list[BizyAirOpenApiParameterBinding]:
        """
        解析并校验 OpenAPI 参数映射配置

        :param raw_bindings: Any，原始 openapi_parameter_mappings 配置
        :return: list[BizyAirOpenApiParameterBinding]，转换后的参数映射定义列表
        """
        if raw_bindings is None:
            return []
        if not isinstance(raw_bindings, list) or not raw_bindings:
            raise ValueError("openapi_parameter_mappings 必须是非空列表")

        

        bindings: list[BizyAirOpenApiParameterBinding] = []
        for index, item in enumerate(raw_bindings):
            if not isinstance(item, dict):
                raise ValueError(f"openapi_parameter_mappings[{index}] 必须是对象")

            field = cls._require_mapping_text(item.get("field"), f"openapi_parameter_mappings[{index}].field")
            if "value" not in item:
                raise ValueError(f"openapi_parameter_mappings[{index}].value 缺失")
            value_type = cls._require_mapping_text(item.get("value_type", "string"), f"openapi_parameter_mappings[{index}].value_type").lower()
            if value_type not in VALID_VALUE_TYPES:
                raise ValueError(f"openapi_parameter_mappings[{index}].value_type 不支持: {value_type}")
            raw_value = item.get("value")
            value_template = "" if raw_value is None else str(raw_value)
            send_if_empty = bool(item.get("send_if_empty", False))

            bindings.append(BizyAirOpenApiParameterBinding(
                field=field,
                value_template=value_template,
                value_type=value_type,
                send_if_empty=send_if_empty,
            ))
        return bindings

    @classmethod
    def build_input_values(
            cls,
            parameter_bindings: list[BizyAirOpenApiParameterBinding],
            template_context: dict[str, Any],
            action_inputs: dict[str, Any],
            action_parameter_names: set[str],
            required_action_parameters: set[str],
            builtin_placeholder_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        根据映射配置和上下文构造 OpenAPI 的 input_values

        :param parameter_bindings: list[BizyAirOpenApiParameterBinding]，已解析的参数映射定义列表
        :param template_context: dict[str, Any]，模板上下文变量映射
        :param action_inputs: dict[str, Any]，当前 action 输入参数值映射
        :param action_parameter_names: set[str]，action 支持的参数名集合
        :param required_action_parameters: set[str]，action 中声明为必填的参数名集合
        :param builtin_placeholder_values: dict[str, Any] | None，已构造好的内置变量占位符值映射
        :return: dict[str, Any]，最终要发送给 OpenAPI 的 input_values 字典
        """
        if not isinstance(template_context, dict) or not template_context:
            raise ValueError("template_context 必须是非空对象")

        placeholder_values = cls._build_placeholder_values(
            template_context,
            builtin_placeholder_values=builtin_placeholder_values,
        )
        input_values: dict[str, Any] = {}
        for index, binding in enumerate(parameter_bindings):
            resolved_value = cls._resolve_template_value(
                binding.value_template,
                placeholder_values,
                action_inputs=action_inputs,
                action_parameter_names=action_parameter_names,
                required_action_parameters=required_action_parameters,
            )
            if not binding.send_if_empty and cls._is_empty_mapping_value(resolved_value):
                continue
            # 占位符替换完成后，按 value_type 做类型转换
            resolved_value = cls._coerce_mapping_value(
                resolved_value, binding.value_type,
                f"openapi_parameter_mappings[{index}].value",
            )
            input_values[binding.field] = resolved_value
        return input_values

    @classmethod
    def resolve_template_value_static(
            cls,
            value_template: Any,
            template_context: dict[str, Any],
            builtin_placeholder_values: dict[str, Any] | None = None,
    ) -> Any:
        """
        基于模板上下文静态解析模板值，不处理 action 参数缺失规则

        :param value_template: Any，待解析的模板值
        :param template_context: dict[str, Any]，模板上下文变量映射
        :param builtin_placeholder_values: dict[str, Any] | None，内置变量占位符值映射
        :return: Any，完成占位符替换后的模板结果
        """
        placeholder_values = cls._build_placeholder_values(
            template_context,
            builtin_placeholder_values=builtin_placeholder_values,
        )
        return cls._resolve_template_value_static(value_template, placeholder_values)

    @classmethod
    def _build_placeholder_values(
            cls,
            template_context: dict[str, Any],
            builtin_placeholder_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        构造模板解析时使用的占位符映射表

        :param template_context: dict[str, Any]，模板上下文变量映射
        :param builtin_placeholder_values: dict[str, Any] | None，内置变量占位符值映射
        :return: dict[str, Any]，完整的占位符到实际值映射表
        """
        placeholder_values = dict(builtin_placeholder_values or {})
        for key, value in template_context.items():
            placeholder_values[f"{{{key}}}"] = value
        return placeholder_values

    @classmethod
    def collect_builtin_placeholder_names_from_bindings(cls, raw_bindings: Any) -> set[str]:
        """
        从参数映射配置中提取被引用到的内置变量名

        :param raw_bindings: Any，原始 openapi_parameter_mappings 配置
        :return: set[str]，本次需要构造的内置变量名称集合
        """
        if raw_bindings is None:
            return set()
        if not isinstance(raw_bindings, list) or not raw_bindings:
            raise ValueError("openapi_parameter_mappings 必须是非空列表")

        required_names: set[str] = set()
        for index, item in enumerate(raw_bindings):
            if not isinstance(item, dict):
                raise ValueError(f"openapi_parameter_mappings[{index}] 必须是对象")
            if "value" not in item:
                raise ValueError(f"openapi_parameter_mappings[{index}].value 缺失")
            required_names.update(
                TemplatePlaceholderUtils.collect_builtin_placeholder_names(
                    item.get("value"),
                    cls.BUILTIN_PLACEHOLDER_NAMES,
                )
            )
        return required_names

    @classmethod
    def _resolve_template_value_static(cls, value_template: Any, placeholder_values: dict[str, Any]) -> Any:
        """
        静态递归解析模板值中的占位符

        :param value_template: Any，待解析的模板值
        :param placeholder_values: dict[str, Any]，占位符到实际值的映射表
        :return: Any，完成占位符替换后的模板结果
        """
        if isinstance(value_template, str):
            stripped = value_template.strip()
            if stripped in placeholder_values:
                return placeholder_values[stripped]

            resolved = value_template
            for placeholder, value in placeholder_values.items():
                resolved = resolved.replace(placeholder, str(value))
            return resolved

        if isinstance(value_template, list):
            return [cls._resolve_template_value_static(item, placeholder_values) for item in value_template]

        if isinstance(value_template, dict):
            return {key: cls._resolve_template_value_static(value, placeholder_values) for key, value in value_template.items()}

        return value_template

    @classmethod
    def _resolve_template_value(
            cls,
            value_template: Any,
            placeholder_values: dict[str, Any],
            action_inputs: dict[str, Any],
            action_parameter_names: set[str],
            required_action_parameters: set[str],
    ) -> Any:
        """
        递归解析模板值中的占位符，并处理 action 参数缺失规则

        :param value_template: Any，待解析的模板值
        :param placeholder_values: dict[str, Any]，占位符到实际值的映射表
        :param action_inputs: dict[str, Any]，当前 action 输入参数值映射
        :param action_parameter_names: set[str]，action 支持的参数名集合
        :param required_action_parameters: set[str]，action 中声明为必填的参数名集合
        :return: Any，完成占位符替换后的模板结果
        """
        if isinstance(value_template, str):
            stripped = value_template.strip()
            if stripped in placeholder_values:
                return placeholder_values[stripped]

            resolved = value_template
            for placeholder, value in placeholder_values.items():
                resolved = resolved.replace(placeholder, str(value))
            return cls._resolve_remaining_placeholders(
                resolved,
                action_inputs=action_inputs,
                action_parameter_names=action_parameter_names,
                required_action_parameters=required_action_parameters,
            )

        if isinstance(value_template, list):
            return [
                cls._resolve_template_value(
                    item,
                    placeholder_values,
                    action_inputs=action_inputs,
                    action_parameter_names=action_parameter_names,
                    required_action_parameters=required_action_parameters,
                )
                for item in value_template
            ]

        if isinstance(value_template, dict):
            return {
                key: cls._resolve_template_value(
                    value,
                    placeholder_values,
                    action_inputs=action_inputs,
                    action_parameter_names=action_parameter_names,
                    required_action_parameters=required_action_parameters,
                )
                for key, value in value_template.items()
            }

        return value_template

    @classmethod
    def _resolve_remaining_placeholders(
            cls,
            resolved_text: str,
            action_inputs: dict[str, Any],
            action_parameter_names: set[str],
            required_action_parameters: set[str],
    ) -> str:
        """
        处理替换完成后仍残留的占位符文本

        :param resolved_text: str，已完成首轮替换的文本
        :param action_inputs: dict[str, Any]，当前 action 输入参数值映射
        :param action_parameter_names: set[str]，action 支持的参数名集合
        :param required_action_parameters: set[str]，action 中声明为必填的参数名集合
        :return: str，处理残留占位符后的最终文本
        """
        result = resolved_text
        for placeholder_name in TemplatePlaceholderUtils.extract_placeholder_names(resolved_text):
            if (
                    placeholder_name in action_parameter_names
                    and placeholder_name not in required_action_parameters
                    and placeholder_name not in action_inputs
            ):
                result = result.replace(f"{{{placeholder_name}}}", "")
                continue
            raise ValueError(f"模板中引用了未定义的变量: {placeholder_name}")
        return result

    @classmethod
    def _is_empty_mapping_value(cls, value: Any) -> bool:
        """
        判断映射结果是否应被视为空值

        :param value: Any，待判断的映射结果值
        :return: bool，结果是否为空
        """
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, dict, tuple, set)):
            return len(value) == 0
        return False

    @classmethod
    def _coerce_mapping_value(cls, value: Any, value_type: str, field_name: str) -> Any:
        """
        按声明的 value_type 强制转换映射值

        :param value: Any，原始映射值
        :param value_type: str，声明的目标类型
        :param field_name: str，当前字段名，用于拼接报错信息
        :return: Any，按目标类型转换后的值
        """
        if value_type == "string":
            if value is None:
                return ""
            return str(value)

        raw_text = "" if value is None else str(value).strip()

        if value_type == "int":
            try:
                return int(raw_text)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field_name} 不是合法整数: {value}") from exc

        if value_type == "boolean":
            normalized = raw_text.lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"{field_name} 不是合法布尔值: {value}")

        if value_type == "json":
            if not raw_text:
                raise ValueError(f"{field_name} 不能为空")
            try:
                return json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{field_name} 不是合法 JSON: {value}") from exc

        raise ValueError(f"{field_name} 的类型不支持: {value_type}")

    @classmethod
    def _require_mapping_text(cls, value: Any, field_name: str) -> str:
        """
        校验映射字段文本非空并返回规范化结果

        :param value: Any，原始字段值
        :param field_name: str，当前字段名，用于拼接报错信息
        :return: str，去除首尾空白后的非空文本
        """
        text = "" if value is None else str(value).strip()
        if not text:
            raise ValueError(f"{field_name} 不能为空")
        return text
