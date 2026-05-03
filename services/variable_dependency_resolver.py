from __future__ import annotations

import random
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Mapping, AbstractSet

from src.common.logger import get_logger
from .builtin_variable_provider import BuiltinVariableProvider
from .custom_variable_registry import CustomVariableDefinition
from .log_utils import short_repr
from .template_placeholder_utils import TemplatePlaceholderUtils

logger = get_logger("bizyair_generate_image_plugin")


@dataclass(frozen=True)
class _ResolvableNode:
    """依赖图中的可解析节点"""

    """
    描述依赖图中的单个节点及其依赖类型

    :param name: str，节点名称，可能是 action_input 名称或自定义变量名称
    :param node_type: Literal["action_input", "custom_variable"]，节点类型
    :param hard_dependencies: frozenset[str]，节点在求值前必须先完成解析的硬依赖集合
    :param soft_dependencies: frozenset[str]，节点仅在模板真正被消费时才触发解析的软依赖集合
    """

    name: str
    node_type: Literal["action_input", "custom_variable"]
    hard_dependencies: frozenset[str]
    soft_dependencies: frozenset[str]


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

    @staticmethod
    def _merge_dependency_maps(
            *dependency_maps: Mapping[str, AbstractSet[str]],
    ) -> dict[str, frozenset[str]]:
        """
        合并多个依赖来源映射并去重

        :param dependency_maps: dict[str, set[str]]，多个依赖来源到依赖名集合的映射
        :return: dict[str, frozenset[str]]，合并后的不可变依赖来源映射
        """
        merged: dict[str, set[str]] = {}
        for dependency_map in dependency_maps:
            for source_name, names in dependency_map.items():
                if not names:
                    continue
                merged.setdefault(source_name, set()).update(names)
        return {source_name: frozenset(sorted(names)) for source_name, names in merged.items() if names}

    def _collect_dynamic_branch_dependency_reasons(
            self,
            definition: CustomVariableDefinition,
            known_node_names: frozenset[str] | set[str],
    ) -> dict[str, frozenset[str]]:
        """
        提取动态条件变量分支模板中的软依赖来源映射

        :param definition: CustomVariableDefinition，自定义变量定义对象
        :param known_node_names: frozenset[str] | set[str]，当前图中允许纳入的节点名集合
        :return: dict[str, frozenset[str]]，依赖来源到依赖名集合的映射
        """
        if definition.condition_type in {None, "fixed_true", "fixed_false"}:
            return {}

        values_refs: set[str] = set()
        for value_template in definition.values:
            values_refs.update(self._scan_dependencies(value_template, known_node_names))

        values_else_refs: set[str] = set()
        for value_template in definition.values_else:
            values_else_refs.update(self._scan_dependencies(value_template, known_node_names))

        return self._merge_dependency_maps(
            {"values": values_refs},
            {"values_else": values_else_refs},
        )

    def _collect_node_dependencies(
            self,
            definition: CustomVariableDefinition,
            known_node_names: frozenset[str] | set[str],
    ) -> tuple[frozenset[str], frozenset[str], dict[str, frozenset[str]]]:
        """
        提取单个自定义变量节点的硬依赖与软依赖

        :param definition: CustomVariableDefinition，自定义变量定义对象
        :param known_node_names: frozenset[str] | set[str]，当前图中允许纳入的节点名集合
        :return: tuple[frozenset[str], frozenset[str], dict[str, frozenset[str]]]，
            (硬依赖集合, 软依赖集合, 软依赖来源映射)
        """
        hard_dependencies: set[str] = set()
        soft_dependency_reasons: dict[str, frozenset[str]] = {}

        if definition.mode in ("dict", "extract"):
            if definition.source and definition.source in known_node_names:
                hard_dependencies.add(definition.source)
            if definition.fallback_value:
                soft_dependency_reasons = self._merge_dependency_maps(
                    {"fallback_value": self._scan_dependencies(definition.fallback_value, known_node_names)}
                )
            soft_dependencies = set().union(*soft_dependency_reasons.values()) if soft_dependency_reasons else set()
            return frozenset(hard_dependencies), frozenset(soft_dependencies), soft_dependency_reasons

        if definition.condition_type in {None, "fixed_true", "fixed_false"}:
            for value_template in [*definition.values, *definition.values_else]:
                hard_dependencies.update(self._scan_dependencies(value_template, known_node_names))
        else:
            soft_dependency_reasons = self._merge_dependency_maps(
                self._collect_dynamic_branch_dependency_reasons(definition, known_node_names)
            )

        # use_raw_condition_source=True 时不将 condition_source 加入硬依赖，
        # 条件判断阶段会直接从 action_inputs 或已求值上下文中取原始值
        if not definition.use_raw_condition_source:
            if definition.condition_source and definition.condition_source in known_node_names:
                hard_dependencies.add(definition.condition_source)

        # use_raw_condition_value=True 时跳过 condition_value 的占位符扫描，
        # 条件判断阶段会直接使用字面文本，不触发依赖解析
        if not definition.use_raw_condition_value:
            if definition.condition_value:
                soft_dependency_reasons = self._merge_dependency_maps(
                    soft_dependency_reasons,
                    {"condition_value": self._scan_dependencies(definition.condition_value, known_node_names)},
                )

        soft_dependencies = set().union(*soft_dependency_reasons.values()) if soft_dependency_reasons else set()
        return frozenset(hard_dependencies), frozenset(soft_dependencies), soft_dependency_reasons

    def _build_graph(self) -> None:
        """
        构建依赖图，为每个参与解析的 action_input 和 custom_variable 创建节点

        action_input 节点：扫描其原始值中引用的自定义变量名作为依赖
        custom_variable 节点：扫描其所有 values 模板中引用的其他 action_input 和自定义变量名作为依赖
        """
        known_node_names = self._action_parameter_names | self._required_custom_variable_keys
        soft_dependency_reason_map: dict[str, dict[str, frozenset[str]]] = {}

        for name in self._action_parameter_names:
            if name not in self._action_inputs:
                continue
            raw_value = self._action_inputs[name]
            hard_dependencies = self._scan_dependencies(raw_value, known_node_names)
            if hard_dependencies:
                self._nodes[name] = _ResolvableNode(
                    name=name,
                    node_type="action_input",
                    hard_dependencies=frozenset(hard_dependencies),
                    soft_dependencies=frozenset(),
                )

        for key in self._required_custom_variable_keys:
            definition = self._custom_variable_definitions.get(key)
            if definition is None:
                continue

            hard_dependencies, soft_dependencies, soft_dependency_reasons = self._collect_node_dependencies(
                definition, known_node_names
            )
            if soft_dependency_reasons:
                soft_dependency_reason_map[key] = soft_dependency_reasons

            self._nodes[key] = _ResolvableNode(
                name=key,
                node_type="custom_variable",
                hard_dependencies=hard_dependencies,
                soft_dependencies=soft_dependencies,
            )

        if self._nodes:
            hard_dependencies = {
                name: sorted(node.hard_dependencies) for name, node in self._nodes.items()
            }
            soft_dependencies = {
                name: sorted(node.soft_dependencies) for name, node in self._nodes.items()
            }
            logger.info(
                f"[依赖解析] 构建依赖图完成: nodes={list(self._nodes.keys())}, "
                f"hard_dependencies={hard_dependencies}, "
                f"soft_dependencies={soft_dependencies}, "
                f"soft_dependency_reasons={soft_dependency_reason_map}"
            )

    def _iter_all_dependencies(self, node: _ResolvableNode) -> frozenset[str]:
        """
        获取单个节点用于排序与循环检测的全部依赖

        :param node: _ResolvableNode，待读取依赖的节点对象
        :return: frozenset[str]，硬依赖与软依赖的并集
        """
        return frozenset(node.hard_dependencies | node.soft_dependencies)

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
            for dep in self._iter_all_dependencies(node):
                if dep in in_degree:
                    in_degree[node.name] = in_degree.get(node.name, 0)

        adjacency: dict[str, list[str]] = {name: [] for name in self._nodes}
        for node in self._nodes.values():
            for dep in self._iter_all_dependencies(node):
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
                for dep in self._iter_all_dependencies(node_obj):
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
        按需解析所有节点，返回已解析的 action_inputs 和 custom_variables

        采用按需递归求值策略：只有被实际消费的节点才会触发求值。
        拓扑排序仅用于循环引用检测，实际求值顺序由依赖关系驱动。

        :param builtin_placeholder_values: dict[str, Any]，已构造的内置变量占位符值映射
        :param llm_value_factory: Callable[[str], Awaitable[str]]，用于 llm 模式变量生成的异步函数
        :param builtin_variable_provider: BuiltinVariableProvider，内置变量提供器
        :return: tuple[dict[str, Any], dict[str, Any]]，(已解析的 action_inputs, 已解析的 custom_variables)
        """
        # 拓扑排序仅用于循环引用检测
        self.topological_sort()

        # 初始化求值上下文（实例状态，供 _ensure_resolved 递归使用）
        self._resolved_action_inputs = dict(self._action_inputs)
        self._resolved_custom_variables: dict[str, Any] = {}
        self._resolved_context: dict[str, Any] = {}
        self._resolving_set: set[str] = set()  # 防止递归求值时重入
        self._builtin_placeholder_values = builtin_placeholder_values
        self._llm_value_factory = llm_value_factory
        self._builtin_variable_provider = builtin_variable_provider

        # 无依赖的 action_inputs 直接写入上下文
        for name, value in self._action_inputs.items():
            if name not in self._nodes:
                self._resolved_context[name] = value

        # 按需求值：这里只触发真正会被外部消费的根节点
        # 软依赖节点虽然已经入图并参与环检测，但不应在这里被整体预解析
        referenced_custom_variable_names = {
            dep
            for node in self._nodes.values()
            for dep in self._iter_all_dependencies(node)
            if dep in self._required_custom_variable_keys
        }
        root_node_names = [
            name for name in self._action_inputs if name in self._nodes
        ] + [
            name
            for name in self._required_custom_variable_keys
            if name in self._nodes and name not in referenced_custom_variable_names
        ]
        for name in root_node_names:
            await self._ensure_resolved(name)

        result = (self._resolved_action_inputs, self._resolved_custom_variables)

        # 清理实例状态
        del self._resolved_action_inputs
        del self._resolved_custom_variables
        del self._resolved_context
        del self._resolving_set
        del self._builtin_placeholder_values
        del self._llm_value_factory
        del self._builtin_variable_provider

        return result

    async def _ensure_resolved(self, name: str) -> None:
        """
        按需确保指定节点已求值，如果尚未求值则递归解析其依赖后求值

        :param name: str，待确保已求值的节点名
        """
        if name in self._resolved_context:
            return

        node = self._nodes.get(name)
        if node is None:
            return

        if name in self._resolving_set:
            return

        self._resolving_set.add(name)
        try:
            # 递归确保所有硬依赖已求值
            # 软依赖不在这里提前展开，避免未命中的条件分支被错误求值
            for dep in node.hard_dependencies:
                await self._ensure_resolved(dep)

            logger.debug(
                f"[依赖解析] 开始解析节点: name={name!r}, node_type={node.node_type}, "
                f"hard_dependencies={sorted(node.hard_dependencies)}, "
                f"soft_dependencies={sorted(node.soft_dependencies)}, "
                f"resolved_context_keys={sorted(self._resolved_context.keys())}"
            )

            if node.node_type == "action_input":
                raw_value = self._action_inputs.get(name, "")
                logger.debug(
                    f"[依赖解析] action_input 解析前: name={name!r}, raw_value={short_repr(raw_value)}"
                )
                resolved_value = self._substitute_placeholders_in_value(
                    raw_value, self._resolved_context, self._builtin_placeholder_values
                )
                self._resolved_action_inputs[name] = resolved_value
                self._resolved_context[name] = resolved_value
                logger.info(f"[依赖解析] action_input 解析完成: name={name!r}, value={short_repr(resolved_value)}")
                return

            definition = self._custom_variable_definitions[name]
            logger.debug(
                f"[依赖解析] custom_variable 解析前: name={name!r}, mode={definition.mode!r}, "
                f"candidates={short_repr(definition.values)}"
            )
            custom_variable_start_time = time.perf_counter()
            resolved_value = await self._resolve_custom_variable(
                definition=definition,
                resolved_context=self._resolved_context,
                builtin_placeholder_values=self._builtin_placeholder_values,
                builtin_variable_provider=self._builtin_variable_provider,
                llm_value_factory=self._llm_value_factory,
            )
            custom_variable_elapsed_seconds = time.perf_counter() - custom_variable_start_time
            self._resolved_custom_variables[name] = resolved_value
            self._resolved_context[name] = resolved_value
            logger.info(
                f"[依赖解析] custom_variable 解析完成: name={name!r}, "
                f"value={short_repr(resolved_value)}, elapsed={custom_variable_elapsed_seconds:.3f}s"
            )
        finally:
            self._resolving_set.discard(name)

    async def _ensure_template_dependencies_resolved(self, template_value: Any) -> None:
        """
        按需解析即将被消费的模板值中引用的依赖变量

        扫描模板中的占位符，对尚未求值的变量触发按需求值。
        该方法用于 fallback_value、condition_value 以及动态条件命中分支的模板消费前准备。

        :param template_value: Any，可能包含占位符的模板值，支持 str、list、dict
        """
        if not template_value:
            return
        known_node_names = self._action_parameter_names | self._required_custom_variable_keys
        referenced_names = self._scan_dependencies(template_value, known_node_names)
        for name in referenced_names:
            await self._ensure_resolved(name)

    async def _ensure_soft_dependencies_resolved(self, template_value: str) -> None:
        """
        兼容旧名称的软依赖解析入口

        :param template_value: str，可能包含 {变量名} 占位符的模板字符串
        :return: None，不返回值
        """
        await self._ensure_template_dependencies_resolved(template_value)

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

        if definition.mode == "dict":
            source_value = self._resolve_named_value(
                definition.source,
                resolved_context,
                builtin_placeholder_values,
                builtin_variable_provider,
            )
            source_key = "" if source_value is None else str(source_value)
            if source_key in definition.entries:
                return definition.entries[source_key]
            if definition.missing_behavior == "keep_placeholder":
                return f"{{{definition.key}}}"
            if definition.missing_behavior == "raise_error":
                raise ValueError(f"字典类型变量 {definition.key} 中不包含 key 为 {source_key!r} 的键")
            # use_default: 只有真正走到 fallback 分支时才解析其依赖
            await self._ensure_template_dependencies_resolved(definition.fallback_value)
            return str(self._substitute_placeholders_in_value(
                definition.fallback_value, resolved_context, builtin_placeholder_values,
            ))

        if definition.mode == "extract":
            source_value = self._resolve_named_value(
                definition.source,
                resolved_context,
                builtin_placeholder_values,
                builtin_variable_provider,
            )
            source_text = "" if source_value is None else str(source_value)
            extracted: str | None = None
            if definition.pattern:
                match = re.search(definition.pattern, source_text)
                if match is not None:
                    try:
                        extracted = match.group(definition.group)
                    except IndexError:
                        extracted = None
            if extracted is not None:
                return extracted.strip()
            if definition.missing_behavior == "keep_placeholder":
                return f"{{{definition.key}}}"
            if definition.missing_behavior == "raise_error":
                raise ValueError(
                    f"extract 类型变量 {definition.key} 的正则未匹配 source={definition.source!r}"
                )
            # use_default: 只有真正走到 fallback 分支时才解析其依赖
            await self._ensure_template_dependencies_resolved(definition.fallback_value)
            return str(self._substitute_placeholders_in_value(
                definition.fallback_value, resolved_context, builtin_placeholder_values,
            ))

        if random.random() > definition.probability:
            return ""

        candidates = definition.values
        if definition.condition_type:
            # use_raw_condition_source=True 时直接从 action_inputs 或已求值上下文取原始值，
            # 不经过 _resolve_named_value（后者会触发内置变量解析等额外逻辑）
            if definition.use_raw_condition_source:
                if definition.condition_source in self._action_inputs:
                    condition_source_value = self._action_inputs[definition.condition_source]
                elif definition.condition_source in resolved_context:
                    condition_source_value = resolved_context[definition.condition_source]
                else:
                    condition_source_value = ""
            else:
                condition_source_value = self._resolve_named_value(
                    definition.condition_source,
                    resolved_context,
                    builtin_placeholder_values,
                    builtin_variable_provider,
                )

            # use_raw_condition_value=True 时直接使用字面文本，
            # 不做占位符替换，不触发依赖解析
            raw_condition_value = definition.condition_value or ""
            if definition.use_raw_condition_value:
                resolved_condition_value = raw_condition_value
            elif raw_condition_value:
                await self._ensure_template_dependencies_resolved(raw_condition_value)
                resolved_condition_value = str(self._substitute_placeholders_in_value(
                    raw_condition_value, resolved_context, builtin_placeholder_values,
                ))
            else:
                resolved_condition_value = ""

            condition_result = self._evaluate_condition(
                definition.condition_type,
                "" if condition_source_value is None else str(condition_source_value),
                resolved_condition_value,
            )
            candidates = definition.values if condition_result else definition.values_else

        if not candidates:
            return ""

        # 对真正命中的候选分支做按需依赖解析
        # 未命中的分支不会在这里被消费，因此不会触发额外求值
        await self._ensure_template_dependencies_resolved(candidates)

        required_builtin_names = TemplatePlaceholderUtils.collect_builtin_placeholder_names(candidates, self._builtin_names, )
        extra_builtin_values = builtin_variable_provider.build_placeholder_values(required_builtin_names)

        placeholder_values = dict(extra_builtin_values)
        placeholder_values.update(builtin_placeholder_values)
        for key, val in resolved_context.items():
            placeholder_values[f"{{{key}}}"] = val

        selected_template = random.choice(candidates)
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

        if definition.mode == "daily_llm":
            from .llm_value_cache import get_daily_llm_cache
            cache = get_daily_llm_cache()
            return await cache.get_or_generate(
                definition.key,
                lambda: llm_value_factory(selected_value),
            )

        return await llm_value_factory(selected_value)

    def _resolve_named_value(
            self,
            name: str | None,
            resolved_context: dict[str, Any],
            builtin_placeholder_values: dict[str, Any],
            builtin_variable_provider: BuiltinVariableProvider,
    ) -> Any:
        """按名字从已解析上下文或内置变量中取值"""
        if not name:
            return ""
        if name in resolved_context:
            return resolved_context[name]

        builtin_placeholder = f"{{{name}}}"
        if builtin_placeholder in builtin_placeholder_values:
            return builtin_placeholder_values[builtin_placeholder]
        if name in self._builtin_names:
            extra_builtin_values = builtin_variable_provider.build_placeholder_values({name})
            return extra_builtin_values.get(builtin_placeholder, "")
        return ""

    @staticmethod
    def _evaluate_condition(condition_type: str, source_value: str, condition_value: str) -> bool:
        """求值第一阶段支持的条件判断类型"""
        if condition_type == "fixed_true":
            return True
        if condition_type == "fixed_false":
            return False
        if condition_type == "length_gt":
            return len(source_value) > int(condition_value)
        if condition_type == "length_lt":
            return len(source_value) < int(condition_value)
        if condition_type == "contains":
            return condition_value in source_value
        if condition_type == "not_contains":
            return condition_value not in source_value
        if condition_type == "regex_match":
            return bool(re.search(condition_value, source_value))
        if condition_type == "regex_not_match":
            return not bool(re.search(condition_value, source_value))
        if condition_type == "equals":
            return source_value == condition_value
        if condition_type == "not_equals":
            return source_value != condition_value
        raise ValueError(f"不支持的条件判断类型: {condition_type}")

    def _resolve_template_recursive(self, value_template: Any, placeholder_values: dict[str, Any]) -> Any:
        """递归解析模板值中的占位符"""

        """
        递归替换模板值中的占位符

        :param value_template: Any，待替换的模板值，支持 str、list、dict
        :param placeholder_values: dict[str, Any]，占位符到实际值的映射
        :return: Any，替换完成后的模板值
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
        计算自定义变量的传递闭包，得到需要纳入依赖图的完整变量集合

        从直接引用的变量出发，递归扫描每个变量的模板引用的其他自定义变量，
        同时扫描 action_inputs 中引用的自定义变量，直到集合不再增长。
        这里返回的是“入图节点集合”，并不表示这些变量都会被立即求值。

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

                referenced_custom_vars: set[str] = set()
                if definition.mode in ("dict", "extract"):
                    if definition.source in all_custom_var_names:
                        referenced_custom_vars.add(definition.source)
                    # fallback_value 中的引用也纳入传递闭包
                    if definition.fallback_value:
                        fb_refs = TemplatePlaceholderUtils.collect_non_builtin_placeholder_names(
                            definition.fallback_value, builtin_names
                        )
                        referenced_custom_vars.update(fb_refs & all_custom_var_names)
                else:
                    if definition.condition_type in {None, "fixed_true", "fixed_false"}:
                        for value_template in [*definition.values, *definition.values_else]:
                            referenced = TemplatePlaceholderUtils.collect_non_builtin_placeholder_names(
                                value_template, builtin_names
                            )
                            referenced_custom_vars.update(referenced & all_custom_var_names)
                    else:
                        # 动态条件变量的 values / values_else 虽然是软依赖，但仍需纳入闭包
                        # 这样才能保证软依赖的下游依赖继续入图，并参与循环检测
                        for value_template in [*definition.values, *definition.values_else]:
                            referenced = TemplatePlaceholderUtils.collect_non_builtin_placeholder_names(
                                value_template, builtin_names
                            )
                            referenced_custom_vars.update(referenced & all_custom_var_names)
                    # use_raw_condition_source=True 时不将 condition_source 纳入传递闭包
                    if not definition.use_raw_condition_source:
                        if definition.condition_source in all_custom_var_names:
                            referenced_custom_vars.add(definition.condition_source)
                    # use_raw_condition_value=True 时不扫描 condition_value 中的占位符引用
                    if not definition.use_raw_condition_value:
                        if definition.condition_value:
                            cv_refs = TemplatePlaceholderUtils.collect_non_builtin_placeholder_names(
                                definition.condition_value, builtin_names
                            )
                            referenced_custom_vars.update(cv_refs & all_custom_var_names)

                next_frontier.update(referenced_custom_vars - required)

            required.update(next_frontier)
            frontier = next_frontier

        return required
