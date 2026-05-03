# bizyair-generate-image-plugin

让 MaiBot 真的会"看着说着画着"的生图插件——把"用户随口一句话"和"调通 BizyAir/NAI 的 API"中间那段又脏又乱的提示词加工活儿，全部抽象成可配置的流水线。

> 同时支持 BizyAir OpenAPI（含图生图）和 NAI Chat 两种后端。两套预设可以并存，一条命令切换。

---

## 它解决了什么问题

如果你拼过提示词，应该对下面这些事不陌生：

- 用户发一句"画我在公园玩"，但你需要的是 `1girl, solo, ..., outdoor scenery, park, daytime, casual outfit, ...` 这种英文 tag 串
- 想让 Bot 的人设贯穿到画里：每张图都自带固定外貌、固定服装规则、固定 NSFW 红线
- 想根据时间/上下文动态切换内容：白天画在阳台，晚上画在房间
- 想让一些"昂贵的设定"（比如"今天 Bot 的心情和日程"）按天缓存，不要每张图都烧一次 LLM

把这些全用硬编码的 if-else 写在 Python 里很恐怖。这个插件做的事情是：**把整个加工链路抽象成"自定义变量 + 依赖图 + 拓扑求值"**，你写 toml，引擎跑剩下的。

---

## 五分钟跑起来

1. 把仓库克隆到 MaiBot 的 `plugins` 目录：
   ```powershell
   cd <maibot根目录>\plugins
   git clone https://github.com/HyperSharkawa/maibot-bizyair-generate-image-plugin
   ```
2. 重启 MaiBot，确认日志里出现 `bizyair_generate_image_plugin`
3. 编辑生成出来的 `config.toml`：
   - 走 BizyAir：把 `bizyair_client.bearer_token` 填上
   - 走 NAI：在 `nai_chat_client.presets[0]` 里填 `base_url` / `api_key` / `model`
4. 在群里说"画一只橘猫"试试

如果你不打算研究全部细节，到这一步就够用了。下面的部分讲"为什么这么设计 + 怎么订制"。

---

## 整体工作流程

```
用户发消息
   │
   ▼
┌────────────────────────────────────────────────┐
│ MaiBot 主决策器（LLM）                          │
│ 读 action_require 判断「要不要生图」              │
│ 按 action_parameters 填决策参数                  │
└────────────────────────────────────────────────┘
   │ {prompt, style, aspect_ratio, ...}
   ▼
┌────────────────────────────────────────────────┐
│ 自定义变量解析引擎（这是插件核心）                  │
│ - 收集所有被引用到的变量                          │
│ - 拓扑排序 + 按需求值                            │
│ - 5 种 mode：literal / llm / dict / extract /    │
│   daily_llm                                     │
│ - 6 种内置变量：{random_seed} / {current_datetime} │
│   {quoted_image_base64} / {recent_chat_context_*} │
└────────────────────────────────────────────────┘
   │ {english_prompt, final_prompt, ...}
   ▼
┌────────────────────────────────────────────────┐
│ 参数映射                                         │
│ - 替换占位符                                     │
│ - 按 value_type 转 string/int/bool/json          │
│ - upload=true 自动上传到 BizyAir OSS             │
└────────────────────────────────────────────────┘
   │ HTTP body
   ▼
┌────────────────────────────────────────────────┐
│ BizyAir OpenAPI 或 NAI Chat                     │
│ → 等待生图 → 下载 → 发送                          │
│ 任何一步失败可走 LLM 改写为自然语言回复            │
└────────────────────────────────────────────────┘
```

每一步都是可插拔的：你可以只用主决策器、跳过变量系统（直接把 `{prompt}` 当英文喂给 API）；也可以让变量系统跑一条 5 层翻译流水线。

---

## 变量系统：插件的灵魂

### 为什么需要它

直观地讲，所有"想让画稳定"的需求最终都会变成"我要让 X 字段在某个条件下是 A，否则是 B，但 B 还得引用 C 的输出，而 C 又要去查个字典……"。

如果用 Python 写就是嵌套 if + 字符串拼接的灾难。变量系统把这个过程**声明化**：你只描述"每个变量怎么算 + 它依赖谁"，引擎自动算依赖图、按拓扑序求值、按需触发软依赖。

### 5 种 mode

