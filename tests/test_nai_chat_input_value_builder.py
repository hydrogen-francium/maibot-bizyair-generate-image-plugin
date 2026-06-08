import json

import pytest

from services.action_parameter_utils import ActionParameterDefinition
from services.nai_chat_input_value_builder import NaiChatInputValueBuilder


class TestNaiChatInputValueBuilder:
    @pytest.mark.asyncio
    async def test_build_message_content_json_normal(self):
        raw = [
            {"field": "prompt", "value_type": "string", "value": "{prompt}"},
            {"field": "steps", "value_type": "int", "value": "23"},
            {"field": "size", "value_type": "json", "value": "[832, 1216]"},
        ]
        bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        result = await NaiChatInputValueBuilder.build_message_content_json(
            parameter_bindings=bindings,
            template_context={"prompt": "a cat"},
            action_inputs={"prompt": "a cat"},
            action_parameter_names={"prompt"},
            required_action_parameters={"prompt"},
            action_parameter_definitions={
                "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True)
            },
            builtin_placeholder_values={},
        )
        assert json.loads(result) == {
            "prompt": "a cat",
            "steps": 23,
            "size": [832, 1216],
        }

    @pytest.mark.asyncio
    async def test_empty_payload_raises(self):
        raw = [
            {"field": "negative_prompt", "value_type": "string", "value": "{missing}"},
        ]
        bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        with pytest.raises(ValueError, match="解析结果为空"):
            await NaiChatInputValueBuilder.build_message_content_json(
                parameter_bindings=bindings,
                template_context={"prompt": "a cat"},
                action_inputs={"prompt": "a cat"},
                action_parameter_names={"prompt", "missing"},
                required_action_parameters={"prompt"},
                action_parameter_definitions={
                    "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True),
                    "missing": ActionParameterDefinition(name="missing", description="缺失", required=False),
                },
                builtin_placeholder_values={},
            )

    @pytest.mark.asyncio
    async def test_prompt_cjk_stripped_before_dump(self):
        # §8：prompt 含中文残留时，送 API 前应被剔除
        raw = [
            {"field": "prompt", "value_type": "string", "value": "{prompt}"},
        ]
        bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        result = await NaiChatInputValueBuilder.build_message_content_json(
            parameter_bindings=bindings,
            template_context={"prompt": "1girl, 红色 dress, smile"},
            action_inputs={"prompt": "1girl, 红色 dress, smile"},
            action_parameter_names={"prompt"},
            required_action_parameters={"prompt"},
            action_parameter_definitions={
                "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True)
            },
            builtin_placeholder_values={},
        )
        assert json.loads(result) == {"prompt": "1girl, dress, smile"}

    def test_sanitize_text_fields_drops_emptied_negative(self):
        # negative_prompt 全是中文 → 清洗后为空 → 该键被移除，不送空串进 NewAPI
        payload = {"prompt": "1girl, blue sky", "negative_prompt": "全中文负面", "steps": 23}
        NaiChatInputValueBuilder._sanitize_text_fields(payload)
        assert payload == {"prompt": "1girl, blue sky", "steps": 23}

    def test_sanitize_text_fields_leaves_non_text_fields(self):
        # size / steps 等非文本字段不受影响
        payload = {"prompt": "1girl", "size": [832, 1216], "steps": 23, "n_samples": 1}
        NaiChatInputValueBuilder._sanitize_text_fields(payload)
        assert payload == {"prompt": "1girl", "size": [832, 1216], "steps": 23, "n_samples": 1}

    def test_sanitize_text_fields_sfw_filter_removes_ecchi(self):
        # /nai nsfw on → sfw_filter=True：剔除 bikini/swimsuit 等擦边 tag，保留普通 tag
        payload = {"prompt": "1girl, bikini, blue sky, smile", "negative_prompt": "lowres"}
        NaiChatInputValueBuilder._sanitize_text_fields(payload, sfw_filter=True)
        assert payload == {"prompt": "1girl, blue sky, smile", "negative_prompt": "lowres"}

    def test_sanitize_text_fields_sfw_off_keeps_ecchi(self):
        # 默认 sfw_filter=False：既有管线故意允许轻量暴露，bikini 不应被剔除
        payload = {"prompt": "1girl, bikini, blue sky, smile"}
        NaiChatInputValueBuilder._sanitize_text_fields(payload, sfw_filter=False)
        assert payload == {"prompt": "1girl, bikini, blue sky, smile"}

    def test_sanitize_text_fields_sfw_filter_still_strips_cjk(self):
        # SFW 开启时 CJK 清洗依旧生效（两道清洗叠加）
        payload = {"prompt": "1girl, 红色 swimsuit, smile"}
        NaiChatInputValueBuilder._sanitize_text_fields(payload, sfw_filter=True)
        assert payload == {"prompt": "1girl, smile"}

    @pytest.mark.asyncio
    async def test_build_message_content_json_sfw_filter_end_to_end(self):
        # 端到端：sfw_filter=True 时构造出的 content_json 已剔除擦边 tag
        raw = [{"field": "prompt", "value_type": "string", "value": "{prompt}"}]
        bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        result = await NaiChatInputValueBuilder.build_message_content_json(
            parameter_bindings=bindings,
            template_context={"prompt": "1girl, swimsuit, smile"},
            action_inputs={"prompt": "1girl, swimsuit, smile"},
            action_parameter_names={"prompt"},
            required_action_parameters={"prompt"},
            action_parameter_definitions={
                "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True)
            },
            builtin_placeholder_values={},
            sfw_filter=True,
        )
        assert json.loads(result) == {"prompt": "1girl, smile"}


