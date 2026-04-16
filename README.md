# bizyair-generate-image-plugin

MaiBot 的生图插件，为 Bot 添加 `generate_image` 动作。支持 `BizyAir OpenAPI` 与 `NAI Chat` 两种生图后端，BizyAir 后端还支持图生图（img2img）。插件提供了自定义变量、条件分支、LLM 辅助翻译、参数映射、运行时预设切换等能力，可以灵活编排从"用户说话"到"调用生图 API"之间的整个提示词加工链路。

## 功能概览

- **LLM 驱动的生图决策**：Bot 根据对话上下文自主判断何时需要生成图片，并填写生图参数
- **自定义变量系统**：支持字面值、LLM 生成、字典查找三种模式，支持条件分支、概率触发、变量间互相引用，可灵活编排多级提示词加工流水线
- **双后端支持**：同一套配置可在 BizyAir OpenAPI 与 NAI Chat 之间一键切换
- **参数映射**：通过配置表将变量映射到后端 API 的实际请求参数，支持 string / int / boolean / json 四种值类型
- **图生图**：BizyAir 后端支持将本地参考图、聊天引用图片或 base64 数据自动上传至 OSS 作为生图输入，本地文件 URL 缓存 8 小时
- **失败回复改写**：生图失败时，可调用 LLM 将技术错误改写为自然的中文回复
- **预设管理命令**：`/dr list` 查看预设、`/dr use` 切换预设、`/dr switch` 开关生图功能，无需重启

## 安装

1. 将仓库克隆到 MaiBot 的 `plugins` 目录：

   ```powershell
   cd <maibot根目录>\plugins
   git clone https://github.com/HyperSharkawa/maibot-bizyair-generate-image-plugin
   ```
2. 重启 MaiBot，确认启动日志中出现插件名 `bizyair_generate_image_plugin`
3. 如果使用 BizyAir，请填写 `bizyair_client.bearer_token`
4. 如果使用 NAI，请在对应 NAI 预设中填写 `base_url`、`api_key`、`model`

## 配置

插件配置分为六个区块：

- `bizyair_client`：BizyAir 接口连接与参数映射
- `nai_chat_client`：NAI Chat 接口连接与参数映射
- `bizyair_generate_image_plugin`：生图动作行为与决策
- `permission_control`：权限管理
- `custom_variables_config`：自定义变量
- `variable_llm_config`：自定义变量 LLM 生成

### `bizyair_client` 字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `bearer_token` | `string` | `""` | BizyAir 的 Bearer Token。**必须填写**，留空时生图不可用 |
| `openapi_url` | `string` | `"https://api.bizyair.cn/w/v1/webapp/task/openapi/create"` | BizyAir OpenAPI 地址，通常无需修改 |
| `app_presets` | `array<object>` | 见下方 | App 预设列表，每个预设对应一个 BizyAir App ID |
| `timeout` | `float` | `180` | 调用 OpenAPI 和下载图片的超时时间（秒） |
| `openapi_parameter_mappings` | `array<object>` | 见下方 | OpenAPI 参数映射表 |

#### `app_presets[]` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `preset_name` | `string` | 预设名称，全局唯一。例如 `default`、`flux_portrait` |
| `app_id` | `int` | 对应 BizyAir 工作流应用的 App ID |
| `description` | `string` | 备注，不参与运行时逻辑 |

#### `openapi_parameter_mappings[]` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `preset_name` | `string` | 关联的预设名称，**不可为空**。可填多个（英文逗号分隔），运行时只加载与当前激活预设匹配的条目 |
| `field` | `string` | OpenAPI 参数名，格式为 `节点ID:节点名.字段名`，例如 `18:BizyAir_NanoBananaProOfficial.prompt` |
| `value_type` | `string` | 参数值类型：`string` / `int` / `boolean` / `json` |
| `value` | `string` | 参数值模板，可引用决策参数 `{参数名}`、自定义变量 `{变量名}` 或内置变量 |
| `send_if_empty` | `bool` | 值为空时是否仍然传参，默认 `false` |
| `upload` | `bool` | 是否将解析后的值上传至 BizyAir OSS 并替换为 URL，默认 `false`。详见**图生图（img2img）**章节 |

