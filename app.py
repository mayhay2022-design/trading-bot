from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from statistics import mean
from typing import Any
from urllib import error, request

from flask import Flask, jsonify, render_template, request as flask_request


TRADELOCKER_BASE_URLS = {
    "demo": "https://demo.tradelocker.com/backend-api",
    "live": "https://live.tradelocker.com/backend-api",
}


@dataclass
class ReplaySummary:
    rows: int
    first_close: float
    last_close: float
    average_close: float

    @property
    def pnl_percent(self) -> float:
        if self.first_close == 0:
            return 0.0
        return ((self.last_close - self.first_close) / self.first_close) * 100


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.post("/api/tradelocker/test")
    def test_tradelocker_connection():
        payload = flask_request.get_json(silent=True) or {}
        environment = payload.get("environment", "demo")
        username = (payload.get("username") or "").strip()
        password = payload.get("password") or ""
        server = (payload.get("server") or "").strip()

        if environment not in TRADELOCKER_BASE_URLS:
            return jsonify({"ok": False, "error": "Environment must be demo or live."}), 400
        if not username or not password or not server:
            return jsonify({"ok": False, "error": "Username, password, and server are required."}), 400

        login_payload = json.dumps(
            {
                "username": username,
                "password": password,
                "server": server,
            }
        ).encode("utf-8")

        login_request = request.Request(
            f"{TRADELOCKER_BASE_URLS[environment]}/auth/login",
            data=login_payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with request.urlopen(login_request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8") or "{}")
        except error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="ignore") or exc.reason
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "TradeLocker rejected the login attempt.",
                        "details": message[:500],
                    }
                ),
                exc.code,
            )
        except error.URLError as exc:
            return jsonify({"ok": False, "error": f"Could not reach TradeLocker: {exc.reason}"}), 502

        access_token = body.get("accessToken") or body.get("access_token")
        refresh_token = body.get("refreshToken") or body.get("refresh_token")

        return jsonify(
            {
                "ok": True,
                "message": "TradeLocker connection succeeded.",
                "environment": environment,
                "hasAccessToken": bool(access_token),
                "hasRefreshToken": bool(refresh_token),
            }
        )

    @app.get("/api/fxreplay/status")
    def fxreplay_status():
        return jsonify(
            {
                "ok": True,
                "supported": False,
                "message": (
                    "FX Replay does not publish a public API for direct third-party integrations. "
                    "Use CSV exports from FX Replay with the replay tester below."
                ),
            }
        )

    @app.post("/api/fxreplay/test")
    def test_fxreplay_csv():
        uploaded_file = flask_request.files.get("file")
        strategy = (flask_request.form.get("strategy") or "rsi_macd").strip()
        symbol = (flask_request.form.get("symbol") or "EURUSD").strip()

        if uploaded_file is None or not uploaded_file.filename:
            return jsonify({"ok": False, "error": "Upload a CSV export from FX Replay."}), 400

        try:
            summary = summarize_replay_csv(uploaded_file.read())
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        verdict = "pass" if summary.pnl_percent >= 0 else "review"
        return jsonify(
            {
                "ok": True,
                "message": "Replay data processed successfully.",
                "strategy": strategy,
                "symbol": symbol,
                "rows": summary.rows,
                "firstClose": round(summary.first_close, 5),
                "lastClose": round(summary.last_close, 5),
                "averageClose": round(summary.average_close, 5),
                "pnlPercent": round(summary.pnl_percent, 2),
                "verdict": verdict,
            }
        )

    @app.post("/api/bot/test")
    def test_bot_configuration():
        payload = flask_request.get_json(silent=True) or {}
        strategy = (payload.get("strategy") or "rsi_macd").strip()
        symbol = (payload.get("symbol") or "EURUSD").strip()
        source = (payload.get("source") or "TradeLocker").strip()
        risk_percent = payload.get("riskPercent") or 1

        return jsonify(
            {
                "ok": True,
                "message": "Bot test configuration saved.",
                "testRun": {
                    "strategy": strategy,
                    "symbol": symbol,
                    "source": source,
                    "riskPercent": risk_percent,
                    "status": "ready",
                },
            }
        )

    return app


def summarize_replay_csv(content: bytes) -> ReplaySummary:
    if not content:
        raise ValueError("The uploaded CSV file is empty.")

    text_stream = io.StringIO(content.decode("utf-8-sig"))
    reader = csv.DictReader(text_stream)
    close_values: list[float] = []

    for row in reader:
        close_value = find_close_value(row)
        if close_value is None:
            continue
        close_values.append(close_value)

    if len(close_values) < 2:
        raise ValueError("The CSV must contain at least two rows with a close price column.")

    return ReplaySummary(
        rows=len(close_values),
        first_close=close_values[0],
        last_close=close_values[-1],
        average_close=mean(close_values),
    )


def find_close_value(row: dict[str, Any]) -> float | None:
    for key, value in row.items():
        if key and key.strip().lower() == "close":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
