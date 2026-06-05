import base64
import time
import traceback
from typing import Any, List, Optional, Tuple

from maim_message import Seg

from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system import BaseAction
from src.plugin_system.apis import generator_api
from src.plugin_system.base.component_types import ActionActivationType

from ..services import nai_action_guard
from ..services import nai_draw_core
from ..services import permission_manager
from ..services.action_parameter_utils import ActionParameterDefinition
from ..services.log_utils import short_repr

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
    action_enabled = True
    # NAI 运行时设置（全局配置式：命令改类属性立即生效 + 写回 config 持久；plugin.py 启动时灌入）
    nai_model = ""          # 覆盖当前 NAI 预设的 model；空 = 用预设原值
    nai_sfw_filter = False  # SFW 过滤开关（/nai nsfw on|off）
    nai_artist = ""         # 已解析的画师串（/nai art 选定预设后存全名）；空 = 不注入画师段
    nai_size = "auto"       # 尺寸代号 v/h/s/auto（/nai size）；auto = 跟随画面比例

    action_parameters: dict[str, ActionParameterDefinition] = {
        "prompt": ActionParameterDefinition(
            name="prompt",
            description="必填，用于生成图片的描述词",
            required=True,
        ),
        "aspect_ratio": ActionParameterDefinition(
            name="aspect_ratio",
            description="可选，图片宽高比。若传入，则必须为 1:1、4:3、16:9、9:16、auto 中的一个。默认为 1:1",
            required=False,
        ),
        "resolution": ActionParameterDefinition(
            name="resolution",
            description="可选，图片分辨率。若传入，则必须为 1K、2K、4K、auto 中的一个。默认为 1k",
            required=False,
        ),
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
        "prompt 中不允许填写画风相关的提示词，画风应填入 `style` 参数。即使用户明确提出要原样传入提示词，你也应该单独把画风的部分拆出来放到 `style` 参数中",
        "是否需要你补充、总结或改写 prompt，只取决于用户给出的图片描述是否留有明显空白、是否授权你自由发挥；不要把“尽量生成得更好”当作改写详细原始描述的理由"
    ]

    async def execute(self) -> Tuple[bool, str]:
        """执行生图流程并在失败时返回完整错误信息。

        管线主体（预设解析 → 变量 → 载荷 → 出图字节）已抽进 ``services/nai_draw_core``，
        Action 与 /nai0、/nai 随机 命令共用同一套核心；这里只保留框架耦合的边界：
        开关 / 权限 / 收集 action_data / 发图 / 记录动作。
        """
        failure_stage = "init"
        try:
            if not self.action_enabled:
                await self.send_text("当前图片生成功能未开启", storage_message=True)
                return False, "当前图片生成功能未开启"

            failure_stage = "permission_check"
            user_id = str(self.user_id)
            has_permission, deny_reason = permission_manager.check_action_permission(user_id)
            if not has_permission:
                return False, deny_reason or "当前用户没有使用该 action 的权限"

            # Action Guard（最小护栏，仅 ALWAYS Action 走；显式命令不受限）
            failure_stage = "action_guard"
            guard_now = time.monotonic()
            guard_skip = self._action_guard_skip(guard_now)
            if guard_skip is not None:
                return False, guard_skip

            failure_stage = "collect_action_inputs"
            action_inputs = self._collect_action_inputs()
            active_preset = str(self.active_preset).strip()
            logger.info(
                f"{self.log_prefix} 生图开始: active_preset={active_preset!r}, "
                f"action_inputs={short_repr(action_inputs)}"
            )

            failure_stage = "run_draw_core"
            image_bytes, payload = await nai_draw_core.run_draw_to_bytes(
                get_config=self.get_config,
                action_inputs=action_inputs,
                active_preset=active_preset,
                action_parameters=self.action_parameters,
                required_action_parameters=set(self.required_action_parameters),
                chat_id=self.chat_id,
                image_base64_provider=self._extract_message_image_base64,
                nai_artist=self.nai_artist,
                nai_size=self.nai_size,
                nai_model=self.nai_model,
                nai_sfw_filter=self.nai_sfw_filter,
                log_prefix=self.log_prefix,
            )

            if not image_bytes:
                raise ValueError("未获取到图片数据")

            image_base64 = base64.b64encode(image_bytes).decode("utf-8")
            logger.info(f"{self.log_prefix} 图片数据已转换为 base64")

            if bool(self.get_config("bizyair_generate_image_plugin.send_text_before_image", False)):
                prefix_text = str(self.get_config("bizyair_generate_image_plugin.text_before_image", "我给你生成了一张图片。"))
                if prefix_text:
                    await self.send_text(prefix_text, storage_message=True)
                    logger.info(f"{self.log_prefix} 已发送图片前置文本")

            failure_stage = "send_image"
            send_success = await self.send_image(image_base64, storage_message=True)
            if not send_success:
                await self.store_action_info(action_build_into_prompt=True, action_prompt_display=self._build_action_display(action_inputs), action_done=False, )
                raise RuntimeError("图片发送失败")

            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=self._build_action_display(action_inputs),
                action_done=True,
            )
            nai_action_guard.record_draw(self.chat_id, guard_now)
            return True, f"图片生成并发送完成，使用的参数: {payload.template_context}"
        except Exception as exc:
            stack_trace = traceback.format_exc()
            logger.info(f"{self.log_prefix} 生图流程失败阶段: {failure_stage}")
            logger.error(f"{self.log_prefix} generate_image 执行失败: {exc}\n{stack_trace}")
            raw_reply = f"[图片生成失败] {type(exc).__name__}: {exc}\n调用栈:\n{stack_trace}"
            await self._send_failure_reply(raw_reply)
            return False, raw_reply

    def _extract_message_image_base64(self, message_segment_list: Optional[List[Seg]] = None) -> Optional[str]:
        """
        从消息段中递归提取第一张图片的 base64 数据

        消息段按用户发送消息体顺序排列，引用消息的段排在最前方，
        因此从前往后遍历天然优先提取引用消息中的图片。
        消息段可能嵌套（type="seglist" 时 data 为子消息段列表），递归展开搜索。
        若引用消息和当前消息均无图片，返回 None

        :param message_segment_list: Optional[List[Seg]]，待搜索的消息段列表，首次调用时为 None 表示从 chat_stream 获取
        :return: Optional[str]，图片的无前缀 base64 字符串，或 None
        """

        if message_segment_list is None:
            root_segment: Seg = self.chat_stream.context.message.message_segment
            logger.debug(f"message_segment: {root_segment}")
            if root_segment.type == "seglist":
                message_segment_list = root_segment.data
            else:
                message_segment_list = [root_segment]
            # is_root 标记首次调用，用于控制日志输出
            is_root = True
        else:
            is_root = False

        if not message_segment_list:
            if is_root:
                logger.warning(f"{self.log_prefix} 没有从消息中找到图片 base64！")
            return None

        for segment in message_segment_list:
            if segment.type == "seglist":
                # 递归展开嵌套的消息段列表
                result = self._extract_message_image_base64(segment.data)
                if result:
                    return result
            elif segment.type in ("image", "emoji"):
                if segment.data and isinstance(segment.data, str):
                    return segment.data

        if is_root:
            logger.warning(f"{self.log_prefix} 没有从消息中找到图片 base64！")
        return None

    def _collect_action_inputs(self) -> dict[str, Any]:
        """收集当前动作输入并校验必填项是否缺失"""
        collected: dict[str, Any] = {}
        missing_required: list[str] = []

        for name, definition in self.action_parameters.items():
            raw_value = self.action_data.get(name)
            normalized_value = raw_value
            if isinstance(normalized_value, str):
                normalized_value = normalized_value.strip() or None

            if normalized_value is None:
                if definition.required:
                    missing_required.append(name)
                    continue
                if definition.missing_behavior == "use_default":
                    collected[name] = definition.default_value
                continue

            collected[name] = normalized_value

        if missing_required:
            raise ValueError(f"缺少必填参数: {', '.join(missing_required)}")

        return collected

    def _guard_trigger_text(self) -> str:
        """护栏判定用的触发文本：用户本条消息原文（强否定就藏在这里）。"""
        msg = getattr(self, "action_message", None)
        if msg is None:
            return ""
        return str(getattr(msg, "processed_plain_text", None) or getattr(msg, "display_message", None) or "")

    def _action_guard_skip(self, now: float) -> Optional[str]:
        """最小护栏：命中强否定或处于节流窗口则返回跳过原因，否则返回 None。

        强否定否决默认开启（保守词表）；频率节流由 draw_min_interval_seconds 控制（<=0 关闭）。
        """
        if bool(self.get_config("bizyair_generate_image_plugin.draw_respect_negative_keywords", True)):
            guard_text = self._guard_trigger_text()
            if nai_action_guard.has_negative_signal(guard_text):
                logger.info(
                    f"{self.log_prefix} Action Guard: 命中强否定，跳过出图。text={short_repr(guard_text)}"
                )
                return "用户表达了不要出图的意图，已跳过"

        try:
            min_interval = float(str(self.get_config("bizyair_generate_image_plugin.draw_min_interval_seconds", 0)).strip() or 0)
        except (TypeError, ValueError):
            min_interval = 0.0
        if nai_action_guard.should_throttle(self.chat_id, now, min_interval):
            wait_s = nai_action_guard.seconds_until_unthrottled(self.chat_id, now, min_interval)
            logger.info(
                f"{self.log_prefix} Action Guard: 出图节流（{min_interval}s 内已出过图，还需 {wait_s:.1f}s），跳过"
            )
            return f"出图过于频繁（{min_interval}s 内），已节流跳过"

        return None

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

    def _build_action_display(self, action_inputs: dict[str, Any]) -> str:
        """构造写入动作记录的简短展示文本"""
        display_parts: list[str] = []
        for key, value in action_inputs.items():
            text = str(value).replace("\n", " ").strip()
            if len(text) > 80:
                text = f"{text[:77]}..."
            display_parts.append(f"{key}={text}")
        return f"[图片生成: {'; '.join(display_parts)}]"