### `nai_chat_client` 字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `presets` | `array<object>` | 见下方 | NAI 预设列表，每个预设单独维护连接信息 |
| `parameter_mappings` | `array<object>` | 见下方 | NAI 参数映射表 |
| `timeout` | `float` | `180` | 调用 NAI Chat 和解析图片的超时时间（秒） |

#### `presets[]` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `preset_name` | `string` | 预设名称，全局唯一 |
| `description` | `string` | 备注，不参与运行时逻辑 |
| `base_url` | `string` | OpenAI Chat Completions 兼容接口地址，例如 `https://your-domain.example.com/v1` |
| `api_key` | `string` | API Key |
| `model` | `string` | 模型名，例如 `nai-diffusion-4-5-full-anlas-0` |

#### `parameter_mappings[]` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `preset_name` | `string` | 关联的预设名称，支持多个（英文逗号分隔） |
| `field` | `string` | JSON 字段名，例如 `prompt`、`size`、`steps` |
| `value_type` | `string` | 参数值类型：`string` / `int` / `boolean` / `json` |
| `value` | `string` | 参数值模板，可引用决策参数、自定义变量、内置变量 |
| `send_if_empty` | `bool` | 值为空时是否仍然传参 |

NAI 调用时，插件会根据 `parameter_mappings` 构造 JSON 对象，序列化为字符串后作为 Chat Completions 请求的 user message content 发送。

### `permission_control` 字段

插件提供两级权限控制：全局黑名单 + 组件级名单（命令和生图动作分别控制）。

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `global_blacklist` | `array[string]` | `[]` | 全局黑名单，优先级最高，名单中的用户不可使用插件的任何功能 |
| `command_user_list_mode` | `string` | `"whitelist"` | 命令权限模式。`whitelist`：仅名单内用户可用；`blacklist`：仅名单内用户不可用 |
| `command_user_list` | `array[string]` | `[]` | 命令权限名单 |
| `action_user_list_mode` | `string` | `"blacklist"` | 生图动作权限模式，规则同上 |
| `action_user_list` | `array[string]` | `[]` | 生图动作权限名单 |

### `bizyair_generate_image_plugin` 字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `action_enabled` | `bool` | `true` | 生图功能总开关 |
| `send_text_before_image` | `bool` | `false` | 是否在发送图片前额外发送一段提示文本 |
| `text_before_image` | `string` | `"我给你生成了一张图片"` | 图片前的提示文本，仅在上一项开启时生效 |
| `enable_rewrite_failure_reply` | `bool` | `true` | 生图失败时是否用 LLM 改写错误信息再发送 |
| `enable_splitter` | `bool` | `false` | 失败回复改写后是否分段发送 |
| `active_preset` | `string` | `default` | 当前激活的预设名称，会同时在 BizyAir 和 NAI 两类预设中查找，因此必须全局唯一 |
| `action_parameters` | `array<object>` | 见下方 | 生图动作允许 LLM 决策时填写的参数列表 |
| `action_require` | `string` | 见下方 | 决策提示词，指导 LLM 何时使用生图动作、如何填写参数，每行一条 |

#### `action_parameters[]` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `string` | 参数名，例如 `prompt` |
| `description` | `string` | 参数说明，会展示给 LLM 帮助其理解参数用途 |
| `required` | `string` | `必填` 或 `选填` |
| `missing_behavior` | `string` | 选填参数缺失时的处理方式：`keep_placeholder`（替换为空字符串）、`raise_error`（报错，仅在该参数被实际引用时触发）、`use_default`（使用默认值） |
| `default_value` | `string` | 仅 `use_default` 时生效 |

### `custom_variables_config` 字段

