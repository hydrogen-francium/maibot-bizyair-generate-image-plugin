# -*- coding: utf-8 -*-
"""NAI 图片反推命令 /nai 反推（P5）。

把一张图反推成 Danbooru tag 串回给用户（复制后可自行 /nai0 重画）：
  Level 1  PNG 元数据（NAI/SD 自带 prompt，纯标准库、零网络、命中即返回）
  Level 2  WD14 在线 Space 兜底（软依赖 gradio_client，未装/失败自动降级）

取图复用现有「消息段递归取首图」逻辑（引用回复的图天然排最前 → 优先），
不依赖 Action 实例、不移植 nai_draw 的重型入站图缓存。

base64 铁律：图字节全程 short_repr 日志，绝不进任何文本 LLM。
"""

import base64
import traceback
from typing import Any, List, Optional, Tuple

from src.common.logger import get_logger
from src.plugin_system import BaseCommand

from ..services import nai_retag_reverser
from ..services import permission_manager
from ..services.log_utils import short_repr

logger = get_logger("bizyair_generate_image_plugin")


def _deny_if_no_permission(command: BaseCommand) -> Optional[str]:
    """统一命令权限检查，无权限时返回拒绝原因，有权限返回 None。"""
    user_id = command.message.message_info.user_info.user_id
    has_permission, deny_reason = permission_manager.check_command_permission(str(user_id))
    return None if has_permission else (deny_reason or "无权限")


def _walk_segments_for_image(segment_list: Optional[List[Any]]) -> Optional[str]:
    """递归遍历消息段列表，返回首张图片的无前缀 base64；引用消息段排最前，天然优先取引用图。"""
    if not segment_list:
        return None
    for segment in segment_list:
        seg_type = getattr(segment, "type", None)
        seg_data = getattr(segment, "data", None)
        if seg_type == "seglist":
            result = _walk_segments_for_image(seg_data)
            if result:
                return result
        elif seg_type in ("image", "emoji"):
            if seg_data and isinstance(seg_data, str):
                return seg_data
    return None


def extract_image_base64_from_message(message: Any) -> Optional[str]:
    """从命令消息里取首张图片 base64（含引用回复的图）。取不到返回 None。

    兜底多条取图路径，规避平台/框架字段差异：
      1) message.chat_stream.context.message.message_segment（与出图 Action 同源）
      2) message.message_segment（部分场景命令消息自带）
    """
    candidates: List[Any] = []

    chat_stream = getattr(message, "chat_stream", None)
    context = getattr(chat_stream, "context", None)
    ctx_message = getattr(context, "message", None)
    ctx_segment = getattr(ctx_message, "message_segment", None)
    if ctx_segment is not None:
        candidates.append(ctx_segment)

    own_segment = getattr(message, "message_segment", None)
    if own_segment is not None:
        candidates.append(own_segment)

    for root in candidates:
        if getattr(root, "type", None) == "seglist":
            segment_list = root.data
        else:
            segment_list = [root]
        result = _walk_segments_for_image(segment_list)
        if result:
            return result
    return None


def build_failed_message(detail: Optional[str], wd14_enabled: bool) -> str:
    """把 ReverseResult.detail 翻成给用户的友好提示。

    注意区分「WD14 服务级失败（Space 全挂/网络）」与「真没识别到」——前者不是图的问题，
    不能误导成「这张图可能不是 AI 生成」（WD14 本就是用来反推任意图含非 AI 图的）。
    """
    base = "没能反推出这张图的提示词。"
    hint = "这张图可能不是 AI 生成，或没有可读的元数据。"
    d = str(detail or "")
    if not wd14_enabled:
        return f"{base}图片没有自带提示词，且未开启 WD14 在线兜底。\n{hint}"
    if "gradio_client" in d:
        return (
            f"{base}图片没有自带提示词，WD14 在线兜底不可用"
            "（未安装 gradio_client，可执行 pip install gradio_client 启用）。\n"
            f"{hint}"
        )
    if "超时" in d:
        return f"{base}WD14 在线识别服务超时了（HF Space 冷启动较慢），稍后再试。"
    # WD14 服务级失败：Space 全连不上 / 调用异常 —— 是在线服务的问题，不是图的问题，别误导
    if ("所有 Spaces" in d) or ("无法使用" in d) or d.startswith("WD14 异常") or d.startswith("WD14:"):
        return f"{base}WD14 在线识别服务暂时不可用（免费 Space 可能挂了或在冷启动），稍后再试。"
    if "未识别到任何标签" in d:
        return f"{base}WD14 没能从这张图识别出有效内容，换张更清晰/主体明确的图再试。"
    # 真正的「无元数据且未走到 WD14」等其它情况，才提示可能非 AI 生成
    return f"{base}{hint}"


