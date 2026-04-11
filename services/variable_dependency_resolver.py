from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from src.common.logger import get_logger
from .builtin_variable_provider import BuiltinVariableProvider
from .custom_variable_registry import CustomVariableDefinition
from .log_utils import short_repr
from .template_placeholder_utils import TemplatePlaceholderUtils

logger = get_logger("bizyair_generate_image_plugin")


@dataclass(frozen=True)
class _ResolvableNode:
    """依赖图中的可解析节点"""

    name: str
    node_type: Literal["action_input", "custom_variable"]
    dependencies: frozenset[str]


class VariableDependencyResolver:
    """
    统一依赖图解析器。

    将 action_inputs 和 custom_variables 视为同一张有向图中的节点，
    通过 Kahn 算法进行拓扑排序、循环引用检测，
    并按拓扑序逐个解析节点的值。
    """

    def __init__(
            self,
            action_inputs: dict[str, Any],
            custom_variable_definitions: dict[str, CustomVariableDefinition],
            action_parameter_names: set[str],
            builtin_names: frozenset[str],
            required_custom_variable_keys: set[str],
    ) -> None:
        """
        初始化依赖解析器并构建依赖图

        :param action_inputs: dict[str, Any]，LLM 决策输出的原始参数值
        :param custom_variable_definitions: dict[str, CustomVariableDefinition]，已解析的自定义变量定义
        :param action_parameter_names: set[str]，action 支持的参数名集合
        :param builtin_names: frozenset[str]，内置变量名称集合
        :param required_custom_variable_keys: set[str]，经传递闭包计算后真正需要解析的自定义变量键名集合
        """
        self._action_inputs = dict(action_inputs)
        self._custom_variable_definitions = custom_variable_definitions
        self._action_parameter_names = set(action_parameter_names)
        self._builtin_names = frozenset(builtin_names)
        self._required_custom_variable_keys = set(required_custom_variable_keys)

        self._all_custom_variable_names = frozenset(custom_variable_definitions.keys())
        self._nodes: dict[str, _ResolvableNode] = {}
        self._build_graph()

    def _build_graph(self) -> None:
        """
        构建依赖图，为每个参与解析的 action_input 和 custom_variable 创建节点

        action_input 节点：扫描其原始值中引用的自定义变量名作为依赖
        custom_variable 节点：扫描其所有 values 模板中引用的其他 action_input 和自定义变量名作为依赖
        """
        known_node_names = self._action_parameter_names | self._required_custom_variable_keys

        for name in self._action_parameter_names:
            if name not in self._action_inputs:
                continue
            raw_value = self._action_inputs[name]
            deps = self._scan_dependencies(raw_value, known_node_names)
            if deps:
                self._nodes[name] = _ResolvableNode(
                    name=name,
                    node_type="action_input",
                    dependencies=frozenset(deps),
                )

        for key in self._required_custom_variable_keys:
            definition = self._custom_variable_definitions.get(key)
            if definition is None:
                continue
            deps: set[str] = set()
            for value_template in definition.values:
                deps.update(self._scan_dependencies(value_template, known_node_names))
            self._nodes[key] = _ResolvableNode(
                name=key,
                node_type="custom_variable",
                dependencies=frozenset(deps),
            )
        if self._nodes:
            logger.info(
                f"[依赖解析] 构建依赖图完成: nodes={list(self._nodes.keys())}, "
                f"dependencies={{name: sorted(node.dependencies) for name, node in self._nodes.items()}}"
            )

    def _scan_dependencies(self, template_value: Any, known_node_names: frozenset[str] | set[str]) -> set[str]:
        """
        扫描模板值中引用的非内置占位符名，并过滤出已知节点名

        :param template_value: Any，待扫描的模板值
        :param known_node_names: frozenset[str] | set[str]，已知的节点名集合
        :return: set[str]，模板中引用到的已知节点名集合
        """
        all_non_builtin = TemplatePlaceholderUtils.collect_non_builtin_placeholder_names(
            template_value, self._builtin_names
        )
        return {name for name in all_non_builtin if name in known_node_names}

    def topological_sort(self) -> list[str]:
        """
        对依赖图执行 Kahn 算法拓扑排序

        :return: list[str]，按解析顺序排列的节点名列表
        :raises ValueError: 当检测到循环引用时，附带环路径描述
        """
        if not self._nodes:
            return []

        in_degree: dict[str, int] = {name: 0 for name in self._nodes}
        for node in self._nodes.values():
            for dep in node.dependencies:
                if dep in in_degree:
                    in_degree[node.name] = in_degree.get(node.name, 0)

        adjacency: dict[str, list[str]] = {name: [] for name in self._nodes}
        for node in self._nodes.values():
            for dep in node.dependencies:
                if dep in adjacency:
                    adjacency[dep].append(node.name)
                    in_degree[node.name] += 1

        queue: deque[str] = deque()
        for name, degree in in_degree.items():
            if degree == 0:
                queue.append(name)

        sorted_order: list[str] = []
        while queue:
            current = queue.popleft()
            sorted_order.append(current)
            for dependent in adjacency.get(current, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(sorted_order) != len(self._nodes):
            cycle_nodes = {name for name, degree in in_degree.items() if degree > 0}
            cycle_path = self._find_cycle_path(cycle_nodes)
            raise ValueError(f"检测到循环引用: {' → '.join(cycle_path)}")

        logger.info(f"[依赖解析] 拓扑排序结果: {sorted_order}")

        return sorted_order

    def _find_cycle_path(self, cycle_nodes: set[str]) -> list[str]:
        """
        在残留的环节点中找出一条具体的环路径用于报错

        :param cycle_nodes: set[str]，拓扑排序后仍有入度的节点集合
        :return: list[str]，环路径中的节点名列表，首尾相同
        """
        if not cycle_nodes:
            return []

        start = next(iter(cycle_nodes))
        visited: dict[str, int] = {}
        path: list[str] = []

        def dfs(node: str) -> list[str] | None:
            if node in visited:
                cycle_start_index = visited[node]
                return path[cycle_start_index:] + [node]
            if node not in cycle_nodes:
                return None
            visited[node] = len(path)
            path.append(node)
            node_obj = self._nodes.get(node)
            if node_obj:
                for dep in node_obj.dependencies:
                    if dep in cycle_nodes:
                        result = dfs(dep)
                        if result is not None:
                            return result
            path.pop()
            del visited[node]
            return None

        return dfs(start) or list(cycle_nodes)

    async def resolve_all(
            self,
            builtin_placeholder_values: dict[str, Any],
            llm_value_factory: Callable[[str], Awaitable[str]],
            builtin_variable_provider: BuiltinVariableProvider,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        按拓扑序解析所有节点，返回已解析的 action_inputs 和 custom_variables

        :param builtin_placeholder_values: dict[str, Any]，已构造的内置变量占位符值映射
        :param llm_value_factory: Callable[[str], Awaitable[str]]，用于 llm 模式变量生成的异步函数
        :param builtin_variable_provider: BuiltinVariableProvider，内置变量提供器
        :return: tuple[dict[str, Any], dict[str, Any]]，(已解析的 action_inputs, 已解析的 custom_variables)
        """
        sorted_order = self.topological_sort()

        resolved_action_inputs = dict(self._action_inputs)
        resolved_custom_variables: dict[str, Any] = {}
        resolved_context: dict[str, Any] = {}

        for name, value in self._action_inputs.items():
            if name not in self._nodes:
                resolved_context[name] = value

        for name in sorted_order:
            node = self._nodes[name]
            logger.debug(
                f"[依赖解析] 开始解析节点: name={name!r}, node_type={node.node_type}, "
                f"dependencies={sorted(node.dependencies)}, resolved_context_keys={sorted(resolved_context.keys())}"
            )

            if node.node_type == "action_input":
                raw_value = self._action_inputs.get(name, "")
                logger.debug(
                    f"[依赖解析] action_input 解析前: name={name!r}, raw_value={short_repr(raw_value)}"
                )
                resolved_value = self._substitute_placeholders_in_value(
                    raw_value, resolved_context, builtin_placeholder_values
                )
                resolved_action_inputs[name] = resolved_value
                resolved_context[name] = resolved_value
                logger.info(f"[依赖解析] action_input 解析完成: name={name!r}, value={short_repr(resolved_value)}")

            elif node.node_type == "custom_variable":
                definition = self._custom_variable_definitions[name]
                logger.debug(
                    f"[依赖解析] custom_variable 解析前: name={name!r}, mode={definition.mode!r}, "
                    f"candidates={short_repr(definition.values)}"
                )
                resolved_value = await self._resolve_custom_variable(
                    definition=definition,
                    resolved_context=resolved_context,
                    builtin_placeholder_values=builtin_placeholder_values,
                    builtin_variable_provider=builtin_variable_provider,
                    llm_value_factory=llm_value_factory,
                )
                resolved_custom_variables[name] = resolved_value
                resolved_context[name] = resolved_value
                logger.info(f"[依赖解析] custom_variable 解析完成: name={name!r}, value={short_repr(resolved_value)}")

        return resolved_action_inputs, resolved_custom_variables

    def _substitute_placeholders_in_value(
            self,
            value: Any,
            resolved_context: dict[str, Any],
            builtin_placeholder_values: dict[str, Any],
    ) -> Any:
        """
        在值中替换所有已解析的占位符

        :param value: Any，待替换的原始值
        :param resolved_context: dict[str, Any]，已解析的变量名到值的映射
        :param builtin_placeholder_values: dict[str, Any]，内置变量占位符映射（键带花括号）
        :return: Any，替换完成后的值
        """
        if not isinstance(value, str):
            return value

        placeholder_values = dict(builtin_placeholder_values)
        for key, val in resolved_context.items():
            placeholder_values[f"{{{key}}}"] = val

        stripped = value.strip()
        if stripped in placeholder_values:
            return placeholder_values[stripped]

        resolved = value
        for placeholder, val in placeholder_values.items():
            resolved = resolved.replace(placeholder, str(val))
        return resolved

    async def _resolve_custom_variable(
            self,
            definition: CustomVariableDefinition,
            resolved_context: dict[str, Any],
            builtin_placeholder_values: dict[str, Any],
            builtin_variable_provider: BuiltinVariableProvider,
            llm_value_factory: Callable[[str], Awaitable[str]],
    ) -> str:
        """
        解析单个自定义变量定义并返回最终文本值

        :param definition: CustomVariableDefinition，待解析的自定义变量定义对象
        :param resolved_context: dict[str, Any]，已解析的所有变量值映射
        :param builtin_placeholder_values: dict[str, Any]，内置变量占位符映射
        :param builtin_variable_provider: BuiltinVariableProvider，内置变量提供器
        :param llm_value_factory: Callable[[str], Awaitable[str]]，LLM 生成函数
        :return: str，自定义变量最终生成的文本结果
        """
        if random.random() > definition.probability:
            return ""

        if not definition.values:
            return ""

        required_builtin_names = TemplatePlaceholderUtils.collect_builtin_placeholder_names(
            definition.values,
            self._builtin_names,
        )
        extra_builtin_values = builtin_variable_provider.build_placeholder_values(required_builtin_names)

        placeholder_values = dict(extra_builtin_values)
        placeholder_values.update(builtin_placeholder_values)
        for key, val in resolved_context.items():
            placeholder_values[f"{{{key}}}"] = val

        selected_template = random.choice(definition.values)
        logger.debug(
            f"[依赖解析] 选中自定义变量模板: name={definition.key!r}, mode={definition.mode!r}, "
            f"selected_template={short_repr(selected_template)}, placeholder_keys={sorted(placeholder_values.keys())}"
        )
        selected_value = self._resolve_template_recursive(selected_template, placeholder_values)
        selected_value = str(selected_value).strip()

        if definition.mode == "literal":
            return selected_value

        if not selected_value:
            return ""
        return await llm_value_factory(selected_value)

    def _resolve_template_recursive(self, value_template: Any, placeholder_values: dict[str, Any]) -> Any:
        """
        递归解析模板值中的占位符

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
            return [self._resolve_template_recursive(item, placeholder_values) for item in value_template]

        if isinstance(value_template, dict):
            return {
                key: self._resolve_template_recursive(val, placeholder_values)
                for key, val in value_template.items()
            }

        return value_template

    @classmethod
    def compute_required_variable_keys(
            cls,
            direct_keys: set[str],
            action_inputs: dict[str, Any],
            custom_variable_definitions: dict[str, CustomVariableDefinition],
            action_parameter_names: set[str],
            builtin_names: frozenset[str],
    ) -> set[str]:
        """
        计算自定义变量的传递闭包，得到真正需要解析的完整变量集合

        从直接引用的变量出发，递归扫描每个变量的模板引用的其他自定义变量，
        同时扫描 action_inputs 中引用的自定义变量，直到集合不再增长。

        :param direct_keys: set[str]，从 parameter_bindings 中直接引用的自定义变量名
        :param action_inputs: dict[str, Any]，LLM 决策输出的原始参数值
        :param custom_variable_definitions: dict[str, CustomVariableDefinition]，已解析的自定义变量定义
        :param action_parameter_names: set[str]，action 支持的参数名集合
        :param builtin_names: frozenset[str]，内置变量名称集合
        :return: set[str]，经传递闭包计算后的完整自定义变量键名集合
        """
        all_custom_var_names = set(custom_variable_definitions.keys())

        keys_from_action_inputs: set[str] = set()
        for name in action_parameter_names:
            if name not in action_inputs:
                continue
            raw_value = action_inputs[name]
            all_placeholders = TemplatePlaceholderUtils.collect_non_builtin_placeholder_names(
                raw_value, builtin_names
            )
            keys_from_action_inputs.update(all_placeholders & all_custom_var_names)

        required = set(direct_keys) | keys_from_action_inputs
        frontier = set(required)

        while frontier:
            next_frontier: set[str] = set()
            for key in frontier:
                definition = custom_variable_definitions.get(key)
                if definition is None:
                    continue
                for value_template in definition.values:
                    referenced = TemplatePlaceholderUtils.collect_non_builtin_placeholder_names(
                        value_template, builtin_names
                    )
                    new_custom_refs = (referenced & all_custom_var_names) - required
                    next_frontier.update(new_custom_refs)
            required.update(next_frontier)
            frontier = next_frontier

        return required
