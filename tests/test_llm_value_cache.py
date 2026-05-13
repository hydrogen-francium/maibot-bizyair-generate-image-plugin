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
        """生成新缓存时，超出保留窗口的非常老历史文件被清除（保留近 7 份用于兜底）"""
        # 8 个旧日期，写入后只保留最近 7 份历史 + 今日一份
        old_dates = [
            "2000-01-01", "2000-01-02", "2000-01-03", "2000-01-04",
            "2000-01-05", "2000-01-06", "2000-01-07", "2000-01-08",
        ]
        for d in old_dates:
            (cache.cache_dir / f"today_state.{d}.json").write_text(
                json.dumps({"value": f"old {d}", "date_key": d}),
                encoding="utf-8",
            )

        async def gen() -> str:
            return "today"

        await cache.get_or_generate("today_state", gen)

        # 最老的那份应被清掉；最近的几份应保留
        assert not (cache.cache_dir / "today_state.2000-01-01.json").exists()
        assert (cache.cache_dir / "today_state.2000-01-08.json").exists()
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
        """两次重试都失败、无历史缓存、无 fallback 时抛 DailyLlmValidationError 且不写盘"""
        async def gen() -> str:
            return "bad"

        def validator(v: str) -> None:
            raise DailyLlmValidationError("too short")

        with pytest.raises(DailyLlmValidationError, match="重试与历史缓存"):
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
    async def test_validator_failure_first_retry_succeeds(self, cache: DailyLlmCache):
        """首次校验失败，当场重试一次成功，应直接返回重试值并写盘"""
        outputs = iter(["bad", "good output"])

        async def gen() -> str:
            return next(outputs)

        def validator(v: str) -> None:
            if len(v) < 5:
                raise DailyLlmValidationError("too short")

        result = await cache.get_or_generate("k", gen, validator=validator)
        assert result == "good output"
        assert cache._cache_path("k").exists()

    @pytest.mark.asyncio
    async def test_validator_failure_falls_back_to_historic_cache(self, cache: DailyLlmCache, tmp_path):
        """重试仍失败时，回退到历史合法缓存"""
        # 准备一份昨天的合法缓存
        yesterday_path = cache.cache_dir / "k.2020-01-01.json"
        yesterday_path.write_text(
            json.dumps({"value": "yesterday valid", "date_key": "2020-01-01"}),
            encoding="utf-8",
        )

        async def gen() -> str:
            return "bad"

        def validator(v: str) -> None:
            raise DailyLlmValidationError("always bad")

        result = await cache.get_or_generate("k", gen, validator=validator)
        assert result == "yesterday valid"
        # 不写今日缓存，等下次合法生成
        assert not cache._cache_path("k").exists()

    @pytest.mark.asyncio
    async def test_validator_failure_falls_back_to_fallback_callable(self, cache: DailyLlmCache):
        """重试失败 + 无历史缓存时，调用 fallback 返回兜底文案"""
        async def gen() -> str:
            return "bad"

        def validator(v: str) -> None:
            raise DailyLlmValidationError("always bad")

        result = await cache.get_or_generate(
            "k", gen, validator=validator, fallback=lambda: "fallback text"
        )
        assert result == "fallback text"
        assert not cache._cache_path("k").exists()


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
