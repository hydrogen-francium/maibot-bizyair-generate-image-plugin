"""
/dr list  —— 列出所有可用的 App 预设
/dr use <preset_name>  —— 切换当前激活的 App 预设
"""

import os
from typing import Optional, Tuple

from src.common.logger import get_logger
from src.plugin_system import BaseCommand

from .generate_image_action import GenerateImageAction
from ..services import permission_manager

logger = get_logger("bizyair_generate_image_plugin")

# config.toml 与本插件目录同级
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_PLUGIN_DIR, "config.toml")


def _collect_all_presets(command: BaseCommand) -> list[dict]:
    """收集两个后端下的所有预设，并标注来源后端"""
    collected: list[dict] = []
    for provider, config_key in (
            ("bizyair_openapi", "bizyair_client.app_presets"),
            ("nai_chat", "nai_chat_client.presets"),
    ):
        presets = command.get_config(config_key, []) or []
        if not isinstance(presets, list):
            raise ValueError(f"{config_key} 必须是列表")
        for index, preset in enumerate(presets):
            if not isinstance(preset, dict):
                raise ValueError(f"{config_key}[{index}] 必须是对象")
            collected.append({
                "provider": provider,
                "preset": preset,
            })
    return collected


class DrListCommand(BaseCommand):
    """列出所有可用的 App 预设（/dr list）"""

    command_name = "dr_list"
    command_description = "列出所有可用的画图 App 预设及当前激活状态"
    command_pattern = r"^/dr\s+list$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        user_id = self.message.message_info.user_info.user_id
        has_permission, deny_reason = permission_manager.check_command_permission(str(user_id))
        if not has_permission:
            return True, deny_reason, 1

        presets = _collect_all_presets(self)
        active: str = GenerateImageAction.active_preset

        if not presets:
            await self.send_text("当前没有配置任何 App 预设。")
            return True, "列出预设：无预设", 1

        lines = ["📋 当前可用的画图 App 预设：\n"]
        for item in presets:
            p = item["preset"]
            provider = item["provider"]
            name = p.get("preset_name", "?")
            desc = p.get("description", "")
            provider_label = "BizyAir" if provider == "bizyair_openapi" else "NAI"
            marker = " ✅(当前使用)" if name == active else ""
            desc_part = f"  {desc}" if desc else ""
            lines.append(f"• {name} [{provider_label}]{marker}{desc_part}")

        await self.send_text("\n".join(lines))
        return True, f"列出了 {len(presets)} 个预设", 1


class DrUseCommand(BaseCommand):
    """切换当前激活的 App 预设（/dr use <preset_name>）"""

    command_name = "dr_use"
    command_description = "切换当前激活的画图 App 预设"
    command_pattern = r"^/dr\s+use\s+(?P<preset_name>\S+)$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        user_id = self.message.message_info.user_info.user_id
        has_permission, deny_reason = permission_manager.check_command_permission(str(user_id))
        if not has_permission:
            return True, deny_reason, 1

        preset_name: str = self.matched_groups.get("preset_name", "").strip()
        if not preset_name:
            await self.send_text("用法：/dr use <预设名称>")
            return False, "缺少预设名称参数", 1

        presets = _collect_all_presets(self)
        available_names = [item["preset"].get("preset_name", "") for item in presets]

        if preset_name not in available_names:
            names_str = "、".join(available_names) if available_names else "（无）"
            await self.send_text(
                f'预设 "{preset_name}" 不存在。\n可用预设：{names_str}'
            )
            return False, f"预设 {preset_name} 不存在", 1

        old_preset = GenerateImageAction.active_preset
        if old_preset == preset_name:
            await self.send_text(f'当前已经是预设 "{preset_name}"，无需切换。')
            return True, "预设未变更", 1

        # 运行时切换
        GenerateImageAction.active_preset = preset_name

        # 保存写入 config.toml
        try:
            from src.common.toml_utils import save_toml_with_format
            save_toml_with_format(
                {"bizyair_generate_image_plugin": {"active_preset": preset_name}},
                _CONFIG_PATH,
                preserve_comments=True,
            )
            persist_msg = "已保存到配置文件。"
        except Exception as e:
            logger.warning(f"[DrUseCommand] 保存 active_preset 失败: {e}")
            persist_msg = "(注意: 保存到配置文件时失败，重启后将恢复原预设)"

        await self.send_text(
            f'✅ 已切换画图预设：{old_preset} → {preset_name}\n{persist_msg}'
        )
        return True, f"切换预设 {old_preset} -> {preset_name}", 1
