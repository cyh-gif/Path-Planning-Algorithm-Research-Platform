from __future__ import annotations

from dataclasses import dataclass
import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Iterable


@dataclass(slots=True, frozen=True)
class SuggestionItem:
    text: str
    source: str


class PlaceSuggestionService:
    """地点联想服务：只使用高德输入提示，历史记录由 UI 层维护。"""

    SOURCE_HISTORY = "历史"
    SOURCE_AMAP = "高德"

    def __init__(
        self,
        amap_key: str = "",
        request_timeout_s: float = 1.2,
        retry: int = 1,
        cache_ttl_s: int = 45,
    ) -> None:
        self.amap_key = amap_key.strip()
        self.request_timeout_s = max(0.3, float(request_timeout_s))
        self.retry = max(0, int(retry))
        self.cache_ttl_s = max(5, int(cache_ttl_s))
        self._cache_lock = threading.Lock()
        self._cache: dict[str, tuple[float, list[SuggestionItem]]] = {}

    def suggest(self, keyword: str, limit: int = 12) -> list[str]:
        return [item.text for item in self.suggest_with_source(keyword, limit)]

    def suggest_with_source(self, keyword: str, limit: int = 12) -> list[SuggestionItem]:
        """根据关键词返回候选地点（仅高德来源）。"""
        normalized = keyword.strip()
        if not normalized:
            return []

        cap = max(3, min(30, int(limit)))
        cache_key = f"{normalized.lower()}::{cap}"
        now = time.time()

        with self._cache_lock:
            cache_hit = self._cache.get(cache_key)
            if cache_hit and (now - cache_hit[0] <= self.cache_ttl_s):
                return list(cache_hit[1])

        results: list[SuggestionItem] = []
        self._append_unique_items(
            results,
            (SuggestionItem(text=item, source=self.SOURCE_AMAP) for item in self._from_amap(normalized, cap)),
            cap,
        )

        with self._cache_lock:
            self._cache[cache_key] = (now, list(results))
            self._prune_cache(now)

        return results

    def _from_amap(self, keyword: str, limit: int) -> list[str]:
        if not self.amap_key:
            return []

        params = urllib.parse.urlencode(
            {
                "key": self.amap_key,
                "keywords": keyword,
                "datatype": "all",
                "citylimit": "false",
            }
        )
        url = f"https://restapi.amap.com/v3/assistant/inputtips?{params}"

        payload: dict[str, object] | None = None
        attempts = self.retry + 1
        for index in range(attempts):
            try:
                with urllib.request.urlopen(url, timeout=self.request_timeout_s) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except Exception:
                if index >= attempts - 1:
                    return []

        if not isinstance(payload, dict) or str(payload.get("status")) != "1":
            return []

        tips = payload.get("tips", [])
        if not isinstance(tips, list):
            return []

        candidates: list[str] = []
        for tip in tips:
            if not isinstance(tip, dict):
                continue
            name = str(tip.get("name", "")).strip()
            district = str(tip.get("district", "")).strip()
            if not name:
                continue
            if district and district not in name:
                candidates.append(f"{district}{name}")
            candidates.append(name)
            if len(candidates) >= limit * 2:
                break

        return candidates

    def _append_unique_items(
        self,
        target: list[SuggestionItem],
        source: Iterable[SuggestionItem],
        limit: int,
    ) -> None:
        existing_text = {item.text for item in target}
        for item in source:
            text = item.text.strip()
            if not text or text in existing_text:
                continue
            target.append(SuggestionItem(text=text, source=item.source.strip() or self.SOURCE_AMAP))
            existing_text.add(text)
            if len(target) >= limit:
                return

    def _prune_cache(self, now: float) -> None:
        expired_keys = [
            key
            for key, (ts, _) in self._cache.items()
            if (now - ts) > self.cache_ttl_s
        ]
        for key in expired_keys:
            self._cache.pop(key, None)
