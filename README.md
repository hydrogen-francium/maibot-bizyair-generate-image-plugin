# bizyair-generate-image-plugin

MaiBot 的文生图插件，新增一个 `generate_image` 动作，让 bot 能够根据对话上下文和用户的自然语言描述调用 BizyAir 图片生成接口并发送到聊天会话。

## 功能

- **文生图 Action**：当 bot 的决策判断当前场景适合发图时，可以选择 `generate_image` 动作生成图片并发送。
- **BizyAir 接入**：通过 BizyAir 接口生成图片，只需配置 API Token 即可用。
- **动态参数驱动**：动作参数完全由配置文件定义。
- **自定义占位符**：支持配置自定义变量，并在 OpenAPI 参数映射中通过 `{变量名}` 引用。
- **灵活配置**：可通过配置文件单独设置 BizyAir OpenAPI 连接信息、超时、动作参数、自定义变量、失败回复改写、图片前的引导文案以及决策提示词。

## 安装

将插件目录（包含 `plugin.py`、`_manifest.json`、`components/` 和 `clients/`）整体放入 MaiBot 的 `plugins` 目录：

```powershell
cd <maibot根目录>\plugins
git clone https://github.com/HyperSharkawa/maibot-bizyair-generate-image-plugin
```

重启 MaiBot 后，插件会自动注册并在启动日志中出现 `bizyair_generate_image_plugin`。

> **前提条件**：确保运行 environment 已通过 `pip` / `uv` 安装 `httpx`。

## 配置

### `bizyair_client` 字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `bearer_token` | `string` | `""` | **必须配置**。BizyAir 的 Bearer Token，留空时 action 不可用。 |
| `openapi_url` | `string` | `https://api.bizyair.cn/w/v1/webapp/task/openapi/create` | BizyAir OpenAPI 地址。 |
| `openapi_web_app_id` | `int` | `39429` | BizyAir OpenAPI 的 `web_app_id`。 |
| `openapi_parameter_mappings` | `list` | 见下文 | OpenAPI `input_values` 参数映射表。用于适配不同 app 的字段名和附加参数。 |

### `bizyair_generate_image_plugin` 字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `timeout` | `float` | `180.0` | 调用 OpenAPI 和下载图片的超时时间（秒）。 |
| `action_parameters` | `list[object]` | 见下文 | 定义 `generate_image` 动作允许模型填写的参数列表。参数名、描述、是否必填全部由用户配置。 |
| `custom_variables` | `list[object]` | 见下文 | 定义可复用的自定义变量。变量结果会并入模板上下文，可在 `openapi_parameter_mappings` 中通过 `{变量名}` 引用。 |
| `send_text_before_image` | `bool` | `false` | 是否在发送图片前先发一段引导文本。默认关闭，避免与 reply action 重复。 |
| `text_before_image` | `string` | `"我给你生成了一张图片。"` | 图片前的引导文本，仅在开启 `send_text_before_image` 时生效。 |
| `enable_rewrite_failure_reply` | `bool` | `true` | 当图片生成 action 失败时，是否调用 LLM 将错误改写为自然语言后发送。 |
| `enable_splitter` | `bool` | `false` | 当启用失败回复重写时，是否对重写结果启用分段发送。 |
| `action_require` | `string` | 详见代码 | 该 action 的决策提示词，多行文本，用于辅助大模型在合适时选择该动作。 |

### `variable_llm_config` 字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `llm_group` | `string` | `"utils"` | 自定义变量在 `llm` 模式下默认使用的模型分组 |
| `llm_list` | `list[string]` | `[]` | 若非空，则直接使用这里列出的模型名，覆盖 `llm_group` |
| `max_tokens` | `int` | `512` | 自定义变量生成时使用的最大输出 token 数 |
| `temperature` | `float` | `0.7` | 自定义变量生成时使用的温度 |
| `slow_threshold` | `float` | `30.0` | 自定义变量生成时使用的慢请求阈值，单位秒 |
| `selection_strategy` | `string` | `"balance"` | 自定义变量生成时使用的模型选择策略 |

> ⚙️ `bizyair_client.bearer_token` 必须填写才能使 action 正常工作。修改配置后需重启 MaiBot 生效。

### OpenAPI 参数映射

插件会根据 `bizyair_client.openapi_parameter_mappings` 构造请求中的 `input_values`。