| mode | 干什么 | 典型场景 |
| --- | --- | --- |
| `literal` | 字面值，可带占位符 | `{二次元画风} = masterpiece, anime, ...` 这种固定模板 |
| `llm` | 模板送给 LLM，输出当变量值 | "中文穿搭描述 → 英文 Danbooru tag" |
| `dict` | 用 source 变量当 key 在 JSON 字典里查 | `emotion_key=pout → puffed cheeks, blush, ...` |
| `extract` | 从 source 变量值里按 regex 抽 group | LLM 输出一段含 `EMOTION:xxx` 的文本，抽出 `xxx` |
| `daily_llm` | 同 llm，但结果按天缓存 | "今日心情/今日日程"一天只算一次 |

### 条件分支与"按需求值"

每个变量都可以挂条件：

- 10 种 `condition_type`：`equals` / `contains` / `regex_match` / `length_gt` / `not_equals` / ...
- 命中走 `values`，未命中走 `values_else`
- 求值时**只解析"实际命中分支"引用到的变量**——这意味着你写 `if 自拍 走 A 路线 else 走 B 路线`，不会两条线都触发 LLM 调用

举例：`final_prompt` 在 `scene_type == selfie` 时引用 selfie 路径上的 7 个变量，否则只引用 `english_free` 一个。当 director 判断这次是 normal 生图时，那 7 个 selfie 翻译节点压根不会被触发。

### 一个真实例子：插件自带的"自拍人设"流水线

```
image_intent (LLM 决策器只填这一个自然语言字段)
   │
   ▼
today_state (daily_llm, 按天缓存的「今日时间表」, 一天一次)
   │
   ▼
director (LLM, 输入 image_intent + today_state + 当前时间)
   │ 输出 5 行 KEY:value 文本
   ▼
extract × 5
   ├─ scene_type        (selfie / normal)
   ├─ emotion_key       (pout / shy / smirk / ...)
   ├─ pose_key          (phone_in_hand / front_of_mirror / ...)
   ├─ outfit_brief      (中文穿搭描述)
   └─ free_prompt       (画面意图描述)
   │
   ▼
翻译层
   ├─ emotion_prompt    (hybrid: 25 个池内走 dict 零开销; 池外走 llm 兜底)
   ├─ pose_prompt       (dict)
   ├─ english_outfit    (llm)
   ├─ english_bg        (llm, 参考时间表 + 当前时间推断地点)
   ├─ english_extra     (llm, 按 pose_key 路由的姿势微变量)
   └─ english_free      (llm, NSFW 红线 + 主体识别)
   │
   ▼
final_prompt (literal + condition: scene_type == selfie ?)
   ├ 自拍：style_base + pose + character + emotion + outfit + bg + extra
   └ 普通：style_base + english_free
```

这条链路的好处：
- **决策器只填一个字段**，剩下 5 个生图维度全靠 director 自动拆解
- **today_state 一天只算一次**，daily_llm 模式按 `.var_cache/{key}.{YYYY-MM-DD}.json` 缓存
- **emotion 用 hybrid 路由**：25 个常用情绪走 dict 零开销，池外的怪词走 LLM 现场翻译
- **scene_type 决定整条链路的下游**：normal 路径压根不会触发 selfie 那 7 个翻译节点

### 内置变量

| 变量名 | 说明 |
| --- | --- |
| `{random_seed}` | 随机 32 位整数 |
| `{current_datetime}` | 当前本地日期时间（`YYYY-MM-DD HH:MM:SS`） |
| `{quoted_image_base64}` | 触发消息中的第一张图（优先取引用消息），无图为空字符串 |
| `{recent_chat_context_10}` | 最近 10 条聊天记录的可读文本 |
| `{recent_chat_context_30}` | 最近 30 条 |
| `{recent_chat_context_50}` | 最近 50 条 |

每次生图执行内每个内置变量只算一次，所有引用位置共享同一个值。

> ⚠️ `daily_llm` 的模板里**不要**引用 `recent_chat_context_*`——当天首次生成时的旧上下文会被全天复用。

---

## 后端切换：BizyAir × NAI

两个后端共用同一份 `custom_variables` 和决策逻辑。差异只在"参数怎么发到 API 上"：

- **BizyAir**：`openapi_parameter_mappings` → 构造 `input_values` → POST 到 OpenAPI
- **NAI**：`parameter_mappings` → 构造 JSON → 塞到 Chat Completions 的 user message content 里

`bizyair_generate_image_plugin.active_preset` 决定走哪条路。BizyAir 预设和 NAI 预设的 `preset_name` 必须**全局唯一**——引擎按"在哪个池子里能找到这个名字"判断走哪个后端。

