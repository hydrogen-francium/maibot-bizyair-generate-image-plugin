from typing import List, Tuple, Type, Union

from src.common.logger import get_logger
from src.plugin_system import BaseAction, BaseCommand, BaseEventHandler, BasePlugin, BaseTool, ConfigField, register_plugin
from src.plugin_system.base.component_types import ActionInfo, CommandInfo, EventHandlerInfo, PythonDependency, ToolInfo
from .components.generate_image_action import GenerateImageAction
from .clients import BizyAirMcpClient, BizyAirOpenApiClient

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
        "bizyair_client": "BizyAir 接口连接配置",
        "bizyair_generate_image_plugin": "BizyAir 文生图 Action 配置",
    }

    config_schema = {
        "bizyair_client": {
            "provider": ConfigField(
                type=str,
                choices=["mcp", "openapi"],
                default="openapi",
                description="选择当前使用的 BizyAir 接口类型。mcp 为 Streamable MCP，openapi 为 HTTP OpenAPI。",
            ),
            "bearer_token": ConfigField(
                type=str,
                default="",
                description="BizyAir 的 Bearer Token。留空时生图 action 不可用。",
            ),
            "mcp_url": ConfigField(
                type=str,
                default=BizyAirMcpClient.MCP_URL,
                description="BizyAir MCP 的 Streamable HTTP 地址。",
            ),
            "openapi_url": ConfigField(
                type=str,
                default=BizyAirOpenApiClient.API_URL,
                description="BizyAir OpenAPI 的 HTTP 地址。",
            ),
            "openapi_web_app_id": ConfigField(
                type=int,
                default=BizyAirOpenApiClient.WEB_APP_ID,
                description="BizyAir OpenAPI 的 web_app_id。",
            ),
            "openapi_parameter_mappings": ConfigField(
                type=list,
                item_type="object",
                item_fields={
                    "field": {
                        "type": "string",
                        "label": "OpenAPI 参数名",
                        "placeholder": "例如 8:BizyAir_NanoBanana2.prompt",
                    },
                    "value": {
                        "type": "json",
                        "label": "参数值模板",
                        "placeholder": '可填字符串、数字、布尔值、对象、数组，例如 "{prompt}"、"{random_seed}" 或 {"meta": ["{prompt}"]}',
                    },
                },
                default=BizyAirOpenApiClient.default_parameter_mapping_config(),
                description=(
                    "OpenAPI input_values 参数映射表。每一项必须包含 field 和 value。"
                    " 支持占位符：{prompt}、{aspect_ratio}、{resolution}、{random_seed}。"
                    " value 支持 JSON 类型，可填写字符串、数字、布尔值、对象或数组。"
                ),
            ),
        },
        "bizyair_generate_image_plugin": {
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
                description="是否在发送图片前额外发送一段提示文本",
            ),
            "text_before_image": ConfigField(
                type=str,
                default="我给你生成了一张图片",
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
