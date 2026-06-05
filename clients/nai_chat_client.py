from __future__ import annotations

import base64
import re
from typing import Any

import openai
from openai import AsyncOpenAI

from src.common.logger import get_logger
from .base import BizyAirBaseClient, BizyAirImageResult

logger = get_logger("bizyair_generate_image_plugin")


class NaiChatError(Exception):
    """NAI Chat 调用异常"""


class NaiChatProtocolError(NaiChatError):
    """NAI Chat 返回结构异常"""


class NaiChatClient(BizyAirBaseClient):
    """NAI Chat 生图客户端"""

    def __init__(
            self,
            bearer_token: str,
            base_url: str,
            model: str,
            timeout: float = 180.0,
            vibe_cache_enabled: bool = False,
    ) -> None:
        """初始化 NAI Chat 客户端"""
        super().__init__(bearer_token=bearer_token, timeout=timeout)
        self.base_url = self._validate_url(base_url, "base_url")
        self.model = self._require_non_empty_text(model, "model")
        # vibe cache（§20.3.1）：仅 vibe 预设 + 配置开启时生效；纯计费优化，失败安全
        self._vibe_cache_enabled = bool(vibe_cache_enabled)
        self.client = AsyncOpenAI(
            api_key=self.bearer_token,
            base_url=self.base_url.rstrip("/"),
            timeout=self.timeout,
            max_retries=2,
        )

    def _build_request_payload(self, content_json: str) -> dict[str, Any]:
        """构造 Chat Completions 请求体"""
        return {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": content_json,
                }
            ],
        }

    async def generate_image(self, content_json: str) -> BizyAirImageResult:
        """调用 NAI Chat 接口并将第一张图片转为 data URL 结果对象"""
        markdown_content = await self.create_chat_completion(content_json=content_json)
        image_bytes = self.extract_first_image_bytes(markdown_content)
        data_url = f"data:image/png;base64,{base64.b64encode(image_bytes).decode('utf-8')}"
        return BizyAirImageResult(image_url=data_url)

    async def generate_and_download(self, content_json: str) -> bytes:
        """直接返回 NAI Chat 生成的图片字节"""
        markdown_content = await self.create_chat_completion(content_json=content_json)
        return self.extract_first_image_bytes(markdown_content)

    async def create_chat_completion(self, content_json: str) -> str:
        """调用 Chat Completions 并返回 assistant content。

        开启 vibe cache 时：发请求前查本地缓存把 controlnet 图改写成 cache_id 复用态（§20.3.1，
        省 1 anlas）；响应后把网关回传的 vibe_cache_ids 落库；服务端 cache_id 失效（疑似 stale 400）
        则清本地 + 以编码态重试一次。vibe cache 任何环节出错都降级、绝不阻断出图（失败安全铁律）。
        """
        # 延迟 import 避免 clients ↔ services 包级循环（client 模块级 import services 会触发
        # services/__init__ → openapi_input_value_builder → clients，形成 partial-init 循环）
        from ..services.nai_vibe_cache_rewrite import (
            looks_like_stale_vibe_cache_error,
            persist_vibe_cache_ids,
            purge_vibe_cache_hits,
            rewrite_content_json_for_vibe_cache,
        )

        effective_json, persist_plan, hit_plan = content_json, [], []
        if self._vibe_cache_enabled:
            try:
                effective_json, persist_plan, hit_plan = rewrite_content_json_for_vibe_cache(
                    content_json, model=self.model, log_prefix="[NAI Chat 客户端]"
                )
            except Exception as exc:  # 失败安全：vibe cache 改写出错降级为原始编码态
                logger.warning(f"[NAI Chat 客户端] vibe cache 改写失败，降级为编码态: {exc}")
                effective_json, persist_plan, hit_plan = content_json, [], []

        self._log_request(effective_json)
        try:
            response = await self.client.chat.completions.create(**self._build_request_payload(effective_json))
        except openai.APIError as exc:
            # §20.3.1：服务端 cache_id 未命中不静默回退。若本次确有命中态且错误像 stale，
            # 清掉本地 stale 映射后以编码态（重新改写，此时全未命中→全字节态）重试一次。
            if hit_plan and looks_like_stale_vibe_cache_error(str(exc)):
                purge_vibe_cache_hits(self.model, hit_plan, log_prefix="[NAI Chat 客户端]")
                logger.warning("[NAI Chat 客户端] vibe cache_id 疑似失效，已清本地，改用编码态重试一次")
                try:
                    effective_json, persist_plan, _ = rewrite_content_json_for_vibe_cache(
                        content_json, model=self.model, log_prefix="[NAI Chat 客户端]"
                    )
                except Exception:
                    effective_json, persist_plan = content_json, []
                self._log_request(effective_json)
                try:
                    response = await self.client.chat.completions.create(**self._build_request_payload(effective_json))
                except openai.APIError as retry_exc:
                    raise NaiChatError(f"NAI Chat 调用失败: {retry_exc}") from retry_exc
            else:
                raise NaiChatError(f"NAI Chat 调用失败: {exc}") from exc

        content = self._parse_markdown_content(response)
        if self._vibe_cache_enabled and persist_plan:
            try:
                persist_vibe_cache_ids(
                    content, model=self.model, persist_plan=persist_plan, log_prefix="[NAI Chat 客户端]"
                )
            except Exception as exc:  # 落库失败不影响出图
                logger.warning(f"[NAI Chat 客户端] vibe cache 落库失败（不影响出图）: {exc}")
        return content

    def _log_request(self, content_json: str) -> None:
        """打印请求体日志：content_json 可能含 i2i/vibe 图片 base64（MB 级）→ 截断预览（base64 铁律）。"""
        preview = content_json if len(content_json) <= 300 else f"{content_json[:300]}...(+{len(content_json) - 300} 字符)"
        logger.info(f"[NAI Chat 客户端] 调用请求体: model={self.model}, content={preview}")

    def _parse_markdown_content(self, data: Any) -> str:
        """解析 chat completion 返回中的 assistant content"""
        choices = getattr(data, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise NaiChatProtocolError(f"choices 不存在或为空: {choices}")

        first_choice = choices[0]
        if first_choice is None:
            raise NaiChatProtocolError(f"choices[0] 不是 object: {first_choice}")

        message = getattr(first_choice, "message", None)
        if message is None:
            raise NaiChatProtocolError(f"choices[0].message 不是 object: {message}")

        content = getattr(message, "content", None)
        text = "" if content is None else str(content).strip()
        if not text:
            raise NaiChatProtocolError("choices[0].message.content 为空")
        return text

    @staticmethod
    def extract_first_image_bytes(markdown_content: str) -> bytes:
        """从 markdown data URI 中提取第一张图片字节"""
        if not markdown_content or not str(markdown_content).strip():
            raise NaiChatProtocolError("markdown_content 为空")

        matches = re.findall(r"data:image/(\w+);base64,([A-Za-z0-9+/=]+)", markdown_content)
        if not matches:
            raise NaiChatProtocolError("未在返回内容中找到 data URI 图片")

        _, b64_data = matches[0]
        try:
            return base64.b64decode(b64_data)
        except Exception as exc:
            raise NaiChatProtocolError("图片 base64 解码失败") from exc