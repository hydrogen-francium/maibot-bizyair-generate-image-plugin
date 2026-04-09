from dataclasses import dataclass, field
from typing import Iterable, Literal

PermissionListMode = Literal["whitelist", "blacklist"]
PermissionComponentType = Literal["command", "action"]


@dataclass
class PermissionManager:
    global_blacklist: set[str] = field(default_factory=set)
    command_user_list: set[str] = field(default_factory=set)
    command_user_list_mode: PermissionListMode = "whitelist"
    action_user_list: set[str] = field(default_factory=set)
    action_user_list_mode: PermissionListMode = "blacklist"

    def configure(
            self,
            *,
            global_blacklist: Iterable[str],
            command_user_list: Iterable[str],
            command_user_list_mode: str,
            action_user_list: Iterable[str],
            action_user_list_mode: str,
    ) -> None:
        """
        根据配置更新权限控制规则

        :param global_blacklist: Iterable[str]，全局禁止使用插件的用户 ID 集合
        :param command_user_list: Iterable[str]，命令权限名单中的用户 ID 集合
        :param command_user_list_mode: str，命令权限名单模式，支持 whitelist 或 blacklist
        :param action_user_list: Iterable[str]，action 权限名单中的用户 ID 集合
        :param action_user_list_mode: str，action 权限名单模式，支持 whitelist 或 blacklist
        :return: None，无返回值
        """
        self.global_blacklist = self._normalize_user_id_set(global_blacklist)
        self.command_user_list = self._normalize_user_id_set(command_user_list)
        self.command_user_list_mode = self._normalize_mode(command_user_list_mode)
        self.action_user_list = self._normalize_user_id_set(action_user_list)
        self.action_user_list_mode = self._normalize_mode(action_user_list_mode)

    def check_command_permission(self, user_id: str) -> tuple[bool, str | None]:
        """
        检查指定用户是否拥有命令调用权限

        :param user_id: str，待检查的用户 ID
        :return: tuple[bool, str | None]，是否允许调用及失败原因
        """
        return self._check_permission(user_id=user_id, component_type="command")

    def check_action_permission(self, user_id: str) -> tuple[bool, str | None]:
        """
        检查指定用户是否拥有 action 调用权限

        :param user_id: str，待检查的用户 ID
        :return: tuple[bool, str | None]，是否允许调用及失败原因
        """
        return self._check_permission(user_id=user_id, component_type="action")

    def _check_permission(
            self,
            *,
            user_id: str,
            component_type: PermissionComponentType,
    ) -> tuple[bool, str | None]:
        """
        按组件类型执行统一的权限检查逻辑

        :param user_id: str，待检查的用户 ID
        :param component_type: PermissionComponentType，待检查的组件类型
        :return: tuple[bool, str | None]，是否允许调用及失败原因
        """
        normalized_user_id = str(user_id).strip()
        if normalized_user_id in self.global_blacklist:
            return False, f"用户 {normalized_user_id} 已被全局禁止使用该插件"

        user_list, list_mode, component_label = self._get_component_rules(component_type)
        is_in_list = normalized_user_id in user_list
        if list_mode == "blacklist" and is_in_list:
            return False, f"用户 {normalized_user_id} 没有使用该{component_label}的权限"
        if list_mode == "whitelist" and not is_in_list:
            return False, f"用户 {normalized_user_id} 没有使用该{component_label}的权限"
        return True, None

    def _get_component_rules(
            self,
            component_type: PermissionComponentType,
    ) -> tuple[set[str], PermissionListMode, str]:
        """
        获取指定组件类型对应的权限名单和模式

        :param component_type: PermissionComponentType，待查询的组件类型
        :return: tuple[set[str], PermissionListMode, str]，用户名单、名单模式和组件中文标识
        """
        if component_type == "command":
            return self.command_user_list, self.command_user_list_mode, "命令"
        return self.action_user_list, self.action_user_list_mode, "action"

    @staticmethod
    def _normalize_mode(mode: str) -> PermissionListMode:
        """
        规范化权限名单模式字符串

        :param mode: str，原始名单模式字符串
        :return: PermissionListMode，规范化后的名单模式
        """
        normalized_mode = str(mode).strip().lower()
        if normalized_mode == "blacklist":
            return "blacklist"
        return "whitelist"

    @staticmethod
    def _normalize_user_id_set(user_ids: Iterable[str]) -> set[str]:
        """
        规范化用户 ID 可迭代对象为去重集合

        :param user_ids: Iterable[str]，原始用户 ID 可迭代对象
        :return: set[str]，去空白、去空值后的用户 ID 集合
        """
        return {str(user_id).strip() for user_id in user_ids if str(user_id).strip()}


permission_manager = PermissionManager()
