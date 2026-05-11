from __future__ import annotations

from typing import Any

import httpx

from src.common.logger import get_logger
from .base import BizyAirBaseClient, BizyAirImageResult, BizyAirOpenApiOutput
from .openapi_models import (
    BizyAirOpenApiContentFilterError,
    BizyAirOpenApiError,
    BizyAirOpenApiProtocolError,
    BizyAirOpenApiResponse,
)

logger = get_logger("bizyair_generate_image_plugin")


class BizyAirOpenApiClient(BizyAirBaseClient):
    """BizyAir 文生图 OpenAPI 客户端"""

    API_URL = "https://api.bizyair.cn/w/v1/webapp/task/openapi/create"
    WEB_APP_ID = 39429
    SUCCESS_STATUS = "Success"

    def __init__(
            self,
            bearer_token: str,
            api_url: str | None = None,
            web_app_id: int = WEB_APP_ID,
            timeout: float = 180.0,
    ) -> None:
        """初始化 OpenAPI 客户端配置"""
        super().__init__(bearer_token=bearer_token, timeout=timeout)
        self.api_url = api_url or self.API_URL
        self.web_app_id = int(web_app_id)

    def _build_request_payload(
            self,
            input_values: dict[str, Any],
            suppress_preview_output: bool = False,
    ) -> dict[str, Any]:
        """构造创建任务请求体"""
        return {
            "web_app_id": self.web_app_id,
            "suppress_preview_output": suppress_preview_output,
            "input_values": input_values,
        }

    async def generate_image(
            self,
            input_values: dict[str, Any],
            suppress_preview_output: bool = False,
    ) -> BizyAirImageResult:
        """创建任务并转换为图片结果"""
        response = await self.create_task(
            input_values=input_values,
            suppress_preview_output=suppress_preview_output,
        )
        return response.to_image_result()

    async def create_task(
            self,
            input_values: dict[str, Any],
            suppress_preview_output: bool = False,
    ) -> BizyAirOpenApiResponse:
        """调用 OpenAPI 创建生成任务"""
        if not isinstance(input_values, dict) or not input_values:
            raise ValueError("input_values 必须是非空对象")

        payload = self._build_request_payload(
            input_values=input_values,
            suppress_preview_output=suppress_preview_output,
        )

        logger.info(f"[OpenAPI 客户端] 调用请求体:{payload}")

        headers = self._build_headers()
        headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            response = await client.post(self.api_url, json=payload)
            if response.status_code == 422:
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                raise BizyAirOpenApiContentFilterError(
                    f"OpenAPI 返回 422 内容审核失败: {body}",
                    status_code=422,
                    body=body,
                )
            response.raise_for_status()
            data = response.json()

        return self._parse_response(data)

    def _parse_response(self, data: dict[str, Any]) -> BizyAirOpenApiResponse:
        """解析并校验 OpenAPI 响应结构"""
        if not isinstance(data, dict):
            raise BizyAirOpenApiProtocolError(f"返回结果不是 JSON object: {type(data)}")

        status = str(data.get("status", "")).strip()
        if status != self.SUCCESS_STATUS:
            body_lower = str(data).lower()
            if "content_filter" in body_lower or "content filter" in body_lower or "审核" in body_lower:
                raise BizyAirOpenApiContentFilterError(
                    f"OpenAPI 调用被内容审核拦截，status={status!r}, body={data}",
                    status_code=None,
                    body=data,
                )
            raise BizyAirOpenApiError(f"OpenAPI 调用失败，status={status!r}, body={data}")

        request_id = self._require_protocol_text(data.get("request_id"), "request_id")
        response_type = self._require_protocol_text(data.get("type"), "type")

        raw_outputs = data.get("outputs")
        if not isinstance(raw_outputs, list) or not raw_outputs:
            raise BizyAirOpenApiProtocolError(f"outputs 不存在或为空: {raw_outputs}")

        outputs: list[BizyAirOpenApiOutput] = []
        for index, item in enumerate(raw_outputs):
            if not isinstance(item, dict):
                raise BizyAirOpenApiProtocolError(f"outputs[{index}] 不是 object: {item}")

            object_url = self._require_protocol_text(item.get("object_url"), f"outputs[{index}].object_url")
            try:
                object_url = self._validate_url(object_url, f"outputs[{index}].object_url")
            except ValueError as exc:
                raise BizyAirOpenApiProtocolError(str(exc)) from exc

            output_ext = self._require_protocol_text(item.get("output_ext"), f"outputs[{index}].output_ext")
            outputs.append(
                BizyAirOpenApiOutput(
                    object_url=object_url,
                    output_ext=output_ext,
                    cost_time=self._optional_int(item.get("cost_time")),
                    audit_status=self._optional_int(item.get("audit_status")),
                    error_type=self._optional_text(item.get("error_type")),
                )
            )

        return BizyAirOpenApiResponse(
            type=response_type,
            status=status,
            request_id=request_id,
            outputs=outputs,
            raw_data=data,
        )

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        """将可选整数字段转换为整数"""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise BizyAirOpenApiProtocolError(f"字段不是合法整数: {value}") from exc

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        """将可选文本字段转换为字符串"""
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _require_protocol_text(value: Any, field_name: str) -> str:
        """校验协议字段文本非空"""
        text = "" if value is None else str(value).strip()
        if not text:
            raise BizyAirOpenApiProtocolError(f"{field_name} 为空")
        return text