_NAI4_MODEL = "nai-diffusion-4-5-full"


async def _build_with_prompt(prompt: str, *, model: str = "", sfw_filter: bool = False) -> dict:
    """便捷封装：用单一 {prompt} 绑定构造 content_json 并解析回 dict。"""
    raw = [{"field": "prompt", "value_type": "string", "value": "{prompt}"}]
    bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
    result = await NaiChatInputValueBuilder.build_message_content_json(
        parameter_bindings=bindings,
        template_context={"prompt": prompt},
        action_inputs={"prompt": prompt},
        action_parameter_names={"prompt"},
        required_action_parameters={"prompt"},
        action_parameter_definitions={
            "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True)
        },
        builtin_placeholder_values={},
        sfw_filter=sfw_filter,
        model=model,
    )
    return json.loads(result)


class TestMultiCharacterChannel:
    """build_message_content_json 的多人 characters[] 通道（NewAPI §7）。"""

    @pytest.mark.asyncio
    async def test_text_multi_splits_into_characters_auto_layout(self):
        prompt = "2girls, indoor,\nchar1:girl, blue hair,\nchar2:girl, white hair,"
        payload = await _build_with_prompt(prompt, model=_NAI4_MODEL)
        assert payload["prompt"] == "2girls, indoor"
        assert payload["characters"] == [
            {"prompt": "girl, blue hair"},
            {"prompt": "girl, white hair"},
        ]
        # 文本路径不带坐标：自动布局
        assert payload["use_coords"] is False
        assert payload["use_order"] is True

    @pytest.mark.asyncio
    async def test_json_multi_with_positions_enables_coords(self):
        prompt = (
            '{"version":3,"format":"multi","global":["2girls","indoor"],'
            '"people":[["girl","blue hair"],["girl","white hair"]],'
            '"positions":["B2","D4"]}'
        )
        payload = await _build_with_prompt(prompt, model=_NAI4_MODEL)
        assert payload["prompt"] == "2girls, indoor"
        assert payload["characters"] == [
            {"prompt": "girl, blue hair", "position": "B2"},
            {"prompt": "girl, white hair", "position": "D4"},
        ]
        assert payload["use_coords"] is True
        assert payload["use_order"] is True

    @pytest.mark.asyncio
    async def test_wrapper_quality_artist_lands_in_global_prompt(self):
        # 包装层质量词/画师串拼在最前，应整体落进 global，不污染角色段
        prompt = (
            "very aesthetic, masterpiece, artist:wlop, 2girls, indoor,\n"
            "char1:girl, blue hair,\nchar2:girl, white hair,"
        )
        payload = await _build_with_prompt(prompt, model=_NAI4_MODEL)
        assert payload["prompt"] == "very aesthetic, masterpiece, artist:wlop, 2girls, indoor"
        assert payload["characters"][0] == {"prompt": "girl, blue hair"}

    @pytest.mark.asyncio
    async def test_char_prompt_cjk_stripped(self):
        # §8：每个 char.prompt 也必须英文，CJK 残留被清洗
        prompt = "2girls, indoor,\nchar1:girl, 蓝色 hair,\nchar2:girl, white hair,"
        payload = await _build_with_prompt(prompt, model=_NAI4_MODEL)
        assert payload["characters"] == [
            {"prompt": "girl, hair"},
            {"prompt": "girl, white hair"},
        ]

    @pytest.mark.asyncio
    async def test_single_person_no_characters(self):
        payload = await _build_with_prompt("solo, 1girl, smile", model=_NAI4_MODEL)
        assert payload == {"prompt": "solo, 1girl, smile"}
        assert "characters" not in payload

    @pytest.mark.asyncio
    async def test_unsupported_model_downgrades_to_single_prompt(self):
        # nai-diffusion-3 不支持多角色：降级单串，flat prompt 原样保留（含 char1:/换行）
        prompt = "2girls, indoor,\nchar1:girl, blue hair,\nchar2:girl, white hair,"
        payload = await _build_with_prompt(prompt, model="nai-diffusion-3")
        assert "characters" not in payload
        assert "use_coords" not in payload
        assert "char1:" in payload["prompt"]
        assert "\n" in payload["prompt"]

    @pytest.mark.asyncio
    async def test_empty_model_does_not_split(self):
        # model 缺省（"")时不启用多角色——保证既有单人测试不被影响
        prompt = "2girls, indoor,\nchar1:girl, blue hair,\nchar2:girl, white hair,"
        payload = await _build_with_prompt(prompt)
        assert "characters" not in payload

    @pytest.mark.asyncio
    async def test_sfw_filter_applies_to_characters(self):
        prompt = "2girls,\nchar1:girl, bikini, smile,\nchar2:girl, dress,"
        payload = await _build_with_prompt(prompt, model=_NAI4_MODEL, sfw_filter=True)
        assert payload["characters"] == [
            {"prompt": "girl, smile"},
            {"prompt": "girl, dress"},
        ]