#### `custom_variables[]` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `key` | `string` | 变量名，例如 `english_prompt` |
| `mode` | `string` | 变量模式：`literal`（直接使用字面值）、`llm`（将模板发送给 LLM，以 LLM 输出作为变量值）、`dict`（根据来源值在字典中查找对应条目） |
| `condition_type` | `string` | 条件类型：`fixed_true`（恒真）、`fixed_false`（恒假）、`length_gt`、`length_lt`、`contains`、`not_contains`、`equals`、`not_equals`、`regex_match`、`regex_not_match` |
| `condition_source` | `string` | 条件判断的来源变量名（不带花括号） |
| `condition_value` | `string` | 条件判断的比较值。根据 `condition_type` 解释为文本、子串或正则表达式。支持 `{变量名}` 引用 |
| `use_raw_condition_source` | `bool` | 默认 `false`。为 `true` 时，直接读取来源变量的原始文本进行条件判断，不会先对其中的占位符做替换。详见**原始值条件判断**章节 |
| `use_raw_condition_value` | `bool` | 默认 `false`。为 `true` 时，比较值作为字面文本直接参与判断，其中的花括号不会被当作变量引用。详见**原始值条件判断**章节 |
| `values` | `string` | `literal` / `llm`：候选值列表（JSON 数组字符串或每行一个值）；`dict`：JSON 对象字符串 |
| `values_else` | `string` | 条件为 false 时使用的候选值列表，格式与 `values` 相同 |
| `source` | `string` | 仅 `dict` 模式：用作字典 key 的来源变量名（不带花括号） |
| `missing_behavior` | `string` | 仅 `dict` 模式：key 未命中时的处理方式（`keep_placeholder` / `raise_error` / `use_default`） |
| `fallback_value` | `string` | 仅 `dict` 模式 + `use_default`：未命中时的默认值，支持 `{变量名}` 引用 |
| `probability` | `float` | 触发概率（`0~1`），仅 `literal` / `llm` 模式生效 |

**执行顺序**（`literal` / `llm` 模式）：先判概率 → 再判条件 → 从命中的 `values` 或 `values_else` 中随机选一条作为模板 → 替换其中的占位符 → `literal` 直接作为结果 / `llm` 发送给 LLM 以输出作为结果。

**`dict` 模式**独立于概率和条件逻辑：读取 `source` 指向的变量值作为 key，在 `values` 字典中查找对应条目并直接返回。

**占位符引用**：`values`、`values_else`、`condition_value`、`fallback_value` 中均可使用 `{变量名}` 引用决策参数、内置变量或其他自定义变量（禁止循环引用）。所有变量按依赖顺序自动解析。

### 占位符引用说明

插件支持三类占位符，可在自定义变量模板和参数映射模板中使用：

1. **决策参数**：`{prompt}`、`{aspect_ratio}` 等，引用 LLM 决策时填写的参数值
2. **内置变量**：`{random_seed}`、`{quoted_image_base64}` 等，插件运行时自动生成（见下方表格）
3. **自定义变量**：`{english_prompt}`、`{style_hint}` 等，引用其他自定义变量的解析结果

此外，LLM 填写的决策参数值中也可以包含自定义变量占位符。例如 LLM 在 `style` 参数中填写 `{二次元画风}`，系统会自动将其替换为对应变量的值。要使用此功能，需在 `action_require` 中告知 LLM 可用的变量名。

### 内置变量

内置变量可在自定义变量的候选值模板和参数映射的值模板中引用。同一次生图执行内，同一内置变量只会计算一次，所有引用位置共享同一个值。

| 变量名 | 说明 |
| --- | --- |
| `{random_seed}` | 随机 32 位整数 |
| `{current_datetime}` | 当前本地日期时间，格式为 `YYYY-MM-DD HH:MM:SS` |
| `{recent_chat_context_10}` | 当前聊天最近 10 条消息的可读文本 |
| `{recent_chat_context_30}` | 当前聊天最近 30 条消息的可读文本 |
| `{recent_chat_context_50}` | 当前聊天最近 50 条消息的可读文本 |
| `{quoted_image_base64}` | 触发消息中第一张图片的 base64（优先从引用消息中提取），无图片时为空字符串 |

