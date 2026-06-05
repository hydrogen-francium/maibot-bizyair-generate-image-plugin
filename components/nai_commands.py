"""NAI 运行时设置命令（全局配置式，P1）。

切换类（标量层，改 GenerateImageAction 类属性 + save_toml_with_format 写回标量）：
- /nai set [代号]      查看 / 切换 NAI 模型（全局覆盖）
- /nai models          列出可用 NAI 模型（配置预设 + 代号表）
- /nai nsfw [on|off]   查看 / 开关 SFW 过滤
- /nai art [序号|off]  查看 / 切换画师串预设
- /nai size [v|h|s|auto] 查看 / 切换出图尺寸

出图类（复用 services/nai_draw_core 出图核心，与 Action 同一套管线）：
- /nai0 <英文 tag>     直发：跳过 LLM 大脑，原始 Danbooru tag 当主体，套包装层
- /nai 随机[自拍]      随机：把场景交给 nai_director 自由发挥

均遵循 bizyair 范式：改 GenerateImageAction 类属性（立即生效）+ save_toml_with_format 写回标量（持久）。
"""

import base64
import traceback
from typing import Optional, Tuple

from src.common.logger import get_logger
from src.plugin_system import BaseCommand

from .generate_image_action import GenerateImageAction
from ..services import nai_draw_core
from ..services import nai_random_scene
from ..services import nai_settings
from ..services import permission_manager
from ..services.log_utils import short_repr

logger = get_logger("bizyair_generate_image_plugin")


def _deny_if_no_permission(command: BaseCommand) -> Optional[str]:
    """统一命令权限检查，无权限时返回拒绝原因，有权限返回 None。"""
    user_id = command.message.message_info.user_info.user_id
    has_permission, deny_reason = permission_manager.check_command_permission(str(user_id))
    return None if has_permission else (deny_reason or "无权限")


async def _run_nai_draw(
        command: BaseCommand,
        *,
        action_inputs: dict,
        log_label: str,
) -> Tuple[bool, Optional[str], int]:
    """命令侧出图公共尾部：选 NAI 预设 → 跑出图核心 → 发图。

    复用 GenerateImageAction 的全局配置式类属性（active_preset / action_parameters /
    nai_artist / nai_size / nai_model / nai_sfw_filter），与 Action 走完全相同的核心管线，
    只是 action_inputs 不同（/nai0 注入 nai_raw_tags 旁路 director；/nai 随机 给 image_intent）。
    """
    active_preset = str(GenerateImageAction.active_preset or "").strip()
    nai_presets = command.get_config("nai_chat_client.presets", []) or []
    nai_preset = nai_settings.resolve_nai_preset_name(active_preset, nai_presets)
    if not nai_preset:
        await command.send_text(
            "未配置任何 NAI 预设，无法出图。请在 config.toml 的 [[nai_chat_client.presets]] 添加。"
        )
        return False, f"{log_label} 无 NAI 预设", 1

    chat_stream = getattr(command.message, "chat_stream", None)
    chat_id = getattr(chat_stream, "stream_id", None)
    logger.info(f"[{log_label}] 出图开始: nai_preset={nai_preset!r}, action_inputs={short_repr(action_inputs)}")

    try:
        image_bytes, payload = await nai_draw_core.run_draw_to_bytes(
            get_config=command.get_config,
            action_inputs=action_inputs,
            active_preset=nai_preset,
            action_parameters=GenerateImageAction.action_parameters,
            required_action_parameters=set(GenerateImageAction.required_action_parameters),
            chat_id=chat_id,
            image_base64_provider=None,
            nai_artist=GenerateImageAction.nai_artist,
            nai_size=GenerateImageAction.nai_size,
            nai_model=GenerateImageAction.nai_model,
            nai_sfw_filter=GenerateImageAction.nai_sfw_filter,
            log_prefix=f"[{log_label}]",
        )
    except Exception as exc:  # noqa: BLE001 — 命令侧出图失败只回提示，不抛给框架
        logger.error(f"[{log_label}] 出图失败: {exc}\n{traceback.format_exc()}")
        await command.send_text(f"出图失败：{type(exc).__name__}: {exc}")
        return False, f"{log_label} 出图失败", 1

    if not image_bytes:
        await command.send_text("出图失败：未获取到图片数据")
        return False, f"{log_label} 无图片数据", 1

    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    sent = await command.send_image(image_base64, storage_message=True)
    if not sent:
        await command.send_text("图片生成成功但发送失败")
        return False, f"{log_label} 发送失败", 1

    logger.info(
        f"[{log_label}] 出图完成: nai_preset={nai_preset!r}, "
        f"content_json={short_repr(payload.provider_payload.get('content_json'))}"
    )
    return True, f"{log_label} 出图完成", 1