class TestModelSupportsMultiCharacter:
    def test_nai4_variants_supported(self):
        for model in (
            "nai-diffusion-4-5-full",
            "nai-diffusion-4-5-curated",
            "nai-diffusion-4-full",
            "NAI-Diffusion-4-Curated",  # 大小写不敏感
        ):
            assert NaiChatInputValueBuilder._model_supports_multi_character(model) is True

    def test_non_nai4_not_supported(self):
        for model in ("nai-diffusion-3", "nai-diffusion-furry-3", "", "gpt-image-1"):
            assert NaiChatInputValueBuilder._model_supports_multi_character(model) is False


class TestNormalizeCharactersForInner:
    _norm = staticmethod(NaiChatInputValueBuilder._normalize_characters_for_inner)

    def test_drops_empty_keys_when_no_position(self):
        out = self._norm([
            {"prompt": "girl, a", "negative_prompt": "", "position": ""},
            {"prompt": "girl, b", "negative_prompt": "", "position": ""},
        ])
        assert out == [{"prompt": "girl, a"}, {"prompt": "girl, b"}]

    def test_keeps_valid_positions_and_negative(self):
        out = self._norm([
            {"prompt": "girl, a", "negative_prompt": "white hair", "position": "b2"},
            {"prompt": "girl, b", "negative_prompt": "", "position": "D4"},
        ])
        assert out == [
            {"prompt": "girl, a", "negative_prompt": "white hair", "position": "B2"},
            {"prompt": "girl, b", "position": "D4"},
        ]

    def test_any_missing_position_clears_all(self):
        # 一项缺合法坐标 → 全部清空，交后端自动布局
        out = self._norm([
            {"prompt": "girl, a", "position": "B2"},
            {"prompt": "girl, b", "position": "X9"},  # 非法
        ])
        assert out == [{"prompt": "girl, a"}, {"prompt": "girl, b"}]

    def test_drops_empty_prompt_then_under_two_returns_empty(self):
        out = self._norm([
            {"prompt": "girl, a", "position": ""},
            {"prompt": "", "position": ""},
        ])
        assert out == []

    def test_single_returns_empty(self):
        assert self._norm([{"prompt": "girl, a"}]) == []

    def test_none_returns_empty(self):
        assert self._norm(None) == []


