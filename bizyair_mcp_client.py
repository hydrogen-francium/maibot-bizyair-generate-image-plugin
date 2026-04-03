from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent


class BizyAirMcpError(Exception):
    """BizyAir MCP 调用异常"""


class BizyAirMcpProtocolError(BizyAirMcpError):
    """BizyAir MCP 返回结果不符合已确认协议"""


@dataclass
class BizyAirImageResult:
    """
    图片生成结果
    当前 MCP 返回的是图片 URL
    """
    image_url: str

    async def download_bytes(self, timeout: float = 180.0) -> bytes:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(self.image_url)
            response.raise_for_status()
            return response.content

    async def save_to_file(self, file_path: str | Path, timeout: float = 180.0) -> Path:
        data = await self.download_bytes(timeout=timeout)
        path = Path(file_path)
        path.write_bytes(data)
        return path


class BizyAirMcpClient:
    """
    BizyAir 文生图 MCP 的 Python Client。

    - 远程 Streamable HTTP MCP
    - Bearer 鉴权
    - tool 名: banana_text_to_image
    - 参数:
        - 116:BizyAir_NanoBananaPro.prompt
        - 116:BizyAir_NanoBananaPro.aspect_ratio
        - 116:BizyAir_NanoBananaPro.resolution
    - 成功返回:
        CallToolResult(
            isError=False,
            structuredContent=None,
            content=[TextContent(text='<image_url>')]
        )
    """

    MCP_URL = "https://api.bizyair.cn/w/v1/mcp/232"
    TOOL_NAME = "banana_text_to_image"

    PROMPT_KEY = "116:BizyAir_NanoBananaPro.prompt"
    ASPECT_RATIO_KEY = "116:BizyAir_NanoBananaPro.aspect_ratio"
    RESOLUTION_KEY = "116:BizyAir_NanoBananaPro.resolution"

    ALLOWED_ASPECT_RATIOS = {
        "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "auto"
    }
    ALLOWED_RESOLUTIONS = {"1K", "2K", "4K", "auto"}

    def __init__(
            self,
            bearer_token: str,
            mcp_url: str | None = None,
            timeout: float = 180.0,
    ) -> None:
        if not bearer_token or not bearer_token.strip():
            raise ValueError("bearer_token 不能为空")

        self.bearer_token = bearer_token.strip()
        self.mcp_url = mcp_url or self.MCP_URL
        self.timeout = timeout

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bearer_token}",
        }

    def _validate_aspect_ratio(self, aspect_ratio: str) -> None:
        if aspect_ratio not in self.ALLOWED_ASPECT_RATIOS:
            raise ValueError(f"aspect_ratio 非法: {aspect_ratio}，必须是 {sorted(self.ALLOWED_ASPECT_RATIOS)} 中之一")

    def _validate_resolution(self, resolution: str) -> None:
        if resolution not in self.ALLOWED_RESOLUTIONS:
            raise ValueError(f"resolution 非法: {resolution}，必须是 {sorted(self.ALLOWED_RESOLUTIONS)} 中之一")

    async def generate_image(
            self,
            prompt: str,
            aspect_ratio: str = "1:1",
            resolution: str = "1K",
    ) -> BizyAirImageResult:
        """
        调用 BizyAir MCP 生成图片，返回图片 URL
        """
        if not prompt or not prompt.strip():
            raise ValueError("prompt 不能为空")

        self._validate_aspect_ratio(aspect_ratio)
        self._validate_resolution(resolution)

        arguments = {
            self.PROMPT_KEY: prompt,
            self.ASPECT_RATIO_KEY: aspect_ratio,
            self.RESOLUTION_KEY: resolution,
        }

        async with httpx.AsyncClient(
                headers=self._build_headers(),
                timeout=self.timeout,
                follow_redirects=True,
        ) as http_client:
            async with streamable_http_client(self.mcp_url, http_client=http_client) as (
                    read_stream,
                    write_stream,
                    _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(self.TOOL_NAME, arguments=arguments)
                    return self._parse_generate_result(result)

    def _parse_generate_result(self, result: CallToolResult) -> BizyAirImageResult:
        """
        返回格式解析：
        - isError=False
        - structuredContent is None
        - content 长度为 1
        - content[0] 是 TextContent
        - text 是图片 URL
        """
        if result.isError:
            raise BizyAirMcpError(f"MCP tool 调用失败: {result}")

        if result.structuredContent is not None:
            raise BizyAirMcpProtocolError(f"返回 structuredContent 不为 None，与预期不符: {result.structuredContent}")

        if len(result.content) != 1:
            raise BizyAirMcpProtocolError(f"返回 content 数量不是 1，与预期不符: {len(result.content)}")

        item = result.content[0]

        if not isinstance(item, TextContent):
            raise BizyAirMcpProtocolError(f"返回 content[0] 不是 TextContent，与预期不符: {type(item)}")

        image_url = item.text.strip()

        if not image_url:
            raise BizyAirMcpProtocolError("返回的图片 URL 为空")

        if not image_url.startswith("http://") and not image_url.startswith("https://"):
            raise BizyAirMcpProtocolError(
                f"返回的 text 不是合法 URL，与预期不符: {image_url}"
            )

        return BizyAirImageResult(image_url=image_url)

    async def generate_and_download(
            self,
            prompt: str,
            aspect_ratio: str = "1:1",
            resolution: str = "1K",
    ) -> bytes:
        """
        生成图片并直接下载为 bytes。
        """
        result = await self.generate_image(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        return await result.download_bytes(timeout=self.timeout)

    async def generate_and_save(
            self,
            prompt: str,
            file_path: str | Path,
            aspect_ratio: str = "1:1",
            resolution: str = "1K",
    ) -> Path:
        """
        生成图片并保存到本地文件
        """
        result = await self.generate_image(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        return await result.save_to_file(file_path=file_path, timeout=self.timeout)