### `variable_llm_config` 字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `llm_group` | `string` | `"utils"` | LLM 模型分组 |
| `llm_list` | `list[string]` | `[]` | 优先使用的模型名称列表，非空时覆盖 `llm_group` |
| `max_tokens` | `int` | `512` | 最大输出 token 数 |
| `temperature` | `float` | `0.7` | 温度 |
| `slow_threshold` | `float` | `30` | 慢请求阈值（秒） |
| `selection_strategy` | `string` | `"balance"` | 模型选择策略：`balance`（负载均衡）或 `random`（随机） |

## 默认预设配置详解

插件自带了一套开箱即用的默认配置。

### 1. 决策参数

| 参数名 | 必填 | 说明 |
| --- | --- | --- |
| `prompt` | 必填 | 图片描述词 |
| `style` | 必填 | 画风。可填自然语言描述，也可填预设变量引用如 `{二次元画风}`、`{写实风}` |
| `aspect_ratio` | 选填 | 宽高比（`1:1`、`4:3`、`16:9`、`9:16`、`auto`），默认 `1:1` |
| `resolution` | 选填 | 分辨率（`1K`、`2K`、`4K`、`auto`），默认 `1k` |

默认的 `action_require` 提示词会引导 LLM 将画面描述写入 `prompt`、将画风写入 `style`，并优先使用 `{二次元画风}` 或 `{写实风}` 等预设变量。

### 2. 自定义变量

默认预设了三个变量：

- **`english_prompt`**（`llm` 模式）：将 `{style}` 和 `{prompt}` 拼合后发送给 LLM，翻译为适合绘图 AI 的英文标签。这是最终传给生图 API 的提示词
- **`二次元画风`**（`literal` 模式）：一组预设的二次元风格英文标签
- **`写实风`**（`literal` 模式）：一组预设的写实风格英文标签

工作原理：LLM 决策时在 `style` 中填写 `{二次元画风}`→ 系统替换为预设标签 → `english_prompt` 同时引用替换后的 `style` 和 `prompt` → 调用 LLM 润色为英文 → 最终结果通过参数映射传给生图 API。

```toml
[[custom_variables_config.custom_variables]]
key = "english_prompt"
mode = "llm"
values = "[\"这是一个用于画图的提示词。请将其变成更适合画图ai的英文标签形式。你的输出会被直接输入到绘图ai中，因此请直接输出内容，不要添加多余的解释。以下是图片要求的画风: {style}\\n\\n以下是生图的描述词: {prompt}\"]"
probability = 1
```

### 3. 预设

默认各有一个 BizyAir 预设和 NAI 预设：

```toml
[bizyair_generate_image_plugin]
active_preset = "default"

[[bizyair_client.app_presets]]
preset_name = "default"
app_id = 50835
description = "默认 BizyAir App"

[[nai_chat_client.presets]]
preset_name = "nai_default"
description = "默认 NAI Chat 预设"
base_url = "https://your-domain.example.com/v1"
api_key = ""
model = "nai-diffusion-4-5-full-anlas-0"
```

两类预设的 `preset_name` 必须全局唯一。`active_preset` 决定当前走哪个后端——填 BizyAir 预设名就走 BizyAir，填 NAI 预设名就走 NAI。切换预设可修改配置或使用 `/dr use <预设名>` 命令。

### 4. BizyAir 参数映射

```toml
[[bizyair_client.openapi_parameter_mappings]]
preset_name = "default"
field = "18:BizyAir_NanoBananaProOfficial.prompt"
value_type = "string"
value = "{english_prompt}"

[[bizyair_client.openapi_parameter_mappings]]
preset_name = "default"
field = "18:BizyAir_NanoBananaProOfficial.aspect_ratio"
value_type = "string"
value = "{aspect_ratio}"

[[bizyair_client.openapi_parameter_mappings]]
preset_name = "default"
field = "18:BizyAir_NanoBananaProOfficial.resolution"
value_type = "string"
value = "{resolution}"
```

三条映射均关联 `default` 预设。`field` 中的 `节点ID:节点名.字段名` 格式需要与你 BizyAir 工作流中的实际节点对应。注意默认配置不会把 `style` 单独映射——它已经在 `english_prompt` 的 LLM 加工环节中被消费了。