切换：编辑 `active_preset` 字段 + 重启，或者运行时 `/dr use <预设名>`（会自动写回 toml）。

---

## 图生图（仅 BizyAir）

```toml
[[bizyair_client.openapi_parameter_mappings]]
preset_name = "img2img"
field = "23:LoadImage.image"
value_type = "string"
value = "{quoted_image_base64}"
upload = true
send_if_empty = false
```

`upload = true` 时，引擎自动判断输入类型：

| 解析后的值 | 处理方式 |
| --- | --- |
| `http(s)://` 开头 | 直接透传 |
| 已存在的本地文件路径 | 读取 → 上传 → 返回 URL（**缓存 8 小时**，文件被改动则失效） |
| 合法 base64 字符串 | 解码 → 上传 → 返回 URL（不缓存） |
| 空字符串 | 按 `send_if_empty` 处理 |

混合用例（有引用图就用引用图，没有就用本地参考图）：

```toml
[[custom_variables_config.custom_variables]]
key = "reference_image"
mode = "literal"
condition_type = "not_equals"
condition_source = "quoted_image_base64"
condition_value = ""
use_raw_condition_source = true
use_raw_condition_value = true
values = ["{quoted_image_base64}"]
values_else = ["D:/images/character.png"]
```

NAI 不支持 `upload`（NAI Chat 接口不需要图片 URL 输入）。

---

## 命令

| 命令 | 作用 |
| --- | --- |
| `/dr list` | 列出所有可用预设 + 当前激活状态 |
| `/dr use <预设名>` | 运行时切换激活预设，自动写回 config.toml |
| `/dr switch <on\|off>` | 运行时开关生图功能，自动写回 config.toml |

三个命令都受 `permission_control.command_user_list` 约束。运行时切换成功但写回失败时，本次会话仍然生效，下次启动会回滚。

---

## 权限

两层过滤：

1. **全局黑名单**（`global_blacklist`）：永远拒绝
2. **组件级名单**：命令和生图动作分开管，各自有白/黑名单模式

举例：所有人都能用生图，但只让管理员能切换预设：

```toml
[permission_control]
command_user_list_mode = "whitelist"
command_user_list = ["管理员QQ号"]
action_user_list_mode = "blacklist"
action_user_list = []
global_blacklist = []
```

---

## 配置字段速查

> 这一段是冷冰冰的字段表。如果你只想知道"X 字段是干嘛的"，直接搜过来。

### `bizyair_client`

| 字段 | 说明 |
| --- | --- |
| `bearer_token` | BizyAir Bearer Token，**必填** |
| `openapi_url` | OpenAPI 地址，通常不用改 |
| `app_presets[]` | 应用预设：`preset_name` / `app_id` / `description` |
| `timeout` | 调用 + 下载的总超时（秒） |
| `openapi_parameter_mappings[]` | 参数映射，详见下方 |

每条 `openapi_parameter_mappings`：
- `preset_name`：关联预设名（多个用英文逗号），不可为空
- `field`：`节点ID:节点名.字段名`
- `value_type`：`string` / `int` / `boolean` / `json`
- `value`：值模板，可引用占位符
- `send_if_empty`：值为空时是否仍传参（默认 false）
- `upload`：是否走 OSS 上传（默认 false）

### `nai_chat_client`

| 字段 | 说明 |
| --- | --- |
| `presets[]` | 单独维护 `base_url` / `api_key` / `model` |
| `parameter_mappings[]` | 顶层 JSON key + 值模板，结构同 BizyAir 但**不支持 upload** |
| `timeout` | 同 BizyAir |

### `bizyair_generate_image_plugin`

| 字段 | 说明 |
| --- | --- |
| `action_enabled` | 总开关 |
| `active_preset` | 当前预设（同时在 BizyAir/NAI 两类预设池里查） |
| `send_text_before_image` | 出图前是否先发一段文本 |
| `text_before_image` | 那段文本的内容 |
| `enable_rewrite_failure_reply` | 失败时是否用 LLM 改写错误 |
| `enable_splitter` | 改写后是否分段发送 |
| `action_parameters[]` | 决策参数定义 |
| `action_require` | 决策提示词，每行一条规则 |

每条 `action_parameters`：
- `name` / `description` / `required`（"必填" / "选填"）
- `missing_behavior`：`keep_placeholder` / `raise_error` / `use_default`
- `default_value`（仅 `use_default` 生效）

### `custom_variables_config.custom_variables[]`

