import base64

import pytest

from clients.base import BizyAirImageResult
from clients.nai_chat_client import NaiChatClient, NaiChatProtocolError


class TestBizyAirImageResultRepr:
    """安全 repr：防止 NAI 的 base64 data URL 把日志撑爆。"""

    def test_data_url_repr_omits_base64_payload(self):
        big = "data:image/png;base64," + "A" * 500_000
        result = BizyAirImageResult(image_url=big)
        text = repr(result)
        # 整段 base64 不得出现在 repr 里
        assert "A" * 500 not in text
        assert "base64 omitted" in text
        assert "data:image/png;base64" in text  # 仍保留可辨识的前缀
        assert len(text) < 200  # repr 必须短

    def test_data_url_repr_in_fstring_is_truncated(self):
        big = "data:image/png;base64," + "Q" * 100_000
        result = BizyAirImageResult(image_url=big)
        assert "Q" * 100 not in f"图片生成完成: {result}"

    def test_normal_https_url_repr_kept_intact(self):
        url = "https://oss.bizyair.cn/output/abc123.png"
        assert url in repr(BizyAirImageResult(image_url=url))

    def test_overlong_non_data_url_truncated(self):
        url = "https://example.com/" + "x" * 400
        text = repr(BizyAirImageResult(image_url=url))
        assert "truncated" in text
        assert "x" * 400 not in text


class TestBizyAirImageResultDownload:
    """data URL 直接解码、不走 HTTP（修 InvalidURL: URL too long——NAI 内联返回整张图的 base64）。"""

    @pytest.mark.asyncio
    async def test_download_bytes_decodes_base64_data_url(self):
        raw = b"\x89PNG\r\n\x1a\n" + b"payload-bytes" * 10
        data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
        result = BizyAirImageResult(image_url=data_url)
        assert await result.download_bytes() == raw

    @pytest.mark.asyncio
    async def test_download_bytes_huge_data_url_does_not_raise_url_too_long(self):
        # 复现报错：2MB 图编码后的 data URL 远超 httpx URL 长度上限，旧实现会 InvalidURL: URL too long
        raw = b"\x00\x01\x02\x03" * 500_000  # 2MB
        data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
        result = BizyAirImageResult(image_url=data_url)
        assert await result.download_bytes() == raw


class TestNaiChatClient:
    def test_extract_first_image_bytes_normal(self):
        raw = b"hello-image"
        b64 = base64.b64encode(raw).decode("utf-8")
        content = f"![image_0](data:image/png;base64,{b64})"
        assert NaiChatClient.extract_first_image_bytes(content) == raw

    def test_extract_first_image_bytes_missing_image_raises(self):
        with pytest.raises(NaiChatProtocolError, match="data URI"):
            NaiChatClient.extract_first_image_bytes("plain text")