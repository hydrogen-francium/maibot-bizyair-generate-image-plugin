import base64
import traceback
from typing import Any, Tuple

from src.common.logger import get_logger
from src.config.api_ada_configs import TaskConfig
from src.config.config import global_config
from src.config.config import model_config
from src.plugin_system import BaseAction
from src.plugin_system.apis import generator_api, llm_api
from src.plugin_system.base.component_types import ActionActivationType
from ..clients import (
    BizyAirOpenApiClient,
)
from ..services import permission_manager
from ..services.action_parameter_utils import ActionParameterDefinition
from ..services.builtin_variable_provider import BuiltinVariableProvider
from ..services.custom_variable_registry import CustomVariableRegistry
from ..services.log_utils import short_repr
from ..services.openapi_input_value_builder import BizyAirOpenApiInputValueBuilder
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
            failure_stage = "permission_check"
            user_id = str(self.user_id)
            has_permission, deny_reason = permission_manager.check_action_permission(user_id)
            if not has_permission:
                return False, deny_reason or "当前用户没有使用该 action 的权限"

            failure_stage = "collect_action_inputs"
            action_inputs = self._collect_action_inputs()
            active_preset = str(self.active_preset).strip()

            failure_stage = "resolve_app_id"
            app_id = self._resolve_active_app_id(active_preset)

            failure_stage = "filter_parameter_bindings"
            all_parameter_bindings_config = self.get_config("bizyair_client.openapi_parameter_mappings", [])
            parameter_bindings_config = self._filter_parameter_bindings_by_preset(
                all_parameter_bindings_config, active_preset
            )
            logger.info(
                f"{self.log_prefix} 生图配置摘要: active_preset={active_preset!r}, app_id={app_id}, "
                f"action_inputs={short_repr(action_inputs)}, custom_variable_keys={list(self.get_config('custom_variables_config.custom_variables', [])) and [str(item.get('key', '')).strip() for item in self.get_config('custom_variables_config.custom_variables', []) if isinstance(item, dict) and str(item.get('key', '')).strip()]}, "
                f"binding_fields={[str(item.get('field', '')).strip() for item in parameter_bindings_config if isinstance(item, dict)]}"
            )
            builtin_variable_provider = BuiltinVariableProvider(
                chat_id=self.chat_id,
                filter_mai=False,
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
            required_builtin_names = BizyAirOpenApiInputValueBuilder.collect_builtin_placeholder_names_from_bindings(
                parameter_bindings_config
            )
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

            failure_stage = "parse_parameter_bindings"
            parameter_bindings = BizyAirOpenApiInputValueBuilder.parse_parameter_bindings(parameter_bindings_config)

            failure_stage = "build_input_values"
            input_values = BizyAirOpenApiInputValueBuilder.build_input_values(
                parameter_bindings=parameter_bindings,
                template_context=template_context,
                action_inputs=resolved_action_inputs,
                action_parameter_names=set(self.action_parameters.keys()),
                required_action_parameters=set(self.required_action_parameters),
                action_parameter_definitions=self.action_parameters,
                builtin_placeholder_values=builtin_placeholder_values,
            )

            failure_stage = "read_token"
            token = str(self.get_config("bizyair_client.bearer_token", "")).strip()
            if not token:
                raise ValueError("插件未配置 bearer_token")

            failure_stage = "read_timeout"
            timeout = float(str(self.get_config("bizyair_client.timeout", 180.0)).strip())

            logger.info(
                f"{self.log_prefix} 图片生成摘要: provider=openapi, active_preset={active_preset!r}, "
                f"app_id={app_id}, "
                f"action_inputs={resolved_action_inputs!r}, "
                f"custom_variable_values={custom_variable_values!r}, "
                f"timeout={timeout}")

            failure_stage = "generate_image_bytes"
            image_bytes = await self._generate_image_bytes(
                token=token,
                app_id=app_id,
                input_values=input_values,
                timeout=timeout,
            )
            image_size_mb = len(image_bytes) / (1024 * 1024)
            logger.info(f"{self.log_prefix} 图片生成完成，已获取图片数据: size={image_size_mb:.2f}MB")

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

    def _resolve_active_app_id(self, active_preset: str) -> int:
        """从 app_presets 中查找 active_preset 对应的 app_id"""
        if not active_preset:
            raise ValueError("active_preset 不能为空，请在配置中设置 active_preset")
        app_presets = self.get_config("bizyair_client.app_presets", [])
        if not isinstance(app_presets, list) or not app_presets:
            raise ValueError("app_presets 未配置或为空列表")
        available_presets = []
        for index, preset in enumerate(app_presets):
            if not isinstance(preset, dict):
                raise ValueError(f"app_presets[{index}] 必须是对象")
            name = str(preset.get("preset_name", "")).strip()
            if name:
                available_presets.append(name)
            if name == active_preset:
                raw_app_id = preset.get("app_id")
                if raw_app_id is None:
                    raise ValueError(f"app_presets 中 preset_name={active_preset!r} 的 app_id 为空")
                logger.info(
                    f"{self.log_prefix} 匹配到 app preset: active_preset={active_preset!r}, "
                    f"app_id={raw_app_id}, available_presets={available_presets}"
                )
                return int(raw_app_id)
        raise ValueError(f"app_presets 中找不到 preset_name={active_preset!r}，请检查 active_preset 配置")

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
            token: str,
            app_id: int,
            input_values: dict[str, Any],
            timeout: float,
    ) -> bytes:
        """创建 OpenAPI 客户端并返回生成后的图片字节"""
        client = BizyAirOpenApiClient(
            bearer_token=token,
            api_url=str(self.get_config("bizyair_client.openapi_url", BizyAirOpenApiClient.API_URL)).strip(),
            web_app_id=app_id,
            timeout=timeout,
        )
        return await client.generate_and_download(input_values=input_values)

    def _build_action_display(self, action_inputs: dict[str, Any]) -> str:
        """构造写入动作记录的简短展示文本"""
        display_parts: list[str] = []
        for key, value in action_inputs.items():
            text = str(value).replace("\n", " ").strip()
            if len(text) > 80:
                text = f"{text[:77]}..."
            display_parts.append(f"{key}={text}")
        return f"[图片生成: {'; '.join(display_parts)}]"
