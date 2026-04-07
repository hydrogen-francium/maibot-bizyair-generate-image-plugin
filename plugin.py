from typing import List, Tuple, Type, Union

from src.common.logger import get_logger
from src.plugin_system import BaseAction, BaseCommand, BaseEventHandler, BasePlugin, BaseTool, ConfigField, register_plugin
from src.plugin_system.base.component_types import ActionInfo, CommandInfo, EventHandlerInfo, PythonDependency, ToolInfo
from .clients import BizyAirOpenApiClient
from .components.generate_image_action import GenerateImageAction

logger = get_logger("bizyair_generate_image_plugin")

DEFAULT_ACTION_PARAMETERS = [
    {
        "name": "prompt",
        "description": "必填，用于生成图片的描述词",
        "required": "必填",
    },
    {
        "name": "aspect_ratio",
        "description": "可选，图片宽高比。若传入，则必须为 1:1、4:3、16:9、9:16、auto 中的一个。默认为 1:1",
        "required": "选填",
    },
    {
        "name": "resolution",
        "description": "可选，图片分辨率。若传入，则必须为 1K、2K、4K、auto 中的一个。默认为 1k",
        "required": "选填",
    },
]

DEFAULT_CUSTOM_VARIABLES = [
    {
        "key": "english_prompt",
        "mode": "llm",
        "values": '["这是一个用于画图的提示词。请将其变成更适合画图ai的英文标签形式。你的输出会被直接输入到绘图ai中，因此请直接输出内容，不要添加多余的解释。以下是提示词: {prompt}"]',
        "probability": 1.0,
    }
]

DEFAULT_OPENAPI_PARAMETER_MAPPINGS = [
    {"field": "18:BizyAir_NanoBananaProOfficial.prompt", "value_type": "string", "value": "{english_prompt}"},
    {"field": "18:BizyAir_NanoBananaProOfficial.aspect_ratio", "value_type": "string", "value": "{aspect_ratio}"},
    {"field": "18:BizyAir_NanoBananaProOfficial.resolution", "value_type": "string", "value": "{resolution}"},
]


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
        "variable_llm_config": "自定义变量 LLM 配置",
    }

    config_schema = {
        "bizyair_client": {
            "bearer_token": ConfigField(
                type=str,
                default="",
                description="BizyAir 的 Bearer Token。留空时生图 action 不可用。",
            ),
            "openapi_url": ConfigField(
                type=str,
                default="https://api.bizyair.cn/w/v1/webapp/task/openapi/create",
                description="BizyAir OpenAPI 的 HTTP 地址。",
            ),
            "openapi_web_app_id": ConfigField(
                type=int,
                default=50835,
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
                    "value_type": {
                        "type": "select",
                        "label": "参数值类型",
                        "choices": ["string", "int", "boolean", "json"],
                        "default": "string",
                    },
                    "value": {
                        "type": "string",
                        "label": "参数值模板",
                        "input_type": "textarea",
                        "placeholder": '可填字符串、数字、布尔值、对象、数组，例如 "{prompt}"、"{random_seed}" 或 {"meta": ["{prompt}"]}',
                    },
                },
                default=DEFAULT_OPENAPI_PARAMETER_MAPPINGS,
                description=(
                    "OpenAPI input_values 参数映射表。每一项必须包含 field、value_type 和 value。"
                    " 支持引用 action_parameters 中定义的任意参数占位符，以及 {random_seed}。"
                    " value 会按 value_type 强制转换为 string、int、boolean 或反序列化为 json。"
                ),
            ),
        },
        "bizyair_generate_image_plugin": {
            "timeout": ConfigField(
                type=float,
                default=180.0,
                description="调用 OpenAPI 和下载图片的超时时间（秒）。",
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
            "enable_rewrite_failure_reply": ConfigField(
                type=bool,
                default=True,
                description="当图片生成 action 失败时，是否调用 LLM 将错误改写为自然语言后发送。",
            ),
            "enable_splitter": ConfigField(
                type=bool,
                default=False,
                description="当启用失败回复重写时，是否对重写结果启用分段发送。",
            ),
            "action_parameters": ConfigField(
                type=list,
                item_type="object",
                item_fields={
                    "name": {
                        "type": "string",
                        "label": "决策参数名",
                        "placeholder": "例如 prompt",
                    },
                    "description": {
                        "type": "string",
                        "label": "参数说明",
                        "placeholder": "例如 用于生成图片的描述词",
                    },
                    "required": {
                        "type": "select",
                        "label": "是否必填",
                        "choices": ["选填", "必填"],
                        "default": "选填",
                    },
                },
                default=DEFAULT_ACTION_PARAMETERS,
                description="generate_image 动作允许决策传入的参数列表。",
            ),
            "custom_variables": ConfigField(
                type=list,
                item_type="object",
                item_fields={
                    "key": {
                        "type": "string",
                        "label": "变量名",
                        "placeholder": "例如 style_hint",
                    },
                    "mode": {
                        "type": "select",
                        "label": "变量值模式",
                        "choices": ["literal", "llm"],
                        "default": "literal",
                    },
                    "values": {
                        "type": "string",
                        "label": "候选值列表",
                        "placeholder": '例如 ["二次元插画", "电影感", "高细节"] ，支持引用 action_inputs 中的 {参数名}',
                    },
                    "probability": {
                        "type": "float",
                        "label": "触发概率",
                        "default": 1.0,
                    },
                },
                default=DEFAULT_CUSTOM_VARIABLES,
                description="自定义变量列表。支持 literal 和 llm 两种模式。两种模式都会先从 values 中随机抽一条，如果是 llm 模式则会调用 llm 生成变量值。支持引用 action_inputs 中的 {参数名}，不允许变量之间互相引用。",
            ),
            "action_require": ConfigField(type=str,
                                          input_type="textarea",
                                          default="\n".join(GenerateImageAction.action_require),
                                          description="图片生成 action 的决策提示词，每行一条。"),
        },
        "variable_llm_config": {
            "llm_group": ConfigField(
                type=str,
                choices=['lpmm_entity_extract', 'lpmm_rdf_build', 'planner', 'replyer', 'tool_use', 'utils', 'vlm'],
                default="utils",
                description="自定义变量生成时使用的 LLM 模型分组。若下方 llm_list 非空，则优先使用 llm_list 覆盖。",
            ),
            "llm_list": ConfigField(
                type=list,
                item_type="string",
                default=[],
                description="自定义变量生成时使用的模型名称列表。为空时使用 llm_group 对应任务配置。",
            ),
            "max_tokens": ConfigField(
                type=int,
                default=512,
                description="自定义变量生成时使用的最大输出 token 数",
            ),
            "temperature": ConfigField(
                type=float,
                default=0.7,
                description="自定义变量生成时使用的温度",
            ),
            "slow_threshold": ConfigField(
                type=float,
                default=30.0,
                description="自定义变量生成时使用的慢请求阈值，单位秒",
            ),
            "selection_strategy": ConfigField(
                type=str,
                choices=['balance', 'random'],
                default="balance",
                description="自定义变量生成时使用的模型选择策略",
            ),
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
        GenerateImageAction.set_action_parameters(
            config.get("action_parameters", DEFAULT_ACTION_PARAMETERS)
        )
        components.append((GenerateImageAction.get_action_info(), GenerateImageAction))
        return components
