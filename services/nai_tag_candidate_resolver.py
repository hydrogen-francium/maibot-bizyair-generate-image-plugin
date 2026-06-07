# -*- coding: utf-8 -*-
"""
Tag 候选检索调度（仅 online）

P4 只实现 online 检索（DanbooruSearchOnline API）。原 nai_draw 的 local embedding 路径
与「online 失败回退 local」均已砍掉——检索失败/超时/无结果一律返回空串降级，绝不阻断出图。

移植自 nai_draw_plugin core/services/tag_candidate_resolver.py（删 local 分支与回退）。
"""

from typing import Any, Dict

from src.common.logger import get_logger

from .nai_danbooru_online_retriever import get_online_retriever

logger = get_logger("bizyair_generate_image_plugin")


async def resolve_tag_candidates(
    retriever_config: Dict[str, Any],
    request_text: str,
    log_prefix: str = "",
) -> str:
    """根据 tag_retriever 配置返回候选标签文本块（仅 online）。

    Args:
        retriever_config: 插件配置中 ``tag_retriever`` 节点的内容
        request_text: 用户当次的中文描述（= image_intent）
        log_prefix: 日志前缀，沿用调用方上下文

    Returns:
        可注入 ``{tag_candidates}`` 的字符串；未启用 / 无结果 / 任何异常时返回空串（失败安全）
    """
    try:
        if not isinstance(retriever_config, dict) or not retriever_config.get("enabled", False):
            return ""
        if not request_text or not request_text.strip():
            return ""

        mode = str(retriever_config.get("mode", "online") or "online").strip().lower()
        if mode != "online":
            logger.info(
                f"{log_prefix} Tag 检索 mode={mode!r}，但本插件仅实现 online，按 online 处理"
            )

        logger.info(
            f"{log_prefix} Tag 检索已启用(online)，query='{request_text[:30]}'"
        )

        retriever = get_online_retriever(
            enabled=True,
            base_url=retriever_config.get("api_url", "https://sakizuki-danboorusearch.hf.space/api"),
            timeout=retriever_config.get("timeout", 90.0),
            search_limit=retriever_config.get("search_limit", 30),
            search_top_k=retriever_config.get("search_top_k", 5),
            related_limit=retriever_config.get("related_limit", 20),
            related_seed_count=retriever_config.get("related_seed_count", 8),
            show_nsfw=retriever_config.get("show_nsfw", True),
            popularity_weight=retriever_config.get("popularity_weight", 0.15),
            search_max_retries=retriever_config.get("search_max_retries", 3),
            search_retry_delay=retriever_config.get("search_retry_delay", 2.0),
        )
        if not retriever:
            return ""

        results = await retriever.retrieve(query=request_text)
        search_count = len(results.get("search", []))
        related_count = len(results.get("related", []))
        if search_count == 0 and related_count == 0:
            logger.info(f"{log_prefix} Tag 在线检索无结果，已跳过")
            return ""

        logger.info(
            f"{log_prefix} Tag 在线检索命中："
            f"search={search_count} related={related_count}"
        )
        return retriever.format_candidates(results)
    except Exception as exc:  # 失败安全：检索任何环节出错都降级为空串，绝不阻断出图
        logger.warning(f"{log_prefix} Tag 检索失败，已跳过: {exc}")
        return ""
