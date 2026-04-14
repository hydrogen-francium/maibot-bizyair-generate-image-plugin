from services.preset_resolution import resolve_active_preset


class TestPresetResolution:
    def test_resolve_bizyair_preset(self):
        result = resolve_active_preset(
            active_preset="default",
            bizyair_presets=[{"preset_name": "default", "app_id": 1, "description": "bizyair"}],
            nai_presets=[{"preset_name": "nai_default", "base_url": "https://example.com/v1", "api_key": "k", "model": "m"}],
        )
        assert result["provider"] == "bizyair_openapi"
        assert result["preset"]["app_id"] == 1

    def test_resolve_nai_preset(self):
        result = resolve_active_preset(
            active_preset="nai_default",
            bizyair_presets=[],
            nai_presets=[{"preset_name": "nai_default", "base_url": "https://example.com/v1", "api_key": "k", "model": "m"}],
        )
        assert result["provider"] == "nai_chat"
        assert result["preset"]["model"] == "m"

    def test_duplicate_preset_name_raises(self):
        try:
            resolve_active_preset(
                active_preset="dup",
                bizyair_presets=[{"preset_name": "dup", "app_id": 1, "description": "bizyair"}],
                nai_presets=[{"preset_name": "dup", "base_url": "https://example.com/v1", "api_key": "k", "model": "m"}],
            )
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "全局唯一" in str(exc)

    def test_missing_preset_raises(self):
        try:
            resolve_active_preset(
                active_preset="missing",
                bizyair_presets=[],
                nai_presets=[],
            )
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "未在 BizyAir 或 NAI 预设中找到" in str(exc)