from __future__ import annotations

import base64
import re
from typing import Any

import httpx

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
    ) -> None:
        """初始化 NAI Chat 客户端"""
        super().__init__(bearer_token=bearer_token, timeout=timeout)
        self.base_url = self._validate_url(base_url, "base_url")
        self.model = self._require_non_empty_text(model, "model")

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
        """调用 Chat Completions 并返回 assistant content"""
        payload = self._build_request_payload(content_json)
        headers = self._build_headers()
        headers["Content-Type"] = "application/json"
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"

        logger.info(f"[NAI Chat 客户端] 调用请求体: {payload}")

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            data = response.json()
        return self._parse_markdown_content(data)

    def _parse_markdown_content(self, data: dict[str, Any]) -> str:
        """解析 chat completion 返回中的 assistant content"""
        if not isinstance(data, dict):
            raise NaiChatProtocolError(f"返回结果不是 JSON object: {type(data)}")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise NaiChatProtocolError(f"choices 不存在或为空: {choices}")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise NaiChatProtocolError(f"choices[0] 不是 object: {first_choice}")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise NaiChatProtocolError(f"choices[0].message 不是 object: {message}")

        content = message.get("content")
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