def _i2i_png_b64(w: int, h: int) -> str:
    """构造已知尺寸的最小 PNG 头 base64（read_image_dimensions 只读头）。"""
    import base64
    import struct
    data = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x00" * 8
    return base64.b64encode(data).decode("ascii")


class TestI2iChannel:
    """builder 的 i2i 通道（NewAPI §20.1）：尺寸对齐原图 + 字段组装 + 越界 clamp + 友好拒绝。"""

    def test_valid_image_overrides_size_and_adds_i2i(self):
        payload = {"prompt": "1girl, smile", "size": [1024, 1024], "steps": 23}
        NaiChatInputValueBuilder._apply_i2i_channel(
            payload, image=_i2i_png_b64(832, 1216), strength=0.5, noise=0.1
        )
        assert payload["size"] == [832, 1216]          # 用原图尺寸覆盖
        assert payload["i2i"]["strength"] == 0.5
        assert payload["i2i"]["noise"] == 0.1
        assert payload["i2i"]["image"]                  # 裸 base64
        assert payload["prompt"] == "1girl, smile"      # 不碰 prompt

    def test_empty_image_raises(self):
        with pytest.raises(ValueError, match="参考图"):
            NaiChatInputValueBuilder._apply_i2i_channel({}, image="", strength=0.7, noise=0.0)

    def test_unparseable_image_raises(self):
        import base64
        with pytest.raises(ValueError, match="无法解析"):
            NaiChatInputValueBuilder._apply_i2i_channel(
                {}, image=base64.b64encode(b"not an image at all").decode(), strength=0.7, noise=0.0
            )

    def test_non_64_multiple_size_raises(self):
        with pytest.raises(ValueError, match="标准尺寸"):
            NaiChatInputValueBuilder._apply_i2i_channel(
                {}, image=_i2i_png_b64(833, 1216), strength=0.7, noise=0.0
            )

    def test_oversized_raises(self):
        # 1216x1216 方图超 1024 上限
        with pytest.raises(ValueError, match="标准尺寸"):
            NaiChatInputValueBuilder._apply_i2i_channel(
                {}, image=_i2i_png_b64(1216, 1216), strength=0.7, noise=0.0
            )

    def test_strength_noise_clamped(self):
        payload = {}
        NaiChatInputValueBuilder._apply_i2i_channel(
            payload, image=_i2i_png_b64(1024, 1024), strength=5.0, noise=-1.0
        )
        assert payload["i2i"]["strength"] == 0.99   # 上界 clamp
        assert payload["i2i"]["noise"] == 0.0        # 下界 clamp

    def test_none_uses_defaults(self):
        payload = {}
        NaiChatInputValueBuilder._apply_i2i_channel(
            payload, image=_i2i_png_b64(1024, 1024), strength=None, noise=None
        )
        assert payload["i2i"]["strength"] == 0.7
        assert payload["i2i"]["noise"] == 0.0

    @pytest.mark.asyncio
    async def test_end_to_end_i2i_enabled_overrides_size(self):
        raw = [
            {"field": "prompt", "value_type": "string", "value": "{prompt}"},
            {"field": "size", "value_type": "json", "value": "[1024, 1024]"},
        ]
        bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        result = await NaiChatInputValueBuilder.build_message_content_json(
            parameter_bindings=bindings,
            template_context={"prompt": "1girl"},
            action_inputs={"prompt": "1girl"},
            action_parameter_names={"prompt"},
            required_action_parameters={"prompt"},
            action_parameter_definitions={
                "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True)
            },
            builtin_placeholder_values={},
            model="nai-diffusion-4-5-full",
            i2i_enabled=True,
            i2i_image=_i2i_png_b64(832, 1216),
            i2i_strength=0.6,
            i2i_noise=0.0,
        )
        payload = json.loads(result)
        assert payload["size"] == [832, 1216]   # i2i 覆盖了映射里的 [1024,1024]
        assert payload["i2i"]["strength"] == 0.6
        assert payload["prompt"] == "1girl"

    @pytest.mark.asyncio
    async def test_i2i_disabled_ignores_image(self):
        raw = [{"field": "prompt", "value_type": "string", "value": "{prompt}"}]
        bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        result = await NaiChatInputValueBuilder.build_message_content_json(
            parameter_bindings=bindings,
            template_context={"prompt": "1girl"},
            action_inputs={"prompt": "1girl"},
            action_parameter_names={"prompt"},
            required_action_parameters={"prompt"},
            action_parameter_definitions={
                "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True)
            },
            builtin_placeholder_values={},
            i2i_enabled=False,
            i2i_image=_i2i_png_b64(832, 1216),
        )
        payload = json.loads(result)
        assert "i2i" not in payload


