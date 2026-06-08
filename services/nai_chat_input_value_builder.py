from __future__ import annotations

import json
import re
from typing import Any

from src.common.logger import get_logger
from .action_parameter_utils import ActionParameterDefinition
from .builtin_variable_provider import BuiltinVariableProvider
from .nai_image_meta import normalize_image_base64, read_image_dimensions
from .nai_prompt_output_parser import resolve_multi_character_payload
from .nai_prompt_postprocess import (
    sanitize_sfw_characters,
    sanitize_sfw_prompt,
    strip_cjk_and_fullwidth,
    strip_cjk_and_fullwidth_from_characters,
)
from .openapi_input_value_builder import BizyAirOpenApiInputValueBuilder
from ..clients import BizyAirOpenApiParameterBinding

logger = get_logger("bizyair_generate_image_plugin")

# NewAPI §8：prompt / negative_prompt 必须英文，含 CJK 或全角符号一律 400。
# 这几个文本字段在 json.dumps 前强制做 CJK/全角清洗（LLM 偶发漏译，纯配置兜不住）。
_NAI_TEXT_FIELDS = ("prompt", "negative_prompt")

# 多角色（NewAPI §7）能力检测：仅 nai-diffusion-4 系列稳定支持 characters[] / use_coords。
# 子串匹配覆盖 nai-diffusion-4-full / -curated / -4-5-full / -4-5-curated（移植 nai_draw）。
_MULTI_CHARACTER_MODEL_KEYWORDS = ("nai-diffusion-4",)

# characters[i].position 5×5 网格字面量 [A-E][1-5]（NewAPI §7.2）。
_POSITION_GRID_RE = re.compile(r"^[A-E][1-5]$")

# NAI 标准尺寸上限（NewAPI §6）：宽高须 64 整除且不超下列上限。i2i 用原图真实尺寸覆盖外层
# size（本仓无 Pillow 不缩放），不合法即拒绝。
_NAI_MAX_PORTRAIT = (832, 1216)
_NAI_MAX_LANDSCAPE = (1216, 832)
_NAI_MAX_SQUARE = (1024, 1024)

# i2i strength / noise 取值区间（NewAPI §20.1）；越界 clamp，缺省用默认。
_I2I_STRENGTH_RANGE = (0.01, 0.99)
_I2I_NOISE_RANGE = (0.0, 0.99)
_I2I_STRENGTH_DEFAULT = 0.7
_I2I_NOISE_DEFAULT = 0.0

# Vibe Transfer（controlnet，NewAPI §20.3）取值区间；强兼单图：一张引用图组 controlnet.images[0]。
# §20.3 不限边长（服务端自行 resize），故不校验尺寸、不覆盖外层 size——与 i2i 的关键差异。
_VIBE_INFO_RANGE = (0.01, 1.0)
_VIBE_INFO_DEFAULT = 0.7
_VIBE_REF_STRENGTH_RANGE = (0.01, 1.0)
_VIBE_REF_STRENGTH_DEFAULT = 0.6
_VIBE_OVERALL_RANGE = (0.0, 1.0)
_VIBE_OVERALL_DEFAULT = 1.0
_VIBE_MAX_IMAGES = 4  # §20.3 controlnet.images 上限

# 角色参考（character_references，NewAPI §20.4）取值区间；仅 V4.5 系列、最多 1 张、不限边长。
_CHARREF_RANGE = (0.0, 1.0)
_CHARREF_FIDELITY_DEFAULT = 1.0
_CHARREF_STRENGTH_DEFAULT = 1.0
_CHARREF_TYPES = ("character", "style", "character&style")
_CHARREF_TYPE_DEFAULT = "character&style"
_CHARREF_MODEL_KEYWORD = "nai-diffusion-4-5"


