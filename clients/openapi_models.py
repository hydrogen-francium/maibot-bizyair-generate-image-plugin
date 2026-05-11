from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import BizyAirImageResult, BizyAirOpenApiOutput


class BizyAirOpenApiError(Exception):
    """BizyAir OpenAPI 调用异常"""


class BizyAirOpenApiProtocolError(BizyAirOpenApiError):
    """BizyAir OpenAPI 返回结果与预期不符"""


class BizyAirOpenApiContentFilterError(BizyAirOpenApiError):
    """BizyAir OpenAPI 因内容审核被拒（HTTP 422 或 audit_status 异常）"""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class BizyAirOpenApiParameterBinding:
    """OpenAPI 参数映射定义对象"""

    field: str
    value_template: Any
    value_type: str = "string"
    send_if_empty: bool = False
    upload: bool = False


@dataclass(frozen=True)
class BizyAirParameterBinding:
    """统一的参数映射定义对象"""

    field: str
    value_template: Any
    value_type: str = "string"
    send_if_empty: bool = False


@dataclass
class BizyAirOpenApiResponse:
    type: str
    status: str
    request_id: str
    outputs: list[BizyAirOpenApiOutput]
    raw_data: dict[str, Any]

    @property
    def primary_image_url(self) -> str:
        """返回主图片地址"""
        if not self.outputs:
            raise BizyAirOpenApiProtocolError("outputs 为空，无法获取图片 URL")
        return self.outputs[0].object_url

    def to_image_result(self) -> BizyAirImageResult:
        """将响应对象转换为图片结果对象"""
        return BizyAirImageResult(image_url=self.primary_image_url)
