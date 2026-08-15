import io
import unittest

from app import create_app


class AppTestCase(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.testing = True
        self.client = app.test_client()

    def test_bot_test_endpoint_returns_ready_state(self):
        response = self.client.post(
            "/api/bot/test",
            json={"strategy": "rsi_macd", "symbol": "EURUSD", "source": "TradeLocker", "riskPercent": 1},
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["testRun"]["status"], "ready")

    def test_fxreplay_csv_endpoint_summarizes_close_prices(self):
        response = self.client.post(
            "/api/fxreplay/test",
            data={
                "strategy": "rsi_macd",
                "symbol": "EURUSD",
                "file": (io.BytesIO(b"timestamp,close\n1,100\n2,110\n"), "prices.csv"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["rows"], 2)
        self.assertEqual(body["pnlPercent"], 10.0)


if __name__ == "__main__":
    unittest.main()
