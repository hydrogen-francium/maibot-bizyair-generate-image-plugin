from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from src.common.logger import get_logger
from src.config.api_ada_configs import TaskConfig
from src.config.config import model_config
from src.plugin_system.apis import llm_api

from ..clients import (
    BizyAirImageResult,
    BizyAirOpenApiClient,
    BizyAirOpenApiContentFilterError,
    NaiChatClient,
)
from . import nai_settings
from .action_parameter_utils import ActionParameterDefinition
from .builtin_variable_provider import BuiltinVariableProvider
from .content_filter_sanitizer import sanitize_input_values
from .custom_variable_registry import CustomVariableRegistry
from .log_utils import short_repr
from .nai_chat_input_value_builder import NaiChatInputValueBuilder
from .openapi_input_value_builder import BizyAirOpenApiInputValueBuilder
from .preset_resolution import resolve_active_preset
from .variable_dependency_resolver import VariableDependencyResolver

logger = get_logger("bizyair_generate_image_plugin")

ConfigGetter = Callable[[str, Any], Any]
ImageBase64Provider = Callable[..., Optional[str]]
LlmValueFactory = Callable[[str], Awaitable[str]]


@dataclass
class DrawPayload:
    """resolve_to_payload 的产物：足以发起一次出图请求 + 复盘所需的上下文。"""

    provider: str
    resolved_preset: dict[str, Any]
    provider_payload: dict[str, Any]
    timeout: float
    template_context: dict[str, Any]


# ──────────────── 变量生成 LLM ────────────────

def build_variable_task_config(get_config: ConfigGetter) -> TaskConfig:
    """优先按显式 llm_list 构造变量生成任务配置，否则回落到 llm_group 任务配置。"""
    llm_list = get_config("variable_llm_config.llm_list", [])
    if isinstance(llm_list, list) and llm_list:
        return TaskConfig(
            model_list=[str(item).strip() for item in llm_list if str(item).strip()],
            max_tokens=int(str(get_config("variable_llm_config.max_tokens", 512)).strip()),
            temperature=float(str(get_config("variable_llm_config.temperature", 0.7)).strip()),
            slow_threshold=float(str(get_config("variable_llm_config.slow_threshold", 30.0)).strip()),
            selection_strategy=str(get_config("variable_llm_config.selection_strategy", "balance")).strip(),
        )
    llm_group = str(get_config("variable_llm_config.llm_group", "utils")).strip()
    return model_config.model_task_config.get_task(llm_group)


async def generate_variable_with_llm(get_config: ConfigGetter, prompt: str, log_prefix: str = "") -> str:
    """使用变量 LLM 配置生成最终变量值。

    防御：框架 llm_api 偶发返回 `success=True` 但 content 为空 / 仅 token 统计占位串
    （如 "Token count: 0"，多见于超长输入触发供应商限制时）。这种"假成功"若被当 tag 送去
    出图，会画出「只有质量词+画师串、无主体」的乱图。这里检测并当失败处理 + 重试一次，
    再不行就抛错，让出图走失败提示，绝不把废串当 prompt。
    """
    last_bad = ""
    for attempt in (1, 2):
        logger.info(f"{log_prefix}[自定义变量] 调用 LLM 生成变量值（第{attempt}次），提示词: {prompt!r}")
        success, content, _, _ = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=build_variable_task_config(get_config),
            request_type="bizyair_custom_variable_generation",
        )
        logger.info(f"{log_prefix}[自定义变量] LLM 原始输出: {content!r}")
        if not success:
            raise RuntimeError(f"LLM 生成自定义变量失败: {content}")
        cleaned = (content or "").strip()
        bad = _is_degenerate_llm_output(cleaned)
        if not bad:
            return cleaned
        last_bad = cleaned
        logger.warning(
            f"{log_prefix}[自定义变量] LLM 返回疑似废输出（{bad}）: {cleaned!r}，"
            f"{'重试一次' if attempt == 1 else '重试后仍废，判失败'}"
        )
    raise RuntimeError(f"LLM 连续返回无效输出（最后一次: {last_bad!r}），已放弃，不出图以免画出无主体乱图")


