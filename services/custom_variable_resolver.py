import json
import random
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.common.logger import get_logger
from ..clients import BizyAirOpenApiClient

logger = get_logger("bizyair_generate_image_plugin")


@dataclass(frozen=True)
class CustomVariableDefinition:
    key: str
    mode: str
    values: list[str]
    probability: float
    index: int


class CustomVariableResolver:
    """按需解析自定义变量"""

    PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]+)\}")
    RESERVED_NAMES = {"random_seed"}

    def __init__(
            self,
            raw_variables: Any,
            action_inputs: dict[str, Any],
            llm_value_factory: Callable[[str], Awaitable[str]],
    ) -> None:
        """初始化变量解析器并预解析变量定义"""
        self.action_inputs = action_inputs
        self.llm_value_factory = llm_value_factory
        self.variable_definitions = self._parse_variable_definitions(raw_variables)

    def collect_required_variable_keys(self, raw_bindings: Any) -> set[str]:
        """从参数映射配置中提取本次真正需要解析的变量名"""
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
            required_keys.update(self._extract_variable_placeholders(item.get("value")))

        return required_keys

    async def resolve_required_variables(self, required_keys: set[str]) -> dict[str, Any]:
        """仅解析本次实际被引用到的变量"""
        if not required_keys:
            return {}

        resolved: dict[str, Any] = {}
        for key in required_keys:
            definition = self.variable_definitions.get(key)
            if definition is None:
                raise ValueError(f"模板中引用了未定义的自定义变量: {key}")
            resolved[key] = await self._resolve_single_variable(definition)
        return resolved

    def _parse_variable_definitions(self, raw_variables: Any) -> dict[str, CustomVariableDefinition]:
        """解析并校验自定义变量配置"""
        if raw_variables is None:
            return {}
        if not isinstance(raw_variables, list):
            raise ValueError("custom_variables 必须是列表")
        if not raw_variables:
            return {}

        definitions: dict[str, CustomVariableDefinition] = {}
        reserved_names = set(self.action_inputs.keys()) | self.RESERVED_NAMES
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

    async def _resolve_single_variable(self, definition: CustomVariableDefinition) -> str:
        """解析单个变量定义"""
        if random.random() > definition.probability:
            return ""

        selected_value = ""
        if definition.values:
            selected_value = str(
                BizyAirOpenApiClient.resolve_template_value_static(
                    random.choice(definition.values),
                    self.action_inputs,
                )
            ).strip()

        if definition.mode == "literal":
            return selected_value

        if not selected_value:
            return ""
        return await self.llm_value_factory(selected_value)

    def _extract_variable_placeholders(self, value: Any) -> set[str]:
        """从模板值中提取自定义变量占位符"""
        placeholder_names = self._extract_all_placeholders(value)
        action_input_names = set(self.action_inputs.keys())
        return {
            name for name in placeholder_names
            if name not in action_input_names and name not in self.RESERVED_NAMES
        }

    def _extract_all_placeholders(self, value: Any) -> set[str]:
        """递归提取模板中的全部占位符名"""
        if isinstance(value, str):
            return {match.group(1).strip() for match in self.PLACEHOLDER_PATTERN.finditer(value) if match.group(1).strip()}
        if isinstance(value, list):
            result: set[str] = set()
            for item in value:
                result.update(self._extract_all_placeholders(item))
            return result
        if isinstance(value, dict):
            result: set[str] = set()
            for item in value.values():
                result.update(self._extract_all_placeholders(item))
            return result
        return set()

    @staticmethod
    def _parse_variable_values(value: Any, field_name: str) -> list[str]:
        """将变量配置值整理为候选字符串列表并兼容 JSON 列表字符串"""
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
                raise ValueError(f"{field_name} 不是合法的 JSON 列表字符串: {exc}") from exc

            if not isinstance(parsed_value, list):
                raise ValueError(f"{field_name} 必须是 JSON 列表")
            return [str(item).strip() for item in parsed_value if str(item).strip()]

        return [line.strip() for line in text.splitlines() if line.strip()]

    @staticmethod
    def _require_text(value: Any, field_name: str) -> str:
        """校验文本字段非空"""
        text = "" if value is None else str(value).strip()
        if not text:
            raise ValueError(f"{field_name} 不能为空")
        return text
