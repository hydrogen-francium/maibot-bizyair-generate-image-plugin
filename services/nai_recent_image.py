# -*- coding: utf-8 -*-
"""抓「群里最近的一张图」的 base64（供 bot 主动 i2i 用）。

复用框架已有图存储，不自建入站缓存：
  message_api.get_recent_messages → 逆序找最近含图消息（is_picid 且非表情包）
  → 从 processed_plain_text 抽 [picid:id] → Images 表查落盘 path → 读回 base64

失败安全：任何环节异常 / 找不到图 → None（调用方据此回退普通文生图）。
base64 铁律：图只在出图通路用，不进 director / 文本 LLM；本服务只返回 base64 给取图通道。
"""

import re
from typing import Optional

from src.common.logger import get_logger

logger = get_logger("bizyair_generate_image_plugin")

# processed_plain_text 里图片占位符形如 [picid:1f2e3d4c-....]（image_id 通常是 UUID，
# 放宽到「冒号后到右括号前的任意非括号字符」以兼容非标准 id）
_PICID_RE = re.compile(r"\[picid:([^\]]+)\]")


def _extract_image_id(text: str) -> Optional[str]:
    """从消息文本里抽第一个 picid。"""
    if not text:
        return None
    m = _PICID_RE.search(text)
    return m.group(1) if m else None


def _fetch_recent_messages(chat_id: str, lookback: int, hours: float) -> list:
    """取最近消息（延迟 import 框架 message_api）。抽出便于测试 monkeypatch。"""
    from src.plugin_system.apis import message_api
    return message_api.get_recent_messages(
        chat_id=chat_id, hours=hours, limit=max(1, int(lookback)), limit_mode="latest"
    ) or []


def _query_image_path(image_id: str) -> Optional[str]:
    """按 image_id 查落盘 path（延迟 import 框架 Images 表）。查不到返回 None。"""
    from src.common.database.database_model import Images
    try:
        record = Images.get(Images.image_id == image_id)
    except Exception:
        return None
    return getattr(record, "path", "") or None


def _read_image_base64(path: str) -> Optional[str]:
    """读落盘图为 base64（延迟 import 框架 image_path_to_base64）。"""
    from src.chat.utils.utils_image import image_path_to_base64
    return image_path_to_base64(path) or None


def get_recent_chat_image_base64(chat_id: str, *, lookback: int = 10, hours: float = 24.0) -> Optional[str]:
    """返回该聊天最近一张图片的无前缀 base64；找不到 / 失败 → None。

    Args:
        chat_id: 聊天流 id
        lookback: 往回最多看多少条消息
        hours: 时间窗（超过此时长的旧消息不看）
    """
    if not chat_id:
        return None
    try:
        messages = _fetch_recent_messages(chat_id, lookback, hours)
        if not messages:
            return None

        # get_recent_messages latest 模式按时间正序返回最新一批；逆序找最近的含图非表情消息
        for msg in reversed(messages):
            if not getattr(msg, "is_picid", False):
                continue
            if getattr(msg, "is_emoji", False):
                continue  # 排除表情包
            image_id = _extract_image_id(str(getattr(msg, "processed_plain_text", "") or ""))
            if not image_id:
                continue
            path = _query_image_path(image_id)
            if not path:
                continue  # 查不到落盘记录，继续找更早的
            b64 = _read_image_base64(path)
            if b64:
                logger.info(f"[i2i] 抓到群里最近图：image_id={image_id}")
                return b64
        return None
    except Exception as exc:
        logger.warning(f"[i2i] 抓群里最近图失败，已跳过: {exc}")
        return None


__all__ = ["get_recent_chat_image_base64"]