# 框架 llm_api 失败/空响应时透传的占位串特征（success 仍为 True 的"假成功"）
_DEGENERATE_LLM_MARKERS = ("token count:", "token count :")


def _is_degenerate_llm_output(text: str) -> str:
    """判定 LLM 输出是否为「废输出」（空 / 框架 token 统计占位串 / 过短无信息）。

    返回非空原因字符串表示废，空字符串表示正常。仅拦明确的失败特征，不误伤正常 tag。
    """
    if not text:
        return "空输出"
    low = text.strip().lower()
    if not low:
        return "空输出"
    # 整串就是 token 统计占位（"Token count: 0" 之类），是框架对空响应的透传
    for marker in _DEGENERATE_LLM_MARKERS:
        if low.startswith(marker):
            return f"token统计占位({text.strip()!r})"
    return ""



# ──────────────── NAI 运行时设置注入 ────────────────

def inject_nai_runtime_inputs(
        action_inputs: dict[str, Any],
        *,
        nai_artist: str,
        nai_size: str,
        log_prefix: str = "",
) -> None:
    """把 NAI 运行时设置（/nai art 画师串、/nai size 尺寸）作为伪 action_input 注入（原地修改）。

    - ``nai_artist``：nai_final_prompt 通过 {nai_artist} 引用 + length_gt 条件判断是否注入画师段。
    - ``nai_size_code``：nai_size dict 变量的 source（v/h/s）；auto 档按 aspect_ratio 推导。

    两者都是已解析的字面量（无占位符），解析器会把无依赖的 action_input 直接灌进
    resolved_context，因此 **无需** 登记进 action_parameter_names。仅在 NAI 预设路径调用。
    （nai_raw_tags 由 /nai0 命令自行注入，不在这里——它是命令专属的直发开关。）
    """
    action_inputs["nai_artist"] = str(nai_artist or "")
    action_inputs["nai_size_code"] = nai_settings.resolve_size_code(
        str(nai_size or "auto"),
        action_inputs.get("aspect_ratio"),
    )
    logger.info(
        f"{log_prefix} 注入 NAI 运行时设置: nai_artist={short_repr(action_inputs['nai_artist'])}, "
        f"nai_size_code={action_inputs['nai_size_code']!r} (size_override={nai_size!r}, "
        f"aspect_ratio={action_inputs.get('aspect_ratio')!r})"
    )


async def inject_tag_candidates(
        get_config: ConfigGetter,
        action_inputs: dict[str, Any],
        *,
        log_prefix: str = "",
) -> None:
    """用 image_intent 调 online Danbooru tag 检索，把候选文本伪注入 action_inputs["tag_candidates"]（原地修改）。

    仿 inject_nai_runtime_inputs：tag_candidates 是已解析字面量（无占位符），解析器会把无依赖的
    action_input 直接灌进 resolved_context，nai_director 模板引用 {tag_candidates} 即生效，
    无需登记进 action_parameter_names。

    **必定**设置 action_inputs["tag_candidates"]（哪怕空串）：模板引用了它，缺 key 会被判「未定义变量」。
    失败安全：未启用 / 无意图（如 /nai0 直发）/ 任何异常 → 空串，绝不阻断出图。仅 NAI 预设路径调用。
    """
    # 延迟 import：规避 services ↔ clients 包级循环（与 nai_vibe_cache_rewrite 同策略）
    from .nai_tag_candidate_resolver import resolve_tag_candidates

    action_inputs["tag_candidates"] = ""
    try:
        # /nai0 直发（nai_raw_tags 非空）时 nai_director 旁路 → 检索同样旁路（与 director 惰性旁路一致，避免无谓触网）
        if str(action_inputs.get("nai_raw_tags") or "").strip():
            return
        query = str(action_inputs.get("image_intent") or "").strip()
        retriever_config = get_config("tag_retriever", {}) or {}
        if not query or not isinstance(retriever_config, dict) or not retriever_config.get("enabled", False):
            return
        candidates = await resolve_tag_candidates(retriever_config, query, log_prefix=log_prefix)
        action_inputs["tag_candidates"] = candidates or ""
        logger.info(f"{log_prefix} 注入 tag_candidates: {short_repr(action_inputs['tag_candidates'])}")
    except Exception as exc:  # 双保险失败安全：注入环节任何异常都降级为空，绝不阻断出图
        logger.warning(f"{log_prefix} tag_candidates 注入失败，已降级为空: {exc}")
        action_inputs["tag_candidates"] = ""


