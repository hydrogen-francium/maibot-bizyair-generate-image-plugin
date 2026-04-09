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
        self.global_blacklist = self._normalize_user_id_set(global_blacklist)
        self.command_user_list = self._normalize_user_id_set(command_user_list)
        self.command_user_list_mode = self._normalize_mode(command_user_list_mode)
        self.action_user_list = self._normalize_user_id_set(action_user_list)
        self.action_user_list_mode = self._normalize_mode(action_user_list_mode)

    def check_command_permission(self, user_id: str) -> tuple[bool, str | None]:
        return self._check_permission(user_id=user_id, component_type="command")

    def check_action_permission(self, user_id: str) -> tuple[bool, str | None]:
        return self._check_permission(user_id=user_id, component_type="action")

    def _check_permission(
            self,
            *,
            user_id: str,
            component_type: PermissionComponentType,
    ) -> tuple[bool, str | None]:
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
        if component_type == "command":
            return self.command_user_list, self.command_user_list_mode, "命令"
        return self.action_user_list, self.action_user_list_mode, "action"

    @staticmethod
    def _normalize_mode(mode: str) -> PermissionListMode:
        normalized_mode = str(mode).strip().lower()
        if normalized_mode == "blacklist":
            return "blacklist"
        return "whitelist"

    @staticmethod
    def _normalize_user_id_set(user_ids: Iterable[str]) -> set[str]:
        return {str(user_id).strip() for user_id in user_ids if str(user_id).strip()}


permission_manager = PermissionManager()