### 5. NAI 参数映射示例

```toml
[[nai_chat_client.parameter_mappings]]
preset_name = "nai_default"
field = "prompt"
value_type = "string"
value = "{english_prompt}"

[[nai_chat_client.parameter_mappings]]
preset_name = "nai_default"
field = "size"
value_type = "json"
value = "[832, 1216]"

[[nai_chat_client.parameter_mappings]]
preset_name = "nai_default"
field = "steps"
value_type = "int"
value = "23"
```

最终构造为 JSON 字符串 `{"prompt":"...","size":[832,1216],"steps":23}`，放入 Chat Completions 请求的 user message content 中。

## 完整运行流程

以下从 Bot 做出生图决策开始，说明每个步骤及其涉及的配置项。

### 步骤 0：权限检查

1. 先检查 `global_blacklist`，命中则直接拒绝
2. 再按组件类型（命令或生图动作）检查对应的权限名单

### 步骤 1：LLM 决策是否生图

用户发送消息后，MaiBot 的 LLM 根据 `action_require` 中的提示词判断是否触发生图动作。例如用户说"帮我画一只猫"，LLM 识别到画图意图，决定调用 `generate_image`。

### 步骤 2：LLM 填写决策参数

LLM 根据 `action_parameters` 中定义的参数列表和描述填写参数值。例如用户说"画一张横图的二次元少女"，LLM 会把"少女"写入 `prompt`，`{二次元画风}` 写入 `style`，`16:9` 写入 `aspect_ratio`。

### 步骤 3：解析自定义变量

插件收集 LLM 填写的决策参数后，按依赖顺序解析所有被引用到的自定义变量：

- 对每个变量，先判概率，再判条件，然后从 `values` 或 `values_else` 中选取一条候选值
- 将候选值中的占位符替换为已解析的实际值
- `literal` 模式直接使用替换后的文本；`llm` 模式将文本发送给 LLM，以 LLM 输出作为最终值
- `dict` 模式根据来源变量的值在字典中查找对应条目
- `variable_llm_config` 控制 LLM 调用时使用的模型、温度、最大 token 等参数

### 步骤 4：构建请求参数

插件将决策参数和自定义变量的值合并，根据参数映射表构建最终的 API 请求参数：

- 将每条映射的值模板中的占位符替换为实际值
- 按 `value_type` 做类型转换（字符串、整数、布尔值或 JSON）
- 替换后为空且 `send_if_empty = false` 的参数会被跳过
- 标记了 `upload = true` 的参数会在此阶段执行文件上传

### 步骤 5：调用生图 API 并下载图片

插件向 BizyAir OpenAPI 或 NAI Chat 发送请求，等待生图完成后下载图片。`timeout` 同时适用于请求和下载阶段。

### 步骤 6：发送图片

- 如果 `send_text_before_image = true`，先发送 `text_before_image` 中配置的提示文本
- 然后发送图片

### 步骤 7：失败处理

如果任何步骤出错：
- `enable_rewrite_failure_reply = true` 时，调用 LLM 将错误改写为自然语言回复（如"图片生成超时了，请稍后再试"）
- 否则直接发送原始错误信息

## 命令

### `/dr list`

列出所有可用预设及当前激活状态。

```
📋 当前可用的画图 App 预设：

• default (App ID: 50835) ✅(当前使用)  默认 BizyAir App
• anime (App ID: 60001)  二次元工作流
```

### `/dr use <预设名>`

运行时切换激活预设，同时写回 `config.toml`。若预设名不存在会提示可用列表。

### `/dr switch <on|off>`

运行时开关生图功能，同时写回 `config.toml`。

> 三个命令均受 `permission_control` 的命令权限控制。写入配置文件失败时，切换仍在本次运行中生效，但重启后会恢复原值。

## 高级用法

### 图生图（img2img）

BizyAir 后端支持图生图。在参数映射中设置 `upload = true`，插件会自动将图片数据上传至 BizyAir OSS 并替换为 URL。

**前提**：你的 BizyAir 工作流 App 需要包含接受图片 URL 输入的节点（如 `LoadImage`）。