def inject_previous_context(
        get_config: ConfigGetter,
        action_inputs: dict[str, Any],
        chat_id: Any,
        *,
        log_prefix: str = "",
) -> None:
    """读会话态上一轮上下文 → 渲染 <previous_prompt_context> 块伪注入 action_inputs["previous_prompt_context"]（原地）。

    仿 inject_tag_candidates：previous_prompt_context 是已解析字面量，解析器把无依赖的 action_input 直接
    灌进 resolved_context，nai_director 模板引用 {previous_prompt_context} 即生效，无需登记进 action_parameter_names。

    **必定**设置 action_inputs["previous_prompt_context"]（哪怕空串）：模板引用了它，缺 key 会被判「未定义变量」。
    失败安全：未启用 / /nai0 直发 / 任何异常 → 空串。仅 NAI 预设路径调用，纯读内存（无 IO，故 sync）。
    """
    # 延迟 import：规避 services 包级循环（与 inject_tag_candidates 同策略）
    from .nai_prompt_memory import get_last_nai_context, render_previous_prompt_block

    action_inputs["previous_prompt_context"] = ""
    try:
        # /nai0 直发（nai_raw_tags 非空）时 nai_director 旁路 → 续承同样旁路（用户拍板：/nai0 不参与）
        if str(action_inputs.get("nai_raw_tags") or "").strip():
            return
        cfg = get_config("prompt_continuity", {}) or {}
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            return
        try:
            ttl = float(cfg.get("inherit_ttl", 3600) or 0)
        except (TypeError, ValueError):
            ttl = 3600.0
        last_prompt, last_request = get_last_nai_context(chat_id, ttl=ttl)
        action_inputs["previous_prompt_context"] = render_previous_prompt_block(last_prompt, last_request)
        logger.info(f"{log_prefix} 注入 previous_prompt_context: {short_repr(action_inputs['previous_prompt_context'])}")
    except Exception as exc:  # 失败安全：续承注入任何异常都降级为空，绝不阻断出图
        logger.warning(f"{log_prefix} previous_prompt_context 注入失败，已降级为空: {exc}")
        action_inputs["previous_prompt_context"] = ""


def record_previous_context(
        get_config: ConfigGetter,
        payload: DrawPayload,
        action_inputs: Optional[dict[str, Any]],
        chat_id: Any,
        *,
        log_prefix: str = "",
) -> None:
    """出图成功后把本轮 nai_director 输出写回会话态，供下次续承。

    失败安全：写回任何环节出错都只记日志，绝不影响已成功的出图。
    /nai0 直发（nai_raw_tags 非空）→ director 未跑 → 不写回（用户拍板：/nai0 不参与）。
    """
    try:
        cfg = get_config("prompt_continuity", {}) or {}
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            return
        inputs = action_inputs or {}
        if str(inputs.get("nai_raw_tags") or "").strip():
            return  # /nai0 不参与
        director_output = str((payload.template_context or {}).get("nai_director") or "").strip()
        if not director_output:
            return
        from .nai_prompt_memory import set_last_nai_context
        request_text = str(inputs.get("image_intent") or "").strip()
        set_last_nai_context(chat_id, director_output, request_text)
        logger.info(f"{log_prefix} 已记录本轮 previous context: {short_repr(director_output)}")
    except Exception as exc:  # 失败安全：写回不影响已成功的出图
        logger.warning(f"{log_prefix} 写回 previous context 失败，已忽略: {exc}")


