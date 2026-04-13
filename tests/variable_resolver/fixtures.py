from unittest.mock import MagicMock

from services.builtin_variable_provider import BuiltinVariableProvider
from services.custom_variable_registry import CustomVariableDefinition
from services.variable_dependency_resolver import VariableDependencyResolver


BUILTIN_NAMES = frozenset({"random_seed", "current_datetime"})


def make_definition(
    key: str,
    mode: str = "literal",
    values: list[str] | None = None,
    probability: float = 1.0,
    index: int = 0,
    condition_type: str | None = None,
    condition_source: str | None = None,
    condition_value: str | None = None,
    values_else: list[str] | None = None,
    source: str | None = None,
    entries: dict[str, str] | None = None,
    missing_behavior: str = "keep_placeholder",
    fallback_value: str = "",
    use_raw_condition_source: bool = False,
    use_raw_condition_value: bool = False,
) -> CustomVariableDefinition:
    """
    构造测试用自定义变量定义对象

    :param key: str，变量键名
    :param mode: str，变量模式
    :param values: list[str] | None，主分支候选模板列表
    :param probability: float，变量命中概率
    :param index: int，变量索引
    :param condition_type: str | None，条件判断类型
    :param condition_source: str | None，条件判断的数据来源
    :param condition_value: str | None，条件判断的比较值模板
    :param values_else: list[str] | None，条件未命中时的候选模板列表
    :param source: str | None，dict 模式的数据来源
    :param entries: dict[str, str] | None，dict 模式键值映射
    :param missing_behavior: str，dict 模式缺失行为
    :param fallback_value: str，dict 模式兜底模板
    :param use_raw_condition_source: bool，条件来源是否使用原始值
    :param use_raw_condition_value: bool，条件参数是否使用字面文本
    :return: CustomVariableDefinition，构造好的变量定义对象
    """
    return CustomVariableDefinition(
        key=key,
        mode=mode,
        values=values or [],
        probability=probability,
        index=index,
        condition_type=condition_type,
        condition_source=condition_source,
        condition_value=condition_value,
        values_else=values_else or [],
        source=source,
        entries=entries or {},
        missing_behavior=missing_behavior,
        fallback_value=fallback_value,
        use_raw_condition_source=use_raw_condition_source,
        use_raw_condition_value=use_raw_condition_value,
    )


def make_resolver(
    action_inputs: dict,
    definitions: dict[str, CustomVariableDefinition],
    action_parameter_names: set[str] | None = None,
    required_keys: set[str] | None = None,
) -> VariableDependencyResolver:
    if action_parameter_names is None:
        action_parameter_names = set(action_inputs.keys())
    if required_keys is None:
        required_keys = set(definitions.keys())
    return VariableDependencyResolver(
        action_inputs=action_inputs,
        custom_variable_definitions=definitions,
        action_parameter_names=action_parameter_names,
        builtin_names=BUILTIN_NAMES,
        required_custom_variable_keys=required_keys,
    )


def mock_builtin_provider() -> BuiltinVariableProvider:
    provider = MagicMock(spec=BuiltinVariableProvider)
    provider.build_placeholder_values.return_value = {}
    provider.variable_names = frozenset()
    return provider
