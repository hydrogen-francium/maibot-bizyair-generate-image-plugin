from dataclasses import dataclass
from typing import Any

VALID_MISSING_BEHAVIORS = {"keep_placeholder", "raise_error", "use_default"}


@dataclass(frozen=True)
class ActionParameterDefinition:
    name: str
    description: str
    required: bool
    missing_behavior: str = "keep_placeholder"
    default_value: str = ""


def normalize_parameter(value: Any, field_name: str) -> str:
    """
    标准化参数名称并校验结果非空

    :param value: Any，待标准化的原始参数名值
    :param field_name: str，当前参数字段名，用于拼接报错信息
    :return: str，去除首尾空白后的合法参数名称
    """
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def is_parameter_required(value: Any, field_name: str) -> bool:
    """
    解析参数是否为必填项的配置值

    :param value: Any，原始必填配置值，支持"必填"或"选填"
    :param field_name: str，当前参数字段名，用于拼接报错信息
    :return: bool，参数是否应被视为必填
    """
    text = "" if value is None else str(value).strip()
    if text == "必填":
        return True
    if text in {"", "选填"}:
        return False
    raise ValueError(f'{field_name} 只能是"选填"或"必填"')


def build_action_parameters(raw_parameters: Any) -> dict[str, ActionParameterDefinition]:
    """
    根据配置构造动作参数定义映射

    :param raw_parameters: Any，原始 action_parameters 配置列表
    :return: dict[str, ActionParameterDefinition]，参数名到参数定义的映射
    """
    if not isinstance(raw_parameters, list) or not raw_parameters:
        raise ValueError("action_parameters 必须是非空列表")

    definitions: dict[str, ActionParameterDefinition] = {}
    for index, item in enumerate(raw_parameters):
        if not isinstance(item, dict):
            raise ValueError(f"action_parameters[{index}] 必须是对象")

        name = normalize_parameter(item.get("name"), f"action_parameters[{index}].name")
        description = normalize_parameter(item.get("description"), f"action_parameters[{index}].description")
        if name in definitions:
            raise ValueError(f"action_parameters[{index}].name 重复: {name}")

        required = is_parameter_required(item.get("required", "选填"), f"action_parameters[{index}].required")

        missing_behavior = str(item.get("missing_behavior", "keep_placeholder")).strip()
        if missing_behavior not in VALID_MISSING_BEHAVIORS:
            raise ValueError(f"action_parameters[{index}].missing_behavior 只能是 {', '.join(sorted(VALID_MISSING_BEHAVIORS))}")

        default_value = "" if item.get("default_value") is None else str(item["default_value"]).strip()

        definitions[name] = ActionParameterDefinition(
            name=name,
            description=description,
            required=required,
            missing_behavior=missing_behavior,
            default_value=default_value,
        )

    return definitions
