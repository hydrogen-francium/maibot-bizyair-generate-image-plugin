import pytest

from services.permission_manager import PermissionManager


@pytest.fixture
def pm() -> PermissionManager:
    return PermissionManager()


class TestGlobalBlacklist:
    def test_blocked_user_denied_command(self, pm: PermissionManager):
        pm.configure(
            global_blacklist=["user1"],
            command_user_list=["user1"],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        ok, reason = pm.check_command_permission("user1")
        assert ok is False
        assert "全局禁止" in reason

    def test_blocked_user_denied_action(self, pm: PermissionManager):
        pm.configure(
            global_blacklist=["user1"],
            command_user_list=[],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        ok, reason = pm.check_action_permission("user1")
        assert ok is False
        assert "全局禁止" in reason


class TestWhitelistMode:
    def test_user_in_whitelist_allowed(self, pm: PermissionManager):
        pm.configure(
            global_blacklist=[],
            command_user_list=["user1"],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        ok, _ = pm.check_command_permission("user1")
        assert ok is True

    def test_user_not_in_whitelist_denied(self, pm: PermissionManager):
        pm.configure(
            global_blacklist=[],
            command_user_list=["user1"],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        ok, reason = pm.check_command_permission("user2")
        assert ok is False
        assert "没有使用" in reason


class TestBlacklistMode:
    def test_user_in_blacklist_denied(self, pm: PermissionManager):
        pm.configure(
            global_blacklist=[],
            command_user_list=[],
            command_user_list_mode="whitelist",
            action_user_list=["user1"],
            action_user_list_mode="blacklist",
        )
        ok, reason = pm.check_action_permission("user1")
        assert ok is False
        assert "没有使用" in reason

    def test_user_not_in_blacklist_allowed(self, pm: PermissionManager):
        pm.configure(
            global_blacklist=[],
            command_user_list=[],
            command_user_list_mode="whitelist",
            action_user_list=["user1"],
            action_user_list_mode="blacklist",
        )
        ok, _ = pm.check_action_permission("user2")
        assert ok is True


class TestUserIdNormalization:
    def test_whitespace_stripped(self, pm: PermissionManager):
        pm.configure(
            global_blacklist=["  user1  "],
            command_user_list=[],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        ok, _ = pm.check_command_permission("user1")
        assert ok is False

    def test_empty_ids_filtered(self, pm: PermissionManager):
        pm.configure(
            global_blacklist=["", "  "],
            command_user_list=[],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        assert pm.global_blacklist == set()


class TestPublicCommand:
    """公开命令（出图 / 反推）：public=True 跳过命令白/黑名单，仅全局黑名单仍拦。"""

    def test_public_allows_user_outside_whitelist(self, pm: PermissionManager):
        # 白名单模式 + 用户不在白名单：普通命令拒、公开命令放行
        pm.configure(
            global_blacklist=[],
            command_user_list=["admin"],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        assert pm.check_command_permission("stranger")[0] is False           # 普通命令受白名单约束
        assert pm.check_command_permission("stranger", public=True)[0] is True  # 公开命令放行

    def test_public_still_blocked_by_global_blacklist(self, pm: PermissionManager):
        # 全局黑名单是硬底线：即便 public=True 也拦
        pm.configure(
            global_blacklist=["banned"],
            command_user_list=[],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        ok, reason = pm.check_command_permission("banned", public=True)
        assert ok is False
        assert "全局禁止" in reason

    def test_public_allows_admin_in_whitelist(self, pm: PermissionManager):
        # 白名单内用户用公开命令当然也放行
        pm.configure(
            global_blacklist=[],
            command_user_list=["admin"],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        assert pm.check_command_permission("admin", public=True)[0] is True

    def test_public_default_false_keeps_old_behavior(self, pm: PermissionManager):
        # 不传 public（默认 False）时行为不变：白名单外仍拒
        pm.configure(
            global_blacklist=[],
            command_user_list=["admin"],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        assert pm.check_command_permission("stranger")[0] is False

    def test_default_command_whitelist_empty_denies_all(self):
        pm = PermissionManager()
        pm.configure(
            global_blacklist=[],
            command_user_list=[],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        ok, _ = pm.check_command_permission("anyone")
        assert ok is False

    def test_default_action_blacklist_empty_allows_all(self):
        pm = PermissionManager()
        pm.configure(
            global_blacklist=[],
            command_user_list=[],
            command_user_list_mode="whitelist",
            action_user_list=[],
            action_user_list_mode="blacklist",
        )
        ok, _ = pm.check_action_permission("anyone")
        assert ok is True