class NaiRetagCommand(BaseCommand):
    """图片反推：/nai 反推 —— 读图片自带 prompt（元数据），未命中走 WD14 兜底。"""

    command_name = "nai_retag"
    command_description = "把图片反推成 Danbooru tag（先读 PNG 元数据，未命中走 WD14 在线兜底）"
    # 注意：框架用 message.processed_plain_text 做命令匹配（src/chat/message_receive/bot.py），
    # 带图/引用图时该文本会被图占位符（如 [回复…的消息：…]、[picid:…]、[图片]）污染、且占位符常排在命令词前，
    # 故不能用 ^/nai\s+反推$ 严格锚定首尾（带图必匹配失败、命令不触发）。
    # 这里放宽：允许命令词前后有图占位符等内容，但「反推」后须接 空白/方括号/行尾，防 /nai 反推xxx 粘连误匹配。
    command_pattern = r"^.*?/nai\s+反推(?:\s|\[|$)"

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        deny = _deny_if_no_permission(self)
        if deny:
            return True, deny, 1

        retag_cfg = self.get_config("retag", {}) or {}
        if not isinstance(retag_cfg, dict):
            retag_cfg = {}
        if not bool(retag_cfg.get("enabled", True)):
            await self.send_text("反推功能未启用（可在 config.toml 的 [retag] 开启 enabled）。")
            return True, "retag 未启用", 1

        # ── 取图（命令自带图 / 引用回复的图）──
        image_base64 = extract_image_base64_from_message(self.message)
        if not image_base64:
            await self.send_text(
                "没找到要反推的图片。\n"
                "用法：发送「/nai 反推」时附带一张图片，或引用一张图片消息再发「/nai 反推」。"
            )
            return True, "retag 无图片", 1

        try:
            image_bytes = base64.b64decode(image_base64.split(",", 1)[-1])
        except Exception as exc:
            logger.warning(f"[nai_retag] 图片 base64 解码失败: {exc}")
            await self.send_text("图片数据解析失败，请换一张图再试。")
            return True, "retag 解码失败", 1

        wd14_enabled = bool(retag_cfg.get("wd14_enabled", True))
        service = self._build_service(retag_cfg, wd14_enabled)

        try:
            result = await service.reverse(image_bytes)
        except Exception as exc:
            logger.error(f"[nai_retag] 反推异常: {exc}\n{traceback.format_exc()}")
            await self.send_text("反推过程出错了，请稍后再试。")
            return True, "retag 异常", 1

        logger.info(f"[nai_retag] source={result.source}, prompt={short_repr(result.prompt)}")

        if result.source == "metadata":
            await self.send_text(f"📄 读取到图片自带提示词（NAI/SD 元数据）：\n{result.prompt}")
            return True, "retag 元数据命中", 1
        if result.source == "wd14":
            await self.send_text(f"🔍 WD14 反推结果（仅供参考）：\n{result.prompt}")
            return True, "retag WD14 命中", 1

        # failed：按 detail 给友好提示
        await self.send_text(build_failed_message(result.detail, wd14_enabled))
        return True, f"retag 失败({result.detail})", 1

    def _build_service(self, retag_cfg: dict, wd14_enabled: bool) -> "nai_retag_reverser.ReverseService":
        """按配置组装反推服务；WD14 客户端延迟构造（软依赖 gradio_client）。"""
        wd14_client = None
        if wd14_enabled:
            try:
                from ..clients.wd14_client import WD14Client

                spaces = retag_cfg.get("wd14_spaces") or None
                wd14_client = WD14Client(
                    timeout=float(retag_cfg.get("wd14_timeout", 60) or 60),
                    proxy=str(retag_cfg.get("wd14_proxy", "") or ""),
                    spaces_config=spaces,
                )
            except Exception as exc:
                logger.warning(f"[nai_retag] WD14 客户端构造失败，仅用元数据反推: {exc}")
                wd14_client = None

        return nai_retag_reverser.ReverseService(
            wd14_client=wd14_client,
            wd14_threshold=float(retag_cfg.get("wd14_threshold", 0.35) or 0.35),
            wd14_character_threshold=float(retag_cfg.get("wd14_character_threshold", 0.8) or 0.8),
            wd14_enabled=wd14_enabled,
        )
