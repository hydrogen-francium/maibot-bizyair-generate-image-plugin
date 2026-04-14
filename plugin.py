from typing import List, Tuple, Type, Union

from src.common.logger import get_logger
from src.plugin_system import BaseAction, BaseCommand, BaseEventHandler, BasePlugin, BaseTool, ConfigField, register_plugin
from src.plugin_system.base.component_types import ActionInfo, CommandInfo, EventHandlerInfo, PythonDependency, ToolInfo
from src.plugin_system.base.config_types import ConfigLayout, ConfigTab
from .components.dr_commands import DrListCommand, DrUseCommand
from .components.generate_image_action import GenerateImageAction
from .services import build_action_parameters, permission_manager

logger = get_logger("bizyair_generate_image_plugin")

DEFAULT_ACTION_PARAMETERS = [
    {
        "name": "prompt",
        "description": "必填，用于生成图片的描述词",
        "required": "必填",
    },
    {
        "name": "style",
        "description": "必填，用于生图的画风。内置 2 种画风，分别为 “二次元画风” 和 “写实风” 。你可以直接在该参数中使用自然语言描述一种画风，也可以直接使用内置的模板。要使用内置画风，必须使用 “{二次元画风}” 这种用大括号括起来的变量引用形式。除非用户有具体要求，否则你应该优先使用优化过的内置画风提示词",
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
        "values": '["这是一个用于画图的提示词。请将其变成更适合画图ai的英文标签形式。你的输出会被直接输入到绘图ai中，因此请直接输出内容，不要添加多余的解释。以下是图片要求的画风: {style}\n\n以下是生图的描述词: {prompt}"]',
        "probability": 1.0,
    },
    {
        "key": "二次元画风",
        "mode": "literal",
        "values": '["masterpiece, best quality, ultra-detailed, digital illustration, pixiv style, anime aesthetic, vibrant color palette, clean lineart, soft cel shading, volumetric lighting, cinematic atmosphere, sharp focus, high-resolution 2D art, refined brushwork, colorful glow, intricate details."]',
        "probability": 1.0,
    },
    {
        "key": "写实风",
        "mode": "literal",
        "values": '["masterpiece, best quality, photorealistic, hyper-realistic, 8k UHD, RAW photo, professional photography, cinematic lighting, ray tracing, global illumination, realistic textures, subsurface scattering, shallow depth of field, sharp focus, film grain, high dynamic range, optical lens flare."]',
        "probability": 1.0,
    },
]

BUILTIN_VARIABLE_DESCRIPTIONS = [
    "{random_seed}：一个随机的 32 位整数",
    "{current_datetime}：当前本地日期时间，格式为 YYYY-MM-DD HH:MM:SS",
    "{recent_chat_context_10}：当前聊天最近 10 条聊天记录的可读文本。",
    "{recent_chat_context_30}：当前聊天最近 30 条聊天记录的可读文本",
    "{recent_chat_context_50}：当前聊天最近 50 条聊天记录的可读文本",
]

DEFAULT_APP_PRESETS = [
    {
        "preset_name": "default",
        "app_id": 50835,
        "description": "默认 BizyAir App",
    },
]

DEFAULT_OPENAPI_PARAMETER_MAPPINGS = [
    {"preset_name": "default", "field": "18:BizyAir_NanoBananaProOfficial.prompt", "value_type": "string", "value": "{english_prompt}"},
    {"preset_name": "default", "field": "18:BizyAir_NanoBananaProOfficial.aspect_ratio", "value_type": "string", "value": "{aspect_ratio}"},
    {"preset_name": "default", "field": "18:BizyAir_NanoBananaProOfficial.resolution", "value_type": "string", "value": "{resolution}"},
]

DEFAULT_NAI_PRESETS = [
    {
        "preset_name": "nai_default",
        "description": "默认 NAI Chat 预设",
        "base_url": "https://your-domain.example.com/v1",
        "api_key": "",
        "model": "nai-diffusion-4-5-full-anlas-0",
    },
]

DEFAULT_NAI_PARAMETER_MAPPINGS = [
    {"preset_name": "nai_default", "field": "prompt", "value_type": "string", "value": "{english_prompt}"},
    {"preset_name": "nai_default", "field": "size", "value_type": "json", "value": "[832, 1216]"},
    {"preset_name": "nai_default", "field": "steps", "value_type": "int", "value": "23"},
]