默认映射：

默认 `web_app_id` 为 `39429`。
```toml
[[bizyair_client.openapi_parameter_mappings]]
field = "17:BizyAir_NanoBananaPro.prompt"
value_type = "string"
value = "{prompt}"

[[bizyair_client.openapi_parameter_mappings]]
field = "17:BizyAir_NanoBananaPro.aspect_ratio"
value_type = "string"
value = "{aspect_ratio}"

[[bizyair_client.openapi_parameter_mappings]]
field = "17:BizyAir_NanoBananaPro.resolution"
value_type = "string"
value = "{resolution}"
```

当前支持的基础占位符：

- `{参数名}`：任意一个在 `bizyair_generate_image_plugin.action_parameters` 中定义并被模型填写的参数
- `{变量名}`：任意一个在 `bizyair_generate_image_plugin.custom_variables` 中定义并成功解析出的变量
- `{random_seed}`：运行时生成一个随机的 32 位非负整数

每个映射项包含以下字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `field` | `string` | 是 | 目标 OpenAPI 的参数名 |
| `value_type` | `string` | 是 | 值类型，支持 `string`、`int`、`boolean`、`json` |
| `value` | `string` | 是 | 参数值模板。可写常量、占位符、JSON 字符串，或包含占位符的复杂 JSON 结构 |

`value` 会先按 `value_type` 做类型转换，再递归解析其中的占位符。

例如适配 `https://bizyair.cn/community/app/40062`：

该 app 的 id 为 `41240` ，请注意填写正确的 `web_app_id` 。
```toml
[[bizyair_client.openapi_parameter_mappings]]
field = "8:BizyAir_NanoBanana2.prompt"
value_type = "string"
value = "{prompt}"

[[bizyair_client.openapi_parameter_mappings]]
field = "8:BizyAir_NanoBanana2.seed"
value_type = "string"
value = "{random_seed}"

[[bizyair_client.openapi_parameter_mappings]]
field = "8:BizyAir_NanoBanana2.aspect_ratio"
value_type = "string"
value = "{aspect_ratio}"

[[bizyair_client.openapi_parameter_mappings]]
field = "8:BizyAir_NanoBanana2.resolution"
value_type = "string"
value = "{resolution}"
```

适配 `web_app_id` `45944`：
```toml
[[bizyair_client.openapi_parameter_mappings]]
field = "19:BizyAir_NanoBananaProOfficial.prompt"
value_type = "string"
value = "{prompt}"

[[bizyair_client.openapi_parameter_mappings]]
field = "19:BizyAir_NanoBananaProOfficial.aspect_ratio"
value_type = "string"
value = "{aspect_ratio}"

[[bizyair_client.openapi_parameter_mappings]]
field = "19:BizyAir_NanoBananaProOfficial.resolution"
value_type = "string"
value = "{resolution}"

[[bizyair_client.openapi_parameter_mappings]]
field = "19:BizyAir_NanoBananaProOfficial.seed"
value_type = "string"
value = "{random_seed}"

[[bizyair_client.openapi_parameter_mappings]]
field = "19:BizyAir_NanoBananaProOfficial.temperature"
value_type = "int"
value = "1"

[[bizyair_client.openapi_parameter_mappings]]
field = "19:BizyAir_NanoBananaProOfficial.top_p"
value_type = "int"
value = "1"
```

`value` 不限于纯占位符，也可以是普通常量，或在一个字符串里混合占位符，例如 `"seed={random_seed}"`。当 `value_type = "json"` 时，可以写对象或数组的 JSON 字符串，内部字符串字段同样会递归替换占位符。

## Action 参数配置

`generate_image` 的动作参数由 `bizyair_generate_image_plugin.action_parameters` 决定。默认配置如下：

```toml
[[bizyair_generate_image_plugin.action_parameters]]
name = "prompt"
description = "用于生成图片的描述词"
required = true

[[bizyair_generate_image_plugin.action_parameters]]
name = "aspect_ratio"
description = "图片宽高比，例如 1:1、16:9、9:16、auto"
required = false

[[bizyair_generate_image_plugin.action_parameters]]
name = "resolution"
description = "图片分辨率，例如 1K、2K、4K、auto"
required = false
```