class TestControlnetChannel:
    """builder 的 vibe(controlnet) 通道（NewAPI §20.3）：多图组装 + 越界 clamp + 不校验尺寸 + 无图拒绝。"""

    def test_valid_single_image(self):
        payload = {"prompt": "1girl", "size": [832, 1216]}
        NaiChatInputValueBuilder._apply_controlnet_channel(
            payload,
            images_data=[{"image": _i2i_png_b64(832, 1216), "info_extracted": 0.5, "strength": 0.4}],
            overall_strength=0.9,
        )
        assert payload["size"] == [832, 1216]          # 不覆盖 size（与 i2i 的关键差异）
        assert payload["prompt"] == "1girl"             # 不碰 prompt
        assert len(payload["controlnet"]["images"]) == 1
        img0 = payload["controlnet"]["images"][0]
        assert img0["image"]
        assert img0["info_extracted"] == 0.5
        assert img0["strength"] == 0.4
        assert payload["controlnet"]["strength"] == 0.9

    def test_multi_images(self):
        # 多图：组成 controlnet.images 多项，各自参数独立
        payload = {}
        NaiChatInputValueBuilder._apply_controlnet_channel(
            payload,
            images_data=[
                {"image": _i2i_png_b64(832, 1216), "info_extracted": 0.7, "strength": 0.6},
                {"image": _i2i_png_b64(800, 600), "info_extracted": 0.5, "strength": 0.4},
            ],
            overall_strength=1.0,
        )
        imgs = payload["controlnet"]["images"]
        assert len(imgs) == 2
        assert imgs[0]["info_extracted"] == 0.7 and imgs[1]["info_extracted"] == 0.5

    def test_truncate_to_4(self):
        payload = {}
        NaiChatInputValueBuilder._apply_controlnet_channel(
            payload,
            images_data=[{"image": _i2i_png_b64(832, 1216)} for _ in range(6)],
            overall_strength=1.0,
        )
        assert len(payload["controlnet"]["images"]) == 4  # 超 4 截断

    def test_per_image_default_falls_back_to_channel_default(self):
        # 单项缺 info_extracted/strength → 回落入参默认 → 再回落常量
        payload = {}
        NaiChatInputValueBuilder._apply_controlnet_channel(
            payload,
            images_data=[{"image": _i2i_png_b64(832, 1216)}],
            info_extracted=0.5, reference_strength=0.45, overall_strength=0.8,
        )
        img0 = payload["controlnet"]["images"][0]
        assert img0["info_extracted"] == 0.5
        assert img0["strength"] == 0.45

    def test_empty_images_raises(self):
        with pytest.raises(ValueError, match="参考图"):
            NaiChatInputValueBuilder._apply_controlnet_channel({}, images_data=[], overall_strength=1.0)
        with pytest.raises(ValueError, match="参考图"):
            NaiChatInputValueBuilder._apply_controlnet_channel({}, images_data=[{"image": ""}], overall_strength=1.0)

    def test_non_standard_size_accepted(self):
        # §20.3 不限边长：非标准尺寸图也不报错（服务端 resize），且不覆盖外层 size
        payload = {}
        NaiChatInputValueBuilder._apply_controlnet_channel(
            payload, images_data=[{"image": _i2i_png_b64(800, 600)}], overall_strength=1.0
        )
        assert payload["controlnet"]["images"][0]["image"]
        assert "size" not in payload

    def test_clamped(self):
        payload = {}
        NaiChatInputValueBuilder._apply_controlnet_channel(
            payload,
            images_data=[{"image": _i2i_png_b64(832, 1216), "info_extracted": 5.0, "strength": -1.0}],
            overall_strength=9.0,
        )
        img0 = payload["controlnet"]["images"][0]
        assert img0["info_extracted"] == 1.0            # 上界
        assert img0["strength"] == 0.01                 # 下界
        assert payload["controlnet"]["strength"] == 1.0 # 上界

    def test_defaults(self):
        payload = {}
        NaiChatInputValueBuilder._apply_controlnet_channel(
            payload, images_data=[{"image": _i2i_png_b64(832, 1216)}], overall_strength=None
        )
        img0 = payload["controlnet"]["images"][0]
        assert img0["info_extracted"] == 0.7
        assert img0["strength"] == 0.6
        assert payload["controlnet"]["strength"] == 1.0

    @pytest.mark.asyncio
    async def test_end_to_end_vibe_enabled(self):
        raw = [
            {"field": "prompt", "value_type": "string", "value": "{prompt}"},
            {"field": "size", "value_type": "json", "value": "[832, 1216]"},
        ]
        bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        result = await NaiChatInputValueBuilder.build_message_content_json(
            parameter_bindings=bindings,
            template_context={"prompt": "1girl"},
            action_inputs={"prompt": "1girl"},
            action_parameter_names={"prompt"},
            required_action_parameters={"prompt"},
            action_parameter_definitions={
                "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True)
            },
            builtin_placeholder_values={},
            vibe_enabled=True,
            vibe_image=_i2i_png_b64(832, 1216),
            vibe_info_extracted=0.6,
        )
        payload = json.loads(result)
        assert payload["size"] == [832, 1216]           # vibe 不覆盖 size
        assert "i2i" not in payload
        assert payload["controlnet"]["images"][0]["info_extracted"] == 0.6
        assert payload["prompt"] == "1girl"


