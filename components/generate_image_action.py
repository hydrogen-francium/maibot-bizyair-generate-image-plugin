import base64
from typing import Optional, Tuple

from src.common.logger import get_logger
from src.plugin_system import BaseAction
from src.plugin_system.base.component_types import ActionActivationType

from ..bizyair_mcp_client import BizyAirMcpClient, BizyAirMcpError, BizyAirMcpProtocolError

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
        "prompt": "用于生成图片的描述词，应尽量具体，包含主体、风格、场景、构图等信息",
        "aspect_ratio": "可选，图片宽高比。若传入，则必须为 1:1、4:3、16:9、9:16、auto 中的一个。默认为 1:1",
        "resolution": "可选，图片分辨率。若传入，则必须为 1K、2K、4K、auto 中的一个。默认为 1k",
    }

    action_require = [
        "当用户明确要求你画图、生成图片、做一张图、出图时使用",
        "当用户给出自然语言描述并期待得到可直接发送的图片时使用",
        "当图片比纯文字更适合满足需求时使用",
        "如果用户指定了画面比例或横图竖图需求，应填写 aspect_ratio 参数",
        "如果没有明确图片生成需求，不要滥用该动作",
    ]

    async def execute(self) -> Tuple[bool, str]:
        prompt = self._get_prompt()
        if not prompt:
            return False, "[图片生成失败] 缺少 prompt 参数"

        token = self._get_string_config("bizyair_generate_image_plugin.bearer_token", "")
        if not token:
            logger.warning(f"{self.log_prefix} 未配置 BizyAir bearer_token")
            return False, "[图片生成失败] 插件未配置 bearer_token"

        aspect_ratio = self._get_aspect_ratio()
        resolution = self._get_resolution()
        timeout = self._get_timeout()

        logger.info(
            f"{self.log_prefix} 开始生成图片: prompt={prompt!r}, aspect_ratio={aspect_ratio}, resolution={resolution}"
        )

        try:
            client = BizyAirMcpClient(
                bearer_token=token,
                mcp_url=self._get_optional_string_config(
                    "bizyair_generate_image_plugin.mcp_url",
                    BizyAirMcpClient.MCP_URL,
                ),
                timeout=timeout,
            )
            image_bytes = await client.generate_and_download(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
            )
        except ValueError as exc:
            logger.warning(f"{self.log_prefix} 图片参数非法: {exc}")
            return False, f"[图片生成失败] 参数非法: {exc}"
        except (BizyAirMcpError, BizyAirMcpProtocolError) as exc:
            logger.error(f"{self.log_prefix} MCP 调用失败: {exc}")
            return False, f"[图片生成失败] MCP 调用失败: {exc}"
        except Exception as exc:
            logger.exception(f"{self.log_prefix} 生成图片时出现未知异常: {exc}")
            return False, f"[图片生成失败] {exc}"

        if not image_bytes:
            return False, "[图片生成失败] 未获取到图片数据"

        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        if self.get_config("bizyair_generate_image_plugin.send_text_before_image", False):
            prefix_text = self._get_string_config(
                "bizyair_generate_image_plugin.text_before_image",
                "我给你生成了一张图片。",
            )
            if prefix_text:
                await self.send_text(prefix_text, storage_message=True)

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
        return True, "图片生成并发送完成"

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

    def _build_action_display(self, prompt: str, aspect_ratio: str, resolution: str) -> str:
        compact_prompt = prompt.replace("\n", " ").strip()
        if len(compact_prompt) > 80:
            compact_prompt = f"{compact_prompt[:77]}..."
        return (
            f"[图片生成: prompt={compact_prompt}; aspect_ratio={aspect_ratio}; resolution={resolution}]"
        )