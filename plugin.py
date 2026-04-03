from typing import List, Tuple, Type, Union

from src.common.logger import get_logger
from src.plugin_system import BaseAction, BaseCommand, BaseEventHandler, BasePlugin, BaseTool, ConfigField, register_plugin
from src.plugin_system.base.component_types import ActionInfo, CommandInfo, EventHandlerInfo, PythonDependency, ToolInfo
from .components.generate_image_action import GenerateImageAction

logger = get_logger("bizyair_generate_image_plugin")


# ===== 插件注册 =====


@register_plugin
class BizyAirGenerateImagePlugin(BasePlugin):
    # 插件基本信息
    plugin_name: str = "bizyair_generate_image_plugin"
    enable_plugin: bool = True
    dependencies: List[str] = []
    python_dependencies: List[PythonDependency] = []
    config_file_name: str = "config.toml"

    config_section_descriptions = {
        "bizyair_generate_image_plugin": "BizyAir 文生图 Action 配置",
    }

    config_schema = {
        "bizyair_generate_image_plugin": {
            "bearer_token": ConfigField(
                type=str,
                default="",
                description="BizyAir MCP 的 Bearer Token。留空时 action 不可用。",
            ),
            "mcp_url": ConfigField(
                type=str,
                default="https://api.bizyair.cn/w/v1/mcp/232",
                description="BizyAir MCP 的 Streamable HTTP 地址。",
            ),
            "timeout": ConfigField(
                type=float,
                default=180.0,
                description="调用 MCP 和下载图片的超时时间（秒）。",
            ),
            "default_aspect_ratio": ConfigField(
                type=str,
                default="1:1",
                description="默认宽高比，可选值如 1:1、4:3、16:9、9:16、auto。",
            ),
            "default_resolution": ConfigField(
                type=str,
                default="1K",
                description="默认分辨率，可选值为 1K、2K、4K、auto。",
            ),
            "send_text_before_image": ConfigField(
                type=bool,
                default=False,
                description="是否在发送图片前额外发送一段提示文本。默认关闭，避免与 reply action 职责重叠。",
            ),
            "text_before_image": ConfigField(
                type=str,
                default="我给你生成了一张图片。",
                description="发送图片前的提示文本，仅在开启 send_text_before_image 时生效。",
            ),
            "action_require": ConfigField(type=str,
                                          input_type="textarea",
                                          default="\n".join(GenerateImageAction.action_require),
                                          description="图片生成 action 的决策提示词，每行一条。"),
        }
    }

    def get_plugin_components(
            self,
    ) -> List[
        Union[
            Tuple[ActionInfo, Type[BaseAction]],
            Tuple[CommandInfo, Type[BaseCommand]],
            Tuple[EventHandlerInfo, Type[BaseEventHandler]],
            Tuple[ToolInfo, Type[BaseTool]],
        ]
    ]:
        components: List[
            Union[
                Tuple[ActionInfo, Type[BaseAction]],
                Tuple[CommandInfo, Type[BaseCommand]],
                Tuple[EventHandlerInfo, Type[BaseEventHandler]],
                Tuple[ToolInfo, Type[BaseTool]],
            ]
        ] = []
        config = self.config.get("bizyair_generate_image_plugin", {})
        if raw_action_require := config.get("action_require"):
            GenerateImageAction.action_require = [line.strip() for line in raw_action_require.split("\n") if line.strip()]
        components.append((GenerateImageAction.get_action_info(), GenerateImageAction))
        return components