class TestCharacterReferenceChannel:
    """builder 的角色参考通道（NewAPI §20.4）：V4.5 门槛 + type 校验 + clamp + 无图/非 V4.5 拒绝。"""

    def test_valid_v45_adds_charref(self):
        payload = {"prompt": "1girl", "size": [832, 1216]}
        NaiChatInputValueBuilder._apply_character_reference_channel(
            payload, image=_i2i_png_b64(832, 1216), model=_NAI4_MODEL,
            ref_type="character", fidelity=0.8, strength=0.9,
        )
        assert payload["size"] == [832, 1216]
        assert payload["prompt"] == "1girl"
        assert len(payload["character_references"]) == 1
        ref0 = payload["character_references"][0]
        assert ref0["image"]
        assert ref0["type"] == "character"
        assert ref0["fidelity"] == 0.8
        assert ref0["strength"] == 0.9

    def test_empty_image_raises(self):
        with pytest.raises(ValueError, match="参考图"):
            NaiChatInputValueBuilder._apply_character_reference_channel(
                {}, image="", model=_NAI4_MODEL, ref_type="character", fidelity=1.0, strength=1.0
            )

    def test_non_v45_model_raises(self):
        # 非 V4.5 系列（含 4-full/4-curated）不支持角色参考 → 友好拒绝，不静默降级
        with pytest.raises(ValueError, match="V4.5"):
            NaiChatInputValueBuilder._apply_character_reference_channel(
                {}, image=_i2i_png_b64(832, 1216), model="nai-diffusion-4-full",
                ref_type="character", fidelity=1.0, strength=1.0,
            )

    def test_type_default_when_missing(self):
        payload = {}
        NaiChatInputValueBuilder._apply_character_reference_channel(
            payload, image=_i2i_png_b64(832, 1216), model=_NAI4_MODEL,
            ref_type=None, fidelity=1.0, strength=1.0,
        )
        assert payload["character_references"][0]["type"] == "character&style"

    def test_invalid_type_falls_back(self):
        payload = {}
        NaiChatInputValueBuilder._apply_character_reference_channel(
            payload, image=_i2i_png_b64(832, 1216), model=_NAI4_MODEL,
            ref_type="garbage", fidelity=1.0, strength=1.0,
        )
        assert payload["character_references"][0]["type"] == "character&style"

    def test_clamped(self):
        payload = {}
        NaiChatInputValueBuilder._apply_character_reference_channel(
            payload, image=_i2i_png_b64(832, 1216), model=_NAI4_MODEL,
            ref_type="style", fidelity=5.0, strength=-1.0,
        )
        ref0 = payload["character_references"][0]
        assert ref0["fidelity"] == 1.0   # 上界
        assert ref0["strength"] == 0.0   # 下界（§20.4 0~1）

    @pytest.mark.asyncio
    async def test_end_to_end_charref_enabled(self):
        raw = [
            {"field": "prompt", "value_type": "string", "value": "{prompt}"},
            {"field": "size", "value_type": "json", "value": "[832, 1216]"},
        ]
        bindings = NaiChatInputValueBuilder.parse_parameter_bindings(raw)
        result = await NaiChatInputValueBuilder.build_message_content_json(
            parameter_bindings=bindings,
            template_context={"prompt": "1girl"},
            action_inputs={"prompt": "1girl"},
            action_parameter_names={"prompt"},
            required_action_parameters={"prompt"},
            action_parameter_definitions={
                "prompt": ActionParameterDefinition(name="prompt", description="提示词", required=True)
            },
            builtin_placeholder_values={},
            model=_NAI4_MODEL,
            charref_enabled=True,
            charref_image=_i2i_png_b64(832, 1216),
            charref_type="character",
        )
        payload = json.loads(result)
        assert payload["size"] == [832, 1216]
        assert payload["character_references"][0]["type"] == "character"
        assert payload["prompt"] == "1girl"


class TestModelSupportsCharacterReference:
    def test_v45_supported(self):
        for model in ("nai-diffusion-4-5-full", "nai-diffusion-4-5-curated", "NAI-Diffusion-4-5-Full"):
            assert NaiChatInputValueBuilder._model_supports_character_reference(model) is True

    def test_non_v45_not_supported(self):
        for model in ("nai-diffusion-4-full", "nai-diffusion-4-curated", "nai-diffusion-3", "", "gpt-image-1"):
            assert NaiChatInputValueBuilder._model_supports_character_reference(model) is False
