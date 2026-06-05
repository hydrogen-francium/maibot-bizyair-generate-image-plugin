# -*- coding: utf-8 -*-
"""出图核心 nai_draw_core 端到端自检：用**真实 config.toml** 跑 resolve_to_payload，
验证 Action 与命令共用的核心管线（解析预设 → 变量 → provider 载荷）产出正确。

为什么用 /nai0 直发路径做主测：它注入 nai_raw_tags 旁路 nai_director，于是整条链
**不碰 LLM、不碰 daily_llm(today_state)、不碰网络**，完全确定可复现——既证明了核心编排
正确，又顺带证明了"director 惰性旁路"在真实 config + 真实 builder 全链路上成立
（llm_value_factory 零调用）。director 路径本身已由 test_nai_wrapper_variables.py
（resolver 级）+ test_nai_config_integration.py::TestNaiBrainIsolation（闭包级）覆盖。

改 config 的 NAI 链（质量词/映射/nai_subject 条件）时本测试应同步更新。
"""

import json
import tomllib
from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from services.action_parameter_utils import build_action_parameters
from services.nai_draw_core import DrawPayload, inject_tag_candidates, resolve_to_payload

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"

# 与 config.toml 的 nai_quality / nai_negative 逐字一致（改 config 同步改这里）
NAI_QUALITY = "very aesthetic, masterpiece, best quality, amazing quality, very detailed, absurdres"
NAI_NEGATIVE = (
    "lowres, worst quality, low quality, bad anatomy, bad hands, missing fingers, "
    "extra digits, fewer digits, jpeg artifacts, signature, watermark, username, "
    "blurry, artistic error, scan, abstract"
)


@pytest.fixture(scope="module")
def config() -> dict:
    with open(CONFIG_PATH, "rb") as fp:
        return tomllib.load(fp)


@pytest.fixture(scope="module")
def get_config(config):
    """复刻框架 get_config：按点号路径在 config 字典上取值，缺失返回 default。"""
    def _get(key: str, default=None):
        node = config
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node
    return _get


@pytest.fixture(scope="module")
def action_parameters(config):
    return build_action_parameters(config["bizyair_generate_image_plugin"]["action_parameters"])


@pytest.fixture(scope="module")
def required_params(action_parameters) -> set[str]:
    return {name for name, d in action_parameters.items() if d.required}


async def _resolve_nai0(get_config, action_parameters, required_params, factory, *, nai_artist="", nai_size="auto"):
    """以 /nai0 直发方式调核心：注入 nai_raw_tags 旁路 director。"""
    return await resolve_to_payload(
        get_config=get_config,
        action_inputs={
            "image_intent": "随便画点啥",   # 必填项需在场，但 /nai0 路径下不会被 director 消费
            "aspect_ratio": "9:16",
            "resolution": "2K",
            "nai_raw_tags": "1girl, cat ears, smile",
        },
        active_preset="nai_default",
        action_parameters=action_parameters,
        required_action_parameters=required_params,
        chat_id="test_chat",
        image_base64_provider=None,
        nai_artist=nai_artist,
        nai_size=nai_size,
        nai_model="",
        nai_sfw_filter=False,
        llm_value_factory=factory,
        log_prefix="[test]",
    )


