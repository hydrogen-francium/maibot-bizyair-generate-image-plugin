import json
import re
from dataclasses import dataclass, field
from typing import Any

from .builtin_variable_provider import BuiltinVariableProvider
from .template_placeholder_utils import TemplatePlaceholderUtils

VALID_VARIABLE_MODES = {"literal", "llm", "dict", "extract", "daily_llm"}

VALID_CONDITION_TYPES = {
    "fixed_true", "fixed_false",
    "length_gt", "length_lt",
    "contains", "not_contains",
    "equals", "not_equals",
    "regex_match", "regex_not_match",
}

VALID_MISSING_BEHAVIORS = {"keep_placeholder", "raise_error", "use_default"}


@dataclass(frozen=True)
class CustomVariableDefinition:
    key: str
    mode: str
    values: list[str]
    probability: float
    index: int
    condition_type: str | None = None
    condition_source: str | None = None
    condition_value: str | None = None
    values_else: list[str] = field(default_factory=list)
    source: str | None = None
    entries: dict[str, str] = field(default_factory=dict)
    missing_behavior: str = "keep_placeholder"
    fallback_value: str = ""
    use_raw_condition_source: bool = False
    use_raw_condition_value: bool = False
    pattern: str | None = None
    group: int = 1
    min_length: int = 0
    required_markers: tuple[str, ...] = ()


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
            if mode not in VALID_VARIABLE_MODES:
                raise ValueError(f"custom_variables[{index}].mode 只能是 {', '.join(sorted(VALID_VARIABLE_MODES))}")

            probability = float(str(item.get("probability", 1.0)).strip())
            if probability < 0 or probability > 1:
                raise ValueError(f"custom_variables[{index}].probability 必须在 0 到 1 之间: {probability}")

            # --- condition 字段 ---
            condition_type = self._parse_optional_text(item.get("condition_type"))
            condition_source: str | None = None
            condition_value: str | None = None
            if condition_type:
                if condition_type not in VALID_CONDITION_TYPES:
                    raise ValueError(f"custom_variables[{index}].condition_type 只能是 {', '.join(sorted(VALID_CONDITION_TYPES))}")
                if condition_type not in {"fixed_true", "fixed_false"}:
                    condition_source = self._require_text(item.get("condition_source"), f"custom_variables[{index}].condition_source", )
                    condition_value = self._require_text(item.get("condition_value"), f"custom_variables[{index}].condition_value", )
                else:
                    condition_source = self._parse_optional_text(item.get("condition_source"))
                    condition_value = self._parse_optional_text(item.get("condition_value"))

            # --- values_else ---
            values_else = self._parse_variable_values(item.get("values_else"), f"custom_variables[{index}].values_else")

            # --- dict 模式特有字段 ---
            source: str | None = None
            entries: dict[str, str] = {}
            missing_behavior = "keep_placeholder"
            fallback_value = ""
            pattern: str | None = None
            group: int = 1

            if mode == "dict":
                source = self._require_text(item.get("source"), f"custom_variables[{index}].source")
                entries = self._parse_variable_values_as_dict(item.get("values"), f"custom_variables[{index}].values")
                missing_behavior = str(item.get("missing_behavior", "keep_placeholder")).strip()
                if missing_behavior not in VALID_MISSING_BEHAVIORS:
                    raise ValueError(f"custom_variables[{index}].missing_behavior 只能是 "                        f"{', '.join(sorted(VALID_MISSING_BEHAVIORS))}")
                fallback_value = "" if item.get("fallback_value") is None else str(item["fallback_value"]).strip()
                values: list[str] = []
            elif mode == "extract":
                source = self._require_text(item.get("source"), f"custom_variables[{index}].source")
                pattern_value = self._require_text(item.get("pattern"), f"custom_variables[{index}].pattern")
                try:
                    re.compile(pattern_value)
                except re.error as exc:
                    raise ValueError(f"custom_variables[{index}].pattern 不是合法的正则: {exc}") from exc
                pattern = pattern_value
                group_raw = item.get("group", 1)
                try:
                    group_int = int(str(group_raw).strip())
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"custom_variables[{index}].group 必须是整数: {group_raw}") from exc
                if group_int < 0:
                    raise ValueError(f"custom_variables[{index}].group 必须 >= 0: {group_int}")
                group = group_int
                missing_behavior = str(item.get("missing_behavior", "keep_placeholder")).strip()
                if missing_behavior not in VALID_MISSING_BEHAVIORS:
                    raise ValueError(f"custom_variables[{index}].missing_behavior 只能是 "                        f"{', '.join(sorted(VALID_MISSING_BEHAVIORS))}")
                fallback_value = "" if item.get("fallback_value") is None else str(item["fallback_value"]).strip()
                values: list[str] = []
            else:
                values = self._parse_variable_values(item.get("values"), f"custom_variables[{index}].values")

            # --- daily_llm 校验字段 ---
            min_length = 0
            required_markers: tuple[str, ...] = ()
            if mode == "daily_llm":
                raw_min = item.get("min_length", 0)
                try:
                    min_length = int(str(raw_min).strip())
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"custom_variables[{index}].min_length 必须是非负整数: {raw_min}"
                    ) from exc
                if min_length < 0:
                    raise ValueError(
                        f"custom_variables[{index}].min_length 必须是非负整数: {min_length}"
                    )
                required_markers = self._parse_required_markers(
                    item.get("required_markers"), f"custom_variables[{index}].required_markers"
                )

            # --- use_raw 标志位 ---
            use_raw_condition_source = bool(item.get("use_raw_condition_source", False))
            use_raw_condition_value = bool(item.get("use_raw_condition_value", False))
            if use_raw_condition_source and not condition_source:
                raise ValueError(
                    f"custom_variables[{index}].use_raw_condition_source 为 true 时，condition_source 不能为空"
                )
            if use_raw_condition_value and not condition_value:
                raise ValueError(
                    f"custom_variables[{index}].use_raw_condition_value 为 true 时，condition_value 不能为空"
                )

            definitions[key] = CustomVariableDefinition(
                key=key,
                mode=mode,
                values=values,
                probability=probability,
                index=index,
                condition_type=condition_type,
                condition_source=condition_source,
                condition_value=condition_value,
                values_else=values_else,
                source=source,
                entries=entries,
                missing_behavior=missing_behavior,
                fallback_value=fallback_value,
                use_raw_condition_source=use_raw_condition_source,
                use_raw_condition_value=use_raw_condition_value,
                pattern=pattern,
                group=group,
                min_length=min_length,
                required_markers=required_markers,
            )

        return definitions

    @staticmethod
    def _parse_required_markers(value: Any, field_name: str) -> tuple[str, ...]:
        """
        解析 daily_llm 的 required_markers，支持 JSON 数组字符串、原生列表或单个字符串

        :param value: Any，原始 required_markers 配置
        :param field_name: str，当前字段名，用于拼接报错信息
        :return: tuple[str, ...]，去重后的非空 marker 元组（保持原序）
        """
        if value is None:
            return ()
        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return ()
            if text.startswith("[") or text.startswith("{"):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{field_name} 不是合法的 JSON 数组字符串: {exc}") from exc
                if not isinstance(parsed, list):
                    raise ValueError(f"{field_name} 必须是 JSON 数组")
                items = parsed
            else:
                items = [text]
        else:
            raise ValueError(f"{field_name} 必须是字符串或字符串数组")

        result: list[str] = []
        seen: set[str] = set()
        for raw in items:
            marker = "" if raw is None else str(raw).strip()
            if not marker or marker in seen:
                continue
            seen.add(marker)
            result.append(marker)
        return tuple(result)

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
    def _parse_variable_values_as_dict(value: Any, field_name: str) -> dict[str, str]:
        """
        将变量配置值解析为字典映射表，用于 dict 模式

        :param value: Any，原始变量候选值配置（JSON 对象字符串）
        :param field_name: str，当前字段名，用于拼接报错信息
        :return: dict[str, str]，键到值的映射
        """
        if value is None:
            return {}
        if isinstance(value, dict):
            return {str(k).strip(): str(v).strip() for k, v in value.items() if str(k).strip()}

        text = str(value).strip()
        if not text:
            return {}

        try:
            parsed_value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} 不是合法的 JSON 对象字符串: {exc}") from exc

        if not isinstance(parsed_value, dict):
            raise ValueError(f"{field_name} 必须是 JSON 对象")
        return {str(k).strip(): str(v).strip() for k, v in parsed_value.items() if str(k).strip()}

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

    @staticmethod
    def _parse_optional_text(value: Any) -> str | None:
        """
        解析可选文本字段，空值返回 None

        :param value: Any，原始字段值
        :return: str | None，去除首尾空白后的文本或 None
        """
        if value is None:
            return None
        text = str(value).strip()
        return text if text else None
