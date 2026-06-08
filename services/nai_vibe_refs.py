# -*- coding: utf-8 -*-
"""画风参考图库（多图 Vibe Transfer 画风锚定）。

与文本画师串（nai_settings 的 nai_artist）互斥：选了画风图，出图时用图做画风、本次忽略画师串。
- 预设来源：config 的 ``[[nai_chat_client.nai_vibe_presets]]``（name + path + 可选 info_extracted/strength）。
- 选中态：``bizyair_generate_image_plugin.nai_vibe_refs``（逗号分隔的预设名，全局，写回 config）。
- 多选：``/nai art photo 1 2`` 选第 1、2 张组合（最多 4 张，§20.3 controlnet 上限）。
- 存图：引用图 base64 → 落盘 reference_images/ → 追加一条 nai_vibe_presets。

纯逻辑 + 文件 IO，可单测（命令本身框架 mock 无法实例化）。base64 铁律：图只进出图通路。
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any, Optional

from src.common.logger import get_logger

logger = get_logger("bizyair_generate_image_plugin")

SETTINGS_SECTION = "bizyair_generate_image_plugin"
NAI_VIBE_REFS_KEY = "nai_vibe_refs"      # str：选中的画风图预设名，逗号分隔；空 = 不挂画风图
PRESETS_PATH = "nai_chat_client.nai_vibe_presets"

MAX_VIBE_IMAGES = 4                       # §20.3 controlnet.images 上限
_CLEAR_TOKENS = frozenset({"off", "none", "清空", "取消", "关"})
# 文件名安全化：中文/字母/数字/下划线连字符保留，其余替换为下划线
_NAME_SANITIZE_RE = re.compile(r"[^\w一-鿿\-]")


def _plugin_root() -> str:
    """services/ 上一级 = 插件根目录。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def normalize_vibe_presets(raw_presets: Any) -> list[dict[str, Any]]:
    """规整 config 的 nai_vibe_presets → [{name, path, info_extracted?, strength?}, ...]，跳过无效条目。"""
    result: list[dict[str, Any]] = []
    if not isinstance(raw_presets, list):
        return result
    for item in raw_presets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        path = str(item.get("path", "") or "").strip()
        if not name or not path:
            continue
        entry: dict[str, Any] = {"name": name, "path": path}
        for k in ("info_extracted", "strength"):
            if item.get(k) is not None:
                entry[k] = item[k]
        result.append(entry)
    return result


def resolve_photo_selection(args: list[str], raw_presets: Any) -> tuple[str, list[dict[str, Any]], list[str]]:
    """解析 /nai art photo 的多选参数。

    :return: (status, chosen, unknown)
        - status ∈ {"clear","set","unknown","empty"}
        - chosen：命中的预设条目列表（set 时非空）
        - unknown：无法识别的 token 列表
    """
    tokens = [t.strip() for t in (args or []) if str(t).strip()]
    if not tokens:
        return ("empty", [], [])
    if len(tokens) == 1 and tokens[0].lower() in _CLEAR_TOKENS:
        return ("clear", [], [])

    presets = normalize_vibe_presets(raw_presets)
    chosen: list[dict[str, Any]] = []
    unknown: list[str] = []
    seen_names: set[str] = set()
    for tok in tokens:
        hit: Optional[dict[str, Any]] = None
        if tok.isdigit():
            idx = int(tok)
            if 1 <= idx <= len(presets):
                hit = presets[idx - 1]
        else:
            for p in presets:
                if p["name"].lower() == tok.lower():
                    hit = p
                    break
        if hit is None:
            unknown.append(tok)
        elif hit["name"] not in seen_names:
            seen_names.add(hit["name"])
            chosen.append(hit)
    if unknown or not chosen:
        return ("unknown", chosen, unknown)
    return ("set", chosen[:MAX_VIBE_IMAGES], [])


def vibe_presets_help(raw_presets: Any) -> str:
    """画风图预设的可读清单（带 1 基序号）。"""
    presets = normalize_vibe_presets(raw_presets)
    if not presets:
        return "  （未配置；用 /nai art photo save <名字> 引用图存入，或在 config 加 [[nai_chat_client.nai_vibe_presets]]）"
    return "\n".join(f"  {i + 1}. {p['name']}" for i, p in enumerate(presets))


def selected_names(nai_vibe_refs: str) -> list[str]:
    """把选中态字符串（逗号分隔）拆成预设名列表。"""
    return [n.strip() for n in str(nai_vibe_refs or "").split(",") if n.strip()]


def load_selected_images_base64(nai_vibe_refs: str, raw_presets: Any) -> list[dict[str, Any]]:
    """按选中态读出各图 base64 + 参数 → [{image, info_extracted?, strength?}, ...]。

    读图失败的条目跳过（失败安全）。返回空列表表示没有可用画风图（调用方据此回退）。
    """
    names = selected_names(nai_vibe_refs)
    if not names:
        return []
    by_name = {p["name"]: p for p in normalize_vibe_presets(raw_presets)}
    root = _plugin_root()
    out: list[dict[str, Any]] = []
    for name in names[:MAX_VIBE_IMAGES]:
        preset = by_name.get(name)
        if not preset:
            logger.warning(f"[画风图] 选中的预设 {name!r} 在 config 里找不到，跳过")
            continue
        path = preset["path"]
        abs_path = path if os.path.isabs(path) else os.path.join(root, path)
        try:
            with open(abs_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except Exception as exc:
            logger.warning(f"[画风图] 读取 {name!r} 的图 {path!r} 失败，跳过: {exc}")
            continue
        entry: dict[str, Any] = {"image": b64}
        for k in ("info_extracted", "strength"):
            if preset.get(k) is not None:
                entry[k] = preset[k]
        out.append(entry)
    return out


def save_reference_image(name: str, image_base64: str, *, ext: str = "png") -> Optional[str]:
    """把引用图 base64 落盘到 reference_images/<安全名>.<ext>，返回相对插件根的路径；失败返回 None。"""
    safe = _NAME_SANITIZE_RE.sub("_", str(name or "").strip()) or "ref"
    rel = f"reference_images/{safe}.{ext}"
    abs_path = os.path.join(_plugin_root(), rel)
    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        raw = base64.b64decode(image_base64.split(",", 1)[-1])
        with open(abs_path, "wb") as f:
            f.write(raw)
        logger.info(f"[画风图] 已存图: {rel}（{len(raw)} bytes）")
        return rel
    except Exception as exc:
        logger.warning(f"[画风图] 存图失败 name={name!r}: {exc}")
        return None


def save_selection(value: str) -> bool:
    """把选中态 nai_vibe_refs 写回 config 的 plugin section（标量，安全）；失败返回 False。"""
    try:
        from src.common.toml_utils import save_toml_with_format

        save_toml_with_format(
            {SETTINGS_SECTION: {NAI_VIBE_REFS_KEY: value}},
            os.path.join(_plugin_root(), "config.toml"),
            preserve_comments=True,
        )
        return True
    except Exception as exc:
        logger.warning(f"[画风图] 写回选中态 {value!r} 失败: {exc}")
        return False


__all__ = [
    "NAI_VIBE_REFS_KEY",
    "MAX_VIBE_IMAGES",
    "normalize_vibe_presets",
    "resolve_photo_selection",
    "vibe_presets_help",
    "selected_names",
    "load_selected_images_base64",
    "save_reference_image",
    "save_selection",
]
