import pytest
from unittest.mock import AsyncMock

from .fixtures import make_definition, make_resolver, mock_builtin_provider


class TestExtractMode:
    @pytest.mark.asyncio
    async def test_extract_basic_capture_group_one(self):
        defs = {
            "director": make_definition(
                "director",
                values=["SCENE_TYPE: selfie\nEMOTION: joy"],
            ),
            "scene_type": make_definition(
                "scene_type",
                mode="extract",
                source="director",
                pattern=r"SCENE_TYPE[:：]\s*(\S+)",
                group=1,
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["scene_type"] == "selfie"

    @pytest.mark.asyncio
    async def test_extract_chinese_colon_works(self):
        defs = {
            "director": make_definition("director", values=["SCENE_TYPE：normal"]),
            "scene_type": make_definition(
                "scene_type",
                mode="extract",
                source="director",
                pattern=r"SCENE_TYPE[:：]\s*(\S+)",
                group=1,
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["scene_type"] == "normal"

    @pytest.mark.asyncio
    async def test_extract_group_zero_returns_full_match(self):
        defs = {
            "director": make_definition("director", values=["EMOTION: joy"]),
            "v": make_definition(
                "v",
                mode="extract",
                source="director",
                pattern=r"EMOTION[:：]\s*\S+",
                group=0,
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["v"] == "EMOTION: joy"

    @pytest.mark.asyncio
    async def test_extract_strips_whitespace(self):
        defs = {
            "director": make_definition("director", values=["EMOTION:    joy   \n"]),
            "v": make_definition(
                "v",
                mode="extract",
                source="director",
                pattern=r"EMOTION[:：]\s*([^\n]+)",
                group=1,
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["v"] == "joy"

    @pytest.mark.asyncio
    async def test_extract_no_match_keep_placeholder(self):
        defs = {
            "director": make_definition("director", values=["only EMOTION line"]),
            "scene_type": make_definition(
                "scene_type",
                mode="extract",
                source="director",
                pattern=r"SCENE_TYPE[:：]\s*(\S+)",
                missing_behavior="keep_placeholder",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["scene_type"] == "{scene_type}"

    @pytest.mark.asyncio
    async def test_extract_no_match_use_default(self):
        defs = {
            "director": make_definition("director", values=["only EMOTION line"]),
            "scene_type": make_definition(
                "scene_type",
                mode="extract",
                source="director",
                pattern=r"SCENE_TYPE[:：]\s*(\S+)",
                missing_behavior="use_default",
                fallback_value="normal",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["scene_type"] == "normal"

    @pytest.mark.asyncio
    async def test_extract_no_match_raise_error(self):
        defs = {
            "director": make_definition("director", values=["only EMOTION line"]),
            "scene_type": make_definition(
                "scene_type",
                mode="extract",
                source="director",
                pattern=r"SCENE_TYPE[:：]\s*(\S+)",
                missing_behavior="raise_error",
            ),
        }
        with pytest.raises(ValueError, match="正则未匹配"):
            await make_resolver(
                action_inputs={},
                definitions=defs,
                action_parameter_names=set(),
            ).resolve_all(
                builtin_placeholder_values={},
                llm_value_factory=AsyncMock(),
                builtin_variable_provider=mock_builtin_provider(),
            )

    @pytest.mark.asyncio
    async def test_extract_fallback_can_reference_other_custom_variable(self):
        """fallback_value 在未命中时应该解析其引用的自定义变量"""
        defs = {
            "director": make_definition("director", values=["nothing"]),
            "default_intent": make_definition("default_intent", values=["a quiet selfie"]),
            "free_prompt": make_definition(
                "free_prompt",
                mode="extract",
                source="director",
                pattern=r"FREE_PROMPT[:：]\s*(.+)",
                missing_behavior="use_default",
                fallback_value="{default_intent}",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["free_prompt"] == "a quiet selfie"

    @pytest.mark.asyncio
    async def test_extract_fallback_can_reference_action_input(self):
        """fallback_value 引用 action_input 也应正常解析"""
        defs = {
            "director": make_definition("director", values=["nothing"]),
            "free_prompt": make_definition(
                "free_prompt",
                mode="extract",
                source="director",
                pattern=r"FREE_PROMPT[:：]\s*(.+)",
                missing_behavior="use_default",
                fallback_value="{image_intent}",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"image_intent": "fallback intent"},
            definitions=defs,
            action_parameter_names={"image_intent"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["free_prompt"] == "fallback intent"

    @pytest.mark.asyncio
    async def test_extract_picks_first_match_when_multiple(self):
        """re.search 默认匹配第一处"""
        defs = {
            "director": make_definition(
                "director",
                values=["EMOTION: joy\nEMOTION: shy"],
            ),
            "v": make_definition(
                "v",
                mode="extract",
                source="director",
                pattern=r"EMOTION[:：]\s*(\S+)",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["v"] == "joy"

    @pytest.mark.asyncio
    async def test_extract_chains_into_dict(self):
        """e2e: director -> extract -> dict 翻译，检验整条链"""
        defs = {
            "director": make_definition(
                "director",
                values=["EMOTION: joy"],
            ),
            "emotion_key": make_definition(
                "emotion_key",
                mode="extract",
                source="director",
                pattern=r"EMOTION[:：]\s*(\S+)",
            ),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion_key",
                entries={"joy": "big smile", "shy": "blushing"},
            ),
        }
        _, cv = await make_resolver(
            action_inputs={},
            definitions=defs,
            action_parameter_names=set(),
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["emotion_key"] == "joy"
        assert cv["emotion_prompt"] == "big smile"
