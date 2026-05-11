import base64
import time
import traceback
from typing import Any, List, Optional, Tuple

from maim_message import Seg

from ..clients import BizyAirImageResult
from src.common.logger import get_logger
from src.config.api_ada_configs import TaskConfig
from src.config.config import global_config
from src.config.config import model_config
from src.plugin_system import BaseAction
from src.plugin_system.apis import generator_api, llm_api
from src.plugin_system.base.component_types import ActionActivationType
from ..clients import (
    BizyAirOpenApiClient,
    BizyAirOpenApiContentFilterError,
    NaiChatClient,
)
from ..services import permission_manager
from ..services.action_parameter_utils import ActionParameterDefinition
from ..services.builtin_variable_provider import BuiltinVariableProvider
from ..services.content_filter_sanitizer import sanitize_input_values
from ..services.custom_variable_registry import CustomVariableRegistry
from ..services.log_utils import short_repr
from ..services.nai_chat_input_value_builder import NaiChatInputValueBuilder
from ..services.openapi_input_value_builder import BizyAirOpenApiInputValueBuilder
from ..services.preset_resolution import resolve_active_preset
from ..services.variable_dependency_resolver import VariableDependencyResolver

logger = get_logger("bizyair_generate_image_plugin")


class GenerateImageAction(BaseAction):
    action_name = "generate_image"
    action_description = (
        "根据用户的自然语言描述生成一张图片并发送到当前聊天。"
        "该动作只负责发图，不负责文字回复，因此可以与 reply 等动作同时使用。"
    )
    activation_type = ActionActivationType.ALWAYS
    parallel_action = True
    associated_types = ["image", "text"]
    active_preset = "default"
    action_enabled = True

    action_parameters: dict[str, ActionParameterDefinition] = {
        "prompt": ActionParameterDefinition(
            name="prompt",
            description="必填，用于生成图片的描述词",
            required=True,
        ),
        "aspect_ratio": ActionParameterDefinition(
            name="aspect_ratio",
            description="可选，图片宽高比。若传入，则必须为 1:1、4:3、16:9、9:16、auto 中的一个。默认为 1:1",
            required=False,
        ),
        "resolution": ActionParameterDefinition(
            name="resolution",
            description="可选，图片分辨率。若传入，则必须为 1K、2K、4K、auto 中的一个。默认为 1k",
            required=False,
        ),
    }
    required_action_parameters: set[str] = set()

    action_require = [
        "当用户明确要求你画图、生成图片、做一张图、出图时使用",
        "当用户给出自然语言描述并期待得到可直接发送的图片时使用",
        "当图片比纯文字更适合满足需求时使用",
        "如果用户指定了画面比例或横图竖图需求，应填写 aspect_ratio 参数",
        "如果没有明确图片生成需求，不要滥用该动作",
        "prompt 参数只传入与图片生成需求直接相关的内容，不要传入对 bot 的称呼、寒暄、与本次生图无关的上下文或指令包装语；例如“帮我按这个描述生成图片：xxx”里，只传入“xxx”本身",
        "如果用户已经给出了具体、详细、信息量大的图片描述，或明确要求“按这个要求/按这个描述生成”，则 prompt 必须对图片描述部分保持原样、一字不差，不允许改写、压缩、总结、补全或擅自润色",
        "如果用户明确要求你自由发挥、帮他想生图描述词、补充设定，或明确表示让你决定场景/构图/画风/细节，则应由你生成或补全 prompt；但用户已经明确指定的主体、角色、元素、动作、构图要求不得擅自修改",
        "如果用户的描述中包含“随意”“随便”“随机”等表示某些维度可自由决定的意思，则只对这些被放开的维度自行补全细节；用户已明确写出的内容必须保留，不得改动，例如用户指定了角色是“初音未来”，则你只能补充场景、画风等未指定或被明确放开的部分",
        "如果用户的要求过于宽泛，只有大方向、主题或少量标签，无法直接形成高质量生图描述，则应在不违背用户已给约束的前提下，自动补充合理的主体细节、场景、构图、风格、光线、镜头或氛围等内容，整理成更完整的 prompt",
        "prompt 中不允许填写画风相关的提示词，画风应填入 `style` 参数。即使用户明确提出要原样传入提示词，你也应该单独把画风的部分拆出来放到 `style` 参数中",
        "是否需要你补充、总结或改写 prompt，只取决于用户给出的图片描述是否留有明显空白、是否授权你自由发挥；不要把“尽量生成得更好”当作改写详细原始描述的理由"
    ]

    async def execute(self) -> Tuple[bool, str]:
        """执行生图流程并在失败时返回完整错误信息"""
        try:
            if not self.action_enabled:
                await self.send_text("当前图片生成功能未开启", storage_message=True)
                return False, "当前图片生成功能未开启"

            failure_stage = "permission_check"
            user_id = str(self.user_id)
            has_permission, deny_reason = permission_manager.check_action_permission(user_id)
            if not has_permission:
                return False, deny_reason or "当前用户没有使用该 action 的权限"

            failure_stage = "collect_action_inputs"
            action_inputs = self._collect_action_inputs()
            active_preset = str(self.active_preset).strip()

            failure_stage = "resolve_preset"
            resolved_preset = self._resolve_active_preset(active_preset)
            provider = resolved_preset["provider"]

            failure_stage = "filter_parameter_bindings"
            all_parameter_bindings_config = self._get_parameter_bindings_config(provider)
            parameter_bindings_config = self._filter_parameter_bindings_by_preset(
                all_parameter_bindings_config, active_preset
            )
            logger.info(
                f"{self.log_prefix} 生图配置摘要: provider={provider!r}, active_preset={active_preset!r}, resolved_preset={short_repr(resolved_preset)}, "
                f"action_inputs={short_repr(action_inputs)}, custom_variable_keys={list(self.get_config('custom_variables_config.custom_variables', [])) and [str(item.get('key', '')).strip() for item in self.get_config('custom_variables_config.custom_variables', []) if isinstance(item, dict) and str(item.get('key', '')).strip()]}, "
                f"binding_fields={[str(item.get('field', '')).strip() for item in parameter_bindings_config if isinstance(item, dict)]}"
            )
            builtin_variable_provider = BuiltinVariableProvider(
                chat_id=self.chat_id,
                filter_mai=False,
                message_image_base64_provider=self._extract_message_image_base64,
            )
            failure_stage = "build_custom_variable_registry"
            custom_variable_registry = CustomVariableRegistry(
                raw_variables=self.get_config("custom_variables_config.custom_variables", []),
                action_parameter_names=set(self.action_parameters.keys()),
            )
            failure_stage = "collect_required_variables"
            direct_variable_keys = custom_variable_registry.collect_required_variable_keys(parameter_bindings_config)
            builtin_names = BuiltinVariableProvider.get_default_variable_names()
            required_variable_keys = VariableDependencyResolver.compute_required_variable_keys(
                direct_keys=direct_variable_keys,
                action_inputs=action_inputs,
                custom_variable_definitions=custom_variable_registry.variable_definitions,
                action_parameter_names=set(self.action_parameters.keys()),
                builtin_names=builtin_names,
            )
            logger.debug(
                f"{self.log_prefix} 变量依赖摘要: direct_variable_keys={sorted(direct_variable_keys)}, "
                f"required_variable_keys={sorted(required_variable_keys)}"
            )

            failure_stage = "build_builtin_placeholders"
            required_builtin_names = self._collect_builtin_placeholder_names(provider, parameter_bindings_config)
            builtin_placeholder_values = builtin_variable_provider.build_placeholder_values(required_builtin_names)
            logger.debug(
                f"{self.log_prefix} 内置变量摘要: required_builtin_names={sorted(required_builtin_names)}, "
                f"builtin_placeholder_values={short_repr(builtin_placeholder_values)}"
            )

            failure_stage = "build_dependency_resolver"
            dependency_resolver = VariableDependencyResolver(
                action_inputs=action_inputs,
                custom_variable_definitions=custom_variable_registry.variable_definitions,
                action_parameter_names=set(self.action_parameters.keys()),
                builtin_names=builtin_names,
                required_custom_variable_keys=required_variable_keys,
            )
            failure_stage = "resolve_variables"
            resolved_action_inputs, custom_variable_values = await dependency_resolver.resolve_all(
                builtin_placeholder_values=builtin_placeholder_values,
                llm_value_factory=self._generate_variable_with_llm,
                builtin_variable_provider=builtin_variable_provider,
            )
            template_context = {**resolved_action_inputs, **custom_variable_values}

            failure_stage = "build_provider_payload"
            provider_payload, timeout = await self._build_provider_payload(
                provider=provider,
                resolved_preset=resolved_preset,
                parameter_bindings_config=parameter_bindings_config,
                template_context=template_context,
                resolved_action_inputs=resolved_action_inputs,
                builtin_placeholder_values=builtin_placeholder_values,
            )

            logger.info(
                f"{self.log_prefix} 图片生成摘要: provider={provider}, active_preset={active_preset!r}, "
                f"resolved_preset={short_repr(resolved_preset)}, "
                f"action_inputs={resolved_action_inputs!r}, "
                f"custom_variable_values={custom_variable_values!r}, "
                f"timeout={timeout}")

            failure_stage = "generate_image_bytes"
            image_bytes = await self._generate_image_bytes(
                provider=provider,
                resolved_preset=resolved_preset,
                provider_payload=provider_payload,
                timeout=timeout,
            )

            if not image_bytes:
                raise ValueError("未获取到图片数据")

            image_base64 = base64.b64encode(image_bytes).decode("utf-8")
            logger.info(f"{self.log_prefix} 图片数据已转换为 base64")

            if bool(self.get_config("bizyair_generate_image_plugin.send_text_before_image", False)):
                prefix_text = str(self.get_config("bizyair_generate_image_plugin.text_before_image", "我给你生成了一张图片。"))
                if prefix_text:
                    await self.send_text(prefix_text, storage_message=True)
                    logger.info(f"{self.log_prefix} 已发送图片前置文本")

            failure_stage = "send_image"
            send_success = await self.send_image(image_base64, storage_message=True)
            if not send_success:
                await self.store_action_info(action_build_into_prompt=True, action_prompt_display=self._build_action_display(action_inputs), action_done=False, )
                raise RuntimeError("图片发送失败")

            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=self._build_action_display(action_inputs),
                action_done=True,
            )
            return True, f"图片生成并发送完成，使用的参数: {template_context}"
        except Exception as exc:
            stack_trace = traceback.format_exc()
            logger.info(f"{self.log_prefix} 生图流程失败阶段: {locals().get('failure_stage', 'unknown')}")
            logger.error(f"{self.log_prefix} generate_image 执行失败: {exc}\n{stack_trace}")
            raw_reply = f"[图片生成失败] {type(exc).__name__}: {exc}\n调用栈:\n{stack_trace}"
            await self._send_failure_reply(raw_reply)
            return False, raw_reply

    def _extract_message_image_base64(self, message_segment_list: Optional[List[Seg]] = None) -> Optional[str]:
        """
        从消息段中递归提取第一张图片的 base64 数据

        消息段按用户发送消息体顺序排列，引用消息的段排在最前方，
        因此从前往后遍历天然优先提取引用消息中的图片。
        消息段可能嵌套（type="seglist" 时 data 为子消息段列表），递归展开搜索。
        若引用消息和当前消息均无图片，返回 None

        :param message_segment_list: Optional[List[Seg]]，待搜索的消息段列表，首次调用时为 None 表示从 chat_stream 获取
        :return: Optional[str]，图片的无前缀 base64 字符串，或 None
        """

        if message_segment_list is None:
            root_segment: Seg = self.chat_stream.context.message.message_segment
            logger.debug(f"message_segment: {root_segment}")
            if root_segment.type == "seglist":
                message_segment_list = root_segment.data
            else:
                message_segment_list = [root_segment]
            # is_root 标记首次调用，用于控制日志输出
            is_root = True
        else:
            is_root = False

        if not message_segment_list:
            if is_root:
                logger.warning(f"{self.log_prefix} 没有从消息中找到图片 base64！")
            return None

        for segment in message_segment_list:
            if segment.type == "seglist":
                # 递归展开嵌套的消息段列表
                result = self._extract_message_image_base64(segment.data)
                if result:
                    return result
            elif segment.type in ("image", "emoji"):
                if segment.data and isinstance(segment.data, str):
                    return segment.data

        if is_root:
            logger.warning(f"{self.log_prefix} 没有从消息中找到图片 base64！")
        return None

    async def _generate_variable_with_llm(self, prompt: str) -> str:
        """使用变量 LLM 配置生成最终变量值"""
        logger.info(f"[自定义变量] 调用 LLM 生成变量值，提示词: {prompt!r}")
        success, content, _, _ = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=self._build_variable_task_config(),
            request_type="bizyair_custom_variable_generation",
        )
        logger.info(f"[自定义变量] LLM 原始输出: {content!r}")
        if not success:
            raise RuntimeError(f"LLM 生成自定义变量失败: {content}")
        return content.strip()

    def _build_variable_task_config(self) -> TaskConfig:
        """优先按显式配置构造变量生成任务配置"""
        llm_list = self.get_config("variable_llm_config.llm_list", [])
        if isinstance(llm_list, list) and llm_list:
            return TaskConfig(
                model_list=[str(item).strip() for item in llm_list if str(item).strip()],
                max_tokens=int(str(self.get_config("variable_llm_config.max_tokens", 512)).strip()),
                temperature=float(str(self.get_config("variable_llm_config.temperature", 0.7)).strip()),
                slow_threshold=float(str(self.get_config("variable_llm_config.slow_threshold", 30.0)).strip()),
                selection_strategy=str(self.get_config("variable_llm_config.selection_strategy", "balance")).strip(),
            )

        llm_group = str(self.get_config("variable_llm_config.llm_group", "utils")).strip()
        return model_config.model_task_config.get_task(llm_group)

    def _collect_action_inputs(self) -> dict[str, Any]:
        """收集当前动作输入并校验必填项是否缺失"""
        collected: dict[str, Any] = {}
        missing_required: list[str] = []

        for name, definition in self.action_parameters.items():
            raw_value = self.action_data.get(name)
            normalized_value = raw_value
            if isinstance(normalized_value, str):
                normalized_value = normalized_value.strip() or None

            if normalized_value is None:
                if definition.required:
                    missing_required.append(name)
                    continue
                if definition.missing_behavior == "use_default":
                    collected[name] = definition.default_value
                continue

            collected[name] = normalized_value

        if missing_required:
            raise ValueError(f"缺少必填参数: {', '.join(missing_required)}")

        return collected

    async def _send_failure_reply(self, raw_reply: str) -> None:
        """发送失败提示并按配置决定是否改写回复"""
        if bool(self.get_config("bizyair_generate_image_plugin.enable_rewrite_failure_reply", True)):
            rewrite_data = {
                "raw_reply": raw_reply,
                "reason": "用户请求生成图片，但动作执行失败。请基于失败原因改写成简洁自然的中文回复。",
            }
            try:
                result_status, data = await generator_api.rewrite_reply(
                    chat_stream=self.chat_stream,
                    reply_data=rewrite_data,
                    enable_chinese_typo=global_config.chinese_typo.enable,
                    enable_splitter=bool(self.get_config("bizyair_generate_image_plugin.enable_splitter", False)),
                )
                if result_status and data and data.reply_set and data.reply_set.reply_data:
                    for reply_seg in data.reply_set.reply_data:
                        send_data = reply_seg.content
                        if isinstance(send_data, str) and send_data:
                            await self.send_text(send_data, storage_message=True)
                    return
                logger.warning(f"{self.log_prefix} 失败回复重写失败，回退原始消息")
            except Exception as exc:
                logger.exception(f"{self.log_prefix} 失败回复重写异常: {exc}")

        await self.send_text(raw_reply, storage_message=True)

    def _resolve_active_preset(self, active_preset: str) -> dict[str, Any]:
        """从两个预设列表中查找 active_preset 对应的完整预设配置"""
        return resolve_active_preset(
            active_preset=active_preset,
            bizyair_presets=self.get_config("bizyair_client.app_presets", []),
            nai_presets=self.get_config("nai_chat_client.presets", []),
        )

    def _get_parameter_bindings_config(self, provider: str) -> list:
        """按后端读取参数映射配置"""
        if provider == "bizyair_openapi":
            return self.get_config("bizyair_client.openapi_parameter_mappings", [])
        if provider == "nai_chat":
            return self.get_config("nai_chat_client.parameter_mappings", [])
        raise ValueError(f"未知的 provider: {provider}")

    def _collect_builtin_placeholder_names(self, provider: str, parameter_bindings_config: Any) -> set[str]:
        """按后端提取本次需要构造的内置变量名"""
        if provider == "bizyair_openapi":
            return BizyAirOpenApiInputValueBuilder.collect_builtin_placeholder_names_from_bindings(parameter_bindings_config)
        if provider == "nai_chat":
            return NaiChatInputValueBuilder.collect_builtin_placeholder_names_from_bindings(parameter_bindings_config)
        raise ValueError(f"未知的 provider: {provider}")

    async def _build_provider_payload(
            self,
            provider: str,
            resolved_preset: dict[str, Any],
            parameter_bindings_config: list,
            template_context: dict[str, Any],
            resolved_action_inputs: dict[str, Any],
            builtin_placeholder_values: dict[str, Any],
    ) -> tuple[dict[str, Any], float]:
        """按后端构造请求载荷与超时配置"""
        preset = resolved_preset["preset"]

        if provider == "bizyair_openapi":
            parameter_bindings = BizyAirOpenApiInputValueBuilder.parse_parameter_bindings(parameter_bindings_config)
            token = str(self.get_config("bizyair_client.bearer_token", "")).strip()
            if not token:
                raise ValueError("插件未配置 bizyair_client.bearer_token")
            input_values = await BizyAirOpenApiInputValueBuilder.build_input_values(
                parameter_bindings=parameter_bindings,
                template_context=template_context,
                action_inputs=resolved_action_inputs,
                action_parameter_names=set(self.action_parameters.keys()),
                required_action_parameters=set(self.required_action_parameters),
                action_parameter_definitions=self.action_parameters,
                builtin_placeholder_values=builtin_placeholder_values,
                upload_api_key=token,
            )
            timeout = float(str(self.get_config("bizyair_client.timeout", 180.0)).strip())
            raw_app_id = preset.get("app_id")
            if raw_app_id is None:
                raise ValueError(f"BizyAir 预设 {self.active_preset!r} 的 app_id 为空")
            logger.info(f"[参数构造] 最终 input_values: {input_values}")
            return {
                "token": token,
                "app_id": int(raw_app_id),
                "input_values": input_values,
            }, timeout

        if provider == "nai_chat":
            parameter_bindings = NaiChatInputValueBuilder.parse_parameter_bindings(parameter_bindings_config)
            content_json = await NaiChatInputValueBuilder.build_message_content_json(
                parameter_bindings=parameter_bindings,
                template_context=template_context,
                action_inputs=resolved_action_inputs,
                action_parameter_names=set(self.action_parameters.keys()),
                required_action_parameters=set(self.required_action_parameters),
                action_parameter_definitions=self.action_parameters,
                builtin_placeholder_values=builtin_placeholder_values,
            )
            api_key = str(preset.get("api_key", "")).strip()
            base_url = str(preset.get("base_url", "")).strip()
            model = str(preset.get("model", "")).strip()
            if not api_key:
                raise ValueError(f"NAI 预设 {self.active_preset!r} 的 api_key 为空")
            if not base_url:
                raise ValueError(f"NAI 预设 {self.active_preset!r} 的 base_url 为空")
            if not model:
                raise ValueError(f"NAI 预设 {self.active_preset!r} 的 model 为空")
            timeout = float(str(self.get_config("nai_chat_client.timeout", 180.0)).strip())
            logger.info(f"[参数构造] 最终 content_json: {content_json}")
            return {
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
                "content_json": content_json,
            }, timeout

        raise ValueError(f"未知的 provider: {provider}")

    @staticmethod
    def _filter_parameter_bindings_by_preset(
            all_bindings: Any,
            active_preset: str,
    ) -> list:
        """过滤出与 active_preset 匹配的参数映射条目"""
        if not isinstance(all_bindings, list):
            return []
        result = []
        for index, item in enumerate(all_bindings):
            if not isinstance(item, dict):
                raise ValueError(f"openapi_parameter_mappings[{index}] 必须是对象")
            raw_preset_name = item.get("preset_name", "")
            if not raw_preset_name or not str(raw_preset_name).strip():
                raise ValueError(f"openapi_parameter_mappings[{index}].preset_name 不能为空")
            preset_names = {p.strip() for p in str(raw_preset_name).split(",") if p.strip()}
            field_name = str(item.get("field", "")).strip() or f"index={index}"
            matched = active_preset in preset_names
            logger.debug(
                f"[参数映射过滤] field={field_name!r}, preset_names={sorted(preset_names)}, "
                f"active_preset={active_preset!r}, matched={matched}"
            )
            if active_preset in preset_names:
                result.append(item)
        logger.info(
            f"[参数映射过滤] active_preset={active_preset!r}, total={len(all_bindings)}, matched={len(result)}"
        )
        return result

    async def _generate_image_bytes(
            self,
            provider: str,
            resolved_preset: dict[str, Any],
            provider_payload: dict[str, Any],
            timeout: float,
    ) -> bytes:
        """按后端创建客户端并返回生成后的图片字节"""
        generate_image_start_time = time.perf_counter()
        generate_result: BizyAirImageResult
        client: BizyAirOpenApiClient | NaiChatClient
        if provider == "bizyair_openapi":
            client = BizyAirOpenApiClient(
                bearer_token=provider_payload["token"],
                api_url=str(self.get_config("bizyair_client.openapi_url", BizyAirOpenApiClient.API_URL)).strip(),
                web_app_id=provider_payload["app_id"],
                timeout=timeout,
            )
            try:
                generate_result = await client.generate_image(input_values=provider_payload["input_values"])
            except BizyAirOpenApiContentFilterError as filter_err:
                sanitized_inputs = sanitize_input_values(provider_payload["input_values"])
                if sanitized_inputs == provider_payload["input_values"]:
                    logger.warning(
                        f"{self.log_prefix} 触发 422 内容审核但清洗后 input_values 未变化，放弃重试"
                    )
                    raise
                logger.warning(
                    f"{self.log_prefix} 触发 422 内容审核，剔除高危 tag 后重试一次: {filter_err}"
                )
                generate_result = await client.generate_image(input_values=sanitized_inputs)

        elif provider == "nai_chat":
            client = NaiChatClient(
                bearer_token=provider_payload["api_key"],
                base_url=provider_payload["base_url"],
                model=provider_payload["model"],
                timeout=timeout,
            )
            generate_result = await client.generate_image(content_json=provider_payload["content_json"])
        else:
            raise ValueError(f"未知的 provider: {provider}, resolved_preset={resolved_preset}")

        generate_image_end_time = time.perf_counter()
        generate_image_elapsed_seconds = generate_image_end_time - generate_image_start_time
        logger.info(f"{self.log_prefix} 图片生成完成: {generate_result}, generate_time={generate_image_elapsed_seconds:.2f}s")
        image_bytes = await generate_result.download_bytes(timeout=client.timeout)
        download_time = time.perf_counter() - generate_image_end_time
        image_size_mb = len(image_bytes) / (1024 * 1024)
        logger.info(f"{self.log_prefix} 图片下载完成，已获取图片数据: size={image_size_mb:.2f}MB, download_time={download_time:.2f}s")
        return image_bytes

    def _build_action_display(self, action_inputs: dict[str, Any]) -> str:
        """构造写入动作记录的简短展示文本"""
        display_parts: list[str] = []
        for key, value in action_inputs.items():
            text = str(value).replace("\n", " ").strip()
            if len(text) > 80:
                text = f"{text[:77]}..."
            display_parts.append(f"{key}={text}")
        return f"[图片生成: {'; '.join(display_parts)}]"