DEFAULT_PERMISSION_USER_LIST = []


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
        "nai_chat_client": "NAI Chat 接口连接配置",
        "bizyair_generate_image_plugin": "BizyAir 文生图 Action 配置",
        "permission_control": "权限管理配置",
        "custom_variables_config": "自定义变量配置",
        "variable_llm_config": "自定义变量 LLM 配置",
    }

    config_layout = ConfigLayout(
        type="tabs",
        tabs=[
            ConfigTab(
                id="client",
                title="BizyAir 配置",
                sections=["bizyair_client"],
                icon="plug",
                order=1,
            ),
            ConfigTab(
                id="nai_chat",
                title="NAI 配置",
                sections=["nai_chat_client"],
                icon="message-circle",
                order=2,
            ),
            ConfigTab(
                id="generate_image",
                title="生图动作",
                sections=["bizyair_generate_image_plugin"],
                icon="image",
                order=3,
            ),
            ConfigTab(
                id="permission_control",
                title="权限管理",
                sections=["permission_control"],
                icon="shield",
                order=4,
            ),
            ConfigTab(
                id="custom_variables",
                title="自定义变量",
                sections=["custom_variables_config"],
                icon="variable",
                order=5,
            ),
            ConfigTab(
                id="variable_llm",
                title="变量 LLM",
                sections=["variable_llm_config"],
                icon="bot",
                order=6,
            ),
        ],
    )

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
            "app_presets": ConfigField(
                type=list,
                item_type="object",
                item_fields={
                    "preset_name": {
                        "type": "string",
                        "label": "预设名称",
                        "placeholder": "例如 flux_portrait，全局唯一，不可重复",
                    },
                    "app_id": {
                        "type": "int",
                        "label": "App ID",
                        "placeholder": "例如 50835",
                    },
                    "description": {
                        "type": "string",
                        "label": "App 描述",
                        "placeholder": "例如 默认人像生图应用，仅用于备注",
                    },
                },
                default=DEFAULT_APP_PRESETS,
                description="App 预设列表。每个预设对应一个 BizyAir App ID，preset_name 全局唯一；description 仅用于备注，不参与运行时逻辑。",
            ),
            "timeout": ConfigField(
                type=float,
                default=180.0,
                description="调用 OpenAPI 和下载图片的超时时间（秒）。",
            ),
            "openapi_parameter_mappings": ConfigField(
                type=list,
                item_type="object",
                item_fields={
                    "preset_name": {
                        "type": "string",
                        "label": "关联预设",
                        "placeholder": "例如 default 或 flux_portrait,anime（多个用英文逗号分隔，不可为空）",
                    },
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
                    "send_if_empty": {
                        "type": "bool",
                        "label": "值为空时仍传参",
                        "default": False,
                    },
                },
                default=DEFAULT_OPENAPI_PARAMETER_MAPPINGS,
                description=(
                    "OpenAPI input_values 参数映射表。每一项必须包含 preset_name、field、value_type、value。"
                    " preset_name 不可为空，可填多个预设名（英文逗号分隔），运行时只加载与 active_preset 匹配的条目。"
                    " 支持引用 action_parameters、自定义变量以及内置变量占位符。"
                    " value 会按 value_type 强制转换为 string、int、boolean 或反序列化为 json。"
                    " 当解析结果为空字符串、null、空数组或空对象时，默认跳过该参数；可通过 send_if_empty 控制是否仍然传参。"
                    f" 当前内置变量包括：{' '.join(BUILTIN_VARIABLE_DESCRIPTIONS)}"
                ),
            ),
        },
        "nai_chat_client": {
            "presets": ConfigField(
                type=list,
                item_type="object",
                item_fields={
                    "preset_name": {
                        "type": "string",
                        "label": "预设名称",
                        "placeholder": "例如 nai_default，全局唯一，不可重复",
                    },
                    "description": {
                        "type": "string",
                        "label": "预设描述",
                        "placeholder": "例如 默认 NAI Chat 模型，仅用于备注",
                    },
                    "base_url": {
                        "type": "string",
                        "label": "基础地址",
                        "placeholder": "例如 https://your-domain.example.com/v1",
                    },
                    "api_key": {
                        "type": "string",
                        "label": "API Key",
                        "placeholder": "例如 sk-xxx",
                    },
                    "model": {
                        "type": "string",
                        "label": "模型名",
                        "placeholder": "例如 nai-diffusion-4-5-full-anlas-0",
                    },
                },
                default=DEFAULT_NAI_PRESETS,
                description="NAI Chat 预设列表。每个预设内单独维护 base_url、api_key、model；preset_name 必须全局唯一。",
            ),
            "parameter_mappings": ConfigField(
                type=list,
                item_type="object",
                item_fields={
                    "preset_name": {
                        "type": "string",
                        "label": "关联预设",
                        "placeholder": "例如 nai_default 或 nai_portrait,nai_landscape（多个用英文逗号分隔，不可为空）",
                    },
                    "field": {
                        "type": "string",
                        "label": "JSON 字段名",
                        "placeholder": "例如 prompt、size、steps",
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
                        "placeholder": '可填字符串、数字、布尔值、对象、数组，例如 "{prompt}"、"{random_seed}" 或 [832, 1216]',
                    },
                    "send_if_empty": {
                        "type": "bool",
                        "label": "值为空时仍传参",
                        "default": False,
                    },
                },
                default=DEFAULT_NAI_PARAMETER_MAPPINGS,
                description=(
                    "NAI Chat user message JSON 参数映射表。每一项必须包含 preset_name、field、value_type、value。"
                    " preset_name 不可为空，可填多个预设名（英文逗号分隔），运行时只加载与 active_preset 匹配的条目。"
                    " 解析结果会组装为 messages[0].content 对应的 JSON 字符串。"
                    " 支持引用 action_parameters、自定义变量以及内置变量占位符。"
                    " value 会按 value_type 强制转换为 string、int、boolean 或反序列化为 json。"
                    " 当解析结果为空字符串、null、空数组或空对象时，默认跳过该参数；可通过 send_if_empty 控制是否仍然传参。"
                    f" 当前内置变量包括：{' '.join(BUILTIN_VARIABLE_DESCRIPTIONS)}"
                ),
            ),
            "timeout": ConfigField(
                type=float,
                default=180.0,
                description="调用 NAI Chat 和解析图片的超时时间（秒）。",
            ),
        },
        "bizyair_generate_image_plugin": {
            "active_preset": ConfigField(
                type=str,
                default="default",
                description="当前激活的生图预设名称。会同时在 BizyAir 与 NAI 两类预设中查找，必须全局唯一。",
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
                        "label": "是否动作必填参数",
                        "choices": ["选填", "必填"],
                        "default": "选填",
                    },
                    "missing_behavior": {
                        "type": "select",
                        "label": "选填参数缺失时的处理方式",
                        "choices": ["keep_placeholder", "raise_error", "use_default"],
                        "default": "keep_placeholder",
                    },
                    "default_value": {
                        "type": "string",
                        "label": "缺失时的默认值",
                        "placeholder": "仅 missing_behavior=use_default 时生效，可为空字符串",
                    },
                },
                default=DEFAULT_ACTION_PARAMETERS,
                description="generate_image 动作允许决策传入的参数列表。必填参数缺失时始终报错；选填参数缺失时再按 missing_behavior 处理。",
            ),
            "action_require": ConfigField(type=str,
                                          input_type="textarea",
                                          default="\n".join(GenerateImageAction.action_require),
                                          description="图片生成 action 的决策提示词，每行一条。"),
        },
        "permission_control": {
            "command_user_list_mode": ConfigField(
                type=str,
                choices=["whitelist", "blacklist"],
                default="whitelist",
                description="命令使用名单模式。whitelist 表示仅名单中的用户可用；blacklist 表示名单中的用户不可用。",
            ),
            "command_user_list": ConfigField(
                type=list,
                item_type="string",
                default=DEFAULT_PERMISSION_USER_LIST,
                description="命令使用名单。填写用户标识字符串列表。",
            ),
            "action_user_list_mode": ConfigField(
                type=str,
                choices=["whitelist", "blacklist"],
                default="blacklist",
                description="Action 使用名单模式。whitelist 表示仅名单中的用户可用；blacklist 表示名单中的用户不可用。",
            ),
            "action_user_list": ConfigField(
                type=list,
                item_type="string",
                default=DEFAULT_PERMISSION_USER_LIST,
                description="Action 使用名单。填写用户标识字符串列表。",
            ),
            "global_blacklist": ConfigField(
                type=list,
                item_type="string",
                default=DEFAULT_PERMISSION_USER_LIST,
                description="全局黑名单。名单中的用户不可使用该插件的任何命令和 action。",
            ),
        },
        "custom_variables_config": {
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
                        "choices": ["literal", "llm", "dict"],
                        "default": "literal",
                    },
                    "condition_type": {
                        "type": "select",
                        "label": "条件判断类型",
                        "choices": ["fixed_true", "fixed_false", "length_gt", "length_lt", "contains", "not_contains", "equals", "not_equals", "regex_match",
                                    "regex_not_match"],
                        "default": "fixed_true",
                    },
                    "use_raw_condition_source": {
                        "type": "boolean",
                        "label": "条件来源使用原始值",
                        "default": False,
                        "description": "为 true 时，condition_source 直接取 action_input 原始值或已求值的自定义变量值，不触发依赖解析",
                    },
                    "use_raw_condition_value": {
                        "type": "boolean",
                        "label": "条件参数使用字面文本",
                        "default": False,
                        "description": "为 true 时，condition_value 作为字面文本参与条件判断，不做占位符替换，不触发依赖解析",
                    },
                    "condition_source": {
                        "type": "string",
                        "label": "条件来源变量名",
                        "placeholder": "例如 prompt 或 aspect_ratio（不带花括号）",
                    },
                    "condition_value": {
                        "type": "string",
                        "label": "条件参数值",
                        "placeholder": "比较值、子串或正则表达式",
                    },
                    "values": {
                        "type": "string",
                        "label": "候选值列表 / 字典内容",
                        "placeholder": 'literal/llm 可填 JSON 数组字符串；dict 可填 JSON 对象字符串',
                    },

                    "values_else": {
                        "type": "string",
                        "label": "条件为 false 时的候选值",
                        "placeholder": '例如 ["默认值"]，格式与 values 相同',
                    },
                    "source": {
                        "type": "string",
                        "label": "字典 key 来源（仅 dict 模式）",
                        "placeholder": "例如 emotion_composition（不带花括号）",
                    },
                    "missing_behavior": {
                        "type": "select",
                        "label": "key 未命中时的行为（仅 dict 模式）",
                        "choices": ["keep_placeholder", "raise_error", "use_default"],
                        "default": "keep_placeholder",
                    },
                    "fallback_value": {
                        "type": "string",
                        "label": "回退默认值（仅 dict 模式 + use_default）",
                        "placeholder": "key 未命中时返回的默认值，可为空字符串",
                    },
                    "probability": {
                        "type": "float",
                        "label": "触发概率",
                        "default": 1.0,
                    },
                },
                default=DEFAULT_CUSTOM_VARIABLES,
                description=(
                    "自定义变量列表。literal 和 llm 模式会在 values/values_else 中选择模板；dict 模式会根据 source 从 JSON 对象中取值。"
                    " literal/llm 支持 probability 与条件判断；dict 模式支持 key miss 行为控制。"
                    "支持引用 action_inputs 中的 {参数名}、内置变量占位符以及其他自定义变量的 {变量名}（禁止循环引用）。"
                    " 决策参数的值中同样支持引用自定义变量占位符，系统会按依赖顺序自动解析。"
                    f" 当前内置变量包括：{' '.join(BUILTIN_VARIABLE_DESCRIPTIONS)}"
                ),
            ),
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
                default=["gemini-3-flash", "qwen3.6-plus", "doubao-seed-2-0-pro"],
                description="自定义变量生成时使用的模型名称列表。为空时使用 llm_group 对应任务配置。",
            ),
            "max_tokens": ConfigField(
                type=int,
                default=10000,
                description="自定义变量生成时使用的最大输出 token 数",
            ),
            "temperature": ConfigField(
                type=float,
                default=1,
                description="自定义变量生成时使用的温度",
            ),
            "slow_threshold": ConfigField(
                type=float,
                default=90.0,
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
        permission_config = self.config.get("permission_control", {})
        if raw_action_require := config.get("action_require"):
            GenerateImageAction.action_require = [line.strip() for line in raw_action_require.split("\n") if line.strip()]
        action_parameters = build_action_parameters(
            config.get("action_parameters", DEFAULT_ACTION_PARAMETERS)
        )
        GenerateImageAction.action_parameters = action_parameters
        GenerateImageAction.required_action_parameters = {
            name for name, definition in action_parameters.items() if definition.required
        }
        GenerateImageAction.active_preset = str(self.config.get("bizyair_generate_image_plugin", {}).get("active_preset", "default")).strip()
        permission_manager.configure(
            global_blacklist=permission_config.get("global_blacklist", DEFAULT_PERMISSION_USER_LIST),
            command_user_list=permission_config.get("command_user_list", DEFAULT_PERMISSION_USER_LIST),
            command_user_list_mode=permission_config.get("command_user_list_mode", "whitelist"),
            action_user_list=permission_config.get("action_user_list", DEFAULT_PERMISSION_USER_LIST),
            action_user_list_mode=permission_config.get("action_user_list_mode", "blacklist"),
        )
        components.append((GenerateImageAction.get_action_info(), GenerateImageAction))
        components.append((DrListCommand.get_command_info(), DrListCommand))
        components.append((DrUseCommand.get_command_info(), DrUseCommand))
        return components