#### 场景 1：本地参考图

适用于需要稳定角色外貌的场景（如角色三视图）：

```toml
[[custom_variables_config.custom_variables]]
key = "reference_image"
mode = "literal"
values = '["D:/images/character_reference.png"]'
condition_type = "fixed_true"

[[bizyair_client.openapi_parameter_mappings]]
preset_name = "图生图预设"
field = "23:LoadImage.image"
value_type = "string"
value = "{reference_image}"
upload = true
send_if_empty = false
```

本地文件的上传结果在内存中缓存 8 小时，同一文件不会重复上传。文件被修改后缓存自动失效。

#### 场景 2：引用消息中的图片

用户在 QQ 中引用一张图片并发送生图指令：

```toml
[[bizyair_client.openapi_parameter_mappings]]
preset_name = "图生图预设"
field = "23:LoadImage.image"
value_type = "string"
value = "{quoted_image_base64}"
upload = true
send_if_empty = false
```

引用图片每次都会重新上传，不缓存。

#### 场景 3：混合模式

有引用图片时用引用图片，没有时用本地默认参考图：

```toml
[[custom_variables_config.custom_variables]]
key = "reference_image"
mode = "literal"
values = ["{quoted_image_base64}"]
condition_type = "not_equals"
condition_source = "quoted_image_base64"
condition_value = ""
values_else = ["D:/images/character_reference.png"]
use_raw_condition_source = true
use_raw_condition_value = true

[[bizyair_client.openapi_parameter_mappings]]
preset_name = "图生图预设"
field = "23:LoadImage.image"
value_type = "string"
value = "{reference_image}"
upload = true
send_if_empty = false
```

#### `upload` 处理规则

| 解析后的值 | 处理方式 |
| --- | --- |
| `http://` 或 `https://` 开头 | 直接透传 |
| 已存在的本地文件路径 | 读取 → 上传 → 返回 URL（缓存 8 小时） |
| 合法的 base64 字符串 | 解码 → 上传 → 返回 URL（不缓存） |
| 空字符串 | 按 `send_if_empty` 规则处理 |

> `upload` 仅对 BizyAir OpenAPI 的参数映射有效，NAI Chat 不支持。

### 原始值条件判断

默认情况下，条件判断的来源值和比较值都会先做占位符替换再比较。但有时你需要比较"原始文本本身"——例如判断 LLM 是否在 `prompt` 中写入了 `{selfie_prompt}` 这个字面文本（而不是它替换后的值）。

- `use_raw_condition_source = true`：直接读取来源变量的原始文本，不做占位符替换
- `use_raw_condition_value = true`：比较值作为字面文本参与判断，花括号不会被解释为变量引用

两个标志通常配合使用，实现"原始文本对原始文本"的精确比较：

```toml
[[custom_variables_config.custom_variables]]
key = "english_prompt"
mode = "literal"
values = ["{style}, {selfie_prompt}"]
condition_type = "equals"
condition_source = "prompt"
condition_value = "{selfie_prompt}"
values_else = ["{llm_translated_english_prompt}"]
use_raw_condition_source = true
use_raw_condition_value = true
```

含义：如果 LLM 在 `prompt` 中原样写入了 `{selfie_prompt}` 这串文本，走自拍分支；否则走普通翻译分支。

### 配置多个预设并快速切换

```toml
[[bizyair_client.app_presets]]
preset_name = "flux_portrait"
app_id = 50835
description = "写实人像"

[[bizyair_client.app_presets]]
preset_name = "anime"
app_id = 60001
description = "二次元"

# flux_portrait 专属映射
[[bizyair_client.openapi_parameter_mappings]]
preset_name = "flux_portrait"
field = "18:BizyAir_NanoBananaProOfficial.prompt"
value_type = "string"
value = "{english_prompt}"

# anime 专属映射
[[bizyair_client.openapi_parameter_mappings]]
preset_name = "anime"
field = "5:KSampler.prompt"
value_type = "string"
value = "{english_prompt}"

# 两个预设共用的映射
[[bizyair_client.openapi_parameter_mappings]]
preset_name = "flux_portrait,anime"
field = "18:BizyAir_NanoBananaProOfficial.aspect_ratio"
value_type = "string"
value = "{aspect_ratio}"
```

