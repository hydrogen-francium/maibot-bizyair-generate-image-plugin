# -*- coding: utf-8 -*-
"""NAI 运行时设置（全局配置式）。

P1 的运行时切换命令（/nai set|nsfw 等）遵循 bizyair 既有范式：**全局生效 + 写回 config.toml**，
不引入 per-session 状态层。落盘只改 ``bizyair_generate_image_plugin`` section 下的**标量字段**——
因为 ``save_toml_with_format`` 对 array-of-tables（如 custom_variables）会整体替换、摧毁注释，
只有顶层 section 下的标量能安全原地更新（与 /dr use 同一套机制）。

本模块只放"标量层"设置（模型覆盖、NSFW 开关、尺寸/画师代号映射常量 + 写回 helper）。
画师串 / 尺寸要进 prompt/size 字段、必须经变量系统，由 Action 注入实现，不在这里落盘。
"""

from __future__ import annotations

import os
from typing import Any

from src.common.logger import get_logger

logger = get_logger("bizyair_generate_image_plugin")

# 运行时可调标量所在的配置 section（与 active_preset 同 section）
SETTINGS_SECTION = "bizyair_generate_image_plugin"

# --- 标量 key（命令写、Action 读，均走类属性载体保证立即生效） ---
NAI_MODEL_KEY = "nai_model"            # str：覆盖当前 NAI 预设的 model；空 = 用预设原值
NAI_SFW_FILTER_KEY = "nai_sfw_filter"  # bool：SFW 过滤开关（剔除 bikini/cleavage 等擦边 tag）
NAI_ARTIST_KEY = "nai_artist"          # str：已解析的画师串（/nai art 选定预设后存全名）；空 = 不注入
NAI_SIZE_KEY = "nai_size"              # str：尺寸代号 v/h/s/auto（auto = 跟随画面比例）

# 模型代号 → NewAPI 模型全名（对齐 nai_draw /nai set 代号 + API 接入文档）
MODEL_ALIASES: dict[str, str] = {
    "3": "nai-diffusion-3",
    "f3": "nai-diffusion-furry-3",
    "4c": "nai-diffusion-4-curated",
    "4": "nai-diffusion-4-full",
    "4.5c": "nai-diffusion-4-5-curated",
    "4.5": "nai-diffusion-4-5-full",
}

# 尺寸代号体系：命令只存「规范代号」(v/h/s/auto)；具体像素映射放在 config 的 nai_size dict 变量里
# （code → [宽, 高]），保持"尽量配置实现"。Action 注入 nai_size_code 作为该 dict 的 source。
# v=竖 / h=横 / s=方 / auto=跟随画面 aspect_ratio。各种别名（中英）都归一到这 4 个规范代号。
SIZE_CODE_ALIASES: dict[str, str] = {
    "v": "v", "竖": "v", "vertical": "v", "portrait": "v", "竖图": "v",
    "h": "h", "横": "h", "horizontal": "h", "landscape": "h", "横图": "h",
    "s": "s", "方": "s", "square": "s", "方图": "s",
    "auto": "auto", "自动": "auto", "跟随": "auto",
}

# 规范代号 → 人类可读标签（仅用于命令回显/帮助；像素值以 config 的 nai_size dict 为准）
_SIZE_CODE_LABELS: dict[str, str] = {
    "v": "832x1216(竖)",
    "h": "1216x832(横)",
    "s": "1024x1024(方)",
    "auto": "跟随画面比例",
}

# aspect_ratio → 尺寸代号（auto 档的推导；与 config 里 nai_size dict 的语义对齐，缺省走竖图）
ASPECT_RATIO_TO_SIZE_CODE: dict[str, str] = {
    "1:1": "s",
    "4:3": "h",
    "16:9": "h",
    "9:16": "v",
    "auto": "v",
}


def resolve_model_alias(code: str) -> str | None:
    """把模型代号或全名解析为模型全名；无法识别返回 None。"""
    candidate = (code or "").strip()
    if not candidate:
        return None
    if candidate in MODEL_ALIASES:
        return MODEL_ALIASES[candidate]
    if candidate in MODEL_ALIASES.values():  # 传入的已经是全名
        return candidate
    return None


def model_alias_help() -> str:
    """生成 `代号=全名` 的可读帮助行。"""
    return "  ".join(f"{code}={name}" for code, name in MODEL_ALIASES.items())


