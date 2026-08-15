import io
import os
import tempfile
import unittest

from app import TradeLockerSession, create_app


class FakeTradeLockerClient:
    def __init__(self):
        self.live_order_calls = 0

    def create_session(self, environment, username, password, server):
        return TradeLockerSession(
            session_id="session-123",
            environment=environment,
            username=username,
            server=server,
            access_token="access-token",
            refresh_token="refresh-token",
            accounts=[
                {
                    "id": 101,
                    "name": "Primary Demo",
                    "currency": "USD",
                    "accNum": 123456,
                    "accountBalance": 25000.0,
                }
            ],
        )

    def list_accounts(self, session):
        return session.accounts

    def get_market_snapshot(self, session, account_id, symbol, resolution, bars):
        return {
            "account": session.accounts[0],
            "instrumentId": 77,
            "symbol": symbol,
            "tradeRouteId": "trade-route-1",
            "closeValues": [1.00, 1.01, 1.02, 1.04, 1.08, 1.12, 1.16, 1.20],
        }

    def place_market_order(self, session, account_id, symbol, quantity, side):
        self.live_order_calls += 1
        return 9001


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.fake_client = FakeTradeLockerClient()
        app = create_app(trade_client=self.fake_client, run_async=False)
        app.testing = True
        self.client = app.test_client()

    def test_create_session_returns_accounts(self):
        response = self.client.post(
            "/api/tradelocker/session",
            json={
                "environment": "demo",
                "username": "user@example.com",
                "password": "secret",
                "server": "demo-server",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["session"]["sessionId"], "session-123")
        self.assertEqual(len(body["session"]["accounts"]), 1)

    def test_bot_start_runs_strategy_and_records_signal(self):
        self.client.post(
            "/api/tradelocker/session",
            json={
                "environment": "demo",
                "username": "user@example.com",
                "password": "secret",
                "server": "demo-server",
            },
        )

        response = self.client.post(
            "/api/bot/start",
            json={
                "sessionId": "session-123",
                "accountId": 101,
                "symbol": "EURUSD",
                "strategy": "moving_average",
                "executionMode": "paper",
                "quantity": 0.01,
                "fastPeriod": 3,
                "slowPeriod": 5,
                "resolution": "15m",
                "pollInterval": 15,
                "stopLossPercent": 1.5,
                "takeProfitPercent": 3.0,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["run"]["status"], "running")
        self.assertEqual(body["run"]["latestSignal"], "buy")
        self.assertEqual(body["run"]["executionCount"], 1)
        self.assertEqual(body["run"]["stopLossPercent"], 1.5)
        self.assertEqual(body["run"]["takeProfitPercent"], 3.0)
        self.assertEqual(body["run"]["position"]["side"], "buy")
        self.assertEqual(body["run"]["pnl"]["totalPercent"], 0.0)
        self.assertEqual(body["run"]["lastExecution"]["mode"], "paper")

    def test_bot_start_live_mode_places_order(self):
        self.client.post(
            "/api/tradelocker/session",
            json={
                "environment": "demo",
                "username": "user@example.com",
                "password": "secret",
                "server": "demo-server",
            },
        )

        response = self.client.post(
            "/api/bot/start",
            json={
                "sessionId": "session-123",
                "accountId": 101,
                "symbol": "EURUSD",
                "strategy": "moving_average",
                "executionMode": "live",
                "quantity": 0.01,
                "fastPeriod": 3,
                "slowPeriod": 5,
                "resolution": "15m",
                "pollInterval": 15,
                "stopLossPercent": 0,
                "takeProfitPercent": 0,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["run"]["lastExecution"]["orderId"], 9001)
        self.assertEqual(self.fake_client.live_order_calls, 1)

    def test_fxreplay_csv_endpoint_summarizes_close_prices(self):
        response = self.client.post(
            "/api/fxreplay/test",
            data={
                "strategy": "moving_average",
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

    def test_sessions_and_runs_persist_between_app_instances(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_file = os.path.join(tmp_dir, "state.json")
            app_one = create_app(trade_client=self.fake_client, run_async=False, state_file=state_file)
            app_one.testing = True
            client_one = app_one.test_client()

            client_one.post(
                "/api/tradelocker/session",
                json={
                    "environment": "demo",
                    "username": "user@example.com",
                    "password": "secret",
                    "server": "demo-server",
                },
            )
            start_response = client_one.post(
                "/api/bot/start",
                json={
                    "sessionId": "session-123",
                    "accountId": 101,
                    "symbol": "EURUSD",
                    "strategy": "moving_average",
                    "executionMode": "paper",
                    "quantity": 0.01,
                    "fastPeriod": 3,
                    "slowPeriod": 5,
                    "resolution": "15m",
                    "pollInterval": 15,
                    "stopLossPercent": 1.0,
                    "takeProfitPercent": 2.0,
                },
            )
            run_id = start_response.get_json()["run"]["runId"]

            app_two = create_app(trade_client=self.fake_client, run_async=False, state_file=state_file)
            app_two.testing = True
            client_two = app_two.test_client()

            session_response = client_two.get("/api/tradelocker/session/session-123")
            run_response = client_two.get(f"/api/bot/status?runId={run_id}")

            self.assertEqual(session_response.status_code, 200)
            self.assertEqual(run_response.status_code, 200)
            self.assertEqual(run_response.get_json()["run"]["stopLossPercent"], 1.0)
            self.assertEqual(run_response.get_json()["run"]["takeProfitPercent"], 2.0)


if __name__ == "__main__":
    unittest.main()