def _is_i2i_preset(preset: dict[str, Any]) -> bool:
    """preset 含 i2i_strength / i2i_noise 即视为 i2i 图生图预设（NewAPI §20.1）。"""
    return preset.get("i2i_strength") is not None or preset.get("i2i_noise") is not None


def _is_vibe_preset(preset: dict[str, Any]) -> bool:
    """preset 含 vibe_info_extracted / vibe_reference_strength / vibe_strength 任一即视为 Vibe Transfer 预设（NewAPI §20.3）。"""
    return (
        preset.get("vibe_info_extracted") is not None
        or preset.get("vibe_reference_strength") is not None
        or preset.get("vibe_strength") is not None
    )


def _is_charref_preset(preset: dict[str, Any]) -> bool:
    """preset 含 charref_type / charref_fidelity / charref_strength 任一即视为角色参考预设（NewAPI §20.4）。"""
    return (
        bool(str(preset.get("charref_type") or "").strip())
        or preset.get("charref_fidelity") is not None
        or preset.get("charref_strength") is not None
    )


def _preset_needs_quoted_image(preset: dict[str, Any]) -> bool:
    """i2i / vibe / 角色参考任一图像条件化预设都要强制收集引用图 base64。"""
    return _is_i2i_preset(preset) or _is_vibe_preset(preset) or _is_charref_preset(preset)


# ──────────────── 参数映射 ────────────────

def get_parameter_bindings_config(get_config: ConfigGetter, provider: str) -> list:
    """按后端读取参数映射配置。"""
    if provider == "bizyair_openapi":
        return get_config("bizyair_client.openapi_parameter_mappings", [])
    if provider == "nai_chat":
        return get_config("nai_chat_client.parameter_mappings", [])
    raise ValueError(f"未知的 provider: {provider}")


def filter_parameter_bindings_by_preset(all_bindings: Any, active_preset: str) -> list:
    """过滤出与 active_preset 匹配的参数映射条目。"""
    if not isinstance(all_bindings, list):
        return []
    result = []
    for index, item in enumerate(all_bindings):
        if not isinstance(item, dict):
            raise ValueError(f"parameter_mappings[{index}] 必须是对象")
        raw_preset_name = item.get("preset_name", "")
        if not raw_preset_name or not str(raw_preset_name).strip():
            raise ValueError(f"parameter_mappings[{index}].preset_name 不能为空")
        preset_names = {p.strip() for p in str(raw_preset_name).split(",") if p.strip()}
        if active_preset in preset_names:
            result.append(item)
    logger.info(
        f"[参数映射过滤] active_preset={active_preset!r}, total={len(all_bindings)}, matched={len(result)}"
    )
    return result


def collect_builtin_placeholder_names(provider: str, parameter_bindings_config: Any) -> set[str]:
    """按后端提取本次需要构造的内置变量名。"""
    if provider == "bizyair_openapi":
        return BizyAirOpenApiInputValueBuilder.collect_builtin_placeholder_names_from_bindings(parameter_bindings_config)
    if provider == "nai_chat":
        return NaiChatInputValueBuilder.collect_builtin_placeholder_names_from_bindings(parameter_bindings_config)
    raise ValueError(f"未知的 provider: {provider}")


# ──────────────── 载荷构造 ────────────────