class NaiSetCommand(BaseCommand):
    """查看 / 切换 NAI 模型（/nai set [代号]）。"""

    command_name = "nai_set"
    command_description = "查看或切换 NAI 模型（全局生效）"
    command_pattern = r"^/nai\s+set(?:\s+(?P<code>\S+))?$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        code = (self.matched_groups.get("code") or "").strip()
        current_override = str(GenerateImageAction.nai_model or "").strip()

        if not code:
            current_text = current_override or "（未设置，使用各 NAI 预设自带 model）"
            await self.send_text(
                "🎨 当前 NAI 模型覆盖：\n"
                f"  {current_text}\n\n"
                f"可切换代号：\n  {nai_settings.model_alias_help()}\n\n"
                "用法：/nai set <代号>，例如 /nai set 4.5；/nai set off 取消覆盖"
            )
            return True, "查看 NAI 模型", 1

        if code.lower() in ("off", "clear", "none", "取消"):
            GenerateImageAction.nai_model = ""
            persisted = nai_settings.save_setting(nai_settings.NAI_MODEL_KEY, "")
            tip = "已保存。" if persisted else "(写回配置失败，重启后恢复)"
            await self.send_text(f"✅ 已取消 NAI 模型覆盖，恢复使用预设自带 model。{tip}")
            return True, "取消 NAI 模型覆盖", 1

        full_name = nai_settings.resolve_model_alias(code)
        if not full_name:
            await self.send_text(
                f'无法识别模型代号 "{code}"。\n可用代号：\n  {nai_settings.model_alias_help()}'
            )
            return False, f"未知模型代号 {code}", 1

        old = current_override or "（预设自带）"
        GenerateImageAction.nai_model = full_name
        persisted = nai_settings.save_setting(nai_settings.NAI_MODEL_KEY, full_name)
        tip = "已保存到配置。" if persisted else "(写回配置失败，重启后恢复)"
        await self.send_text(f"✅ NAI 模型已切换：{old} → {full_name}\n{tip}")
        return True, f"切换 NAI 模型 -> {full_name}", 1


class NaiModelsCommand(BaseCommand):
    """列出可用 NAI 模型（/nai models）。"""

    command_name = "nai_models"
    command_description = "列出可用的 NAI 模型与代号"
    command_pattern = r"^/nai\s+models$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        lines = ["📋 NAI 模型代号："]
        for code, name in nai_settings.MODEL_ALIASES.items():
            lines.append(f"  {code} = {name}")

        presets = self.get_config("nai_chat_client.presets", []) or []
        preset_models = []
        for preset in presets:
            if isinstance(preset, dict):
                name = str(preset.get("preset_name", "?"))
                model = str(preset.get("model", "")).strip() or "（未填）"
                preset_models.append(f"  {name}: {model}")
        if preset_models:
            lines.append("\n已配置 NAI 预设的 model：")
            lines.extend(preset_models)

        override = str(GenerateImageAction.nai_model or "").strip()
        lines.append(f"\n当前全局覆盖：{override or '（未设置）'}")
        lines.append("切换：/nai set <代号>")
        await self.send_text("\n".join(lines))
        return True, "列出 NAI 模型", 1


class NaiNsfwCommand(BaseCommand):
    """查看 / 开关 SFW 过滤（/nai nsfw [on|off]）。"""

    command_name = "nai_nsfw"
    command_description = "查看或开关 NAI 的 SFW 过滤"
    command_pattern = r"^/nai\s+nsfw(?:\s+(?P<state>on|off))?$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        state = (self.matched_groups.get("state") or "").strip().lower()
        current = bool(GenerateImageAction.nai_sfw_filter)

        if not state:
            status = "开启（剔除擦边 tag）" if current else "关闭（允许轻量暴露）"
            await self.send_text(
                f"🔞 NAI SFW 过滤当前：{status}\n用法：/nai nsfw on|off"
            )
            return True, "查看 NSFW 过滤", 1

        enabled = state == "on"
        if enabled == current:
            await self.send_text(f"SFW 过滤已经是{'开启' if enabled else '关闭'}状态，无需切换。")
            return True, "NSFW 过滤未变更", 1

        GenerateImageAction.nai_sfw_filter = enabled
        persisted = nai_settings.save_setting(nai_settings.NAI_SFW_FILTER_KEY, enabled)
        tip = "已保存到配置。" if persisted else "(写回配置失败，重启后恢复)"
        await self.send_text(
            f"✅ NAI SFW 过滤已{'开启' if enabled else '关闭'}。{tip}"
        )
        return True, f"切换 NSFW 过滤 -> {'on' if enabled else 'off'}", 1


