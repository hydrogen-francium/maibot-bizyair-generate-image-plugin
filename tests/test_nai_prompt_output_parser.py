# -*- coding: utf-8 -*-
"""NAI 多人输出解析（services/nai_prompt_output_parser.py）单测。

移植 nai_draw test_prompt_output_parser.py 的多人相关用例（JSON v3 + char1:/char2: 文本双路径）。
"""

from services import nai_prompt_output_parser as parser


class TestExtractMultiCharacterPayloadJson:
    def test_with_positions(self):
        text = (
            '{"version":3,"format":"multi","intent":"normal","continuity":"new",'
            '"global":["2girls","indoor","year 2025"],'
            '"people":[["girl","blue hair","blue dress"],["girl","white hair","white kimono"]],'
            '"positions":["B2","D4"]}'
        )
        payload = parser.extract_multi_character_payload(text)
        assert payload is not None
        assert payload["global_text"] == "2girls, indoor, year 2025"
        assert len(payload["characters"]) == 2
        assert payload["characters"][0]["prompt"] == "girl, blue hair, blue dress"
        assert payload["characters"][0]["position"] == "B2"
        assert payload["characters"][1]["position"] == "D4"
        assert payload["has_coords"] is True

    def test_without_positions(self):
        text = (
            '{"version":3,"format":"multi",'
            '"global":["2girls","park"],'
            '"people":[["girl","laughing"],["girl","running"]]}'
        )
        payload = parser.extract_multi_character_payload(text)
        assert payload is not None
        assert len(payload["characters"]) == 2
        assert payload["characters"][0]["position"] == ""
        assert payload["has_coords"] is False

    def test_drops_invalid_position(self):
        text = (
            '{"version":3,"format":"multi",'
            '"global":["2girls","indoor"],'
            '"people":[["girl","a"],["girl","b"]],'
            '"positions":["X9","D3"]}'
        )
        payload = parser.extract_multi_character_payload(text)
        assert payload is not None
        assert payload["characters"][0]["position"] == ""   # X9 不匹配 [A-E][1-5]
        assert payload["characters"][1]["position"] == "D3"
        assert payload["has_coords"] is False

    def test_returns_none_for_single_format(self):
        text = '{"version":3,"format":"single","global":["solo","1girl"],"people":[["girl","smile"]]}'
        assert parser.extract_multi_character_payload(text) is None

    def test_returns_none_when_under_two(self):
        text = '{"version":3,"format":"multi","global":["1girl"],"people":[["girl","smile"]]}'
        assert parser.extract_multi_character_payload(text) is None

    def test_returns_none_for_v1(self):
        assert parser.extract_multi_character_payload('{"version":1,"format":"multi","prompt":"x"}') is None

    def test_returns_none_for_plain_text(self):
        assert parser.extract_multi_character_payload("2girls,\nchar1:a,\nchar2:b") is None


class TestExtractMultiCharacterPayloadText:
    def test_multiline_charn_prefix(self):
        text = (
            "2girls, nsfw, indoor, year 2026,\n"
            "char1:girl, in foreground, hatsune miku (vocaloid), blush,\n"
            "char2:girl, beside girl, luo tianyi (vocaloid), closed eyes,"
        )
        payload = parser.extract_multi_character_payload_from_text(text)
        assert payload is not None
        assert payload["global_text"] == "2girls, nsfw, indoor, year 2026"
        assert len(payload["characters"]) == 2
        assert payload["characters"][0]["prompt"] == "girl, in foreground, hatsune miku (vocaloid), blush"
        assert payload["characters"][0]["position"] == ""
        assert payload["has_coords"] is False

    def test_pipe_format(self):
        text = "2girls, street | girl a, smile | girl b, smile"
        payload = parser.extract_multi_character_payload_from_text(text)
        assert payload is not None
        assert payload["global_text"] == "2girls, street"
        assert payload["characters"][0]["prompt"] == "girl a, smile"

    def test_returns_none_for_single_line(self):
        assert parser.extract_multi_character_payload_from_text("solo, 1girl, smile") is None

    def test_tolerates_fullwidth_colon(self):
        # charN 前缀正则兼容中英文冒号
        text = "2girls, park,\nchar1：girl, smile,\nchar2：girl, laugh,"
        payload = parser.extract_multi_character_payload_from_text(text)
        assert payload is not None
        assert len(payload["characters"]) == 2
        assert payload["characters"][1]["prompt"] == "girl, laugh"

    def test_wrapper_quality_artist_lands_in_global(self):
        # 关键：包装层 nai_quality, nai_artist 拼在最前，应整体落进 global 段（不污染角色段）
        text = (
            "very aesthetic, masterpiece, artist:wlop, 2girls, indoor,\n"
            "char1:girl, blue hair,\n"
            "char2:girl, white hair,"
        )
        payload = parser.extract_multi_character_payload_from_text(text)
        assert payload is not None
        assert payload["global_text"] == "very aesthetic, masterpiece, artist:wlop, 2girls, indoor"
        assert payload["characters"][0]["prompt"] == "girl, blue hair"


class TestResolveMultiCharacterPayload:
    def test_prefers_json_when_available(self):
        json_text = (
            '{"version":3,"format":"multi","global":["2girls","park"],'
            '"people":[["girl","smile"],["girl","laugh"]],"positions":["B3","D3"]}'
        )
        rendered = "2girls, park,\nchar1:girl, smile,\nchar2:girl, laugh,"
        payload = parser.resolve_multi_character_payload(json_text, rendered)
        assert payload is not None
        assert payload["characters"][0]["position"] == "B3"   # JSON 路径才有 position
        assert payload["has_coords"] is True

    def test_falls_back_to_text_when_json_missing(self):
        rendered = "2girls, park,\nchar1:girl, smile,\nchar2:girl, laugh,"
        payload = parser.resolve_multi_character_payload("not a json", rendered)
        assert payload is not None
        assert len(payload["characters"]) == 2
        assert payload["has_coords"] is False

    def test_returns_none_for_single_person(self):
        assert parser.resolve_multi_character_payload("not a json", "solo, 1girl, smile") is None


class TestParseStructuredPromptPayload:
    def test_v3_metadata(self):
        text = (
            '{"version":3,"format":"single","intent":"selfie","continuity":"adjust",'
            '"global":["selfie","mirror selfie"],"people":[]}'
        )
        payload = parser.parse_structured_prompt_payload(text)
        assert payload is not None
        assert payload["intent"] == "selfie"
        assert payload["continuity"] == "adjust"

    def test_code_fence_stripped(self):
        text = '```json\n{"version":3,"format":"multi","global":["a"],"people":[["x"],["y"]]}\n```'
        payload = parser.parse_structured_prompt_payload(text)
        assert payload is not None
        assert payload["format"] == "multi"

    def test_plain_text_returns_none(self):
        assert parser.parse_structured_prompt_payload("just some tags, not json") is None
