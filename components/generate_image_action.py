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
from ..services.builtin_variable_provider import BuiltinVariableProvider
from ..services.custom_variable_resolver import CustomVariableResolver
from ..services.openapi_input_value_builder import BizyAirOpenApiInputValueBuilder

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

    action_parameters = {
        "prompt": "必填，用于生成图片的描述词",
        "aspect_ratio": "可选，图片宽高比。若传入，则必须为 1:1、4:3、16:9、9:16、auto 中的一个。默认为 1:1",
        "resolution": "可选，图片分辨率。若传入，则必须为 1K、2K、4K、auto 中的一个。默认为 1k",
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
        "是否需要你补充、总结或改写 prompt，只取决于用户给出的图片描述是否留有明显空白、是否授权你自由发挥；不要把“尽量生成得更好”当作改写详细原始描述的理由"
    ]

    async def execute(self) -> Tuple[bool, str]:
        """执行生图流程并在失败时返回完整错误信息"""
        try:
            user_id = str(self.user_id)
            has_permission, deny_reason = permission_manager.check_action_permission(user_id)
            if not has_permission:
                return False, deny_reason or "当前用户没有使用该 action 的权限"

            action_inputs = self._collect_action_inputs()
            active_preset = str(self.active_preset).strip()
            app_id = self._resolve_active_app_id(active_preset)
            all_parameter_bindings_config = self.get_config("bizyair_client.openapi_parameter_mappings", [])
            parameter_bindings_config = self._filter_parameter_bindings_by_preset(
                all_parameter_bindings_config, active_preset
            )
            builtin_variable_provider = BuiltinVariableProvider(
                chat_id=self.chat_id,
                filter_mai=False,
            )
            custom_variable_resolver = CustomVariableResolver(
                raw_variables=self.get_config("custom_variables_config.custom_variables", []),
                action_inputs=action_inputs,
                action_parameter_names=set(self.action_parameters.keys()),
                llm_value_factory=self._generate_variable_with_llm,
                builtin_variable_provider=builtin_variable_provider,
            )
            required_variable_keys = custom_variable_resolver.collect_required_variable_keys(parameter_bindings_config)
            custom_variable_values = await custom_variable_resolver.resolve_required_variables(required_variable_keys)
            template_context = {**action_inputs, **custom_variable_values}
            parameter_bindings = BizyAirOpenApiInputValueBuilder.parse_parameter_bindings(parameter_bindings_config)
            required_builtin_names = BizyAirOpenApiInputValueBuilder.collect_builtin_placeholder_names_from_bindings(
                parameter_bindings_config
            )
            builtin_placeholder_values = builtin_variable_provider.build_placeholder_values(required_builtin_names)
            input_values = BizyAirOpenApiInputValueBuilder.build_input_values(
                parameter_bindings=parameter_bindings,
                template_context=template_context,
                action_inputs=action_inputs,
                action_parameter_names=set(self.action_parameters.keys()),
                required_action_parameters=set(self.required_action_parameters),
                builtin_placeholder_values=builtin_placeholder_values,
            )

            token = str(self.get_config("bizyair_client.bearer_token", "")).strip()
            if not token:
                raise ValueError("插件未配置 bearer_token")

            timeout = float(str(self.get_config("bizyair_client.timeout", 180.0)).strip())

            logger.info(
                f"{self.log_prefix} 开始生成图片: provider=openapi, active_preset={active_preset!r}, app_id={app_id}, action_inputs={action_inputs!r}, custom_variable_values={custom_variable_values!r}, timeout={timeout}")

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

        for name in self.action_parameters:
            raw_value = self.action_data.get(name)
            normalized_value = raw_value
            if isinstance(normalized_value, str):
                normalized_value = normalized_value.strip() or None
            if normalized_value is None:
                if name in self.required_action_parameters:
                    missing_required.append(name)
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
        for index, preset in enumerate(app_presets):
            if not isinstance(preset, dict):
                raise ValueError(f"app_presets[{index}] 必须是对象")
            name = str(preset.get("preset_name", "")).strip()
            if name == active_preset:
                raw_app_id = preset.get("app_id")
                if raw_app_id is None:
                    raise ValueError(f"app_presets 中 preset_name={active_preset!r} 的 app_id 为空")
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
            if active_preset in preset_names:
                result.append(item)
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
