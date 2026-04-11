import json
from dataclasses import dataclass
from typing import Any

from .builtin_variable_provider import BuiltinVariableProvider
from .template_placeholder_utils import TemplatePlaceholderUtils


@dataclass(frozen=True)
class CustomVariableDefinition:
    key: str
    mode: str
    values: list[str]
    probability: float
    index: int


class CustomVariableRegistry:
    """自定义变量定义注册表"""

    def __init__(
            self,
            raw_variables: Any,
            action_parameter_names: set[str],
    ) -> None:
        self.action_parameter_names = set(action_parameter_names)
        self.variable_definitions = self._parse_variable_definitions(raw_variables)

    def collect_required_variable_keys(self, raw_bindings: Any) -> set[str]:
        """
        从参数映射配置中提取本次真正会使用到的自定义变量名

        :param raw_bindings: Any，原始 openapi_parameter_mappings 配置
        :return: set[str]，本次需要解析的自定义变量键名集合
        """
        if raw_bindings is None:
            return set()
        if not isinstance(raw_bindings, list) or not raw_bindings:
            raise ValueError("openapi_parameter_mappings 必须是非空列表")

        required_keys: set[str] = set()
        for index, item in enumerate(raw_bindings):
            if not isinstance(item, dict):
                raise ValueError(f"openapi_parameter_mappings[{index}] 必须是对象")
            if "value" not in item:
                raise ValueError(f"openapi_parameter_mappings[{index}].value 缺失")
            required_keys.update(
                TemplatePlaceholderUtils.collect_custom_placeholder_names(
                    item.get("value"),
                    action_parameter_names=self.action_parameter_names,
                    builtin_names=BuiltinVariableProvider.get_default_variable_names(),
                )
            )

        return required_keys

    def _parse_variable_definitions(self, raw_variables: Any) -> dict[str, CustomVariableDefinition]:
        """
        解析并校验自定义变量配置列表

        :param raw_variables: Any，原始 custom_variables 配置
        :return: dict[str, CustomVariableDefinition]，变量键名到变量定义对象的映射
        """
        if raw_variables is None:
            return {}
        if not isinstance(raw_variables, list):
            raise ValueError("custom_variables 必须是列表")
        if not raw_variables:
            return {}

        definitions: dict[str, CustomVariableDefinition] = {}
        reserved_names = self.action_parameter_names | BuiltinVariableProvider.get_default_variable_names()
        for index, item in enumerate(raw_variables):
            if not isinstance(item, dict):
                raise ValueError(f"custom_variables[{index}] 必须是对象")

            key = self._require_text(item.get("key"), f"custom_variables[{index}].key")
            if key in reserved_names:
                raise ValueError(f"custom_variables[{index}].key 不允许与 action 参数或保留名冲突: {key}")
            if key in definitions:
                raise ValueError(f"custom_variables[{index}].key 重复: {key}")

            mode = self._require_text(item.get("mode", "literal"), f"custom_variables[{index}].mode").lower()
            if mode not in {"literal", "llm"}:
                raise ValueError(f"custom_variables[{index}].mode 只能是 literal 或 llm")

            probability = float(str(item.get("probability", 1.0)).strip())
            if probability < 0 or probability > 1:
                raise ValueError(f"custom_variables[{index}].probability 必须在 0 到 1 之间: {probability}")

            values = self._parse_variable_values(item.get("values"), f"custom_variables[{index}].values")
            definitions[key] = CustomVariableDefinition(
                key=key,
                mode=mode,
                values=values,
                probability=probability,
                index=index,
            )

        return definitions

    @staticmethod
    def _parse_variable_values(value: Any, field_name: str) -> list[str]:
        """
        将变量配置值整理为候选字符串列表，并兼容 JSON 列表字符串

        :param value: Any，原始变量候选值配置
        :param field_name: str，当前字段名，用于拼接报错信息
        :return: list[str]，清洗后的候选字符串列表
        """
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]

        text = str(value).strip()
        if not text:
            return []

        if text.startswith("["):
            try:
                parsed_value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{field_name} 不是合法的 JSON 列表字符串: {exc} parsed_value: {text}") from exc

            if not isinstance(parsed_value, list):
                raise ValueError(f"{field_name} 必须是 JSON 列表")
            return [str(item).strip() for item in parsed_value if str(item).strip()]

        return [line.strip() for line in text.splitlines() if line.strip()]

    @staticmethod
    def _require_text(value: Any, field_name: str) -> str:
        """
        校验文本字段非空并返回去空白结果

        :param value: Any，原始字段值
        :param field_name: str，当前字段名，用于拼接报错信息
        :return: str，去除首尾空白后的非空文本
        """
        text = "" if value is None else str(value).strip()
        if not text:
            raise ValueError(f"{field_name} 不能为空")
        return text
