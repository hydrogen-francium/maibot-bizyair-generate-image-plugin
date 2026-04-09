from __future__ import annotations

import re
from typing import Any


class TemplatePlaceholderUtils:
    """模板占位符扫描与分类工具"""

    PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]+)\}")

    @classmethod
    def extract_placeholder_names(cls, value: str) -> list[str]:
        """
        提取字符串中的占位符名称列表

        :param value: str，待扫描的模板字符串
        :return: list[str]，按出现顺序提取出的占位符名称列表
        """
        return [match.group(1).strip() for match in cls.PLACEHOLDER_PATTERN.finditer(value) if match.group(1).strip()]

    @classmethod
    def extract_placeholder_names_from_any(cls, value: Any) -> set[str]:
        """
        递归提取任意模板值中的占位符名称集合

        :param value: Any，待扫描的模板值，可以是字符串、列表、字典或其他类型
        :return: set[str]，提取到的占位符名称集合
        """
        if isinstance(value, str):
            return set(cls.extract_placeholder_names(value))
        if isinstance(value, list):
            result: set[str] = set()
            for item in value:
                result.update(cls.extract_placeholder_names_from_any(item))
            return result
        if isinstance(value, dict):
            result: set[str] = set()
            for item in value.values():
                result.update(cls.extract_placeholder_names_from_any(item))
            return result
        return set()

    @classmethod
    def collect_builtin_placeholder_names(cls, value: Any, builtin_names: set[str] | frozenset[str]) -> set[str]:
        """
        从模板值中提取被引用到的内置变量名

        :param value: Any，待扫描的模板值
        :param builtin_names: set[str] | frozenset[str]，可识别的内置变量名集合
        :return: set[str]，模板中实际引用到的内置变量名集合
        """
        return {
            name for name in cls.extract_placeholder_names_from_any(value)
            if name in builtin_names
        }

    @classmethod
    def collect_custom_placeholder_names(
            cls,
            value: Any,
            *,
            action_parameter_names: set[str],
            builtin_names: set[str] | frozenset[str],
    ) -> set[str]:
        """
        从模板值中提取自定义变量名

        :param value: Any，待扫描的模板值
        :param action_parameter_names: set[str]，action 参数名称集合
        :param builtin_names: set[str] | frozenset[str]，内置变量名称集合
        :return: set[str]，排除 action 参数和内置变量后的自定义变量名集合
        """
        return {
            name for name in cls.extract_placeholder_names_from_any(value)
            if name not in action_parameter_names and name not in builtin_names
        }
