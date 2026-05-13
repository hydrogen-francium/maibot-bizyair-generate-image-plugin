"""Prompt 内容审核兜底清洗器：用于 BizyAir 返回 422 / content_filter 时做一次保守重试。"""

from __future__ import annotations

import re
from typing import Iterable

# 直接删除的高危/未成年/露骨词（小写匹配，按完整词替换）
_DROP_TERMS: tuple[str, ...] = (
    "nude", "naked", "fully naked", "topless", "bottomless",
    "exposed nipples", "exposed nipple", "nipple", "nipples",
    "pussy", "vagina", "penis", "cum", "cumshot",
    "explicit sexual", "sex", "sexual", "sexually",
    "loli", "lolita", "shota", "shotacon",
    "child", "children", "kid", "kids", "minor", "underage", "preteen",
    "teen", "teens", "teenage", "teenager", "young girl", "young boy", "schoolgirl",
    "blood", "gore", "violent injury", "decapitation",
    "lingerie", "underwear", "bra", "panties", "thong", "g-string",
    "cleavage", "casual cleavage", "midriff", "bare midriff",
    "bikini", "swimsuit",
    "peeking through fingers", "peeking between fingers",
    "looking through gaps", "hands covering face while peeking",
)

# 替换为温和版本的词组
_REPLACE_MAP: tuple[tuple[str, str], ...] = (
    ("bare shoulders", "off-shoulder top"),
    ("bare back", "open-back top"),
    ("short shorts", "casual shorts"),
    ("off-shoulder", "loose collar"),
)


def sanitize_prompt_for_content_filter(prompt: str, max_chars: int = 900) -> str:
    """对 prompt 做一次保守清洗：删高危词、替换边缘词，保留主体；过长则按 tag 截短到 max_chars 以内。"""
    if not prompt:
        return prompt
    text = prompt
    for src, dst in _REPLACE_MAP:
        text = re.sub(rf"\b{re.escape(src)}\b", dst, text, flags=re.IGNORECASE)
    for term in _DROP_TERMS:
        text = re.sub(rf"\b{re.escape(term)}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*,\s*,+", ", ", text)
    text = re.sub(r"^[\s,]+", "", text)
    text = re.sub(r"[\s,]+$", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip()
    if max_chars and len(text) > max_chars:
        tags = [t.strip() for t in text.split(",") if t.strip()]
        out: list[str] = []
        running = 0
        for tag in tags:
            cost = len(tag) + (2 if out else 0)
            if running + cost > max_chars:
                break
            out.append(tag)
            running += cost
        text = ", ".join(out) if out else text[:max_chars].rstrip(", ")
    return text


def sanitize_input_values(input_values: dict, prompt_field_keys: Iterable[str] | None = None, max_chars: int = 900) -> dict:
    """对 input_values 里所有形如 *.prompt 的字段执行清洗，返回新字典。"""
    sanitized = dict(input_values)
    keys_to_clean = set(prompt_field_keys) if prompt_field_keys else set()
    for key in list(sanitized.keys()):
        if key in keys_to_clean or str(key).lower().endswith(".prompt") or key == "prompt":
            value = sanitized[key]
            if isinstance(value, str) and value.strip():
                sanitized[key] = sanitize_prompt_for_content_filter(value, max_chars=max_chars)
    return sanitized
