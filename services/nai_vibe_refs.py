# -*- coding: utf-8 -*-
"""画风参考图库（多图 Vibe Transfer 画风锚定）——扫文件夹版。

与文本画师串（nai_settings 的 nai_artist）互斥：选了画风图，出图时用图做画风、本次忽略画师串。
- 图库来源：直接扫 ``reference_images/`` 文件夹（文件名去扩展名 = 预设名，按文件名排序编号）。
  丢图进文件夹 / 用 /nai art photo save 存图 → 立即可选，不碰 config、不重启。
- 选中态：``bizyair_generate_image_plugin.nai_vibe_refs``（逗号分隔的图名，全局，写回 config）。
- 多选：``/nai art photo 1 2`` 选第 1、2 张组合（最多 4 张，§20.3 controlnet 上限）。
- info_extracted / strength（画风迁移强度）用 config 全局默认键
  ``nai_chat_client.vibe_default_info_extracted`` / ``vibe_default_strength``（缺省回落常量）。

纯逻辑 + 文件 IO，可单测。base64 铁律：图只进出图通路。
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any, Optional

from src.common.logger import get_logger

logger = get_logger("bizyair_generate_image_plugin")

SETTINGS_SECTION = "bizyair_generate_image_plugin"
NAI_VIBE_REFS_KEY = "nai_vibe_refs"      # str：选中的画风图名，逗号分隔；空 = 不挂画风图
REFS_DIRNAME = "reference_images"        # 画风图库文件夹（相对插件根）

MAX_VIBE_IMAGES = 4                       # §20.3 controlnet.images 上限
_CLEAR_TOKENS = frozenset({"off", "none", "清空", "取消", "关"})
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
# 文件名安全化：中文/字母/数字/下划线连字符保留，其余替换为下划线
_NAME_SANITIZE_RE = re.compile(r"[^\w一-鿿\-]")

# info_extracted / strength 兜底常量（config 全局键缺省时用）
_DEFAULT_INFO_EXTRACTED = 0.7
_DEFAULT_STRENGTH = 0.6


def _plugin_root() -> str:
    """services/ 上一级 = 插件根目录。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _refs_dir() -> str:
    return os.path.join(_plugin_root(), REFS_DIRNAME)


def list_vibe_images() -> list[dict[str, str]]:
    """扫 reference_images/ → [{name(去扩展名), filename, path(相对插件根)}, ...]，按文件名排序。

    只认图片扩展名；.gitkeep 等非图自动忽略。文件夹不存在 / 读失败 → 空列表（失败安全）。
    """
    d = _refs_dir()
    out: list[dict[str, str]] = []
    try:
        names = sorted(os.listdir(d))
    except Exception:
        return out
    for fn in names:
        stem, ext = os.path.splitext(fn)
        if ext.lower() not in _IMAGE_EXTS or not stem:
            continue
        out.append({"name": stem, "filename": fn, "path": f"{REFS_DIRNAME}/{fn}"})
    return out


def resolve_photo_selection(args: list[str]) -> tuple[str, list[dict[str, str]], list[str]]:
    """解析 /nai art photo 的多选参数（基于扫文件夹结果）。

    :return: (status, chosen, unknown)
        - status ∈ {"clear","set","unknown","empty"}
        - chosen：命中的图条目列表（set 时非空）
        - unknown：无法识别的 token
    """
    tokens = [t.strip() for t in (args or []) if str(t).strip()]
    if not tokens:
        return ("empty", [], [])
    if len(tokens) == 1 and tokens[0].lower() in _CLEAR_TOKENS:
        return ("clear", [], [])

    images = list_vibe_images()
    chosen: list[dict[str, str]] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        hit: Optional[dict[str, str]] = None
        if tok.isdigit():
            idx = int(tok)
            if 1 <= idx <= len(images):
                hit = images[idx - 1]
        else:
            for img in images:
                if img["name"].lower() == tok.lower():
                    hit = img
                    break
        if hit is None:
            unknown.append(tok)
        elif hit["name"] not in seen:
            seen.add(hit["name"])
            chosen.append(hit)
    if unknown or not chosen:
        return ("unknown", chosen, unknown)
    return ("set", chosen[:MAX_VIBE_IMAGES], [])


def vibe_images_help() -> str:
    """画风图库的可读清单（带 1 基序号）。"""
    images = list_vibe_images()
    if not images:
        return "  （图库为空；用 /nai art photo save <名字> 引用图存入，或直接把图丢进 reference_images/ 文件夹）"
    return "\n".join(f"  {i + 1}. {img['name']}" for i, img in enumerate(images))


def selected_names(nai_vibe_refs: str) -> list[str]:
    """把选中态字符串（逗号分隔）拆成图名列表。"""
    return [n.strip() for n in str(nai_vibe_refs or "").split(",") if n.strip()]


def load_selected_images_base64(
    nai_vibe_refs: str,
    *,
    default_info_extracted: Any = None,
    default_strength: Any = None,
) -> list[dict[str, Any]]:
    """按选中态（图名）从文件夹读出各图 base64 + 全局默认强度 → [{image, info_extracted, strength}, ...]。

    读图失败 / 名字在文件夹找不到的条目跳过（失败安全）。返回空列表 = 没有可用画风图（调用方回退）。
    """
    names = selected_names(nai_vibe_refs)
    if not names:
        return []
    by_name = {img["name"]: img for img in list_vibe_images()}
    root = _plugin_root()
    try:
        ie = float(default_info_extracted) if default_info_extracted is not None else _DEFAULT_INFO_EXTRACTED
    except (TypeError, ValueError):
        ie = _DEFAULT_INFO_EXTRACTED
    try:
        st = float(default_strength) if default_strength is not None else _DEFAULT_STRENGTH
    except (TypeError, ValueError):
        st = _DEFAULT_STRENGTH
    out: list[dict[str, Any]] = []
    for name in names[:MAX_VIBE_IMAGES]:
        img = by_name.get(name)
        if not img:
            logger.warning(f"[画风图] 选中的 {name!r} 在 reference_images/ 找不到，跳过")
            continue
        abs_path = os.path.join(root, img["path"])
        try:
            with open(abs_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except Exception as exc:
            logger.warning(f"[画风图] 读取 {name!r}（{img['path']}）失败，跳过: {exc}")
            continue
        out.append({"image": b64, "info_extracted": ie, "strength": st})
    return out


def save_reference_image(name: str, image_base64: str, *, ext: str = "png") -> Optional[str]:
    """把引用图 base64 落盘到 reference_images/<安全名>.<ext>，返回去扩展名的图名（即可立即 /nai art photo 选）。失败返回 None。"""
    safe = _NAME_SANITIZE_RE.sub("_", str(name or "").strip()) or "ref"
    abs_path = os.path.join(_refs_dir(), f"{safe}.{ext}")
    try:
        os.makedirs(_refs_dir(), exist_ok=True)
        raw = base64.b64decode(image_base64.split(",", 1)[-1])
        with open(abs_path, "wb") as f:
            f.write(raw)
        logger.info(f"[画风图] 已存图: {REFS_DIRNAME}/{safe}.{ext}（{len(raw)} bytes）")
        return safe
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
    "list_vibe_images",
    "resolve_photo_selection",
    "vibe_images_help",
    "selected_names",
    "load_selected_images_base64",
    "save_reference_image",
    "save_selection",
]