class TestResolveToPayloadNai0:
    @pytest.mark.asyncio
    async def test_nai0_end_to_end_no_llm(self, get_config, action_parameters, required_params):
        # /nai0 直发：真实 config 全链路 → content_json，且 director 一次都不跑
        factory = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
        payload = await _resolve_nai0(get_config, action_parameters, required_params, factory)

        assert isinstance(payload, DrawPayload)
        assert payload.provider == "nai_chat"
        # director 惰性旁路：真实 config + 真实 builder 全链路上 LLM 零调用
        assert factory.await_count == 0

        content = json.loads(payload.provider_payload["content_json"])
        # 主体直发：质量词 + 原始 tag（无画师段，因为 nai_artist 空）
        assert content["prompt"] == f"{NAI_QUALITY}, 1girl, cat ears, smile"
        assert content["negative_prompt"] == NAI_NEGATIVE
        # aspect_ratio=9:16 + nai_size=auto → 代号 v → [832, 1216]（value_type=json 解析成 list）
        assert content["size"] == [832, 1216]
        assert content["steps"] == 23
        assert content["n_samples"] == 1

    @pytest.mark.asyncio
    async def test_nai0_provider_payload_carries_preset_credentials(self, get_config, action_parameters, required_params):
        # 载荷应带上 nai_default 预设的连接信息（命令直接拿去发请求）
        payload = await _resolve_nai0(get_config, action_parameters, required_params, AsyncMock())
        pp = payload.provider_payload
        assert pp["model"] == "nai-diffusion-4-5-full"
        assert pp["base_url"].startswith("http")
        assert pp["api_key"]
        assert payload.timeout > 0

    @pytest.mark.asyncio
    async def test_nai0_artist_injected_between_quality_and_subject(self, get_config, action_parameters, required_params):
        # /nai art 选定画师串后：质量词 → 画师串 → 原始 tag，director 仍不跑
        factory = AsyncMock(return_value="X")
        payload = await _resolve_nai0(
            get_config, action_parameters, required_params, factory, nai_artist="artist:wlop, artist:as109",
        )
        content = json.loads(payload.provider_payload["content_json"])
        assert content["prompt"] == f"{NAI_QUALITY}, artist:wlop, artist:as109, 1girl, cat ears, smile"
        assert factory.await_count == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "nai_size, expected",
        [
            ("v", [832, 1216]),
            ("h", [1216, 832]),
            ("s", [1024, 1024]),
            ("auto", [832, 1216]),  # auto + 9:16 → v
        ],
    )
    async def test_nai0_size_override(self, get_config, action_parameters, required_params, nai_size, expected):
        payload = await _resolve_nai0(
            get_config, action_parameters, required_params, AsyncMock(), nai_size=nai_size,
        )
        content = json.loads(payload.provider_payload["content_json"])
        assert content["size"] == expected

    @pytest.mark.asyncio
    async def test_nai0_without_image_intent_still_works(self, get_config, action_parameters, required_params):
        # /nai0 命令只注入 nai_raw_tags，不提供必填的 image_intent。
        # 因 director 被旁路，{image_intent} 从不出现在任何已解析模板里 → raise_error 不触发。
        # 本测试锁定该前提：命令无需伪造 image_intent。
        factory = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
        payload = await resolve_to_payload(
            get_config=get_config,
            action_inputs={"nai_raw_tags": "1girl, smile"},
            active_preset="nai_default",
            action_parameters=action_parameters,
            required_action_parameters=required_params,
            chat_id="test_chat",
            llm_value_factory=factory,
            log_prefix="[test]",
        )
        content = json.loads(payload.provider_payload["content_json"])
        assert content["prompt"] == f"{NAI_QUALITY}, 1girl, smile"
        assert factory.await_count == 0
        # 未提供 aspect_ratio + nai_size=auto → 缺省竖图
        assert content["size"] == [832, 1216]


class TestResolveToPayloadMultiCharacter:
    """多人 characters[] 通道端到端（/nai0 直发多人串，旁路 director，零 LLM）。

    锁 step ③：核心把解析出的 model 透传进 builder，builder 据此启用 / 降级多角色通道。
    真实 config 的 nai_default 预设 model = nai-diffusion-4-5-full（支持多角色）。
    """

    _MULTI_TAGS = "2girls, indoor,\nchar1:girl, blue hair,\nchar2:girl, white hair,"

    @pytest.mark.asyncio
    async def test_nai0_multi_person_splits_characters(self, get_config, action_parameters, required_params):
        factory = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
        payload = await resolve_to_payload(
            get_config=get_config,
            action_inputs={
                "aspect_ratio": "9:16",
                "resolution": "2K",
                "nai_raw_tags": self._MULTI_TAGS,
            },
            active_preset="nai_default",
            action_parameters=action_parameters,
            required_action_parameters=required_params,
            chat_id="test_chat",
            nai_model="",  # 用预设自带 nai-diffusion-4-5-full
            llm_value_factory=factory,
            log_prefix="[test]",
        )
        content = json.loads(payload.provider_payload["content_json"])
        # 多角色生效：质量词落进 global 段（不污染角色段），角色段分离
        assert content["prompt"] == f"{NAI_QUALITY}, 2girls, indoor"
        assert content["characters"] == [
            {"prompt": "girl, blue hair"},
            {"prompt": "girl, white hair"},
        ]
        assert content["use_coords"] is False  # 文本路径无坐标 → 自动布局
        assert content["use_order"] is True
        assert factory.await_count == 0

    @pytest.mark.asyncio
    async def test_nai0_multi_person_downgrades_on_v3_model(self, get_config, action_parameters, required_params):
        # /nai set nai-diffusion-3：模型不支持多角色 → 降级单串，characters 不出现，flat 串保留
        payload = await resolve_to_payload(
            get_config=get_config,
            action_inputs={
                "aspect_ratio": "9:16",
                "resolution": "2K",
                "nai_raw_tags": self._MULTI_TAGS,
            },
            active_preset="nai_default",
            action_parameters=action_parameters,
            required_action_parameters=required_params,
            chat_id="test_chat",
            nai_model="nai-diffusion-3",  # 覆盖为不支持多角色的版本
            llm_value_factory=AsyncMock(),
            log_prefix="[test]",
        )
        pp = payload.provider_payload
        assert pp["model"] == "nai-diffusion-3"  # /nai set 覆盖透传生效
        content = json.loads(pp["content_json"])
        assert "characters" not in content
        assert "use_coords" not in content
        assert "char1:" in content["prompt"]


