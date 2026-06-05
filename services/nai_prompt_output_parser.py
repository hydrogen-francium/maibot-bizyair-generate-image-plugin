# -*- coding: utf-8 -*-
"""NAI 多人输出解析（移植 nai_draw core/utils/prompt_output_parser.py 的多人子集）。

把 nai_director 大脑输出的「多人」内容稳定抽成 NewAPI `characters[]` 通道所需的结构：
``{"global_text": str, "characters": [{"prompt", "negative_prompt", "position"}], "has_coords": bool}``。

双路径（统一入口 :func:`resolve_multi_character_payload`，JSON 优先、文本兜底）：
- **JSON v3**：``{"version":3,"format":"multi","global":[...],"people":[[...],...],"positions":["B2",...]}``
  （支持 5×5 网格 position；P2 当前 director 走文本路径，但本通道一并移植好，
  将来想手动坐标只改 director 文案即可启用，无需返工）
- **文本**：``"global,\nchar1:p1,\nchar2:p2,"``（或旧版单行 ``|`` 分隔）——P2 默认路径，
  无 position（has_coords=False，交由后端按 use_order 自动布局）

解析为单人 / 失败时返回 None，调用方回退到原有单串通道。纯逻辑、可单测。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

# 5×5 网格坐标 [A-E][1-5]（NewAPI 多角色 position 字面量）
_POSITION_GRID_RE = re.compile(r"^[A-E][1-5]$")

# 拍平多人字符串的 charN: 前缀（兼容大小写、中英文冒号、可选空格）
_CHAR_PREFIX_RE = re.compile(r"^char\s*\d+\s*[:：]\s*", re.IGNORECASE)


def _join_tags(tags: Any) -> str:
    """把 tag 列表 join 成逗号串；非列表或空返回空串。"""
    if not tags or not isinstance(tags, list):
        return ""
    return ", ".join(t.strip() for t in tags if isinstance(t, str) and t.strip()).strip()


def _strip_code_fence(text: str) -> str:
    """去掉可能的 ```lang ... ``` 包裹（只做轻量处理）。"""
    s = (text or "").strip()
    if not (s.startswith("```") and s.endswith("```")):
        return s
    inner = s[3:-3].strip()
    if "\n" not in inner:
        return inner.strip()
    first_line, rest = inner.split("\n", 1)
    if first_line.strip().isalpha() and len(first_line.strip()) < 15:
        return rest.strip()
    return inner.strip()


def parse_structured_prompt_payload(text: str) -> Optional[Dict[str, Any]]:
    """从结构化输出中提取原始 JSON payload；失败返回 None。"""
    cleaned = _strip_code_fence(text).strip()
    if not cleaned:
        return None

    candidates = [cleaned]
    if any(token in cleaned for token in ('"prompt"', '"global"', '"people"')):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(cleaned[start:end + 1])

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        version = obj.get("version")
        has_v2_fields = isinstance(obj.get("global"), list)
        has_v1_prompt = isinstance(obj.get("prompt"), str) and obj.get("prompt", "").strip()
        if version == 2 or version == 3 or (isinstance(version, int) and version >= 2):
            if has_v2_fields or has_v1_prompt:
                return obj
            continue
        if has_v1_prompt:
            return obj
    return None


def extract_multi_character_payload(text: str) -> Optional[Dict[str, Any]]:
    """从 v3 multi JSON 抽出结构化角色 payload；非多人 / 人数 < 2 / 字段缺失返回 None。

    - 仅当 ``format == "multi"`` 且 ``people`` 至少 2 个非空角色才返回结构化结果
    - ``position`` 不匹配 ``[A-E][1-5]`` 时规整为 ``""``（不抛错，仅丢弃该坐标）
    - ``has_coords`` 为 True 当且仅当所有角色都有合法坐标，否则交由后端自动布局
    """
    obj = parse_structured_prompt_payload(text)
    if not obj:
        return None

    version = obj.get("version")
    if not (version == 2 or version == 3 or (isinstance(version, int) and version >= 2)):
        return None
    if str(obj.get("format", "") or "").strip().lower() != "multi":
        return None

    raw_people = obj.get("people", []) or []
    if not isinstance(raw_people, list):
        return None

    valid_people: List[List[str]] = []
    for person_tags in raw_people:
        if not isinstance(person_tags, list):
            continue
        person_line = [t.strip() for t in person_tags if isinstance(t, str) and t.strip()]
        if person_line:
            valid_people.append(person_line)

    if len(valid_people) < 2:
        return None

    global_text = _join_tags(obj.get("global"))
    if not global_text:
        return None

    raw_positions = obj.get("positions", []) or []
    if not isinstance(raw_positions, list):
        raw_positions = []

    characters: List[Dict[str, str]] = []
    normalized_positions: List[str] = []
    for index, tags in enumerate(valid_people):
        position = ""
        if index < len(raw_positions):
            candidate = str(raw_positions[index] or "").strip().upper()
            if _POSITION_GRID_RE.match(candidate):
                position = candidate
        normalized_positions.append(position)
        characters.append({"prompt": _join_tags(tags), "negative_prompt": "", "position": position})

    has_coords = bool(normalized_positions) and all(p for p in normalized_positions)
    return {"global_text": global_text, "characters": characters, "has_coords": has_coords}


def _split_multi_person_segments(text: str) -> List[str]:
    """把拍平的多人字符串切成 [global, char1, char2, ...] 段。

    支持多行 ``"global,\\nchar1:p1,\\nchar2:p2,"`` 与旧版单行 ``|`` 分隔两种形态。
    """
    stripped = (text or "").strip()
    if not stripped:
        return []
    if "\n" in stripped:
        return [seg.strip() for seg in stripped.split("\n") if seg.strip()]
    if "|" in stripped:
        return [seg.strip() for seg in stripped.split("|") if seg.strip()]
    return [stripped]


def extract_multi_character_payload_from_text(text: str) -> Optional[Dict[str, Any]]:
    """从拍平后的多人字符串反解出结构化角色 payload（P2 默认路径）。

    只要输出了 ``char1:/char2:`` 多段格式即进入 characters[] 通道。反解路径不带 position，
    故 ``has_coords`` 永远为 False（后端按 use_order 自动布局）。解析为单人 / 失败返回 None。
    """
    segments = _split_multi_person_segments(text)
    if len(segments) < 3:
        # 至少 1 段 global + 2 段角色才认为是多人
        return None

    global_text = segments[0].strip().rstrip(",").strip()
    if not global_text:
        return None

    characters: List[Dict[str, str]] = []
    for raw_segment in segments[1:]:
        cleaned = raw_segment.strip().lstrip("|").strip().rstrip(",").strip()
        cleaned = _CHAR_PREFIX_RE.sub("", cleaned).strip()
        if not cleaned:
            continue
        characters.append({"prompt": cleaned, "negative_prompt": "", "position": ""})

    if len(characters) < 2:
        return None

    return {"global_text": global_text, "characters": characters, "has_coords": False}


def resolve_multi_character_payload(
        raw_llm_response: str,
        rendered_text: str,
) -> Optional[Dict[str, Any]]:
    """统一入口：优先用 v3 JSON 抽取，失败时回退到从拍平文本反解。

    :param raw_llm_response: 可能是 JSON / JSON+噪声 / 纯文本任意一种
    :param rendered_text: 拍平后的最终字符串（含 char1:/char2:）
    :return: 结构化 payload；单人或都解析失败返回 None。
    """
    from_json = extract_multi_character_payload(raw_llm_response)
    if from_json is not None:
        return from_json
    return extract_multi_character_payload_from_text(rendered_text)
