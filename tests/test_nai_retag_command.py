# -*- coding: utf-8 -*-
"""nai_retag_command 单测：聚焦纯逻辑（取图 + 失败文案）。

BaseCommand 在测试环境是框架 MagicMock，无法真实例化 execute（与现有 nai_commands 同因）。
故只测命令里真正有逻辑、不依赖框架的部分：
- extract_image_base64_from_message：消息段递归取首图、引用回复优先、多路径兜底
- NaiRetagCommand._failed_message：failed 文案三分支
"""

from types import SimpleNamespace

from _bizyair_plugin.components.nai_retag_command import (
    extract_image_base64_from_message,
    _walk_segments_for_image,
    build_failed_message,
)


def _seg(seg_type, data):
    return SimpleNamespace(type=seg_type, data=data)


def _message_with_segment(root_seg):
    """构造 message.chat_stream.context.message.message_segment = root_seg。"""
    ctx_message = SimpleNamespace(message_segment=root_seg)
    context = SimpleNamespace(message=ctx_message)
    chat_stream = SimpleNamespace(context=context)
    return SimpleNamespace(chat_stream=chat_stream, message_segment=None)


class TestWalkSegments:
    def test_finds_image_in_flat_list(self):
        segs = [_seg("text", "hi"), _seg("image", "BASE64DATA")]
        assert _walk_segments_for_image(segs) == "BASE64DATA"

    def test_recurses_into_seglist(self):
        nested = _seg("seglist", [_seg("text", "x"), _seg("image", "INNER")])
        segs = [_seg("text", "y"), nested]
        assert _walk_segments_for_image(segs) == "INNER"

    def test_emoji_counts_as_image(self):
        assert _walk_segments_for_image([_seg("emoji", "EMOJIB64")]) == "EMOJIB64"

    def test_returns_first_image_reply_first(self):
        # 引用消息段排最前 → 优先返回引用图
        segs = [_seg("image", "QUOTED"), _seg("image", "CURRENT")]
        assert _walk_segments_for_image(segs) == "QUOTED"

    def test_no_image_returns_none(self):
        assert _walk_segments_for_image([_seg("text", "a"), _seg("at", "b")]) is None

    def test_empty_returns_none(self):
        assert _walk_segments_for_image([]) is None
        assert _walk_segments_for_image(None) is None

    def test_non_string_image_data_skipped(self):
        # image 段 data 不是 str → 跳过
        assert _walk_segments_for_image([_seg("image", {"url": "x"})]) is None


class TestExtractFromMessage:
    def test_extract_via_chat_stream_seglist(self):
        root = _seg("seglist", [_seg("text", "看这图"), _seg("image", "IMGB64")])
        msg = _message_with_segment(root)
        assert extract_image_base64_from_message(msg) == "IMGB64"

    def test_extract_single_image_root(self):
        root = _seg("image", "SOLO_IMG")
        msg = _message_with_segment(root)
        assert extract_image_base64_from_message(msg) == "SOLO_IMG"

    def test_fallback_to_own_message_segment(self):
        # chat_stream 路径无图，回落到 message.message_segment
        empty_ctx = _message_with_segment(_seg("text", "no image"))
        empty_ctx.message_segment = _seg("seglist", [_seg("image", "OWN_IMG")])
        assert extract_image_base64_from_message(empty_ctx) == "OWN_IMG"

    def test_no_image_anywhere_returns_none(self):
        msg = _message_with_segment(_seg("text", "纯文字"))
        assert extract_image_base64_from_message(msg) is None

    def test_missing_attributes_returns_none(self):
        # message 没有 chat_stream / message_segment → 不崩，返回 None
        assert extract_image_base64_from_message(SimpleNamespace()) is None


class TestFailedMessage:
    def test_wd14_disabled(self):
        msg = build_failed_message("未启用 WD14 兜底", wd14_enabled=False)
        assert "未开启 WD14" in msg

    def test_gradio_missing(self):
        msg = build_failed_message("WD14: 未安装 gradio_client，无法调用 WD14 在线 Space", wd14_enabled=True)
        assert "gradio_client" in msg
        assert "pip install gradio_client" in msg

    def test_timeout(self):
        msg = build_failed_message("WD14 调用超时", wd14_enabled=True)
        assert "超时" in msg

    def test_service_unavailable_not_misleading(self):
        # WD14 服务级失败（Space 全挂）：不能误导成「这张图可能不是 AI 生成」
        msg = build_failed_message("WD14: 所有 Spaces 都无法使用: timeout", wd14_enabled=True)
        assert "服务暂时不可用" in msg
        assert "AI 生成" not in msg  # 关键：不再误导是图的问题

    def test_wd14_exception_not_misleading(self):
        msg = build_failed_message("WD14 异常: connection refused", wd14_enabled=True)
        assert "服务暂时不可用" in msg
        assert "AI 生成" not in msg

    def test_no_tags_recognized(self):
        # WD14 跑通但没识别到 → 提示换图，不说「不是 AI 生成」
        msg = build_failed_message("WD14 未识别到任何标签", wd14_enabled=True)
        assert "没能反推" in msg
        assert "没能从这张图识别出有效内容" in msg
        assert "AI 生成" not in msg

    def test_truly_generic_still_hints_non_ai(self):
        # 其它未知 detail 才保留「可能非 AI 生成」兜底
        msg = build_failed_message("image_bytes 为空", wd14_enabled=True)
        assert "没能反推" in msg
        assert "AI 生成" in msg

