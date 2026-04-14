from __future__ import annotations

from typing import Any


def resolve_active_preset(
        active_preset: str,
        bizyair_presets: Any,
        nai_presets: Any,
) -> dict[str, Any]:
    """从两个预设列表中查找 active_preset 对应的完整预设配置"""
    if not active_preset:
        raise ValueError("active_preset 不能为空，请在配置中设置 active_preset")

    resolved_matches: list[dict[str, Any]] = []
    for provider, presets, config_name in (
            ("bizyair_openapi", bizyair_presets, "bizyair_client.app_presets"),
            ("nai_chat", nai_presets, "nai_chat_client.presets"),
    ):
        if presets is None:
            continue
        if not isinstance(presets, list):
            raise ValueError(f"{config_name} 必须是列表")
        for index, preset in enumerate(presets):
            if not isinstance(preset, dict):
                raise ValueError(f"{config_name}[{index}] 必须是对象")
            name = str(preset.get("preset_name", "")).strip()
            if name == active_preset:
                resolved_matches.append({
                    "provider": provider,
                    "preset": preset,
                })

    if not resolved_matches:
        raise ValueError(f"未在 BizyAir 或 NAI 预设中找到 preset_name={active_preset!r}")
    if len(resolved_matches) > 1:
        raise ValueError(f"preset_name={active_preset!r} 在多个后端中重复，preset_name 必须全局唯一")
    return resolved_matches[0]