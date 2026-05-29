from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote_to_bytes

import httpx


@dataclass(repr=False)
class BizyAirImageResult:
    """统一的图片结果对象。"""

    image_url: str

    def __repr__(self) -> str:
        """安全 repr：data URL / 超长 URL 一律截断，避免把整张图的 base64 打进日志撑爆。

        NAI Chat 后端返回的是内联 data:image/png;base64,<整张图> 的 data URL，
        默认 dataclass repr 会把它整个打出来。这里统一截断。
        """
        url = self.image_url
        if isinstance(url, str) and url.startswith("data:"):
            prefix = url.split(",", 1)[0]
            return f"BizyAirImageResult(image_url='{prefix},<{len(url)} chars base64 omitted>')"
        if isinstance(url, str) and len(url) > 256:
            return f"BizyAirImageResult(image_url={url[:80]!r}…<{len(url)} chars, truncated>)"
        return f"BizyAirImageResult(image_url={url!r})"

    async def download_bytes(self, timeout: float = 180.0) -> bytes:
        # data URL（NAI Chat 后端把整张图内联返回）：图片就在 URL 里，直接解码，不能也不必走 HTTP
        if isinstance(self.image_url, str) and self.image_url.startswith("data:"):
            return self._decode_data_url(self.image_url)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(self.image_url, timeout=timeout)
            response.raise_for_status()
            return response.content

    @staticmethod
    def _decode_data_url(data_url: str) -> bytes:
        """解码 data: URL 的图片字节（支持 base64 与百分号编码两种 payload）。"""
        header, sep, payload = data_url.partition(",")
        if not sep:
            raise ValueError("非法 data URL：缺少逗号分隔的 payload")
        if ";base64" in header.lower():
            return base64.b64decode(payload)
        return unquote_to_bytes(payload)

    async def save_to_file(self, file_path: str | Path, timeout: float = 180.0) -> Path:
        data = await self.download_bytes(timeout=timeout)
        path = Path(file_path)
        path.write_bytes(data)
        return path


@dataclass
class BizyAirOpenApiOutput:
    object_url: str
    output_ext: str
    cost_time: int | None = None
    audit_status: int | None = None
    error_type: str | None = None


class BizyAirBaseClient(ABC):
    """BizyAir 客户端公共基类。"""

    def __init__(self, bearer_token: str, timeout: float = 180.0) -> None:
        if not bearer_token or not bearer_token.strip():
            raise ValueError("bearer_token 不能为空")

        self.bearer_token = bearer_token.strip()
        self.timeout = timeout if timeout > 0 else 180.0

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bearer_token}",
        }

    @staticmethod
    def _normalize_resolution(resolution: str) -> str:
        text = str(resolution).strip()
        if not text:
            return text
        if text.lower() == "auto":
            return "auto"
        return text.upper()

    @staticmethod
    def _validate_choice(value: str, allowed_values: Iterable[str], field_name: str) -> str:
        allowed_set = set(allowed_values)
        if value not in allowed_set:
            raise ValueError(f"{field_name} 非法: {value}，必须是 {sorted(allowed_set)} 中之一")
        return value

    @staticmethod
    def _validate_url(url: str, field_name: str = "url") -> str:
        text = str(url).strip()
        if not text:
            raise ValueError(f"{field_name} 不能为空")
        if not text.startswith("http://") and not text.startswith("https://"):
            raise ValueError(f"{field_name} 不是合法 URL: {text}")
        return text

    @staticmethod
    def _require_non_empty_text(value: str, field_name: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{field_name} 不能为空")
        return text

    async def _download_image_bytes(self, image_url: str) -> bytes:
        result = BizyAirImageResult(image_url=self._validate_url(image_url, "image_url"))
        return await result.download_bytes(timeout=self.timeout)

    async def _save_image_file(self, image_url: str, file_path: str | Path) -> Path:
        result = BizyAirImageResult(image_url=self._validate_url(image_url, "image_url"))
        return await result.save_to_file(file_path=file_path, timeout=self.timeout)

    @abstractmethod
    async def generate_image(self, *args, **kwargs) -> BizyAirImageResult:
        raise NotImplementedError

    async def generate_and_save(self, *args, file_path: str | Path, **kwargs) -> Path:
        result = await self.generate_image(*args, **kwargs)
        return await result.save_to_file(file_path=file_path, timeout=self.timeout)
