import base64

import pytest

from clients.nai_chat_client import NaiChatClient, NaiChatProtocolError


class TestNaiChatClient:
    def test_extract_first_image_bytes_normal(self):
        raw = b"hello-image"
        b64 = base64.b64encode(raw).decode("utf-8")
        content = f"![image_0](data:image/png;base64,{b64})"
        assert NaiChatClient.extract_first_image_bytes(content) == raw

    def test_extract_first_image_bytes_missing_image_raises(self):
        with pytest.raises(NaiChatProtocolError, match="data URI"):
            NaiChatClient.extract_first_image_bytes("plain text")