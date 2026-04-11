from __future__ import annotations

from typing import Any


def short_repr(value: Any, limit: int = 160) -> str:
    """生成适合日志输出的简短文本表示。"""
    text = repr(value).replace("\n", "\\n")
    if len(text) > limit:
        return f"{text[:limit - 3]}..."
    return text