async def build_provider_payload(
        get_config: ConfigGetter,
        *,
        provider: str,
        resolved_preset: dict[str, Any],
        parameter_bindings_config: list,
        template_context: dict[str, Any],
        resolved_action_inputs: dict[str, Any],
        builtin_placeholder_values: dict[str, Any],
        action_parameters: dict[str, ActionParameterDefinition],
        required_action_parameters: set[str],
        active_preset: str,
        nai_model_override: str = "",
        nai_sfw_filter: bool = False,
) -> tuple[dict[str, Any], float]:
    """按后端构造请求载荷与超时配置。"""
    preset = resolved_preset["preset"]
    action_parameter_names = set(action_parameters.keys())

    if provider == "bizyair_openapi":
        parameter_bindings = BizyAirOpenApiInputValueBuilder.parse_parameter_bindings(parameter_bindings_config)
        token = str(get_config("bizyair_client.bearer_token", "")).strip()
        if not token:
            raise ValueError("插件未配置 bizyair_client.bearer_token")
        input_values = await BizyAirOpenApiInputValueBuilder.build_input_values(
            parameter_bindings=parameter_bindings,
            template_context=template_context,
            action_inputs=resolved_action_inputs,
            action_parameter_names=action_parameter_names,
            required_action_parameters=set(required_action_parameters),
            action_parameter_definitions=action_parameters,
            builtin_placeholder_values=builtin_placeholder_values,
            upload_api_key=token,
        )
        timeout = float(str(get_config("bizyair_client.timeout", 180.0)).strip())
        raw_app_id = preset.get("app_id")
        if raw_app_id is None:
            raise ValueError(f"BizyAir 预设 {active_preset!r} 的 app_id 为空")
        logger.info(f"[参数构造] 最终 input_values: {input_values}")
        return {
            "token": token,
            "app_id": int(raw_app_id),
            "input_values": input_values,
        }, timeout

    if provider == "nai_chat":
        parameter_bindings = NaiChatInputValueBuilder.parse_parameter_bindings(parameter_bindings_config)
        api_key = str(preset.get("api_key", "")).strip()
        base_url = str(preset.get("base_url", "")).strip()
        # /nai set 的全局模型覆盖优先；为空则用预设自带 model
        model = str(nai_model_override or "").strip() or str(preset.get("model", "")).strip()
        if not api_key:
            raise ValueError(f"NAI 预设 {active_preset!r} 的 api_key 为空")
        if not base_url:
            raise ValueError(f"NAI 预设 {active_preset!r} 的 base_url 为空")
        if not model:
            raise ValueError(f"NAI 预设 {active_preset!r} 的 model 为空")
        # 图像条件化预设（i2i §20.1 / vibe §20.3 / 角色参考 §20.4）：把引用图 base64 交给 builder
        # 组装对应内层字段。三者用独立预设触发（一个预设一种能力），共用同一张引用图来源。
        i2i_enabled = _is_i2i_preset(preset)
        vibe_enabled = _is_vibe_preset(preset)
        charref_enabled = _is_charref_preset(preset)
        needs_image = i2i_enabled or vibe_enabled or charref_enabled
        quoted_image = str(builtin_placeholder_values.get("{quoted_image_base64}") or "") if needs_image else ""
        # model 提前解析：builder 据此判定多角色 characters[] 通道（§7）与角色参考模型门槛（§20.4 仅 V4.5）
        content_json = await NaiChatInputValueBuilder.build_message_content_json(
            parameter_bindings=parameter_bindings,
            template_context=template_context,
            action_inputs=resolved_action_inputs,
            action_parameter_names=action_parameter_names,
            required_action_parameters=set(required_action_parameters),
            action_parameter_definitions=action_parameters,
            builtin_placeholder_values=builtin_placeholder_values,
            sfw_filter=bool(nai_sfw_filter),
            model=model,
            i2i_enabled=i2i_enabled,
            i2i_image=quoted_image if i2i_enabled else "",
            i2i_strength=preset.get("i2i_strength"),
            i2i_noise=preset.get("i2i_noise"),
            vibe_enabled=vibe_enabled,
            vibe_image=quoted_image if vibe_enabled else "",
            vibe_info_extracted=preset.get("vibe_info_extracted"),
            vibe_reference_strength=preset.get("vibe_reference_strength"),
            vibe_strength=preset.get("vibe_strength"),
            charref_enabled=charref_enabled,
            charref_image=quoted_image if charref_enabled else "",
            charref_type=preset.get("charref_type"),
            charref_fidelity=preset.get("charref_fidelity"),
            charref_strength=preset.get("charref_strength"),
        )
        timeout = float(str(get_config("nai_chat_client.timeout", 180.0)).strip())
        # 图像条件化时 content_json 含 MB 级图片 base64 → 短 repr，避免撑爆日志（base64 铁律）
        logger.info(
            f"[参数构造] 最终 content_json: "
            f"{short_repr(content_json) if needs_image else content_json}"
        )
        # vibe cache（§20.3.1）：仅 vibe 预设 + 配置开启时启用；client 据此做查改写/落库/stale 重试
        vibe_cache_enabled = vibe_enabled and bool(get_config("nai_chat_client.vibe_cache_enabled", True))
        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "content_json": content_json,
            "vibe_cache_enabled": vibe_cache_enabled,
        }, timeout

    raise ValueError(f"未知的 provider: {provider}")


