# -*- coding: utf-8 -*-
"""
会话态 continuity —— 上一轮提示词记忆（per-chat 内存 + TTL）

每次正常出图后记录 nai_director 生成的英文 tag 串；下次出图把它渲染成
<previous_prompt_context> 块（内含三档继承规则）喂回 nai_director，让大脑自判
本轮属于「微调 / 换角色保场景 / 全新主题」，自主决定继承多少——零显式指令。

移植自 nai_draw core/services/prompt_memory.py（render_previous_prompt_block 逐字）
+ core/services/session_state.py 的 last_nai_context 部分（get/set）。
**砍掉**：自拍专属锚点续承（_last_selfie_context）、JSON continuity 字段、allow_inherit 各种门控。

存储：模块级内存 dict，key = chat_id（群共享 / 私聊隔离由 chat_id 粒度天然实现），
重启丢失（短期续承可接受）。TTL 由调用方传入（config prompt_continuity.inherit_ttl）。
"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from src.common.logger import get_logger

logger = get_logger("bizyair_generate_image_plugin")

# key = chat_id；value = (prompt, request, timestamp)
_last_nai_context: Dict[str, Tuple[str, str, float]] = {}


def reset_prompt_memory() -> None:
    """清空所有会话的上一轮上下文（测试用）。"""
    _last_nai_context.clear()


def set_last_nai_context(chat_id: str, prompt: str, request: str = "") -> None:
    """记录某会话的上一轮 nai_director 输出（英文 tag 串）+ 用户本轮意图。

    prompt 空白则不记录（避免空串污染续承）。
    """
    if not chat_id:
        return
    if not isinstance(prompt, str) or not prompt.strip():
        return
    _last_nai_context[str(chat_id)] = (prompt.strip(), (request or "").strip(), time.time())


def get_last_nai_context(chat_id: str, ttl: float = 0) -> Tuple[Optional[str], Optional[str]]:
    """取某会话的上一轮 (prompt, request)；无数据或超 ttl 过期 → (None, None)。

    ttl <= 0 表示永不过期；过期条目顺手清除。
    """
    if not chat_id:
        return None, None
    entry = _last_nai_context.get(str(chat_id))
    if entry is None:
        return None, None
    prompt, request, ts = entry
    if ttl and ttl > 0 and (time.time() - ts) > ttl:
        _last_nai_context.pop(str(chat_id), None)
        return None, None
    return prompt, (request or None)


def render_previous_prompt_block(
    last_prompt: Optional[str],
    last_request: Optional[str] = None,
) -> str:
    """渲染 {previous_prompt_context} 块：有上一轮 → 带三档继承规则；无 → 占位引导全新生成。

    逐字移植 nai_draw core/services/prompt_memory.py。
    """
    previous = (last_prompt or "").strip()
    if not previous:
        return (
            "<previous_prompt_context>\n"
            "（无上一轮提示词，请完全按照本次用户请求生成全新提示词）\n"
            "</previous_prompt_context>"
        )

    # 可选注入上一轮用户请求（帮助 LLM 做 diff 推理）
    request_section = ""
    req = (last_request or "").strip()
    if req:
        request_section = f"\n【上一轮用户请求】\n{req}\n"

    parts = [
        "<previous_prompt_context>\n",
        "【上一轮 LLM 生成的提示词（系统注入，非用户输入的英文tag；仅供「续画」时参考）】\n",
        previous, "\n",
        request_section, "\n",
        "【继承规则（必须严格遵守，默认全新）】\n",
        "本轮出图大多由用户的新消息自动触发，**默认与上一轮无关**。请先判断本次用户请求是否为「明确的续画指令」：\n\n",
        "仅当本次请求里出现明确的续画词时（如「再来一张」「换个姿势」「接着上面」「还是这个/这身/这套」「同样的角色再画」），才视为续画：\n",
        "   → 以上方提示词为底稿，仅修改用户本轮明确要求变更的部分，保留其余标签\n\n",
        "**否则（绝大多数情况）一律视为全新主题**：\n",
        "   → **完全忽略上方提示词**，严格按本次用户请求 + 聊天上下文重新生成，不要保留上一轮的角色、动作、服装、场景\n",
        "</previous_prompt_context>",
    ]
    return "".join(parts)
