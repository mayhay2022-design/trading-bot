import io

from app import create_app


def test_bot_test_endpoint_returns_ready_state():
    app = create_app()
    client = app.test_client()

    response = client.post(
        "/api/bot/test",
        json={"strategy": "rsi_macd", "symbol": "EURUSD", "source": "TradeLocker", "riskPercent": 1},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["testRun"]["status"] == "ready"


def test_fxreplay_csv_endpoint_summarizes_close_prices():
    app = create_app()
    client = app.test_client()

    response = client.post(
        "/api/fxreplay/test",
        data={
            "strategy": "rsi_macd",
            "symbol": "EURUSD",
            "file": (io.BytesIO(b"timestamp,close\n1,100\n2,110\n"), "prices.csv"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["rows"] == 2
    assert body["pnlPercent"] == 10.0