切换只需 `/dr use anime` 或修改 `active_preset`，无需改动映射配置。

### 使用多个候选值模板

`values` 支持多条候选值，每次随机抽取一条，增加生成结果的多样性：

```toml
[[custom_variables_config.custom_variables]]
key = "english_prompt"
mode = "llm"
values = """[
  "请将以下描述转换为英文绘图标签: {prompt}",
  "Translate this to English image generation tags: {prompt}",
  "Convert to AI art prompt tags: {prompt}"
]"""
probability = 1
```

### 变量间互相引用

自定义变量可以引用其他自定义变量，系统自动按依赖顺序解析（禁止循环引用）：

```toml
# 先根据聊天上下文推断画风
[[custom_variables_config.custom_variables]]
key = "style_tags"
mode = "llm"
values = '["根据以下聊天记录推断氛围，输出1~3个英文画风标签。聊天记录：{recent_chat_context_10}"]'

# 再将 prompt 和 style_tags 组合后翻译
[[custom_variables_config.custom_variables]]
key = "english_prompt"
mode = "llm"
values = '["翻译为绘图AI英文标签。画风参考：{style_tags}。描述：{prompt}"]'
```

### 在决策参数中引用变量

LLM 填写的参数值中也可以包含 `{变量名}`，系统会自动替换。在 `action_require` 中告知 LLM 可用的变量名即可。默认配置就在使用这套机制——LLM 在 `style` 中填写 `{二次元画风}`，系统替换为预设标签后传入下游。

## 常见问题

| 问题 | 排查方向 |
| --- | --- |
| 生图动作不触发 | 检查 `bearer_token` 是否填写；检查 `action_require` 是否包含合适的决策规则；查看日志确认 LLM 是否识别了生图意图 |
| 报错"未配置 bearer_token" | 填写 `bizyair_client.bearer_token` |
| 图片不符合预期 | 调整 `action_require` 的决策提示词；优化自定义变量中的 LLM 提示词模板；检查参数映射的 `field` 是否与工作流节点匹配 |
| 报错"引用了未定义的变量" | 确认 `{变量名}` 在自定义变量或决策参数中有定义 |
| 报错"检测到循环引用" | 变量 A 引用 B、B 又引用 A，调整模板打破循环 |
| LLM 翻译效果差 | 在 `variable_llm_config` 中换用更强的模型；优化 `values` 中的提示词模板 |
| 生图超时 | 增大 `timeout`；检查 BizyAir API 状态 |
| 失败回复太长或太技术化 | 确认 `enable_rewrite_failure_reply = true`；检查 LLM 连接 |

## 后续开发计划

| 功能 | 说明 | 状态 |
| --- | --- | --- |
| **决策参数引用变量** | LLM 在参数值中写 `{变量名}`，系统按依赖顺序自动替换 | ✅ 已完成 |
| **变量间互相引用** | 自定义变量可相互引用，通过拓扑排序确定解析顺序 | ✅ 已完成 |
| **条件判断** | 10 种条件类型，根据判断结果选择不同的候选值分支 | ✅ 已完成 |
| **原始值条件比较** | 跳过占位符替换，直接用原始文本做条件判断 | ✅ 已完成 |
| **图生图** | BizyAir 后端支持本地文件、base64、引用图片自动上传至 OSS | ✅ 已完成 |
| **消息图片变量** | `{quoted_image_base64}` 从触发消息的引用中提取图片 | ✅ 已完成 |
| **更多内置变量** | 当前日程等，使生图行为更拟人 | 🚧 进行中 |
| **变量持久化** | 全局 KV 缓存，跨任务保留上下文信息 | 🚧 计划中 |
| **独立 WebUI** | 插件专属可视化配置界面，替代框架自带 WebUI | 🚧 计划中 |

> 修改配置后需重启 MaiBot 生效（`/dr use` 和 `/dr switch` 命令除外）。
