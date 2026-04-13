import pytest
from unittest.mock import AsyncMock

from services.variable_dependency_resolver import VariableDependencyResolver

from .fixtures import BUILTIN_NAMES, make_definition, make_resolver, mock_builtin_provider


class TestResolveAllFallbackValueRefs:
    @pytest.mark.asyncio
    async def test_fallback_refs_custom_variable(self):
        defs = {
            "default_text": make_definition("default_text", values=["neutral smile"]),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="use_default",
                fallback_value="{default_text}",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"emotion": "sad"},
            definitions=defs,
            action_parameter_names={"emotion"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["emotion_prompt"] == "neutral smile"

    @pytest.mark.asyncio
    async def test_fallback_refs_builtin_variable(self):
        defs = {
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="use_default",
                fallback_value="seed={random_seed}",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"emotion": "sad"},
            definitions=defs,
            action_parameter_names={"emotion"},
        ).resolve_all(
            builtin_placeholder_values={"{random_seed}": 42},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["emotion_prompt"] == "seed=42"

    @pytest.mark.asyncio
    async def test_fallback_refs_action_input(self):
        defs = {
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="use_default",
                fallback_value="{prompt}",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"emotion": "sad", "prompt": "a cat"},
            definitions=defs,
            action_parameter_names={"emotion", "prompt"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["emotion_prompt"] == "a cat"

    @pytest.mark.asyncio
    async def test_fallback_plain_text(self):
        defs = {
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="use_default",
                fallback_value="plain text",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"emotion": "sad"},
            definitions=defs,
            action_parameter_names={"emotion"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["emotion_prompt"] == "plain text"

    @pytest.mark.asyncio
    async def test_fallback_mixed_refs(self):
        defs = {
            "suffix": make_definition("suffix", values=["!!!"]),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="use_default",
                fallback_value="seed={random_seed}, end={suffix}",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"emotion": "sad"},
            definitions=defs,
            action_parameter_names={"emotion"},
        ).resolve_all(
            builtin_placeholder_values={"{random_seed}": 99},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["emotion_prompt"] == "seed=99, end=!!!"

    @pytest.mark.asyncio
    async def test_fallback_not_consumed_wont_trigger_llm_dependency(self):
        llm_factory = AsyncMock(return_value="generated")
        defs = {
            "llm_piece": make_definition("llm_piece", mode="llm", values=["generate me"]),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion",
                entries={"joy": "big smile"},
                missing_behavior="use_default",
                fallback_value="{llm_piece}",
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"emotion": "joy"},
            definitions=defs,
            action_parameter_names={"emotion"},
            required_keys={"emotion_prompt"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=llm_factory,
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["emotion_prompt"] == "big smile"
        llm_factory.assert_not_awaited()


class TestResolveAllConditionValueRefs:
    @pytest.mark.asyncio
    async def test_condition_value_refs_custom_var_true(self):
        defs = {
            "threshold": make_definition("threshold", values=["5"]),
            "style_hint": make_definition(
                "style_hint",
                values=["long mode"],
                condition_type="length_gt",
                condition_source="prompt",
                condition_value="{threshold}",
                values_else=["short mode"],
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"prompt": "123456"},
            definitions=defs,
            action_parameter_names={"prompt"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["style_hint"] == "long mode"

    @pytest.mark.asyncio
    async def test_condition_value_refs_custom_var_false(self):
        defs = {
            "threshold": make_definition("threshold", values=["10"]),
            "style_hint": make_definition(
                "style_hint",
                values=["long mode"],
                condition_type="length_gt",
                condition_source="prompt",
                condition_value="{threshold}",
                values_else=["short mode"],
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"prompt": "123456"},
            definitions=defs,
            action_parameter_names={"prompt"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["style_hint"] == "short mode"

    @pytest.mark.asyncio
    async def test_condition_value_refs_builtin(self):
        defs = {
            "style_hint": make_definition(
                "style_hint",
                values=["has seed"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="{random_seed}",
                values_else=["no seed"],
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"prompt": "seed is 42 here"},
            definitions=defs,
            action_parameter_names={"prompt"},
        ).resolve_all(
            builtin_placeholder_values={"{random_seed}": "42"},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["style_hint"] == "has seed"

    @pytest.mark.asyncio
    async def test_condition_value_refs_action_input(self):
        defs = {
            "style_hint": make_definition(
                "style_hint",
                values=["contains keyword"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="{keyword}",
                values_else=["no keyword"],
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"prompt": "draw a cat", "keyword": "cat"},
            definitions=defs,
            action_parameter_names={"prompt", "keyword"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["style_hint"] == "contains keyword"

    @pytest.mark.asyncio
    async def test_condition_value_plain_text(self):
        defs = {
            "style_hint": make_definition(
                "style_hint",
                values=["long mode"],
                condition_type="length_gt",
                condition_source="prompt",
                condition_value="5",
                values_else=["short mode"],
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"prompt": "123456"},
            definitions=defs,
            action_parameter_names={"prompt"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["style_hint"] == "long mode"


class TestResolveAllDynamicBranchSoftDeps:
    @pytest.mark.asyncio
    async def test_values_branch_not_selected_wont_resolve_else_chain(self):
        llm_factory = AsyncMock(return_value="generated else")
        defs = {
            "branch_llm": make_definition("branch_llm", mode="llm", values=["generate else"]),
            "style_hint": make_definition(
                "style_hint",
                values=["main branch"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="cat",
                values_else=["{branch_llm}"],
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"prompt": "cat on sofa"},
            definitions=defs,
            action_parameter_names={"prompt"},
            required_keys={"style_hint"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=llm_factory,
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["style_hint"] == "main branch"
        llm_factory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_values_else_branch_selected_wont_resolve_main_chain(self):
        llm_factory = AsyncMock(return_value="generated main")
        defs = {
            "branch_llm": make_definition("branch_llm", mode="llm", values=["generate main"]),
            "style_hint": make_definition(
                "style_hint",
                values=["{branch_llm}"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="cat",
                values_else=["else branch"],
            ),
        }
        _, cv = await make_resolver(
            action_inputs={"prompt": "dog on sofa"},
            definitions=defs,
            action_parameter_names={"prompt"},
            required_keys={"style_hint"},
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=llm_factory,
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["style_hint"] == "else branch"
        llm_factory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_soft_dependency_chain_kept_in_closure_and_resolved_on_demand(self):
        defs = {
            "base": make_definition("base", values=["core"]),
            "branch": make_definition("branch", values=["value:{base}"]),
            "selector": make_definition(
                "selector",
                values=["main"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="dog",
                values_else=["{branch}"],
            ),
        }
        required = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"selector"},
            action_inputs={"prompt": "cat"},
            custom_variable_definitions=defs,
            action_parameter_names={"prompt"},
            builtin_names=BUILTIN_NAMES,
        )
        assert required == {"selector", "branch", "base"}

        _, cv = await make_resolver(
            action_inputs={"prompt": "cat"},
            definitions=defs,
            action_parameter_names={"prompt"},
            required_keys=required,
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=AsyncMock(),
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert cv["selector"] == "value:core"

    @pytest.mark.asyncio
    async def test_non_selfie_prompt_wont_trigger_selfie_branch(self):
        llm_factory = AsyncMock(side_effect=["translated prompt"])
        defs = {
            "final_prompt": make_definition(
                "final_prompt",
                values=["{selfie_prompt}"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="自拍",
                values_else=["{english_prompt}"],
            ),
            "selfie_prompt": make_definition(
                "selfie_prompt",
                values=["{selfie_composition_prompt}, {emotion_prompt}"],
            ),
            "selfie_composition_prompt": make_definition(
                "selfie_composition_prompt",
                mode="dict",
                source="selfie_composition",
                entries={"phone_in_hand": "selfie angle"},
                missing_behavior="raise_error",
            ),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion_composition",
                entries={"joy": "smile"},
                missing_behavior="raise_error",
            ),
            "english_prompt": make_definition(
                "english_prompt",
                mode="llm",
                values=["style={style}; prompt={prompt}"],
            ),
            "手绘风": make_definition("手绘风", values=["painted style"]),
        }
        required = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"final_prompt"},
            action_inputs={
                "prompt": "夜晚天空风景",
                "style": "{手绘风}",
            },
            custom_variable_definitions=defs,
            action_parameter_names={"prompt", "style", "selfie_composition", "emotion_composition"},
            builtin_names=BUILTIN_NAMES,
        )
        assert {"selfie_prompt", "selfie_composition_prompt", "emotion_prompt", "english_prompt", "手绘风"} <= required

        resolved_inputs, cv = await make_resolver(
            action_inputs={
                "prompt": "夜晚天空风景",
                "style": "{手绘风}",
            },
            definitions=defs,
            action_parameter_names={"prompt", "style", "selfie_composition", "emotion_composition"},
            required_keys=required,
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=llm_factory,
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_inputs["style"] == "painted style"
        assert cv["final_prompt"] == "translated prompt"
        llm_factory.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_real_config_non_selfie_flow_wont_resolve_selfie_variables(self):
        llm_factory = AsyncMock(side_effect=["translated landscape prompt", " "])
        defs = {
            "final_prompt": make_definition(
                "final_prompt",
                values=["{selfie_prompt}"],
                condition_type="contains",
                condition_source="prompt",
                condition_value="{selfie_prompt}",
                use_raw_condition_value=True,
                values_else=["{english_prompt}"],
            ),
            "selfie_prompt": make_definition(
                "selfie_prompt",
                values=["{selfie_composition_prompt}, {character_base_prompt}, {emotion_prompt}, {outfit_prompt}, {selfie_background_prompt}"],
            ),
            "手绘风": make_definition(
                "手绘风",
                values=["anime screencap aesthetic, painterly cel shade, rich color harmony, expressive brushstroke lines, depth-filled eye rendering, modern anime production art, polished commercial illustration"],
            ),
            "english_prompt": make_definition(
                "english_prompt",
                mode="llm",
                values=["这是一个用于画图的提示词。请将其变成更适合画图ai的英文标签形式。你的输出会被直接输入到绘图ai中，因此请直接输出内容，不要添加多余的解释。以下是图片要求的画风: {style}\n\n以下是生图的描述词: {prompt}"],
            ),
            "emotion_prompt": make_definition(
                "emotion_prompt",
                mode="dict",
                source="emotion_composition",
                entries={"joy": "big cheerful smile"},
                missing_behavior="raise_error",
            ),
            "selfie_composition_prompt": make_definition(
                "selfie_composition_prompt",
                mode="dict",
                source="selfie_composition",
                entries={"phone_in_hand": "selfie photo"},
                missing_behavior="raise_error",
            ),
            "outfit_prompt": make_definition(
                "outfit_prompt",
                mode="llm",
                values=["以下是提示词: {outfit}"],
            ),
            "selfie_background_prompt": make_definition(
                "selfie_background_prompt",
                mode="llm",
                values=["背景: {selfie_composition}, 风格: {style}"],
            ),
            "character_base_prompt": make_definition(
                "character_base_prompt",
                values=["1girl, solo"],
            ),
            "width": make_definition(
                "width",
                mode="dict",
                source="aspect_ratio",
                entries={"16:9": "1820"},
                missing_behavior="raise_error",
            ),
            "height": make_definition(
                "height",
                mode="dict",
                source="aspect_ratio",
                entries={"16:9": "1024"},
                missing_behavior="raise_error",
            ),
            "negative_prompt": make_definition(
                "negative_prompt",
                mode="llm",
                values=["以下是画风正面提示词: \n<style>{style}</style>\n 以下是内容正面提示词: \n<prompt>{english_prompt}</prompt>"],
            ),
        }
        required = VariableDependencyResolver.compute_required_variable_keys(
            direct_keys={"final_prompt", "height", "negative_prompt", "width"},
            action_inputs={
                "prompt": "宁静的夜晚天空风景，深蓝色渐变夜空，繁星密布，银河清晰可见，一轮明月与薄云，远处山脉与森林剪影，前景有微风拂过的草地，整体氛围清澈安静、治愈",
                "style": "{手绘风}",
                "aspect_ratio": "16:9",
                "resolution": "2K",
            },
            custom_variable_definitions=defs,
            action_parameter_names={
                "prompt",
                "style",
                "aspect_ratio",
                "resolution",
                "selfie_composition",
                "emotion_composition",
                "outfit",
            },
            builtin_names=BUILTIN_NAMES,
        )
        assert {"selfie_prompt", "selfie_composition_prompt", "emotion_prompt", "outfit_prompt", "selfie_background_prompt", "character_base_prompt", "english_prompt", "手绘风"} <= required

        resolved_inputs, cv = await make_resolver(
            action_inputs={
                "prompt": "宁静的夜晚天空风景，深蓝色渐变夜空，繁星密布，银河清晰可见，一轮明月与薄云，远处山脉与森林剪影，前景有微风拂过的草地，整体氛围清澈安静、治愈",
                "style": "{手绘风}",
                "aspect_ratio": "16:9",
                "resolution": "2K",
            },
            definitions=defs,
            action_parameter_names={
                "prompt",
                "style",
                "aspect_ratio",
                "resolution",
                "selfie_composition",
                "emotion_composition",
                "outfit",
            },
            required_keys=required,
        ).resolve_all(
            builtin_placeholder_values={},
            llm_value_factory=llm_factory,
            builtin_variable_provider=mock_builtin_provider(),
        )
        assert resolved_inputs["style"].startswith("anime screencap aesthetic")
        assert cv["height"] == "1024"
        assert cv["width"] == "1820"
        assert cv["final_prompt"] == "translated landscape prompt"
        assert cv["negative_prompt"] == " "
        assert llm_factory.await_count == 2
