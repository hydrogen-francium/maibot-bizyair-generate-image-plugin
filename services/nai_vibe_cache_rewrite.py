# -*- coding: utf-8 -*-
"""Vibe cache 与 content_json 的协同纯逻辑（NewAPI §20.3.1 / §20.3.2）。

bizyair 的 controlnet 字段已由 builder 组装进 content_json 字符串，故 vibe cache 的
"发请求前查缓存改写 / 响应后落库 / stale 清理"在 client 层对 content_json 字符串做。
本模块把这套语义抽成纯函数（除注入的 SQLite service 外无副作用），便于单测；
client 只负责编排调用。移植 nai_draw nai_web_client 的 _apply_vibe_cache_to_controlnet /
_persist_vibe_cache / _looks_like_stale_vibe_cache_error / _purge_vibe_cache_hits 语义。

**失败安全铁律**：vibe cache 是纯计费优化，任何环节出错都绝不能阻断出图——改写/落库/解析
失败一律降级（原样发字节、跳过落库），仅损失"省 1 anlas"。
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.common.logger import get_logger
from .nai_vibe_cache import (
    VibeCacheService,
    compute_image_hash,
    get_vibe_cache_service,
    quantize_info_extracted,
)

logger = get_logger("bizyair_generate_image_plugin")

# §20.3.1 vibe_cache_ids 注释：<!-- vibe_cache_ids:[{"index":0,"cache_id":"..."}] -->
_VIBE_CACHE_COMMENT_PATTERN = re.compile(r"<!--\s*vibe_cache_ids:\s*(\[.*?])\s*-->")

# §20.3.1 网关错误文案不固定，用 cache_id 关键字 + 这些 stale 标记词做模糊匹配
_STALE_MARKERS = (
    "not found", "invalid", "expired", "unknown", "missing",
    "找不到", "未命中", "无效", "过期", "不存在",
)


def extract_vibe_cache_ids(content: str) -> list[dict[str, Any]]:
    """解析响应 message.content 里的 vibe_cache_ids 注释 → [{"index": int, "cache_id": str}, ...]。

    无注释 / 解析失败 / 结构非法时返回空列表。
    """
    if not content:
        return []
    match = _VIBE_CACHE_COMMENT_PATTERN.search(content)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(1))
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        cache_id = str(item.get("cache_id") or "").strip()
        # index 必须是真整数（排除 bool），cache_id 非空
        if not isinstance(index, int) or isinstance(index, bool) or not cache_id:
            continue
        cleaned.append({"index": index, "cache_id": cache_id})
    return cleaned


def rewrite_content_json_for_vibe_cache(
        content_json: str,
        *,
        model: str,
        service: VibeCacheService | None = None,
        log_prefix: str = "",
) -> tuple[str, list[tuple[int, str, float]], list[tuple[str, float]]]:
    """发请求前：对 content_json 的 controlnet.images 查本地缓存，命中改写为 cache_id 复用态。

    返回 ``(effective_content_json, persist_plan, hit_plan)``：
    - ``persist_plan``：``[(index, image_hash, info_extracted)]``，未命中、本次将发字节的条目，供响应后落库。
    - ``hit_plan``：``[(image_hash, info_extracted)]``，命中并改写为 cache_id 的条目，供 stale 时清理。

    无 controlnet / 解析失败 / 无可改写项 → 原样返回 content_json + 空 plan。失败安全：绝不抛错。
    """
    try:
        payload = json.loads(content_json)
    except (ValueError, TypeError):
        return content_json, [], []
    if not isinstance(payload, dict):
        return content_json, [], []
    controlnet = payload.get("controlnet")
    if not isinstance(controlnet, dict):
        return content_json, [], []
    raw_images = controlnet.get("images")
    if not isinstance(raw_images, list) or not raw_images:
        return content_json, [], []

    svc = service or get_vibe_cache_service()
    new_images: list[Any] = []
    persist_plan: list[tuple[int, str, float]] = []
    hit_plan: list[tuple[str, float]] = []

    for index, item in enumerate(raw_images):
        if not isinstance(item, dict):
            new_images.append(item)
            continue
        if item.get("cache_id"):  # 已是 cache_id 复用态：原样透传
            new_images.append(item)
            continue
        image_raw = item.get("image")
        if not isinstance(image_raw, str) or not image_raw.strip():
            new_images.append(item)
            continue
        image_hash = compute_image_hash(image_raw)
        info_extracted = quantize_info_extracted(item.get("info_extracted"))
        cached_id = svc.lookup(image_hash=image_hash, model_id=model, info_extracted=info_extracted) if image_hash else None
        if cached_id:
            replacement: dict[str, Any] = {"cache_id": cached_id}
            if item.get("strength") is not None:
                replacement["strength"] = item["strength"]
            new_images.append(replacement)
            hit_plan.append((image_hash, info_extracted))
            continue
        persist_plan.append((index, image_hash, info_extracted))
        new_images.append(item)

    if not hit_plan and not persist_plan:
        return content_json, [], []  # 全是已复用态 / 无有效图：不改写

    if hit_plan:
        total = len(raw_images)
        if not persist_plan:
            logger.info(f"{log_prefix}[vibe cache] 全量命中 {len(hit_plan)}/{total}：跳过编码并省 1 anlas 流量附加费（§20.3.2）")
        else:
            logger.info(f"{log_prefix}[vibe cache] 部分命中 {len(hit_plan)}/{total}：省命中图编码；仍含字节态条目，1 anlas 按请求计仍扣")

    controlnet = dict(controlnet)
    controlnet["images"] = new_images
    payload["controlnet"] = controlnet
    return json.dumps(payload, ensure_ascii=False), persist_plan, hit_plan


def persist_vibe_cache_ids(
        content: str,
        *,
        model: str,
        persist_plan: list[tuple[int, str, float]],
        service: VibeCacheService | None = None,
        log_prefix: str = "",
) -> int:
    """响应后：把 content 里 vibe_cache_ids 注释按 index 落库。返回落库条数。失败安全。"""
    if not persist_plan:
        return 0
    returned = extract_vibe_cache_ids(content)
    if not returned:
        return 0
    by_index = {entry["index"]: entry["cache_id"] for entry in returned}
    svc = service or get_vibe_cache_service()
    persisted = 0
    for index, image_hash, info_extracted in persist_plan:
        cache_id = by_index.get(index)
        if not cache_id:
            continue
        if svc.persist(image_hash=image_hash, model_id=model, info_extracted=info_extracted, cache_id=cache_id):
            persisted += 1
    if persisted:
        logger.info(f"{log_prefix}[vibe cache] 落库 {persisted} 条；下次同图同 info_extracted 可走 cache_id 复用态省 1 anlas")
    return persisted


def looks_like_stale_vibe_cache_error(error_message: str) -> bool:
    """启发式判断错误是否像 §20.3.1「cache_id 服务端找不到」的 400。

    用 ``cache_id`` 关键字 + stale 标记词模糊匹配；判错代价仅是多清一次本地缓存，
    比放着 stale cache_id 反复 400 更稳。
    """
    if not error_message:
        return False
    lowered = str(error_message).lower()
    if "cache_id" not in lowered and "cache id" not in lowered:
        return False
    return any(marker in lowered for marker in _STALE_MARKERS)


def purge_vibe_cache_hits(
        model: str,
        hit_plan: list[tuple[str, float]],
        *,
        service: VibeCacheService | None = None,
        log_prefix: str = "",
) -> int:
    """按 (image_hash, model, info_extracted) 清掉本地命中过、但服务端已失效的条目。返回删除条数。"""
    if not hit_plan:
        return 0
    svc = service or get_vibe_cache_service()
    deleted = 0
    for image_hash, info_extracted in hit_plan:
        if svc.delete(image_hash=image_hash, model_id=model, info_extracted=info_extracted):
            deleted += 1
    if deleted:
        logger.warning(f"{log_prefix}[vibe cache] 服务端 cache_id 已失效，清本地 {deleted} 条 stale 映射；下次请求重新编码落库")
    return deleted


__all__ = [
    "extract_vibe_cache_ids",
    "rewrite_content_json_for_vibe_cache",
    "persist_vibe_cache_ids",
    "looks_like_stale_vibe_cache_error",
    "purge_vibe_cache_hits",
]
