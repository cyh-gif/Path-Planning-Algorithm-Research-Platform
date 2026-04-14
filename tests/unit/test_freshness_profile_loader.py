import json
from pathlib import Path
import tempfile
import unittest

from src.services.freshness_profile_loader import FruitProfileLoadError, load_fruit_profiles


class TestFreshnessProfileLoader(unittest.TestCase):
    def test_should_load_valid_profiles(self) -> None:
        payload = [
            {"id": "apple", "name": "苹果", "freshness_init": 100, "decay_multiplier": 0.85},
            {"id": "banana", "name": "香蕉", "freshness_init": 118, "decay_multiplier": 0.95},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fruit_profiles.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            profiles = load_fruit_profiles(path)

        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[0].fruit_id, "apple")
        self.assertEqual(profiles[1].name, "香蕉")

    def test_should_raise_error_when_missing_required_field(self) -> None:
        payload = [{"id": "apple", "name": "苹果", "freshness_init": 100}]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fruit_profiles.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(FruitProfileLoadError):
                load_fruit_profiles(path)

    def test_should_raise_error_when_id_duplicated(self) -> None:
        payload = [
            {"id": "apple", "name": "苹果", "freshness_init": 100, "decay_multiplier": 0.85},
            {"id": "apple", "name": "苹果-重复", "freshness_init": 98, "decay_multiplier": 1.0},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fruit_profiles.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(FruitProfileLoadError):
                load_fruit_profiles(path)


if __name__ == "__main__":
    unittest.main()