def _i2i_png_b64(w: int, h: int) -> str:
    """构造已知尺寸的最小 PNG 头 base64。"""
    import base64
    import struct
    data = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x00" * 8
    return base64.b64encode(data).decode("ascii")


class TestResolveToPayloadI2i:
    """i2i 预设端到端（真实 config nai_i2i 预设 + 注入图，/nai0 直发旁路 director，零 LLM）。

    锁 step ③+核心串联：core 在 i2i 预设强制收集 quoted_image_base64 → 传 builder →
    i2i 字段就位 + size 用原图真实尺寸覆盖映射值。
    """

    @pytest.mark.asyncio
    async def test_nai_i2i_assembles_i2i_and_overrides_size(self, get_config, action_parameters, required_params):
        # 图 1024x1024（方），而 aspect_ratio=9:16 算出的 nai_size 是 [832,1216]（竖）→ 验证被图覆盖
        img = _i2i_png_b64(1024, 1024)
        factory = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
        payload = await resolve_to_payload(
            get_config=get_config,
            action_inputs={"aspect_ratio": "9:16", "resolution": "2K", "nai_raw_tags": "1girl, cat ears"},
            active_preset="nai_i2i",
            action_parameters=action_parameters,
            required_action_parameters=required_params,
            chat_id="test_chat",
            image_base64_provider=lambda *a, **k: img,
            llm_value_factory=factory,
            log_prefix="[test]",
        )
        content = json.loads(payload.provider_payload["content_json"])
        assert content["size"] == [1024, 1024]            # 被原图尺寸覆盖
        assert content["i2i"]["image"]
        assert content["i2i"]["strength"] == 0.7          # 来自 nai_i2i 预设
        assert content["i2i"]["noise"] == 0.0
        assert content["prompt"].endswith("1girl, cat ears")  # /nai0 直发主体
        assert factory.await_count == 0                   # director 旁路

    @pytest.mark.asyncio
    async def test_nai_i2i_no_image_raises_friendly(self, get_config, action_parameters, required_params):
        with pytest.raises(ValueError, match="参考图"):
            await resolve_to_payload(
                get_config=get_config,
                action_inputs={"aspect_ratio": "9:16", "resolution": "2K", "nai_raw_tags": "1girl"},
                active_preset="nai_i2i",
                action_parameters=action_parameters,
                required_action_parameters=required_params,
                chat_id="test_chat",
                image_base64_provider=lambda *a, **k: None,  # 无图
                llm_value_factory=AsyncMock(),
                log_prefix="[test]",
            )

    @pytest.mark.asyncio
    async def test_nai_i2i_non_standard_size_raises_friendly(self, get_config, action_parameters, required_params):
        with pytest.raises(ValueError, match="标准尺寸"):
            await resolve_to_payload(
                get_config=get_config,
                action_inputs={"aspect_ratio": "9:16", "resolution": "2K", "nai_raw_tags": "1girl"},
                active_preset="nai_i2i",
                action_parameters=action_parameters,
                required_action_parameters=required_params,
                chat_id="test_chat",
                image_base64_provider=lambda *a, **k: _i2i_png_b64(800, 600),  # 非 64 整除/非标准
                llm_value_factory=AsyncMock(),
                log_prefix="[test]",
            )


