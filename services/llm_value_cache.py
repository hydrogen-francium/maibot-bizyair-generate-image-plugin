"""按天缓存 LLM 生成结果。

用于 mode = "daily_llm" 的自定义变量：
- 第一次访问当天 → 调 LLM 生成 → 写文件 → 返回
- 当天再次访问 → 直接读文件返回
- 跨天访问 → 旧缓存被惰性清理，重新生成

缓存文件命名：.var_cache/{key}.{YYYY-MM-DD}.json
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable

_singleton: "DailyLlmCache | None" = None


def get_daily_llm_cache() -> "DailyLlmCache":
    """模块级单例：按需懒初始化，缓存目录固定为插件根下的 .var_cache/"""
    global _singleton
    if _singleton is None:
        plugin_root = Path(__file__).resolve().parent.parent
        _singleton = DailyLlmCache(plugin_root / ".var_cache")
    return _singleton


class DailyLlmCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _today_key() -> str:
        return date.today().isoformat()

    def _cache_path(self, key: str, today: str | None = None) -> Path:
        return self.cache_dir / f"{key}.{today or self._today_key()}.json"

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _purge_stale(self, key: str) -> None:
        """删除该 key 在非今天的旧缓存文件"""
        keep = self._cache_path(key).name
        for f in self.cache_dir.glob(f"{key}.*.json"):
            if f.name != keep:
                try:
                    f.unlink()
                except OSError:
                    pass

    async def get_or_generate(
        self,
        key: str,
        generator: Callable[[], Awaitable[str]],
    ) -> str:
        """按 key 取今日缓存值；未命中则调 generator 生成并写盘。

        :param key: str，变量名，作为缓存文件名前缀
        :param generator: 无参 async 函数，返回字符串值（命中时不调用）
        :return: str，今日的缓存值（命中或新生成）
        """
        async with self._get_lock(key):
            today = self._today_key()
            cache_path = self._cache_path(key, today)

            if cache_path.exists():
                try:
                    data = json.loads(cache_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "value" in data and data.get("date_key") == today:
                        return str(data["value"])
                except (json.JSONDecodeError, OSError):
                    pass

            self._purge_stale(key)
            value = await generator()
            try:
                cache_path.write_text(
                    json.dumps(
                        {"value": value, "date_key": today},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
            return value
