"""按天缓存 LLM 生成结果。

用于 mode = "daily_llm" 的自定义变量：
- 第一次访问当天 → 调 LLM 生成 → 校验通过 → 写文件 → 返回
- 当天再次访问 → 直接读文件返回
- 跨天访问 → 旧缓存被惰性清理，重新生成

校验失败时三层兜底（不写盘以避免污染当天）：
1. 重新调一次 LLM；通过则写盘返回
2. 仍失败 → 找历史最近一份合法缓存（昨天/前天），返回其 value
3. 没有历史缓存 → 调 fallback() 兜底；fallback 也无值才抛 DailyLlmValidationError

缓存文件命名：.var_cache/{key}.{YYYY-MM-DD}.json
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger("bizyair_generate_image_plugin")

_singleton: "DailyLlmCache | None" = None


def get_daily_llm_cache() -> "DailyLlmCache":
    """模块级单例：按需懒初始化，缓存目录固定为插件根下的 .var_cache/"""
    global _singleton
    if _singleton is None:
        plugin_root = Path(__file__).resolve().parent.parent
        _singleton = DailyLlmCache(plugin_root / ".var_cache")
    return _singleton


class DailyLlmValidationError(RuntimeError):
    """daily_llm 输出未通过校验，不应写入缓存。"""


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

    def _purge_stale(self, key: str, keep_days: int = 7) -> None:
        """保留该 key 最近 keep_days 份缓存（按文件名日期降序），其余删除"""
        files_with_date: list[tuple[str, Path]] = []
        for f in self.cache_dir.glob(f"{key}.*.json"):
            date_part = f.stem[len(key) + 1:]
            if not date_part:
                continue
            files_with_date.append((date_part, f))
        files_with_date.sort(reverse=True)
        for _, f in files_with_date[keep_days:]:
            try:
                f.unlink()
            except OSError:
                pass

    async def get_or_generate(
        self,
        key: str,
        generator: Callable[[], Awaitable[str]],
        validator: Callable[[str], None] | None = None,
        fallback: Callable[[], str] | None = None,
    ) -> str:
        """按 key 取今日缓存值；未命中则调 generator 生成并写盘。

        :param key: str，变量名，作为缓存文件名前缀
        :param generator: 无参 async 函数，返回字符串值（命中时不调用）
        :param validator: 可选校验函数；不通过应抛 DailyLlmValidationError 或任何异常
        :param fallback: 可选无参兜底函数；当 LLM 重试也校验失败且无历史合法缓存时使用
        :return: str，今日缓存值。三层兜底顺序：当场重试 → 历史缓存 → fallback

        - 仅当首次 generator + validator 通过时写盘；其余路径不写盘，避免污染当天剩余调用
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

            # ── 第 1 次尝试 ──
            value = await generator()
            try:
                if validator is not None:
                    validator(value)
                self._write_cache(cache_path, value, today)
                return value
            except Exception as first_exc:
                if validator is None:
                    raise
                logger.warning(
                    f"[daily_llm] {key} 首次校验失败，准备重试一次: {first_exc}"
                )

            # ── 第 2 次尝试（重试 LLM）──
            try:
                retry_value = await generator()
                validator(retry_value)
                self._write_cache(cache_path, retry_value, today)
                logger.info(f"[daily_llm] {key} 重试一次后校验通过")
                return retry_value
            except Exception as retry_exc:
                logger.warning(
                    f"[daily_llm] {key} 重试仍失败，回退到历史缓存: {retry_exc}"
                )

            # ── 第 3 层：历史合法缓存 ──
            historic = self._load_latest_historic(key, exclude_date=today)
            if historic is not None:
                logger.warning(
                    f"[daily_llm] {key} 使用历史缓存兜底（未写入今日缓存以便后续合法生成）"
                )
                return historic

            # ── 第 4 层：fallback ──
            if fallback is not None:
                try:
                    fb = fallback()
                except Exception as fb_exc:
                    logger.error(f"[daily_llm] {key} fallback 调用异常: {fb_exc}")
                    fb = ""
                if fb:
                    logger.warning(f"[daily_llm] {key} 使用 fallback 文案兜底")
                    return fb

            raise DailyLlmValidationError(
                f"daily_llm 变量 {key} 经重试与历史缓存兜底后仍无法获得合法值"
            )

    def _write_cache(self, cache_path: Path, value: str, today: str) -> None:
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

    def _load_latest_historic(self, key: str, exclude_date: str) -> str | None:
        """在缓存目录里找该 key 的最新历史合法缓存（不含今天）"""
        candidates: list[tuple[str, Path]] = []
        for f in self.cache_dir.glob(f"{key}.*.json"):
            try:
                date_part = f.stem[len(key) + 1:]
            except Exception:
                continue
            if not date_part or date_part == exclude_date:
                continue
            candidates.append((date_part, f))
        candidates.sort(reverse=True)
        for _, path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("value"), str) and data["value"]:
                    return str(data["value"])
            except (json.JSONDecodeError, OSError):
                continue
        return None
