import unittest
from unittest.mock import patch, Mock

from app.services import fx_service


class TestFXService(unittest.TestCase):

    def setUp(self):
        fx_service._rates_cache["data"] = None
        fx_service._rates_cache["fetched_at"] = 0

    def tearDown(self):
        fx_service._rates_cache["data"] = None
        fx_service._rates_cache["fetched_at"] = 0

    def test_usd_quote(self):
        result = fx_service.get_commercial_fx_quote("USD")
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["rate"], 1.0)
        self.assertFalse(result["stale"])

    @patch("app.services.fx_service.requests.get")
    def test_commercial_quote_fetches_live_rate(self, mock_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"quote": "NGN", "rate": 1500.0, "date": "2026-09-15"},
        ]
        mock_get.return_value = response

        result = fx_service.get_commercial_fx_quote("NGN")

        self.assertEqual(result["currency"], "NGN")
        self.assertEqual(result["rate"], 1500.0)
        self.assertEqual(result["source"], "frankfurter.dev")
        self.assertEqual(result["rate_timestamp"], "2026-09-15T00:00:00Z")
        self.assertFalse(result["stale"])
        mock_get.assert_called_once()

    @patch("app.services.fx_service.requests.get")
    def test_fresh_cache_is_reused(self, mock_get):
        fx_service._rates_cache["data"] = {
            "rates": {"NGN": 1500.0},
            "dates": {"NGN": "2026-09-15"},
        }
        import time
        fx_service._rates_cache["fetched_at"] = time.time()

        result = fx_service.get_commercial_fx_quote("NGN")

        self.assertEqual(result["rate"], 1500.0)
        mock_get.assert_not_called()

    @patch("app.services.fx_service.requests.get")
    def test_expired_cache_fails_closed(self, mock_get):
        import time

        fx_service._rates_cache["data"] = {
            "rates": {"NGN": 1500.0},
            "dates": {"NGN": "2026-09-15"},
        }
        fx_service._rates_cache["fetched_at"] = (
            time.time() - fx_service.CACHE_TTL_SECONDS - 1
        )

        mock_get.side_effect = RuntimeError("provider unavailable")

        with self.assertRaises(RuntimeError):
            fx_service.get_commercial_fx_quote("NGN")

    @patch("app.services.fx_service.requests.get")
    def test_missing_currency_fails(self, mock_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"quote": "EUR", "rate": 0.85, "date": "2026-09-15"},
        ]
        mock_get.return_value = response

        with self.assertRaises(ValueError):
            fx_service.get_commercial_fx_quote("NGN")

    @patch("app.services.fx_service.requests.get")
    def test_nonpositive_rate_fails(self, mock_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"quote": "NGN", "rate": 0, "date": "2026-09-15"},
        ]
        mock_get.return_value = response

        with self.assertRaises(ValueError):
            fx_service.get_commercial_fx_quote("NGN")


if __name__ == "__main__":
    unittest.main()