def resolve_nai_preset_name(active_preset: str, nai_presets: Any) -> str | None:
    """选定 /nai0、/nai 随机 等 NAI 专属命令使用的 NAI 预设名（纯逻辑、可单测）。

    - 当前 active_preset 本身就是某个 NAI 预设 → 用它（尊重用户已切到的 NAI 预设；
      /nai set 的 model 覆盖也会在核心里叠加生效）。
    - 否则（当前在 GPT 预设、或 active_preset 不在 NAI 预设里）→ 回落到**首个** NAI 预设，
      使这些命令始终是「NAI 专属」语义（与 nai_draw 一致），不受当前 GPT 激活态影响。
    - 一个 NAI 预设都没配 → None（命令据此提示用户去配 [[nai_chat_client.presets]]）。
    """
    names: list[str] = []
    if isinstance(nai_presets, list):
        for preset in nai_presets:
            if isinstance(preset, dict):
                name = str(preset.get("preset_name", "")).strip()
                if name:
                    names.append(name)
    active = (active_preset or "").strip()
    if active in names:
        return active
    return names[0] if names else None


def plugin_config_path() -> str:
    """services/ 的上一级即插件根目录，config.toml 在那里。"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.toml")


def save_setting(key: str, value: Any) -> bool:
    """把单个标量写回 config.toml 的 plugin section（保留注释）；失败返回 False。"""
    try:
        from src.common.toml_utils import save_toml_with_format

        save_toml_with_format(
            {SETTINGS_SECTION: {key: value}},
            plugin_config_path(),
            preserve_comments=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — 写盘失败不应中断命令，本次仍靠类属性生效
        logger.warning(f"[nai_settings] 写回 {key}={value!r} 失败: {exc}")
        return False


# ──────────────── 尺寸代号（/nai size） ────────────────

def resolve_size_alias(code: str) -> str | None:
    """把任意尺寸别名（中英）归一到规范代号 v/h/s/auto；无法识别返回 None。"""
    candidate = (code or "").strip().lower()
    if not candidate:
        return None
    return SIZE_CODE_ALIASES.get(candidate)


def aspect_ratio_to_size_code(aspect_ratio: Any) -> str:
    """auto 档：按画面比例推导尺寸代号；未知比例缺省走竖图（与旧 nai_size fallback 一致）。"""
    key = str(aspect_ratio or "").strip().lower()
    return ASPECT_RATIO_TO_SIZE_CODE.get(key, "v")


def resolve_size_code(size_override: str, aspect_ratio: Any) -> str:
    """命令层 override 优先（v/h/s）；为 auto/空时回落到按 aspect_ratio 推导。

    这是 Action 注入 nai_size_code 时调用的唯一桥接函数——纯函数、可单测。
    """
    normalized = resolve_size_alias(size_override or "")
    if normalized in ("v", "h", "s"):
        return normalized
    # auto / 不可识别 → 跟随画面比例
    return aspect_ratio_to_size_code(aspect_ratio)


def size_alias_help() -> str:
    """生成 `代号=标签` 的可读帮助行。"""
    return "  ".join(f"{code}={label}" for code, label in _SIZE_CODE_LABELS.items())


# ──────────────── 画师串预设（/nai art） ────────────────

# 取消画师串的关键词（大小写不敏感；中文无大小写）
ARTIST_CLEAR_TOKENS = {"off", "clear", "none", "取消", "关闭", "无"}


def normalize_artist_presets(raw: Any) -> list[dict]:
    """把 config 的 nai_artist_presets 规范为 [{name, prompt}]，过滤掉缺名/缺串的非法项。"""
    presets: list[dict] = []
    if not isinstance(raw, list):
        return presets
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if name and prompt:
            presets.append({"name": name, "prompt": prompt})
    return presets


def resolve_artist_choice(arg: str, raw_presets: Any) -> tuple[str, str, str]:
    """解析 /nai art 的参数。

    :return: (status, name, prompt)；status ∈ {"clear", "set", "unknown"}。
        - clear：用户要求取消画师串
        - set：命中预设（按 1 基序号或名称，名称大小写不敏感）
        - unknown：无法识别
    """
    candidate = (arg or "").strip()
    if not candidate:
        return ("unknown", "", "")
    if candidate.lower() in ARTIST_CLEAR_TOKENS:
        return ("clear", "", "")

    presets = normalize_artist_presets(raw_presets)
    # 1 基序号
    if candidate.isdigit():
        index = int(candidate)
        if 1 <= index <= len(presets):
            chosen = presets[index - 1]
            return ("set", chosen["name"], chosen["prompt"])
        return ("unknown", "", "")
    # 名称匹配（大小写不敏感）
    for preset in presets:
        if preset["name"].lower() == candidate.lower():
            return ("set", preset["name"], preset["prompt"])
    return ("unknown", "", "")


def artist_presets_help(raw_presets: Any) -> str:
    """生成画师预设的可读清单（带 1 基序号）。"""
    presets = normalize_artist_presets(raw_presets)
    if not presets:
        return "  （未配置；在 config.toml 的 [[nai_chat_client.nai_artist_presets]] 添加 name/prompt）"
    return "\n".join(f"  {i + 1}. {p['name']}" for i, p in enumerate(presets))