class NaiChatInputValueBuilder:
    """负责将 action/context/config 组装为 NAI Chat user message JSON"""

    BUILTIN_PLACEHOLDER_NAMES = BuiltinVariableProvider.get_default_variable_names()

    @classmethod
    def parse_parameter_bindings(cls, raw_bindings: Any) -> list[BizyAirOpenApiParameterBinding]:
        """解析并校验 NAI 参数映射配置"""
        return BizyAirOpenApiInputValueBuilder.parse_parameter_bindings(raw_bindings)

    @classmethod
    async def build_message_content_json(
            cls,
            parameter_bindings: list[BizyAirOpenApiParameterBinding],
            template_context: dict[str, Any],
            action_inputs: dict[str, Any],
            action_parameter_names: set[str],
            required_action_parameters: set[str],
            action_parameter_definitions: dict[str, ActionParameterDefinition] | None = None,
            builtin_placeholder_values: dict[str, Any] | None = None,
            sfw_filter: bool = False,
            model: str = "",
            i2i_enabled: bool = False,
            i2i_image: str = "",
            i2i_strength: Any = None,
            i2i_noise: Any = None,
            vibe_enabled: bool = False,
            vibe_image: str = "",
            vibe_images_data: list[dict[str, Any]] | None = None,
            vibe_info_extracted: Any = None,
            vibe_reference_strength: Any = None,
            vibe_strength: Any = None,
            charref_enabled: bool = False,
            charref_image: str = "",
            charref_type: Any = None,
            charref_fidelity: Any = None,
            charref_strength: Any = None,
    ) -> str:
        """构造 messages[0].content 对应的 JSON 字符串"""
        payload = await BizyAirOpenApiInputValueBuilder.build_input_values(
            parameter_bindings=parameter_bindings,
            template_context=template_context,
            action_inputs=action_inputs,
            action_parameter_names=action_parameter_names,
            required_action_parameters=required_action_parameters,
            action_parameter_definitions=action_parameter_definitions,
            builtin_placeholder_values=builtin_placeholder_values,
        )
        if not payload:
            raise ValueError("NAI parameter_mappings 解析结果为空，无法构造 user message content")
        cls._apply_multi_character_channel(payload, model=model, sfw_filter=sfw_filter)
        if i2i_enabled:
            cls._apply_i2i_channel(payload, image=i2i_image, strength=i2i_strength, noise=i2i_noise)
        if vibe_enabled:
            # 多图优先：vibe_images_data 每项 {image, info_extracted?, strength?}；
            # 回退单图 vibe_image（独立 nai_vibe 预设引用图那条路）。
            images_data = list(vibe_images_data or [])
            if not images_data and str(vibe_image or "").strip():
                images_data = [{"image": vibe_image}]
            cls._apply_controlnet_channel(
                payload,
                images_data=images_data,
                info_extracted=vibe_info_extracted,
                reference_strength=vibe_reference_strength,
                overall_strength=vibe_strength,
            )
        if charref_enabled:
            cls._apply_character_reference_channel(
                payload,
                image=charref_image,
                model=model,
                ref_type=charref_type,
                fidelity=charref_fidelity,
                strength=charref_strength,
            )
        cls._sanitize_text_fields(payload, sfw_filter=sfw_filter)
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def _apply_i2i_channel(
            cls,
            payload: dict[str, Any],
            *,
            image: str,
            strength: Any,
            noise: Any,
    ) -> None:
        """组装 NewAPI §20.1 的 i2i 字段（原地改 payload）；仅 i2i 预设调用。

        本仓无 Pillow 不缩放图：i2i 要求 image 宽高严格 == 外层 size，故用**原图真实尺寸覆盖
        外层 size**（前提 64 整除 + 不超 NAI 上限），无图 / 解不出尺寸 / 非标准尺寸一律抛错
        （友好中文），由 Action 捕获回复用户——不静默踩上游 400、不静默回退纯文生图。
        """
        img = str(image or "").strip()
        if not img:
            raise ValueError("i2i 预设需要一张参考图，请引用一条带图的消息，或随出图请求附带图片")
        dims = read_image_dimensions(img)
        if dims is None:
            raise ValueError("无法解析参考图尺寸，请发送原图（PNG/JPEG/WebP）而非缩略图")
        width, height = dims
        if not cls._is_valid_nai_size(width, height):
            raise ValueError(
                f"参考图尺寸 {width}x{height} 不是 NAI 标准尺寸（宽高须为 64 的倍数且不超 "
                f"竖 832x1216 / 横 1216x832 / 方 1024x1024）；i2i 建议针对 NAI 之前生成的图"
            )
        payload["size"] = [width, height]
        payload["i2i"] = {
            "image": normalize_image_base64(img),
            "strength": cls._clamp_range(strength, *_I2I_STRENGTH_RANGE, default=_I2I_STRENGTH_DEFAULT),
            "noise": cls._clamp_range(noise, *_I2I_NOISE_RANGE, default=_I2I_NOISE_DEFAULT),
        }
        logger.info(
            f"[NAI Chat 构造] i2i 通道启用: size 对齐原图 {width}x{height}, "
            f"strength={payload['i2i']['strength']}, noise={payload['i2i']['noise']}"
        )

    @staticmethod
    def _is_valid_nai_size(width: int, height: int) -> bool:
        """NewAPI §6：宽高均为正、64 整除、且不超对应方向上限（竖 / 横 / 方）。"""
        if width <= 0 or height <= 0:
            return False
        if width % 64 != 0 or height % 64 != 0:
            return False
        if width == height:
            return width <= _NAI_MAX_SQUARE[0]
        if height > width:  # 竖图
            return width <= _NAI_MAX_PORTRAIT[0] and height <= _NAI_MAX_PORTRAIT[1]
        return width <= _NAI_MAX_LANDSCAPE[0] and height <= _NAI_MAX_LANDSCAPE[1]  # 横图

    @staticmethod
    def _clamp_range(value: Any, lo: float, hi: float, *, default: float) -> float:
        """把数值 clamp 进 [lo, hi]；None / 非数值用 default。"""
        if value is None:
            return default
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    @classmethod
    def _apply_controlnet_channel(
            cls,
            payload: dict[str, Any],
            *,
            images_data: list[dict[str, Any]],
            info_extracted: Any = None,
            reference_strength: Any = None,
            overall_strength: Any = None,
    ) -> None:
        """组装 NewAPI §20.3 的 controlnet（Vibe Transfer）字段（原地改 payload）。

        多图：``images_data`` 每项 ``{image, info_extracted?, strength?}``，逐张组 controlnet.images；
        每张缺省的 info_extracted/strength 回落到入参默认（再回落常量），越界 clamp。最多 4 张超量截断。
        §20.3 不限边长（服务端 resize），故**不校验尺寸、不覆盖外层 size**——与 i2i 的关键差异。
        无任何有效图抛友好中文错，由 Action 捕获回复用户。多图 cache 由 nai_vibe_cache_rewrite 按 images 数组自动处理。
        """
        entries: list[dict[str, Any]] = []
        for item in (images_data or []):
            img = str((item or {}).get("image") or "").strip()
            if not img:
                continue
            ie = item.get("info_extracted", info_extracted)
            st = item.get("strength", reference_strength)
            entries.append({
                "image": normalize_image_base64(img),
                "info_extracted": cls._clamp_range(ie, *_VIBE_INFO_RANGE, default=_VIBE_INFO_DEFAULT),
                "strength": cls._clamp_range(st, *_VIBE_REF_STRENGTH_RANGE, default=_VIBE_REF_STRENGTH_DEFAULT),
            })
            if len(entries) >= _VIBE_MAX_IMAGES:
                break
        if not entries:
            raise ValueError("vibe 需要至少一张参考图，请引用带图消息 / 附带图片，或先用 /nai art photo save 存图")
        payload["controlnet"] = {
            "images": entries,
            "strength": cls._clamp_range(overall_strength, *_VIBE_OVERALL_RANGE, default=_VIBE_OVERALL_DEFAULT),
        }
        logger.info(
            f"[NAI Chat 构造] vibe(controlnet) 通道启用: {len(entries)} 图, "
            f"overall={payload['controlnet']['strength']}"
        )

    @classmethod
    def _apply_character_reference_channel(
            cls,
            payload: dict[str, Any],
            *,
            image: str,
            model: str,
            ref_type: Any,
            fidelity: Any,
            strength: Any,
    ) -> None:
        """组装 NewAPI §20.4 的 character_references 字段（原地改 payload）；仅角色参考预设调用。

        §20.4：仅 V4.5 系列模型支持，最多 1 张，不限边长（服务端自动 upscale/pad）。强兼单图：
        一张引用图组成 character_references[0]，type/fidelity/strength 走预设默认。模型不支持时
        抛友好中文错（不静默降级出普通图——切了角色参考预设却没生效会误导用户）。
        """
        img = str(image or "").strip()
        if not img:
            raise ValueError("角色参考预设需要一张参考图，请引用一条带图的消息，或随出图请求附带图片")
        if not cls._model_supports_character_reference(model):
            raise ValueError(
                f"角色参考（character reference）仅 NAI V4.5 系列模型支持，当前模型 {model!r}；"
                f"请先 /nai set nai-diffusion-4-5-full，或改用其它预设"
            )
        raw_type = str(ref_type or "").strip()
        if raw_type and raw_type not in _CHARREF_TYPES:
            logger.warning(
                f"[NAI Chat 构造] 角色参考 type={raw_type!r} 非法（仅 {_CHARREF_TYPES}），已回落默认 {_CHARREF_TYPE_DEFAULT!r}"
            )
            raw_type = ""
        entry: dict[str, Any] = {
            "image": normalize_image_base64(img),
            "type": raw_type or _CHARREF_TYPE_DEFAULT,
            "fidelity": cls._clamp_range(fidelity, *_CHARREF_RANGE, default=_CHARREF_FIDELITY_DEFAULT),
            "strength": cls._clamp_range(strength, *_CHARREF_RANGE, default=_CHARREF_STRENGTH_DEFAULT),
        }
        payload["character_references"] = [entry]
        logger.info(
            f"[NAI Chat 构造] 角色参考通道启用: 单图, type={entry['type']}, "
            f"fidelity={entry['fidelity']}, strength={entry['strength']}"
        )

    @staticmethod
    def _model_supports_character_reference(model: str) -> bool:
        """角色参考是否被模型支持：仅 nai-diffusion-4-5 系列（子串匹配，大小写不敏感，§20.4）。"""
        return _CHARREF_MODEL_KEYWORD in str(model or "").lower()

    @classmethod
    def _apply_multi_character_channel(
            cls,
            payload: dict[str, Any],
            *,
            model: str,
            sfw_filter: bool,
    ) -> None:
        """检测多人 → 拆 NewAPI characters[] 通道（原地修改 payload）。

        移植 nai_draw 的「模型嗅探 + characters 规范化」语义（§7）：
        1. 在**未清洗的原始 prompt** 上解析（JSON v3 / char1:char2: 文本双路径），
           这样将来 director 改走 JSON 通道时不会被文本清洗破坏结构；
        2. 仅 nai-diffusion-4* 系列支持，否则降级为单 prompt 路径并打 warning；
        3. §8 CJK 清洗（强制）+ SFW（可选）逐字段作用于 global 与每个 char.prompt；
        4. 规范化为内层 JSON 形态后，**有效角色 ≥ 2 才提交**——清洗 / 规范化后不足 2 人时
           不动 payload，维持原单 prompt 串（无信息丢失，flat 串随后由 _sanitize_text_fields 清洗）。

        提交时 prompt 被替换为 global 段，并写入 characters / use_coords / use_order。
        """
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return

        resolved = resolve_multi_character_payload(prompt, prompt)
        if not resolved:
            return  # 单人或解析失败：保持单 prompt 路径

        if not cls._model_supports_multi_character(model):
            logger.warning(
                f"[NAI Chat 构造] 模型 {model!r} 不在多角色支持列表（{_MULTI_CHARACTER_MODEL_KEYWORDS}），"
                f"已降级为单 prompt 路径，characters({len(resolved.get('characters', []))} 项) 已忽略"
            )
            return

        global_text = resolved.get("global_text", "") or ""
        characters = resolved.get("characters", []) or []

        # §8：global 与每个 char.prompt 都必须英文。CJK 清洗强制，SFW 按开关。
        global_text, characters = strip_cjk_and_fullwidth_from_characters(global_text, characters)
        if sfw_filter:
            global_text, characters = sanitize_sfw_characters(global_text, characters)

        characters = cls._normalize_characters_for_inner(characters)
        if len(characters) < 2 or not global_text.strip():
            logger.info("[NAI Chat 构造] 多人解析/清洗后有效角色 < 2，维持单 prompt 路径")
            return

        payload["prompt"] = global_text.strip()
        payload["characters"] = characters
        payload["use_coords"] = all(bool(item.get("position")) for item in characters)
        payload["use_order"] = True
        logger.info(
            f"[NAI Chat 构造] 多人通道启用: {len(characters)} 角色, "
            f"use_coords={payload['use_coords']}, use_order=True"
        )

    @staticmethod
    def _model_supports_multi_character(model: str) -> bool:
        """模型是否稳定支持 characters[]：仅 nai-diffusion-4 系列（子串匹配，大小写不敏感）。"""
        lowered = str(model or "").lower()
        return any(keyword in lowered for keyword in _MULTI_CHARACTER_MODEL_KEYWORDS)

    @staticmethod
    def _normalize_characters_for_inner(
            characters: list[dict[str, Any]] | None,
    ) -> list[dict[str, str]]:
        """把角色列表归一为内层 JSON 直接可用的形态（移植 nai_draw _normalize_characters_for_inner）。

        - 丢弃 prompt 为空的项；
        - position 不匹配 [A-E][1-5] 时落为 ""；
        - **任一项缺 position 时把所有 position 一并清空**，交后端自动布局（避免文档约束冲突）；
        - 输出体里只保留非空的 negative_prompt / position 键（§7.2 position 只接受字符串）；
        - 有效角色 < 2 时返回 []，由调用方判定是否降级回单串。
        """
        if not characters:
            return []

        cleaned: list[dict[str, str]] = []
        for item in characters:
            if not isinstance(item, dict):
                continue
            char_prompt = str(item.get("prompt") or "").strip()
            if not char_prompt:
                continue
            char_negative = str(item.get("negative_prompt") or "").strip()
            raw_position = str(item.get("position") or "").strip().upper()
            position = raw_position if _POSITION_GRID_RE.match(raw_position) else ""
            cleaned.append({"prompt": char_prompt, "negative_prompt": char_negative, "position": position})

        if len(cleaned) < 2:
            return []

        # 部分角色缺合法 position：全部清空，让后端按 use_order 自动布局
        if any(not item["position"] for item in cleaned):
            for item in cleaned:
                item["position"] = ""

        result: list[dict[str, str]] = []
        for item in cleaned:
            entry: dict[str, str] = {"prompt": item["prompt"]}
            if item["negative_prompt"]:
                entry["negative_prompt"] = item["negative_prompt"]
            if item["position"]:
                entry["position"] = item["position"]
            result.append(entry)
        return result

    @staticmethod
    def _sanitize_text_fields(payload: dict[str, Any], sfw_filter: bool = False) -> None:
        """对 prompt / negative_prompt 等英文文本字段做后处理（原地修改）。

        1. §8 CJK/全角清洗（强制）：含 CJK 必 400，LLM 偶发漏译时兜底。
        2. SFW 过滤（可选，sfw_filter=True）：剔除 bikini/cleavage 等擦边 tag；默认关闭，
           因为既有管线故意允许轻量暴露，仅 /nai nsfw on 时启用。
        清洗后若整段被清空（极端情况，例如全中文未翻译），则移除该键，交由下游
        send_if_empty / 必填校验处理，避免把空串送进 NewAPI。
        """
        for field in _NAI_TEXT_FIELDS:
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            cleaned = strip_cjk_and_fullwidth(value).strip()
            if sfw_filter and cleaned:
                cleaned = sanitize_sfw_prompt(cleaned).strip()
            if cleaned:
                if cleaned != value:
                    logger.info(f"[NAI Chat 构造] 字段 {field} 已做 CJK/SFW 清洗")
                payload[field] = cleaned
            else:
                logger.warning(f"[NAI Chat 构造] 字段 {field} 清洗后为空，已移除")
                payload.pop(field, None)

    @classmethod
    def collect_builtin_placeholder_names_from_bindings(cls, raw_bindings: Any) -> set[str]:
        """从 NAI 参数映射配置中提取被引用到的内置变量名"""
        return BizyAirOpenApiInputValueBuilder.collect_builtin_placeholder_names_from_bindings(raw_bindings)