class TestResolveToPayloadVibe:
    """vibe(controlnet) 预设端到端（真实 config nai_vibe + 注图，/nai0 直发旁路 director，零 LLM）。

    锁核心串联：core 在 vibe 预设强制收集 quoted_image_base64 → 传 builder → controlnet 字段就位，
    且 **不覆盖 size**（与 i2i 的关键差异，§20.3 不限边长）。
    """

    @pytest.mark.asyncio
    async def test_nai_vibe_assembles_controlnet(self, get_config, action_parameters, required_params):
        img = _i2i_png_b64(832, 1216)
        factory = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
        payload = await resolve_to_payload(
            get_config=get_config,
            action_inputs={"aspect_ratio": "9:16", "resolution": "2K", "nai_raw_tags": "1girl, cat ears"},
            active_preset="nai_vibe",
            action_parameters=action_parameters,
            required_action_parameters=required_params,
            chat_id="test_chat",
            image_base64_provider=lambda *a, **k: img,
            llm_value_factory=factory,
            log_prefix="[test]",
        )
        content = json.loads(payload.provider_payload["content_json"])
        assert content["size"] == [832, 1216]              # vibe 不覆盖 size（aspect 9:16 → v）
        assert "i2i" not in content
        cn = content["controlnet"]
        assert len(cn["images"]) == 1
        assert cn["images"][0]["image"]
        assert cn["images"][0]["info_extracted"] == 0.7    # nai_vibe 预设默认
        assert cn["images"][0]["strength"] == 0.6
        assert cn["strength"] == 1.0
        assert content["prompt"].endswith("1girl, cat ears")
        assert factory.await_count == 0

    @pytest.mark.asyncio
    async def test_nai_vibe_no_image_raises_friendly(self, get_config, action_parameters, required_params):
        with pytest.raises(ValueError, match="参考图"):
            await resolve_to_payload(
                get_config=get_config,
                action_inputs={"aspect_ratio": "9:16", "resolution": "2K", "nai_raw_tags": "1girl"},
                active_preset="nai_vibe",
                action_parameters=action_parameters,
                required_action_parameters=required_params,
                chat_id="test_chat",
                image_base64_provider=lambda *a, **k: None,
                llm_value_factory=AsyncMock(),
                log_prefix="[test]",
            )


class TestResolveToPayloadCharref:
    """角色参考预设端到端（真实 config nai_charref + 注图，/nai0 直发旁路 director，零 LLM）。

    锁核心串联 + 模型门槛：默认预设 model=nai-diffusion-4-5-full 装配成功；/nai set 覆盖为非 V4.5
    时友好拒绝（不静默降级）。
    """

    @pytest.mark.asyncio
    async def test_nai_charref_assembles_character_references(self, get_config, action_parameters, required_params):
        img = _i2i_png_b64(832, 1216)
        factory = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
        payload = await resolve_to_payload(
            get_config=get_config,
            action_inputs={"aspect_ratio": "9:16", "resolution": "2K", "nai_raw_tags": "1girl, cat ears"},
            active_preset="nai_charref",
            action_parameters=action_parameters,
            required_action_parameters=required_params,
            chat_id="test_chat",
            image_base64_provider=lambda *a, **k: img,
            nai_model="",   # 用预设自带 nai-diffusion-4-5-full（支持角色参考）
            llm_value_factory=factory,
            log_prefix="[test]",
        )
        content = json.loads(payload.provider_payload["content_json"])
        assert content["size"] == [832, 1216]              # charref 不覆盖 size
        refs = content["character_references"]
        assert len(refs) == 1
        assert refs[0]["image"]
        assert refs[0]["type"] == "character&style"        # nai_charref 预设默认
        assert refs[0]["fidelity"] == 1.0
        assert refs[0]["strength"] == 1.0
        assert content["prompt"].endswith("1girl, cat ears")
        assert factory.await_count == 0

    @pytest.mark.asyncio
    async def test_nai_charref_no_image_raises_friendly(self, get_config, action_parameters, required_params):
        with pytest.raises(ValueError, match="参考图"):
            await resolve_to_payload(
                get_config=get_config,
                action_inputs={"aspect_ratio": "9:16", "resolution": "2K", "nai_raw_tags": "1girl"},
                active_preset="nai_charref",
                action_parameters=action_parameters,
                required_action_parameters=required_params,
                chat_id="test_chat",
                image_base64_provider=lambda *a, **k: None,
                llm_value_factory=AsyncMock(),
                log_prefix="[test]",
            )

    @pytest.mark.asyncio
    async def test_nai_charref_non_v45_model_raises(self, get_config, action_parameters, required_params):
        # /nai set nai-diffusion-4-full 覆盖为非 V4.5 → 角色参考友好拒绝
        with pytest.raises(ValueError, match="V4.5"):
            await resolve_to_payload(
                get_config=get_config,
                action_inputs={"aspect_ratio": "9:16", "resolution": "2K", "nai_raw_tags": "1girl"},
                active_preset="nai_charref",
                action_parameters=action_parameters,
                required_action_parameters=required_params,
                chat_id="test_chat",
                image_base64_provider=lambda *a, **k: _i2i_png_b64(832, 1216),
                nai_model="nai-diffusion-4-full",
                llm_value_factory=AsyncMock(),
                log_prefix="[test]",
            )


