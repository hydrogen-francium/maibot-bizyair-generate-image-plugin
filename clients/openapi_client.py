from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any, List

import httpx

from src.common.logger import get_logger
from .base import BizyAirBaseClient, BizyAirImageResult, BizyAirOpenApiOutput

logger = get_logger("bizyair_generate_image_plugin")


class BizyAirOpenApiError(Exception):
    """BizyAir OpenAPI 调用异常"""


class BizyAirOpenApiProtocolError(BizyAirOpenApiError):
    """BizyAir OpenAPI 返回结果与预期不符"""


@dataclass(frozen=True)
class BizyAirOpenApiParameterBinding:
    field: str
    value_template: Any
    value_type: str = "string"


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


class BizyAirOpenApiClient(BizyAirBaseClient):
    """BizyAir 文生图 OpenAPI 客户端"""

    API_URL = "https://api.bizyair.cn/w/v1/webapp/task/openapi/create"
    WEB_APP_ID = 39429
    DEFAULT_RANDOM_SEED_MIN = 0
    DEFAULT_RANDOM_SEED_MAX = 2147483647

    SEED_PLACEHOLDER = "{random_seed}"
    SUCCESS_STATUS = "Success"

    def __init__(
            self,
            bearer_token: str,
            api_url: str | None = None,
            web_app_id: int = WEB_APP_ID,
            timeout: float = 180.0,
            parameter_bindings: list[BizyAirOpenApiParameterBinding] | None = None,
    ) -> None:
        """初始化 OpenAPI 客户端配置"""
        super().__init__(bearer_token=bearer_token, timeout=timeout)
        self.api_url = api_url or self.API_URL
        self.web_app_id = int(web_app_id)
        self.parameter_bindings: List[BizyAirOpenApiParameterBinding] = parameter_bindings or []

    @classmethod
    def parse_parameter_bindings(cls, raw_bindings: Any) -> list[BizyAirOpenApiParameterBinding]:
        """解析并校验参数映射配置"""
        if raw_bindings is None:
            return []
        if not isinstance(raw_bindings, list) or not raw_bindings:
            raise ValueError("openapi_parameter_mappings 必须是非空列表")

        bindings: list[BizyAirOpenApiParameterBinding] = []
        for index, item in enumerate(raw_bindings):
            if not isinstance(item, dict):
                raise ValueError(f"openapi_parameter_mappings[{index}] 必须是对象")

            field = cls._require_mapping_text(item.get("field"), f"openapi_parameter_mappings[{index}].field")
            if "value" not in item:
                raise ValueError(f"openapi_parameter_mappings[{index}].value 缺失")
            value_type = cls._parse_value_type(item.get("value_type", "string"), f"openapi_parameter_mappings[{index}].value_type")
            value_template = cls._coerce_mapping_value(item.get("value"), value_type, f"openapi_parameter_mappings[{index}].value")

            bindings.append(BizyAirOpenApiParameterBinding(field=field, value_template=value_template, value_type=value_type))
        return bindings

    def _build_request_payload(self, template_context: dict[str, Any], suppress_preview_output: bool = False) -> dict[str, Any]:
        """构造创建任务请求体"""
        return {
            "web_app_id": self.web_app_id,
            "suppress_preview_output": suppress_preview_output,
            "input_values": self._build_input_values(template_context),
        }

    def _build_input_values(self, template_context: dict[str, Any]) -> dict[str, Any]:
        """根据映射规则构造 input_values"""
        placeholder_values = self._build_placeholder_values(template_context)
        input_values: dict[str, Any] = {}
        for binding in self.parameter_bindings:
            input_values[binding.field] = self._resolve_template_value(binding.value_template, placeholder_values)
        return input_values

    def _build_placeholder_values(self, template_context: dict[str, Any]) -> dict[str, Any]:
        """构造占位符到实际值的映射表"""
        placeholder_values = {
            self.SEED_PLACEHOLDER: self._generate_random_seed(),
        }
        for key, value in template_context.items():
            placeholder_values[f"{{{key}}}"] = value
        return placeholder_values

    @classmethod
    def resolve_template_value_static(cls, value_template: Any, template_context: dict[str, Any]) -> Any:
        """基于模板上下文静态解析模板值"""
        placeholder_values = {f"{{{key}}}": value for key, value in template_context.items()}
        return cls._resolve_template_value_static(value_template, placeholder_values)

    @classmethod
    def _resolve_template_value_static(cls, value_template: Any, placeholder_values: dict[str, Any]) -> Any:
        """静态递归解析模板值中的占位符"""
        if isinstance(value_template, str):
            stripped = value_template.strip()
            if stripped in placeholder_values:
                return placeholder_values[stripped]

            resolved = value_template
            for placeholder, value in placeholder_values.items():
                resolved = resolved.replace(placeholder, str(value))
            return resolved

        if isinstance(value_template, list):
            return [cls._resolve_template_value_static(item, placeholder_values) for item in value_template]

        if isinstance(value_template, dict):
            return {key: cls._resolve_template_value_static(value, placeholder_values) for key, value in value_template.items()}

        return value_template

    def _resolve_template_value(self, value_template: Any, placeholder_values: dict[str, Any]) -> Any:
        """递归解析模板值中的占位符"""
        if isinstance(value_template, str):
            stripped = value_template.strip()
            if stripped in placeholder_values:
                return placeholder_values[stripped]

            resolved = value_template
            for placeholder, value in placeholder_values.items():
                resolved = resolved.replace(placeholder, str(value))
            return resolved

        if isinstance(value_template, list):
            return [self._resolve_template_value(item, placeholder_values) for item in value_template]

        if isinstance(value_template, dict):
            return {key: self._resolve_template_value(value, placeholder_values) for key, value in value_template.items()}

        return value_template

    @classmethod
    def _parse_value_type(cls, value: Any, field_name: str) -> str:
        """解析并校验映射值类型"""
        text = cls._require_mapping_text(value, field_name).lower()
        allowed = {"string", "int", "boolean", "json"}
        if text not in allowed:
            raise ValueError(f"{field_name} 只能是 string、int、boolean、json 之一")
        return text

    @classmethod
    def _coerce_mapping_value(cls, value: Any, value_type: str, field_name: str) -> Any:
        """按声明类型强制转换映射值"""
        if value_type == "string":
            if value is None:
                return ""
            return str(value)

        raw_text = "" if value is None else str(value).strip()

        if value_type == "int":
            try:
                return int(raw_text)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field_name} 不是合法整数: {value}") from exc

        if value_type == "boolean":
            normalized = raw_text.lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"{field_name} 不是合法布尔值: {value}")

        if value_type == "json":
            if not raw_text:
                raise ValueError(f"{field_name} 不能为空")
            try:
                return json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{field_name} 不是合法 JSON: {value}") from exc

        raise ValueError(f"{field_name} 的类型不支持: {value_type}")

    @classmethod
    def _require_mapping_text(cls, value: Any, field_name: str) -> str:
        """校验映射字段文本非空"""
        text = "" if value is None else str(value).strip()
        if not text:
            raise ValueError(f"{field_name} 不能为空")
        return text

    @classmethod
    def _generate_random_seed(cls) -> int:
        """生成随机种子值"""
        return random.randint(cls.DEFAULT_RANDOM_SEED_MIN, cls.DEFAULT_RANDOM_SEED_MAX)

    async def generate_image(
            self,
            action_inputs: dict[str, Any],
            template_context: dict[str, Any] | None = None,
            suppress_preview_output: bool = False,
    ) -> BizyAirImageResult:
        """创建任务并转换为图片结果"""
        response = await self.create_task(
            action_inputs=action_inputs,
            template_context=template_context,
            suppress_preview_output=suppress_preview_output,
        )
        return response.to_image_result()

    async def create_task(
            self,
            action_inputs: dict[str, Any],
            template_context: dict[str, Any] | None = None,
            suppress_preview_output: bool = False,
    ) -> BizyAirOpenApiResponse:
        """调用 OpenAPI 创建生成任务"""
        if not isinstance(action_inputs, dict) or not action_inputs:
            raise ValueError("action_inputs 必须是非空对象")

        if template_context is None:
            template_context = action_inputs
        if not isinstance(template_context, dict) or not template_context:
            raise ValueError("template_context 必须是非空对象")

        payload = self._build_request_payload(
            template_context=template_context,
            suppress_preview_output=suppress_preview_output,
        )

        logger.info(f"准备调用 OpenAPI 。请求体:{payload}")

        headers = self._build_headers()
        headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            response = await client.post(self.api_url, json=payload)
            response.raise_for_status()
            data = response.json()

        return self._parse_response(data)

    def _parse_response(self, data: dict[str, Any]) -> BizyAirOpenApiResponse:
        """解析并校验 OpenAPI 响应结构"""
        if not isinstance(data, dict):
            raise BizyAirOpenApiProtocolError(f"返回结果不是 JSON object: {type(data)}")

        status = str(data.get("status", "")).strip()
        if status != self.SUCCESS_STATUS:
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