| 字段 | 说明 |
| --- | --- |
| `key` | 变量名 |
| `mode` | `literal` / `llm` / `dict` / `extract` / `daily_llm` |
| `values` / `values_else` | 候选值（JSON 数组或字符串）；dict 模式下是 JSON 对象 |
| `condition_type` / `condition_source` / `condition_value` | 条件分支 |
| `use_raw_condition_source` / `use_raw_condition_value` | 跳过占位符替换的"原始文本对原始文本"比较 |
| `source` | dict / extract 模式：来源变量名（不带花括号） |
| `pattern` / `group` | extract 模式：正则 + 捕获组编号 |
| `missing_behavior` / `fallback_value` | dict / extract：未命中时的处理（`keep_placeholder` / `raise_error` / `use_default`） |
| `probability` | 触发概率（仅 literal/llm） |

**执行顺序**（literal/llm）：先判概率 → 再判条件 → 从命中分支的 `values` 或 `values_else` 中随机选一条 → 替换占位符 → literal 直接返回 / llm 送给 LLM 后返回。

**dict** 独立于概率/条件：直接读 `source` 变量值当 key 查表。

**extract**：从 `source` 变量值里按 `pattern` 抽出 `group` 捕获组（`re.search`）。

**daily_llm**：行为同 `llm`，但结果按 `key` + 当天日期写到 `.var_cache/{key}.{YYYY-MM-DD}.json`，跨天惰性清理。**不要在模板里引用动态内置变量**，否则当天首次生成的旧值会被全天复用。

### `variable_llm_config`

| 字段 | 说明 |
| --- | --- |
| `llm_group` | 模型分组（默认 `utils`） |
| `llm_list[]` | 优先模型名列表，非空时覆盖 group |
| `max_tokens` / `temperature` / `slow_threshold` | LLM 调用参数 |
| `selection_strategy` | `balance`（负载均衡）或 `random` |

### `permission_control`

| 字段 | 说明 |
| --- | --- |
| `global_blacklist[]` | 全局黑名单 |
| `command_user_list_mode` | 命令模式：`whitelist` / `blacklist` |
| `command_user_list[]` | 命令名单 |
| `action_user_list_mode` / `action_user_list[]` | 生图动作的同款配置 |

---

## 常见坑

| 现象 | 怎么排查 |
| --- | --- |
| 决策器不触发生图 | `action_require` 里的规则太严或冲突；查日志看 LLM 输出的动作选择 |
| 报错"未配置 bearer_token" | 字面意思 |
| 报错"检测到循环引用" | A 引用 B、B 又引用 A，断开其中一边 |
| 报错"引用了未定义的变量" | 模板里的 `{xxx}` 没有对应的自定义变量、决策参数或内置变量 |
| 图片不符预期 | 优先看 LLM 翻译节点（`english_*`）的输出；其次检查 dict 是否命中 |
| 生图超时 | 调大 `timeout`；BizyAir 实际响应慢的话考虑改工作流模型 |
| 自拍画面里出现手机 | 检查 `pose_prompt` 中 `phone_in_hand` 那条 tag，强调 `the arm is completely outside the frame` 和 `no phone visible` |
| 自拍出现"双手捧脸" | 同上，再加 `exactly one visible hand in the frame`；并确认 `emotion_dict` / `english_extra` 没输出含 `both hands` 的 tag |
| `daily_llm` 不更新 | 删 `.var_cache/{key}.*.json`；或检查模板是否误引用了动态内置变量 |
| 切换预设没生效 | 写回 toml 失败的话本次仍生效但重启回滚；查日志的写回错误 |

---

## 路线图

| 项 | 状态 |
| --- | --- |
| 决策参数互相引用变量 | ✅ |
| 变量间相互引用（拓扑排序） | ✅ |
| 10 种条件类型 + values_else 分支 | ✅ |
| 软依赖按需求值（不命中分支不触发 LLM） | ✅ |
| 原始值条件比较（跳过占位符替换） | ✅ |
| 图生图（本地文件 / base64 / 引用消息） | ✅ |
| `extract` 模式（regex 抽取） | ✅ |
| `daily_llm` 模式（按天缓存） | ✅ |
| `pose_prompt` 微变量随机化（避免每张图同款手臂） | ✅ |
| 独立 WebUI（替代框架自带的 ConfigLayout） | 🚧 |
| 跨任务持久化变量 | 🚧 |
| 决策器流式调用 | 🚧 |

---

> 改动 `config.toml` 后需要重启 MaiBot 生效（`/dr use` 和 `/dr switch` 除外）。
