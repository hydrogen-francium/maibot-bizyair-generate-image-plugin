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
- /nai 描述 <文本>     描述：把用户描述当意图注入 director 路径出图
- /nai 反推 重绘 +图   反推重绘：反推图片 tag → 暴露提示词 → 用它直发出图（= 反推 + /nai0）

均遵循 bizyair 范式：改 GenerateImageAction 类属性（立即生效）+ save_toml_with_format 写回标量（持久）。
"""

import base64
import traceback
from typing import Optional, Tuple

from src.common.logger import get_logger
from src.plugin_system import BaseCommand

from .generate_image_action import GenerateImageAction
from .nai_retag_command import (
    build_failed_message,
    build_reverse_service,
    extract_image_base64_from_message as _extract_image_from_message,
)
from ..services import nai_draw_core
from ..services import nai_random_scene
from ..services import nai_settings
from ..services import nai_vibe_refs
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
            nai_vibe_refs=GenerateImageAction.nai_vibe_refs,
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
            status = "开启（SFW：只出全年龄向）" if current else "关闭（放开：容忍所有 NSFW 内容）"
            await self.send_text(
                f"🔞 NAI SFW 过滤当前：{status}\n"
                "用法：/nai nsfw on|off\n"
                "  on = SFW，大脑只产出全年龄向、后处理再剔除擦边 tag\n"
                "  off = 放开，大脑按意图如实表达露骨内容、后处理不删（未成年硬底线任何档位都不放开）"
            )
            return True, "查看 NSFW 过滤", 1

        enabled = state == "on"
        if enabled == current:
            await self.send_text(f"SFW 过滤已经是{'开启' if enabled else '关闭'}状态，无需切换。")
            return True, "NSFW 过滤未变更", 1

        GenerateImageAction.nai_sfw_filter = enabled
        persisted = nai_settings.save_setting(nai_settings.NAI_SFW_FILTER_KEY, enabled)
        tip = "已保存到配置。" if persisted else "(写回配置失败，重启后恢复)"
        effect = "（SFW：只出全年龄向）" if enabled else "（放开：容忍所有 NSFW，未成年硬底线仍守）"
        await self.send_text(
            f"✅ NAI SFW 过滤已{'开启' if enabled else '关闭'}{effect}。{tip}"
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
        cur_vibe = str(GenerateImageAction.nai_vibe_refs or "").strip()

        # 无参：画师串 + 画风图并列菜单
        if not arg:
            artist_text = current or "（未设置）"
            vibe_text = cur_vibe or "（未选）"
            active_note = "（画风图生效中，画师串本次让位）" if cur_vibe else ""
            await self.send_text(
                "🎨 NAI 画风设置\n"
                f"【文本画师串】当前：{artist_text}{'' if cur_vibe == '' else '（被画风图覆盖）'}\n"
                f"{nai_settings.artist_presets_help(raw_presets)}\n"
                f"  切换：/nai art <序号|名称>；取消：/nai art off\n\n"
                f"【画风参考图】当前：{vibe_text} {active_note}\n"
                f"{nai_vibe_refs.vibe_images_help()}\n"
                "  选择(可多张)：/nai art photo <序号...>，如 /nai art photo 1 2；取消：/nai art photo off\n"
                "  存图：/nai art photo save <名字> + 引用一张图\n\n"
                "注：画师串与画风图互斥——选了画风图，出图就用图做画风、画师串本次不拼。"
            )
            return True, "查看 NAI 画风设置", 1

        # 画风图子命令分流：/nai art photo ...
        if arg.lower() == "photo" or arg.lower().startswith("photo "):
            return await self._handle_photo(arg)

        # 文本画师串（原逻辑）
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
            vibe_warn = "\n⚠️ 当前已选画风参考图，画师串会被它覆盖；如需用画师串请先 /nai art photo off" if cur_vibe else ""
            await self.send_text(f"✅ NAI 画师串已切换为「{name}」。{tip}{vibe_warn}")
            return True, f"切换 NAI 画师串 -> {name}", 1

        await self.send_text(
            f'无法识别画师选择 "{arg}"。\n可选预设：\n{nai_settings.artist_presets_help(raw_presets)}\n\n'
            "用法：/nai art <序号|名称>；/nai art off 取消；/nai art photo <序号...> 选画风图"
        )
        return False, f"未知画师选择 {arg}", 1

    async def _handle_photo(self, arg: str) -> Tuple[bool, Optional[str], int]:
        """处理 /nai art photo 子命令：选择/取消画风参考图（不含 save，save 由带图命令处理）。"""
        # 去掉开头的 "photo"，剩下的是序号/名字/off
        rest = arg[len("photo"):].strip()
        tokens = rest.split() if rest else []

        # save 子命令需带图，这条纯文字路径下提示用法（实际存图走 NaiArtPhotoSaveCommand 宽松 pattern）
        if tokens and tokens[0].lower() == "save":
            await self.send_text(
                "存画风图用法：/nai art photo save <名字>，并**引用一张图片**或随消息附带图片。\n"
                "（纯文字没有图，存不了）"
            )
            return True, "画风图 save 用法", 1

        status, chosen, unknown = nai_vibe_refs.resolve_photo_selection(tokens)

        if status == "empty":
            await self.send_text(
                "🖼 画风参考图（扫 reference_images/ 文件夹）：\n"
                f"{nai_vibe_refs.vibe_images_help()}\n\n"
                "用法：/nai art photo <序号...> 选择(可多张)；/nai art photo off 取消"
            )
            return True, "查看画风图", 1

        if status == "clear":
            GenerateImageAction.nai_vibe_refs = ""
            persisted = nai_vibe_refs.save_selection("")
            tip = "已保存。" if persisted else "(写回配置失败，重启后恢复)"
            await self.send_text(f"✅ 已取消画风参考图，恢复文本画师串/无。{tip}")
            return True, "取消画风图", 1

        if status == "set":
            names = [c["name"] for c in chosen]
            GenerateImageAction.nai_vibe_refs = ",".join(names)
            persisted = nai_vibe_refs.save_selection(",".join(names))
            tip = "已保存。" if persisted else "(写回配置失败，重启后恢复)"
            await self.send_text(
                f"✅ 已选画风参考图（{len(names)} 张）：{('、'.join(names))}\n"
                f"出图将用这些图做画风锚定，文本画师串本次让位。{tip}"
            )
            return True, f"选画风图 -> {names}", 1

        # unknown
        hint = f"无法识别：{('、'.join(unknown))}\n" if unknown else ""
        await self.send_text(
            f"{hint}可选画风图：\n{nai_vibe_refs.vibe_images_help()}\n\n"
            "用法：/nai art photo <序号...>；/nai art photo off 取消"
        )
        return False, f"未知画风图选择 {unknown}", 1


class NaiArtPhotoSaveCommand(BaseCommand):
    """存画风参考图：/nai art photo save <名字> + 引用/附带一张图 → 落盘 reference_images/。

    带图消息：框架用 processed_plain_text 匹配，图占位符会污染该文本且常排命令词前，
    故 pattern 宽松（容前后占位符、不锚定首尾），参 nai_retag 反推命令的修复。
    """

    command_name = "nai_art_photo_save"
    command_description = "存一张画风参考图（/nai art photo save <名字> + 引用图）"
    command_pattern = r"^.*?/nai\s+art\s+photo\s+save(?:\s+(?P<name>[^\[\]]+?))?(?:\s|\[|$)"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        name = (self.matched_groups.get("name") or "").strip()
        if not name:
            await self.send_text("用法：/nai art photo save <名字>，并引用一张图片或随消息附带图片。")
            return True, "画风图 save 缺名字", 1

        image_b64 = _extract_image_from_message(self.message)
        if not image_b64:
            await self.send_text(
                f"没找到要存的图片。请在发「/nai art photo save {name}」时附带图片，或引用一张图片消息。"
            )
            return True, "画风图 save 无图", 1

        saved_name = nai_vibe_refs.save_reference_image(name, image_b64)
        if not saved_name:
            await self.send_text("存图失败（落盘出错），稍后再试。")
            return True, "画风图 save 落盘失败", 1

        # 扫文件夹模式：落盘即入库，立即可选，无需改 config / 重启
        images = nai_vibe_refs.list_vibe_images()
        idx = next((i + 1 for i, im in enumerate(images) if im["name"] == saved_name), None)
        idx_tip = f"（序号 {idx}）" if idx else ""
        await self.send_text(
            f"✅ 画风图已存入图库：「{saved_name}」{idx_tip}\n"
            f"立即可用：/nai art photo {idx or saved_name} 选中它出图。\n"
            f"当前图库共 {len(images)} 张，/nai art photo 查看全部。"
        )
        return True, f"存画风图 {saved_name}", 1


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


class NaiDescribeCommand(BaseCommand):
    """描述出图：/nai 描述 <文本> —— 把用户给的描述当意图直接注入 NAI 链路。

    与 /nai 随机 同路：用户文本作 image_intent 灌进 director 路径，走 translater 提炼
    + Danbooru tag 检索 + 包装层，只是意图来自用户而非随机 nudge。质量词/画师串/
    画风图/负面词/尺寸照常套上（/nai art、/nai size 切）。
    """

    command_name = "nai_describe"
    command_description = "按文字描述出图（描述当意图走 LLM 大脑）"
    command_pattern = r"^/nai\s+描述(?:\s+(?P<intent>.+))?$"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        intent = (self.matched_groups.get("intent") or "").strip()
        if not intent:
            await self.send_text(
                "用法：/nai 描述 <你想画的内容>\n"
                "示例：/nai 描述 黄昏的海边，少女撑伞回头\n"
                "（描述交给 LLM 大脑提炼成画面，自动套质量词/画师串/负面词/尺寸；\n"
                "想给原始英文 tag 直发请用 /nai0。）"
            )
            return True, "查看 /nai 描述 用法", 1

        logger.info(f"[nai_describe] intent={intent!r}")
        return await _run_nai_draw(self, action_inputs={"image_intent": intent}, log_label="nai_describe")


# /nai 反推 重绘 命令 pattern（提为模块常量，与 nai_retag_command.NAI_RETAG_PATTERN 互斥：
# 那条用 (?!\s+重绘) 让位，框架命令分发只取首个匹配，两者必须互斥）。
NAI_RETAG_REDRAW_PATTERN = r"^.*?/nai\s+反推\s+重绘(?:\s|\[|$)"


class NaiRetagRedrawCommand(BaseCommand):
    """反推重绘：/nai 反推 重绘 + 一张图 —— 反推出 Danbooru tag，暴露提示词后用它直发出图。

    = /nai 反推（取图 → PNG 元数据 / WD14 反推）+ /nai0（反推 tag 当 nai_raw_tags 直发，
    跳过 director、套质量词/画师串/负面词/尺寸）。命中后先把反推提示词发出来（透明可复制），再出图。

    带图消息的 processed_plain_text 含图占位符，故 pattern 宽松（与 /nai 反推 同策略）；
    与 NaiRetagCommand 互斥——那条 pattern 用 (?!\\s+重绘) 把「重绘」让给这里（框架命令分发只取首个匹配）。
    """

    command_name = "nai_retag_redraw"
    command_description = "反推图片提示词并用它直接重绘（/nai 反推 重绘 + 引用/附带一张图）"
    command_pattern = NAI_RETAG_REDRAW_PATTERN

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        retag_cfg = self.get_config("retag", {}) or {}
        if not isinstance(retag_cfg, dict):
            retag_cfg = {}
        if not bool(retag_cfg.get("enabled", True)):
            await self.send_text("反推功能未启用（可在 config.toml 的 [retag] 开启 enabled）。")
            return True, "反推重绘 未启用", 1

        # 取图（命令自带图 / 引用回复的图），与 /nai 反推 同源
        image_base64 = _extract_image_from_message(self.message)
        if not image_base64:
            await self.send_text(
                "没找到要反推重绘的图片。\n"
                "用法：发「/nai 反推 重绘」时附带一张图片，或引用一张图片消息再发「/nai 反推 重绘」。"
            )
            return True, "反推重绘 无图片", 1

        try:
            image_bytes = base64.b64decode(image_base64.split(",", 1)[-1])
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[nai_retag_redraw] 图片 base64 解码失败: {exc}")
            await self.send_text("图片数据解析失败，请换一张图再试。")
            return True, "反推重绘 解码失败", 1

        wd14_enabled = bool(retag_cfg.get("wd14_enabled", True))
        service = build_reverse_service(retag_cfg, wd14_enabled)
        try:
            result = await service.reverse(image_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[nai_retag_redraw] 反推异常: {exc}\n{traceback.format_exc()}")
            await self.send_text("反推过程出错了，请稍后再试。")
            return True, "反推重绘 异常", 1

        logger.info(f"[nai_retag_redraw] source={result.source}, prompt={short_repr(result.prompt)}")

        if result.source not in ("metadata", "wd14") or not str(result.prompt or "").strip():
            # 反推失败：给与 /nai 反推 一致的友好提示，不出图
            await self.send_text(build_failed_message(result.detail, wd14_enabled))
            return True, f"反推重绘 反推失败({result.detail})", 1

        tags = result.prompt.strip()
        source_label = "图片自带元数据" if result.source == "metadata" else "WD14 反推"
        # 先暴露反推提示词（透明、可复制），再用它直发出图
        await self.send_text(
            f"🔁 反推重绘 | 来源：{source_label}\n反推提示词：\n{tags}\n（用它直接出图中…）"
        )

        # nai_raw_tags 直发：复用 /nai0 同款链路（跳过 director，套质量词/画师串/负面词/尺寸）
        return await _run_nai_draw(self, action_inputs={"nai_raw_tags": tags}, log_label="nai_retag_redraw")
