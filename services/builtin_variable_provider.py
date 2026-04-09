from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.plugin_system.apis import message_api


@dataclass(frozen=True)
class BuiltinVariableDefinition:
    name: str
    resolver: Callable[[], Any]


class BuiltinVariableProvider:
    """管理内置变量的注册、按需解析与实例级缓存"""

    DEFAULT_RANDOM_SEED_MIN = 0
    DEFAULT_RANDOM_SEED_MAX = 2147483647
    DEFAULT_RECENT_CHAT_CONTEXT_HOURS = 24.0
    DEFAULT_RECENT_CHAT_CONTEXT_LIMITS = (10, 30, 50)

    def __init__(self, *, chat_id: str, filter_mai: bool = False) -> None:
        """
        初始化内置变量提供器并注册默认变量定义

        :param chat_id: str，当前 action 所属聊天会话 ID
        :param filter_mai: bool，获取聊天记录时是否过滤 MaiBot 自身消息
        :return: None，无返回值
        """

        if not chat_id or not isinstance(chat_id, str):
            raise ValueError("chat_id 必须是非空字符串")

        self.chat_id = chat_id
        self.filter_mai = filter_mai
        self._definitions: dict[str, BuiltinVariableDefinition] = {}
        self._cache: dict[str, Any] = {}
        self._register_default_definitions()

    @property
    def variable_names(self) -> frozenset[str]:
        """
        返回当前已注册的内置变量名称集合

        :return: frozenset[str]，当前 provider 中可用的内置变量名集合
        """

        return frozenset(self._definitions.keys())

    def register(self, name: str, resolver: Callable[[], Any]) -> None:
        """
        注册一个新的内置变量解析函数

        :param name: str，内置变量名称，可带或不带花括号
        :param resolver: Callable[[], Any]，用于延迟构造变量值的无参函数
        :return: None，无返回值
        """
        normalized_name = self._normalize_name(name)
        if normalized_name in self._definitions:
            raise ValueError(f"内置变量重复注册: {normalized_name}")
        self._definitions[normalized_name] = BuiltinVariableDefinition(name=normalized_name, resolver=resolver)

    @classmethod
    def get_default_variable_names(cls) -> frozenset[str]:
        """
        返回默认内置变量名称集合

        :return: frozenset[str]，默认注册的内置变量名称集合
        """
        return frozenset({"random_seed"} | {f"recent_chat_context_{limit}" for limit in cls.DEFAULT_RECENT_CHAT_CONTEXT_LIMITS})

    def build_placeholder_values(self, required_names: set[str] | None = None) -> dict[str, Any]:
        """
        按需解析指定内置变量并返回占位符映射

        :param required_names: set[str] | None，本次需要构造的内置变量名集合，传空时构造全部已注册变量
        :return: dict[str, Any]，形如 `{变量名}` 到变量值的映射字典
        """
        names = self.variable_names if required_names is None else {self._normalize_name(name) for name in required_names}
        unknown_names = names - self.variable_names
        if unknown_names:
            raise ValueError(f"存在未定义的内置变量: {', '.join(sorted(unknown_names))}")

        placeholder_values: dict[str, Any] = {}
        for name in names:
            if name not in self._cache:
                self._cache[name] = self._definitions[name].resolver()
            placeholder_values[f"{{{name}}}"] = self._cache[name]
        return placeholder_values

    @classmethod
    def _normalize_name(cls, name: str) -> str:
        """
        规范化内置变量名称并移除外层花括号

        :param name: str，原始内置变量名称
        :return: str，校验后的标准变量名
        """
        text = "" if name is None else str(name).strip()
        if not text:
            raise ValueError("内置变量名不能为空")
        if text.startswith("{") and text.endswith("}"):
            text = text[1:-1].strip()
        if not text:
            raise ValueError("内置变量名不能为空")
        return text

    def _register_default_definitions(self) -> None:
        """
        注册当前 provider 默认支持的内置变量

        :return: None，无返回值
        """
        self.register("random_seed", self._build_random_seed)
        for limit in self.DEFAULT_RECENT_CHAT_CONTEXT_LIMITS:
            self.register(
                f"recent_chat_context_{limit}",
                lambda limit=limit: self._build_recent_chat_context(limit),
            )

    def _build_random_seed(self) -> int:
        """
        生成随机种子内置变量的值

        :return: int，位于预设区间内的随机整数种子
        """
        return random.randint(self.DEFAULT_RANDOM_SEED_MIN, self.DEFAULT_RANDOM_SEED_MAX)

    def _build_recent_chat_context(self, limit: int) -> str:
        """
        获取指定条数的最近聊天记录并格式化为可读文本

        :param limit: int，需要读取的最近聊天消息条数
        :return: str，格式化后的聊天上下文文本
        """
        messages = message_api.get_recent_messages(
            chat_id=self.chat_id,
            hours=self.DEFAULT_RECENT_CHAT_CONTEXT_HOURS,
            limit=limit,
            limit_mode="latest",
            filter_mai=self.filter_mai,
        )
        return message_api.build_readable_messages_to_str(
            messages,
            replace_bot_name=True,
            timestamp_mode="absolute",
        )