class TestInjectTagCandidates:
    """inject_tag_candidates 伪注入单元测试：mock resolve_tag_candidates，零网络。

    必定 set tag_candidates（模板引用，缺 key 会判未定义变量）；/nai0(nai_raw_tags 非空)、
    未启用、无 image_intent 均跳过检索；异常失败安全降级空串。
    """

    @staticmethod
    def _get_config(enabled):
        cfg = {"enabled": enabled}
        return lambda k, d=None: (cfg if k == "tag_retriever" else d)

    @pytest.mark.asyncio
    async def test_injects_when_enabled(self, monkeypatch):
        mock_resolve = AsyncMock(return_value="<tag_candidates>X</tag_candidates>")
        monkeypatch.setattr(
            "services.nai_tag_candidate_resolver.resolve_tag_candidates", mock_resolve
        )
        action_inputs = {"image_intent": "画猫娘"}
        await inject_tag_candidates(self._get_config(True), action_inputs, log_prefix="[t]")
        assert action_inputs["tag_candidates"] == "<tag_candidates>X</tag_candidates>"
        mock_resolve.assert_awaited_once()
        # query 取自 image_intent（位置实参第二个）
        assert mock_resolve.call_args.args[1] == "画猫娘"

    @pytest.mark.asyncio
    async def test_skips_when_nai_raw_tags_present(self, monkeypatch):
        # /nai0 直发：nai_raw_tags 非空 → director 旁路 → 检索同样旁路（不触网）
        mock_resolve = AsyncMock(return_value="SHOULD_NOT_BE_USED")
        monkeypatch.setattr(
            "services.nai_tag_candidate_resolver.resolve_tag_candidates", mock_resolve
        )
        action_inputs = {"image_intent": "画猫娘", "nai_raw_tags": "1girl, solo"}
        await inject_tag_candidates(self._get_config(True), action_inputs)
        assert action_inputs["tag_candidates"] == ""
        mock_resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, monkeypatch):
        mock_resolve = AsyncMock(return_value="X")
        monkeypatch.setattr(
            "services.nai_tag_candidate_resolver.resolve_tag_candidates", mock_resolve
        )
        action_inputs = {"image_intent": "画猫娘"}
        await inject_tag_candidates(self._get_config(False), action_inputs)
        assert action_inputs["tag_candidates"] == ""
        mock_resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_no_image_intent(self, monkeypatch):
        mock_resolve = AsyncMock(return_value="X")
        monkeypatch.setattr(
            "services.nai_tag_candidate_resolver.resolve_tag_candidates", mock_resolve
        )
        action_inputs = {"image_intent": "   "}
        await inject_tag_candidates(self._get_config(True), action_inputs)
        assert action_inputs["tag_candidates"] == ""
        mock_resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_always_sets_key_when_intent_missing(self, monkeypatch):
        # 模板始终引用 {tag_candidates}：即便完全无 image_intent 也必须有该 key
        mock_resolve = AsyncMock(return_value="X")
        monkeypatch.setattr(
            "services.nai_tag_candidate_resolver.resolve_tag_candidates", mock_resolve
        )
        action_inputs = {}
        await inject_tag_candidates(self._get_config(True), action_inputs)
        assert action_inputs["tag_candidates"] == ""
        mock_resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failsafe_on_exception(self, monkeypatch):
        # 失败安全：resolve_tag_candidates 抛异常 → 不向上抛，降级空串
        mock_resolve = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(
            "services.nai_tag_candidate_resolver.resolve_tag_candidates", mock_resolve
        )
        action_inputs = {"image_intent": "画猫娘"}
        await inject_tag_candidates(self._get_config(True), action_inputs)
        assert action_inputs["tag_candidates"] == ""
