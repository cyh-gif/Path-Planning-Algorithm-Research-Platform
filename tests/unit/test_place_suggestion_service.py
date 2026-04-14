import json
import unittest
from unittest.mock import patch

from src.services.place_suggestion_service import PlaceSuggestionService


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class TestPlaceSuggestionService(unittest.TestCase):
    def test_should_return_amap_source_only(self) -> None:
        payload = {
            "status": "1",
            "tips": [
                {"name": "中关村", "district": "北京市海淀区"},
                {"name": "中山公园", "district": "上海市长宁区"},
            ],
        }

        with patch(
            "src.services.place_suggestion_service.urllib.request.urlopen",
            return_value=_FakeHttpResponse(payload),
        ):
            service = PlaceSuggestionService(amap_key="test-key", request_timeout_s=1.0, retry=0)
            rows = service.suggest_with_source("中", 10)

        self.assertGreaterEqual(len(rows), 2)
        self.assertTrue(all(item.source == PlaceSuggestionService.SOURCE_AMAP for item in rows))


if __name__ == "__main__":
    unittest.main()
