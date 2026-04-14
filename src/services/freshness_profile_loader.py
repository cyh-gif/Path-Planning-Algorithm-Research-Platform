from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


class FruitProfileLoadError(ValueError):
    """水果保鲜参数加载失败。"""


@dataclass(frozen=True, slots=True)
class FruitProfile:
    fruit_id: str
    name: str
    freshness_init: float
    decay_multiplier: float


def load_fruit_profiles(profile_path: Path) -> list[FruitProfile]:
    """从 JSON 文件加载水果保鲜参数并做基础校验。"""
    if not profile_path.exists():
        raise FruitProfileLoadError(f"水果参数文件不存在: {profile_path}")

    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FruitProfileLoadError(f"水果参数文件读取失败: {profile_path}") from exc

    if isinstance(payload, dict):
        raw_items = payload.get("fruits")
    else:
        raw_items = payload

    if not isinstance(raw_items, list) or not raw_items:
        raise FruitProfileLoadError("水果参数 JSON 必须是非空数组，或包含 fruits 数组。")

    profiles: list[FruitProfile] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()

    for index, row in enumerate(raw_items):
        if not isinstance(row, dict):
            raise FruitProfileLoadError(f"水果参数第 {index + 1} 项不是对象。")

        profile = _parse_row(row, index)
        if profile.fruit_id in seen_ids:
            raise FruitProfileLoadError(f"水果 ID 重复: {profile.fruit_id}")
        if profile.name in seen_names:
            raise FruitProfileLoadError(f"水果名称重复: {profile.name}")

        seen_ids.add(profile.fruit_id)
        seen_names.add(profile.name)
        profiles.append(profile)

    return profiles


def _parse_row(row: dict[str, Any], index: int) -> FruitProfile:
    required_fields = ("id", "name", "freshness_init", "decay_multiplier")
    for field in required_fields:
        if field not in row:
            raise FruitProfileLoadError(f"水果参数第 {index + 1} 项缺少字段: {field}")

    fruit_id = str(row["id"]).strip()
    name = str(row["name"]).strip()
    if not fruit_id or not name:
        raise FruitProfileLoadError(f"水果参数第 {index + 1} 项 id/name 不能为空。")

    try:
        freshness_init = float(row["freshness_init"])
        decay_multiplier = float(row["decay_multiplier"])
    except (TypeError, ValueError) as exc:
        raise FruitProfileLoadError(f"水果参数第 {index + 1} 项 freshness_init/decay_multiplier 必须是数值。") from exc

    if decay_multiplier <= 0:
        raise FruitProfileLoadError(f"水果参数第 {index + 1} 项 decay_multiplier 必须大于 0。")

    return FruitProfile(
        fruit_id=fruit_id,
        name=name,
        freshness_init=freshness_init,
        decay_multiplier=decay_multiplier,
    )