class NaiArtCommand(BaseCommand):
    """查看 / 切换 NAI 画师串预设（/nai art [序号|名称|off]）。"""

    command_name = "nai_art"
    command_description = "查看或切换 NAI 画师串预设（全局生效）"
    command_pattern = r"^/nai\s+art(?:\s+(?P<arg>.+))?$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        arg = (self.matched_groups.get("arg") or "").strip()
        raw_presets = self.get_config("nai_chat_client.nai_artist_presets", []) or []
        current = str(GenerateImageAction.nai_artist or "").strip()

        if not arg:
            current_text = current or "（未设置，不注入画师串）"
            await self.send_text(
                "🎨 当前 NAI 画师串：\n"
                f"  {current_text}\n\n"
                f"可选预设：\n{nai_settings.artist_presets_help(raw_presets)}\n\n"
                "用法：/nai art <序号|名称> 切换；/nai art off 取消"
            )
            return True, "查看 NAI 画师串", 1

        status, name, prompt = nai_settings.resolve_artist_choice(arg, raw_presets)

        if status == "clear":
            GenerateImageAction.nai_artist = ""
            persisted = nai_settings.save_setting(nai_settings.NAI_ARTIST_KEY, "")
            tip = "已保存。" if persisted else "(写回配置失败，重启后恢复)"
            await self.send_text(f"✅ 已取消 NAI 画师串注入。{tip}")
            return True, "取消 NAI 画师串", 1

        if status == "set":
            GenerateImageAction.nai_artist = prompt
            persisted = nai_settings.save_setting(nai_settings.NAI_ARTIST_KEY, prompt)
            tip = "已保存到配置。" if persisted else "(写回配置失败，重启后恢复)"
            await self.send_text(f"✅ NAI 画师串已切换为「{name}」：\n  {prompt}\n{tip}")
            return True, f"切换 NAI 画师串 -> {name}", 1

        await self.send_text(
            f'无法识别画师选择 "{arg}"。\n可选预设：\n{nai_settings.artist_presets_help(raw_presets)}\n\n'
            "用法：/nai art <序号|名称>；/nai art off 取消"
        )
        return False, f"未知画师选择 {arg}", 1


class NaiSizeCommand(BaseCommand):
    """查看 / 切换 NAI 出图尺寸（/nai size [v|h|s|auto]）。"""

    command_name = "nai_size"
    command_description = "查看或切换 NAI 出图尺寸（全局生效）"
    command_pattern = r"^/nai\s+size(?:\s+(?P<code>\S+))?$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        code = (self.matched_groups.get("code") or "").strip()
        current = str(GenerateImageAction.nai_size or "auto").strip() or "auto"

        if not code:
            await self.send_text(
                f"📐 当前 NAI 尺寸：{current}\n"
                f"可选：{nai_settings.size_alias_help()}\n"
                "用法：/nai size v|h|s|auto（auto=跟随画面比例）"
            )
            return True, "查看 NAI 尺寸", 1

        canonical = nai_settings.resolve_size_alias(code)
        if not canonical:
            await self.send_text(
                f'无法识别尺寸 "{code}"。\n可选：{nai_settings.size_alias_help()}'
            )
            return False, f"未知尺寸 {code}", 1

        GenerateImageAction.nai_size = canonical
        persisted = nai_settings.save_setting(nai_settings.NAI_SIZE_KEY, canonical)
        tip = "已保存到配置。" if persisted else "(写回配置失败，重启后恢复)"
        labels = {
            "v": "竖图 832x1216",
            "h": "横图 1216x832",
            "s": "方图 1024x1024",
            "auto": "跟随画面比例",
        }
        await self.send_text(f"✅ NAI 尺寸已设为：{labels.get(canonical, canonical)}。{tip}")
        return True, f"切换 NAI 尺寸 -> {canonical}", 1


class Nai0Command(BaseCommand):
    """直发：/nai0 <英文 Danbooru tag> —— 跳过 LLM 大脑，原始 tag 当主体，复用包装层。

    把用户给的英文 tag 作为 nai_raw_tags 注入；nai_subject 条件变量据此走直发分支，
    nai_director 一次都不跑（惰性旁路），但质量词/画师串/负面词/尺寸包装照常套上。
    """

    command_name = "nai0"
    command_description = "用原始英文 Danbooru tag 直接出图（跳过 LLM 大脑，自动套包装层）"
    command_pattern = r"^/nai0(?:\s+(?P<tags>.+))?$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        tags = (self.matched_groups.get("tags") or "").strip()
        if not tags:
            await self.send_text(
                "用法：/nai0 <英文 Danbooru tag>\n"
                "示例：/nai0 1girl, cat ears, smile, cafe, window light\n"
                "（直接出图、不经 LLM 大脑；会自动套上质量词/画师串/负面词/尺寸。\n"
                "画师串用 /nai art 切，尺寸用 /nai size 切。）"
            )
            return True, "查看 /nai0 用法", 1

        return await _run_nai_draw(self, action_inputs={"nai_raw_tags": tags}, log_label="nai0")


class NaiRandomCommand(BaseCommand):
    """随机：/nai 随机[自拍] —— 把画什么交给 nai_director 自由发挥。

    只随机挑一个「创作方向 nudge」灌进 image_intent，走正常 director 路径；director 结合
    today_state 时间表 + 最近聊天上下文自调控出当下合理画面。「自拍」走自拍构图。
    """

    command_name = "nai_random"
    command_description = "随机出图（场景交给 LLM 大脑自由发挥）；加「自拍」走自拍构图"
    command_pattern = r"^/nai\s+随机\s*(?P<selfie>自拍)?$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        selfie = bool((self.matched_groups.get("selfie") or "").strip())
        intent = nai_random_scene.pick_random_intent(selfie=selfie)
        logger.info(f"[nai_random] selfie={selfie}, intent={intent!r}")
        return await _run_nai_draw(self, action_inputs={"image_intent": intent}, log_label="nai_random")