# ──────────────── 编排：变量 → 载荷 → 字节 ────────────────

async def resolve_to_payload(
        *,
        get_config: ConfigGetter,
        action_inputs: dict[str, Any],
        active_preset: str,
        action_parameters: dict[str, ActionParameterDefinition],
        required_action_parameters: set[str],
        chat_id: Any,
        image_base64_provider: Optional[ImageBase64Provider] = None,
        nai_artist: str = "",
        nai_size: str = "auto",
        nai_model: str = "",
        nai_sfw_filter: bool = False,
        llm_value_factory: Optional[LlmValueFactory] = None,
        log_prefix: str = "",
) -> DrawPayload:
    """解析活动预设 → 变量 → provider 载荷（不触网，可单测）。

    action_inputs 会被拷贝后再注入 NAI 伪输入，不修改调用方的 dict。
    Action 与命令（/nai0、/nai 随机）共用本函数，只是 action_inputs / nai_* 设置不同。
    """
    action_inputs = dict(action_inputs)

    resolved_preset = resolve_active_preset(
        active_preset=active_preset,
        bizyair_presets=get_config("bizyair_client.app_presets", []),
        nai_presets=get_config("nai_chat_client.presets", []),
    )
    provider = resolved_preset["provider"]

    # NAI 运行时设置（画师串/尺寸）注入；仅 NAI 预设生效，GPT 路径不受影响
    if provider == "nai_chat":
        inject_nai_runtime_inputs(action_inputs, nai_artist=nai_artist, nai_size=nai_size, log_prefix=log_prefix)
        # P4：online tag 检索结果伪注入 {tag_candidates}（image_intent 空时自然跳过；失败安全降级）
        await inject_tag_candidates(get_config, action_inputs, log_prefix=log_prefix)
        # continuity：读会话态上一轮 → 伪注入 {previous_prompt_context}（/nai0 旁路；失败安全降级）
        inject_previous_context(get_config, action_inputs, chat_id, log_prefix=log_prefix)

    all_bindings = get_parameter_bindings_config(get_config, provider)
    parameter_bindings_config = filter_parameter_bindings_by_preset(all_bindings, active_preset)

    builtin_variable_provider = BuiltinVariableProvider(
        chat_id=chat_id,
        filter_mai=False,
        message_image_base64_provider=image_base64_provider or (lambda *a, **k: None),
    )
    registry = CustomVariableRegistry(
        raw_variables=get_config("custom_variables_config.custom_variables", []),
        action_parameter_names=set(action_parameters.keys()),
    )
    direct_variable_keys = registry.collect_required_variable_keys(parameter_bindings_config)
    builtin_names = BuiltinVariableProvider.get_default_variable_names()
    required_variable_keys = VariableDependencyResolver.compute_required_variable_keys(
        direct_keys=direct_variable_keys,
        action_inputs=action_inputs,
        custom_variable_definitions=registry.variable_definitions,
        action_parameter_names=set(action_parameters.keys()),
        builtin_names=builtin_names,
    )

    required_builtin_names = collect_builtin_placeholder_names(provider, parameter_bindings_config)
    # 图像条件化预设（i2i / vibe / 角色参考）：强制收集引用图 base64（图来源链：
    # Action _extract_message_image_base64 → quoted_image_base64）。仅进 builtin 值供 builder
    # 对应通道用，不进 director 闭包（director 模板未引用它，天然隔离 base64 不进文本 LLM）。
    if provider == "nai_chat" and _preset_needs_quoted_image(resolved_preset["preset"]):
        required_builtin_names = set(required_builtin_names) | {"quoted_image_base64"}
    builtin_placeholder_values = builtin_variable_provider.build_placeholder_values(required_builtin_names)
    logger.info(
        f"{log_prefix} 出图编排: provider={provider!r}, active_preset={active_preset!r}, "
        f"required_variable_keys={sorted(required_variable_keys)}, "
        f"required_builtin_names={sorted(required_builtin_names)}"
    )

    if llm_value_factory is None:
        async def llm_value_factory(prompt: str) -> str:  # type: ignore[misc]
            return await generate_variable_with_llm(get_config, prompt, log_prefix=log_prefix)

    dependency_resolver = VariableDependencyResolver(
        action_inputs=action_inputs,
        custom_variable_definitions=registry.variable_definitions,
        action_parameter_names=set(action_parameters.keys()),
        builtin_names=builtin_names,
        required_custom_variable_keys=required_variable_keys,
    )
    resolved_action_inputs, custom_variable_values = await dependency_resolver.resolve_all(
        builtin_placeholder_values=builtin_placeholder_values,
        llm_value_factory=llm_value_factory,
        builtin_variable_provider=builtin_variable_provider,
    )
    template_context = {**resolved_action_inputs, **custom_variable_values}

    # 诊断日志（P5 排障）：完整打印 NAI 大脑输出与拼好的最终正面 prompt，不截断，便于核对
    # 「角色/动作与意图不符」类问题——直接看大脑吐了什么、画师串+主体怎么拼的。
    if provider == "nai_chat":
        _diag_director = template_context.get("nai_director")
        _diag_final = template_context.get("nai_final_prompt")
        _diag_subject = template_context.get("nai_subject")
        logger.info(
            f"{log_prefix} [反推排障] image_intent={template_context.get('image_intent')!r}\n"
            f"  nai_raw_tags={template_context.get('nai_raw_tags')!r}\n"
            f"  nai_director(大脑原始输出)={_diag_director!r}\n"
            f"  nai_subject(主体来源)={_diag_subject!r}\n"
            f"  nai_artist(画师串)={template_context.get('nai_artist')!r}\n"
            f"  nai_final_prompt(最终正面)={_diag_final!r}\n"
            f"  tag_candidates={short_repr(template_context.get('tag_candidates'))}\n"
            f"  previous_prompt_context={short_repr(template_context.get('previous_prompt_context'))}"
        )

    provider_payload, timeout = await build_provider_payload(
        get_config,
        provider=provider,
        resolved_preset=resolved_preset,
        parameter_bindings_config=parameter_bindings_config,
        template_context=template_context,
        resolved_action_inputs=resolved_action_inputs,
        builtin_placeholder_values=builtin_placeholder_values,
        action_parameters=action_parameters,
        required_action_parameters=required_action_parameters,
        active_preset=active_preset,
        nai_model_override=nai_model,
        nai_sfw_filter=nai_sfw_filter,
    )
    logger.info(
        f"{log_prefix} 出图载荷就绪: provider={provider}, active_preset={active_preset!r}, "
        f"custom_variable_values={short_repr(custom_variable_values)}, timeout={timeout}"
    )
    return DrawPayload(
        provider=provider,
        resolved_preset=resolved_preset,
        provider_payload=provider_payload,
        timeout=timeout,
        template_context=template_context,
    )


