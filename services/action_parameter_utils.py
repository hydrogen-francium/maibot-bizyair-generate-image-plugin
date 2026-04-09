from typing import Any


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

    :param value: Any，原始必填配置值，支持“必填”或“选填”
    :param field_name: str，当前参数字段名，用于拼接报错信息
    :return: bool，参数是否应被视为必填
    """
    text = "" if value is None else str(value).strip()
    if text == "必填":
        return True
    if text in {"", "选填"}:
        return False
    raise ValueError(f"{field_name} 只能是“选填”或“必填”")


def build_action_parameters(raw_parameters: Any) -> tuple[dict[str, str], set[str]]:
    """
    根据配置构造动作参数描述映射和必填参数集合

    :param raw_parameters: Any，原始 action_parameters 配置列表
    :return: tuple[dict[str, str], set[str]]，参数名到描述的映射及必填参数名集合
    """
    if not isinstance(raw_parameters, list) or not raw_parameters:
        raise ValueError("action_parameters 必须是非空列表")

    action_parameters: dict[str, str] = {}
    required_parameters: set[str] = set()
    for index, item in enumerate(raw_parameters):
        if not isinstance(item, dict):
            raise ValueError(f"action_parameters[{index}] 必须是对象")

        name = normalize_parameter(item.get("name"), f"action_parameters[{index}].name")
        description = normalize_parameter(item.get("description"), f"action_parameters[{index}].description")
        if name in action_parameters:
            raise ValueError(f"action_parameters[{index}].name 重复: {name}")

        action_parameters[name] = description
        if is_parameter_required(item.get("required", "选填"), f"action_parameters[{index}].required"):
            required_parameters.add(name)

    return action_parameters, required_parameters
