import base64
from typing import Any, Optional, Tuple

from src.common.logger import get_logger
from src.plugin_system import BaseAction
from src.plugin_system.base.component_types import ActionActivationType
from ..clients import (
    BizyAirMcpClient,
    BizyAirMcpError,
    BizyAirMcpProtocolError,
    BizyAirOpenApiClient,
    BizyAirOpenApiError,
    BizyAirOpenApiProtocolError,
)

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

    action_parameters = {
        "prompt": "必填，用于生成图片的描述词",
        "aspect_ratio": "可选，图片宽高比。若传入，则必须为 1:1、4:3、16:9、9:16、auto 中的一个。默认为 1:1",
        "resolution": "可选，图片分辨率。若传入，则必须为 1K、2K、4K、auto 中的一个。默认为 1k",
    }

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
        prompt = self._get_prompt()
        if not prompt:
            return False, "[图片生成失败] 缺少 prompt 参数"

        token = self._get_string_config("bizyair_client.bearer_token", "")
        if not token:
            logger.warning(f"{self.log_prefix} 未配置 BizyAir bearer_token")
            return False, "[图片生成失败] 插件未配置 bearer_token"

        provider = self._get_provider()
        aspect_ratio = self._get_aspect_ratio()
        resolution = self._get_resolution()
        timeout = self._get_timeout()

        logger.info(
            f"{self.log_prefix} 开始生成图片: provider={provider}, prompt={prompt!r}, "
            f"aspect_ratio={aspect_ratio}, resolution={resolution}"
        )

        try:
            image_bytes = await self._generate_image_bytes(
                provider=provider,
                token=token,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                timeout=timeout,
            )
            image_size_mb = len(image_bytes) / (1024 * 1024)
            logger.info(f"{self.log_prefix} 图片生成完成，已获取图片数据: size={image_size_mb:.2f}MB")
        except ValueError as exc:
            logger.warning(f"{self.log_prefix} 图片参数非法: {exc}")
            return False, f"[图片生成失败] 参数非法: {exc}"
        except (BizyAirMcpError, BizyAirMcpProtocolError) as exc:
            logger.error(f"{self.log_prefix} MCP 调用失败: {exc}")
            return False, f"[图片生成失败] MCP 调用失败: {exc}"
        except (BizyAirOpenApiError, BizyAirOpenApiProtocolError) as exc:
            logger.error(f"{self.log_prefix} OpenAPI 调用失败: {exc}")
            return False, f"[图片生成失败] OpenAPI 调用失败: {exc}"
        except Exception as exc:
            logger.exception(f"{self.log_prefix} 生成图片时出现未知异常: {exc}")
            return False, f"[图片生成失败] {exc}"

        if not image_bytes:
            return False, "[图片生成失败] 未获取到图片数据"

        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        logger.info(f"{self.log_prefix} 图片数据已转换为 base64")

        if self.get_config("bizyair_generate_image_plugin.send_text_before_image", False):
            prefix_text = self._get_string_config("bizyair_generate_image_plugin.text_before_image", "我给你生成了一张图片。", )
            if prefix_text:
                await self.send_text(prefix_text, storage_message=True)
                logger.info(f"{self.log_prefix} 已发送图片前置文本")

        send_success = await self.send_image(image_base64, storage_message=True)
        if not send_success:
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=self._build_action_display(prompt, aspect_ratio, resolution),
                action_done=False,
            )
            return False, "[图片生成失败] 图片发送失败"

        await self.store_action_info(
            action_build_into_prompt=True,
            action_prompt_display=self._build_action_display(prompt, aspect_ratio, resolution),
            action_done=True,
        )
        return True, f"图片生成并发送完成，使用的生图prompt: {prompt}"

    def _get_prompt(self) -> str:
        prompt = self.action_data.get("prompt", "")
        if prompt is None:
            return ""
        return str(prompt).strip()

    def _get_aspect_ratio(self) -> str:
        aspect_ratio = self.action_data.get(
            "aspect_ratio",
            self.get_config("bizyair_generate_image_plugin.default_aspect_ratio", "1:1"),
        )
        return str(aspect_ratio).strip() or "1:1"

    def _get_resolution(self) -> str:
        resolution = self.action_data.get(
            "resolution",
            self.get_config("bizyair_generate_image_plugin.default_resolution", "1K"),
        )
        return str(resolution).strip() or "1K"

    def _get_timeout(self) -> float:
        raw_timeout = self.get_config("bizyair_generate_image_plugin.timeout", 180.0)
        try:
            timeout = float(raw_timeout)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            timeout = 180.0
        return timeout if timeout > 0 else 180.0

    def _get_provider(self) -> str:
        provider = self._get_string_config("bizyair_client.provider", "mcp").lower()
        return provider if provider in {"mcp", "openapi"} else "mcp"

    async def _generate_image_bytes(
            self,
            provider: str,
            token: str,
            prompt: str,
            aspect_ratio: str,
            resolution: str,
            timeout: float,
    ) -> bytes:
        if provider == "openapi":
            parameter_bindings = BizyAirOpenApiClient.parse_parameter_bindings(
                self.get_config(
                    "bizyair_client.openapi_parameter_mappings",
                    BizyAirOpenApiClient.default_parameter_mapping_config(),
                )
            )
            client = BizyAirOpenApiClient(
                bearer_token=token,
                api_url=self._get_optional_string_config("bizyair_client.openapi_url", BizyAirOpenApiClient.API_URL),
                web_app_id=self._get_int_config("bizyair_client.openapi_web_app_id", BizyAirOpenApiClient.WEB_APP_ID),
                timeout=timeout,
                parameter_bindings=parameter_bindings,
            )
            return await client.generate_and_download(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
            )

        client = BizyAirMcpClient(
            bearer_token=token,
            mcp_url=self._get_optional_string_config("bizyair_client.mcp_url", BizyAirMcpClient.MCP_URL),
            timeout=timeout,
        )
        return await client.generate_and_download(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )

    def _get_string_config(self, key: str, default: str) -> str:
        value = self.get_config(key, default)
        if value is None:
            return default
        if isinstance(value, dict):
            return default
        return str(value).strip()

    def _get_optional_string_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        value = self.get_config(key, default)
        if value is None:
            return default
        if isinstance(value, dict):
            return default
        text = str(value).strip()
        return text or default

    def _get_int_config(self, key: str, default: int) -> int:
        value = self.get_config(key, default)
        if value is None or isinstance(value, dict):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _get_raw_config(self, key: str, default: Any) -> Any:
        value = self.get_config(key, default)
        return default if value is None else value

    def _build_action_display(self, prompt: str, aspect_ratio: str, resolution: str) -> str:
        compact_prompt = prompt.replace("\n", " ").strip()
        if len(compact_prompt) > 80:
            compact_prompt = f"{compact_prompt[:77]}..."
        return f"[图片生成: prompt={compact_prompt}; aspect_ratio={aspect_ratio}; resolution={resolution}]"