每个参数对象包含以下字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | `string` | 是 | 动作参数名，同时也是映射模板中使用的占位符名，例如 `{prompt}`。 |
| `description` | `string` | 是 | 给决策模型看的参数说明。 |
| `required` | `bool` | 否 | 是否要求模型必须填写该参数。 |

这意味着插件不再内置固定业务字段。你可以把动作参数改成任何名字，例如 `subject`、`style`、`camera`、`negative_prompt`，然后在 `openapi_parameter_mappings` 中自行把这些参数映射到目标 app 的字段。

## 自定义变量配置

`bizyair_generate_image_plugin.custom_variables` 用于定义可复用的模板变量。它们会在 action 参数收集完成后先被解析，再与 action 参数一起组成最终的 `template_context`。

默认配置如下：

```toml
[[bizyair_generate_image_plugin.custom_variables]]
key = "style_hint"
mode = "literal"
values = ["二次元插画", "电影感", "高细节"]
probability = 1.0
```

每个变量对象包含以下字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `key` | `string` | 是 | 变量名，可在模板中通过 `{key}` 引用 |
| `mode` | `string` | 是 | 变量模式，支持 `literal` 或 `llm` |
| `values` | `list[string]` 或 `string` | 是 | 候选值列表。支持直接传 JSON 列表，或传 JSON 列表字符串 |
| `probability` | `float` | 否 | 触发概率，范围为 `0` 到 `1` |

当前规则：

- `literal` 和 `llm` 两种模式都会先从 `values` 中随机抽一条
- 当前实现是先抽样，再只解析被抽中的那一条，避免无意义的模板展开
- `values` 中支持引用 `action_inputs` 里的 `{参数名}`
- 当前不允许变量之间互相引用
- `key` 不允许与 action 参数名或保留名 `random_seed` 冲突
- 若 `mode = "llm"`，抽中的那一条文本会直接作为提示词发送给变量 LLM 生成最终值

例如：

```toml
[[bizyair_generate_image_plugin.custom_variables]]
key = "style_hint"
mode = "literal"
values = ["{prompt}，二次元插画", "{prompt}，电影感", "{prompt}，高细节"]
probability = 1.0

[[bizyair_client.openapi_parameter_mappings]]
field = "17:BizyAir_NanoBananaPro.prompt"
value_type = "string"
value = "{style_hint}"
```

如果变量模式为 `llm`：

```toml
[[bizyair_generate_image_plugin.custom_variables]]
key = "enhanced_prompt"
mode = "llm"
values = ["请把这个画面需求扩写成高质量生图提示词：{prompt}"]
probability = 1.0

[[bizyair_client.openapi_parameter_mappings]]
field = "17:BizyAir_NanoBananaPro.prompt"
value_type = "string"
value = "{enhanced_prompt}"
```

## Action 行为

当 bot 决策选择 `generate_image` 后，插件会：

1. 获取 `action_data` 中由 `action_parameters` 定义的所有参数。
2. 按 `custom_variables` 配置解析自定义变量，得到可复用的模板上下文。
3. 使用 `bizyair_client` 配置节中的 OpenAPI 连接参数和参数映射调用 BizyAir 接口生成图片。
4. 下载生成的图片并转为 base64。
5. （可选）若开启 `send_text_before_image`，先发一段引导文本。
6. 将图片以 `image` 类型发送到当前聊天会话。
7. 记录 action 执行信息，供后续上下文使用。

当调用失败时，插件会先构造原始错误信息；如果开启 `enable_rewrite_failure_reply`，则会调用 MaiBot 的回复重写，将错误改写成自然语言文本再发送；重写失败时发送原始错误信息。

## 示例场景

- 用户说"画一只可爱的蓝白猫娘" → bot 按配置好的参数结构填写并生成图片。
- 用户要求"来一张赛博朋克风格的夜景，16:9 横图，高清" → 如果你在参数配置中保留了 `aspect_ratio`、`resolution`，模型会一并填写。
- 用户让 bot 为某段文字配图，模型会根据当前 `action_parameters` 的定义组织对应参数。


## 免责声明

- 本插件通过外部 BizyAir OpenAPI 服务生成图片，**图片质量与可用性由 BizyAir 服务提供方决定**。
- 请遵守当地法律法规及 BizyAir 服务的用户协议，合法合规地使用生成功能。
- 开发者对第三方服务的稳定性、图片内容或因使用本插件产生的任何后果不承担责任。