async def generate_image_bytes(
        get_config: ConfigGetter,
        *,
        provider: str,
        resolved_preset: dict[str, Any],
        provider_payload: dict[str, Any],
        timeout: float,
        log_prefix: str = "",
) -> bytes:
    """按后端创建客户端、发起出图（含 bizyair 422 内容审核重试）并下载图片字节。"""
    generate_image_start_time = time.perf_counter()
    generate_result: BizyAirImageResult
    client: BizyAirOpenApiClient | NaiChatClient
    if provider == "bizyair_openapi":
        client = BizyAirOpenApiClient(
            bearer_token=provider_payload["token"],
            api_url=str(get_config("bizyair_client.openapi_url", BizyAirOpenApiClient.API_URL)).strip(),
            web_app_id=provider_payload["app_id"],
            timeout=timeout,
        )
        try:
            generate_result = await client.generate_image(input_values=provider_payload["input_values"])
        except BizyAirOpenApiContentFilterError as filter_err:
            sanitized_inputs = sanitize_input_values(provider_payload["input_values"])
            if sanitized_inputs == provider_payload["input_values"]:
                logger.warning(
                    f"{log_prefix} 触发 422 内容审核但清洗后 input_values 未变化，放弃重试"
                )
                raise
            logger.warning(
                f"{log_prefix} 触发 422 内容审核，剔除高危 tag 后重试一次: {filter_err}"
            )
            generate_result = await client.generate_image(input_values=sanitized_inputs)

    elif provider == "nai_chat":
        client = NaiChatClient(
            bearer_token=provider_payload["api_key"],
            base_url=provider_payload["base_url"],
            model=provider_payload["model"],
            timeout=timeout,
            vibe_cache_enabled=bool(provider_payload.get("vibe_cache_enabled", False)),
        )
        generate_result = await client.generate_image(content_json=provider_payload["content_json"])
    else:
        raise ValueError(f"未知的 provider: {provider}, resolved_preset={resolved_preset}")

    generate_image_end_time = time.perf_counter()
    logger.info(
        f"{log_prefix} 图片生成完成: {generate_result}, "
        f"generate_time={generate_image_end_time - generate_image_start_time:.2f}s"
    )
    image_bytes = await generate_result.download_bytes(timeout=client.timeout)
    image_size_mb = len(image_bytes) / (1024 * 1024)
    logger.info(
        f"{log_prefix} 图片下载完成: size={image_size_mb:.2f}MB, "
        f"download_time={time.perf_counter() - generate_image_end_time:.2f}s"
    )
    return image_bytes


async def run_draw_to_bytes(**kwargs: Any) -> tuple[bytes, DrawPayload]:
    """便捷封装：resolve_to_payload + generate_image_bytes。

    透传 resolve_to_payload 的全部关键字参数；返回 (图片字节, DrawPayload)。
    """
    get_config = kwargs["get_config"]
    log_prefix = kwargs.get("log_prefix", "")
    payload = await resolve_to_payload(**kwargs)
    image_bytes = await generate_image_bytes(
        get_config,
        provider=payload.provider,
        resolved_preset=payload.resolved_preset,
        provider_payload=payload.provider_payload,
        timeout=payload.timeout,
        log_prefix=log_prefix,
    )
    # continuity：出图成功 → 把本轮 nai_director 输出写回会话态供下次续承（仅 nai_chat；失败安全，不影响已成功的出图）
    if payload.provider == "nai_chat":
        record_previous_context(
            get_config, payload, kwargs.get("action_inputs"), kwargs.get("chat_id"), log_prefix=log_prefix
        )
    return image_bytes, payload
