"""DailyLlmCache 单元测试：命中、未命中、跨天清理、并发加锁、validator 拦截污染输出。"""
import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from services import llm_value_cache
from services.llm_value_cache import DailyLlmCache, DailyLlmValidationError


@pytest.fixture
def cache(tmp_path: Path) -> DailyLlmCache:
    return DailyLlmCache(tmp_path / "var_cache")


class TestDailyLlmCacheHitMiss:
    @pytest.mark.asyncio
    async def test_cache_miss_calls_generator_and_writes_file(self, cache: DailyLlmCache, tmp_path: Path):
        async def gen() -> str:
            return "fresh value"

        result = await cache.get_or_generate("today_state", gen)
        assert result == "fresh value"

        cache_file = cache._cache_path("today_state")
        assert cache_file.exists()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["value"] == "fresh value"
        assert data["date_key"] == date.today().isoformat()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_generator(self, cache: DailyLlmCache):
        cache_file = cache._cache_path("today_state")
        cache_file.write_text(
            json.dumps({"value": "cached", "date_key": date.today().isoformat()}),
            encoding="utf-8",
        )

        call_count = 0

        async def gen() -> str:
            nonlocal call_count
            call_count += 1
            return "should not run"

        result = await cache.get_or_generate("today_state", gen)
        assert result == "cached"
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_stale_date_key_treated_as_miss(self, cache: DailyLlmCache):
        cache_file = cache._cache_path("today_state")
        cache_file.write_text(
            json.dumps({"value": "yesterday", "date_key": "2000-01-01"}),
            encoding="utf-8",
        )

        async def gen() -> str:
            return "today fresh"

        result = await cache.get_or_generate("today_state", gen)
        assert result == "today fresh"

    @pytest.mark.asyncio
    async def test_corrupt_json_treated_as_miss(self, cache: DailyLlmCache):
        cache_file = cache._cache_path("today_state")
        cache_file.write_text("{not valid json", encoding="utf-8")

        async def gen() -> str:
            return "regenerated"

        result = await cache.get_or_generate("today_state", gen)
        assert result == "regenerated"


class TestPurgeStale:
    @pytest.mark.asyncio
    async def test_old_day_cache_files_purged_on_miss(self, cache: DailyLlmCache):
        old_path = cache.cache_dir / "today_state.2000-01-01.json"
        old_path.write_text(
            json.dumps({"value": "old", "date_key": "2000-01-01"}),
            encoding="utf-8",
        )
        another_old = cache.cache_dir / "today_state.1999-12-31.json"
        another_old.write_text(
            json.dumps({"value": "older", "date_key": "1999-12-31"}),
            encoding="utf-8",
        )

        async def gen() -> str:
            return "today"

        await cache.get_or_generate("today_state", gen)

        assert not old_path.exists()
        assert not another_old.exists()
        assert cache._cache_path("today_state").exists()

    @pytest.mark.asyncio
    async def test_other_keys_not_purged(self, cache: DailyLlmCache):
        other_key = cache.cache_dir / "other_key.2000-01-01.json"
        other_key.write_text(
            json.dumps({"value": "x", "date_key": "2000-01-01"}),
            encoding="utf-8",
        )

        async def gen() -> str:
            return "today"

        await cache.get_or_generate("today_state", gen)
        assert other_key.exists()


class TestValidator:
    @pytest.mark.asyncio
    async def test_validator_pass_writes_cache(self, cache: DailyLlmCache):
        async def gen() -> str:
            return "valid output with all required markers"

        def validator(v: str) -> None:
            if "required" not in v:
                raise DailyLlmValidationError("missing marker")

        result = await cache.get_or_generate("k", gen, validator=validator)
        assert result == "valid output with all required markers"
        assert cache._cache_path("k").exists()

    @pytest.mark.asyncio
    async def test_validator_fail_raises_and_does_not_write_cache(self, cache: DailyLlmCache):
        async def gen() -> str:
            return "bad"

        def validator(v: str) -> None:
            raise DailyLlmValidationError("too short")

        with pytest.raises(DailyLlmValidationError, match="too short"):
            await cache.get_or_generate("k", gen, validator=validator)

        assert not cache._cache_path("k").exists()

    @pytest.mark.asyncio
    async def test_validator_not_called_on_cache_hit(self, cache: DailyLlmCache):
        cache_file = cache._cache_path("k")
        cache_file.write_text(
            json.dumps({"value": "cached value", "date_key": date.today().isoformat()}),
            encoding="utf-8",
        )

        validator_calls = 0

        def validator(v: str) -> None:
            nonlocal validator_calls
            validator_calls += 1

        async def gen() -> str:
            return "should not run"

        result = await cache.get_or_generate("k", gen, validator=validator)
        assert result == "cached value"
        assert validator_calls == 0

    @pytest.mark.asyncio
    async def test_validator_failure_allows_retry_in_same_day(self, cache: DailyLlmCache):
        """validator 失败不写盘，允许同一天再次尝试生成（不会读到污染缓存）"""
        outputs = iter(["bad", "good output"])

        async def gen() -> str:
            return next(outputs)

        def validator(v: str) -> None:
            if len(v) < 5:
                raise DailyLlmValidationError("too short")

        with pytest.raises(DailyLlmValidationError):
            await cache.get_or_generate("k", gen, validator=validator)

        result = await cache.get_or_generate("k", gen, validator=validator)
        assert result == "good output"
        assert cache._cache_path("k").exists()


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_calls_for_same_key_only_run_generator_once(self, cache: DailyLlmCache):
        call_count = 0
        gate = asyncio.Event()

        async def gen() -> str:
            nonlocal call_count
            call_count += 1
            await gate.wait()
            return "shared"

        async def trigger():
            asyncio.get_event_loop().call_later(0.05, gate.set)

        task1 = asyncio.create_task(cache.get_or_generate("k", gen))
        task2 = asyncio.create_task(cache.get_or_generate("k", gen))
        await trigger()
        results = await asyncio.gather(task1, task2)

        assert call_count == 1
        assert results == ["shared", "shared"]


class TestSingleton:
    def test_get_daily_llm_cache_returns_same_instance(self):
        # 复用模块级单例，多次调用应返回同一对象
        instance1 = llm_value_cache.get_daily_llm_cache()
        instance2 = llm_value_cache.get_daily_llm_cache()
        assert instance1 is instance2

    def test_singleton_cache_dir_under_plugin_root(self):
        instance = llm_value_cache.get_daily_llm_cache()
        assert instance.cache_dir.name == ".var_cache"
        assert instance.cache_dir.exists()
