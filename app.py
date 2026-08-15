from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Callable
from urllib import error, parse, request
from uuid import uuid4

from flask import Flask, jsonify, render_template, request as flask_request


TRADELOCKER_BASE_URLS = {
    "demo": "https://demo.tradelocker.com/backend-api",
    "live": "https://live.tradelocker.com/backend-api",
}

SUPPORTED_RESOLUTIONS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1H": 3_600_000,
    "4H": 14_400_000,
    "1D": 86_400_000,
}


class TradeLockerApiError(Exception):
    def __init__(self, status_code: int, message: str, details: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.details = details


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


@dataclass
class TradeLockerSession:
    session_id: str
    environment: str
    username: str
    server: str
    access_token: str
    refresh_token: str | None
    accounts: list[dict[str, Any]]
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class BotRun:
    run_id: str
    session_id: str
    account_id: int
    symbol: str
    strategy: str
    quantity: float
    fast_period: int
    slow_period: int
    resolution: str
    poll_interval: int
    execution_mode: str
    stop_loss_percent: float = 0.0
    take_profit_percent: float = 0.0
    status: str = "starting"
    latest_signal: str = "hold"
    latest_price: float | None = None
    position_side: str = "flat"
    entry_price: float | None = None
    unrealized_pnl_percent: float = 0.0
    realized_pnl_percent: float = 0.0
    total_pnl_percent: float = 0.0
    execution_count: int = 0
    last_executed_signal: str | None = None
    last_error: str | None = None
    last_exit_reason: str | None = None
    last_execution: dict[str, Any] | None = None
    latest_analysis: dict[str, Any] | None = None
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    worker: threading.Thread | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "sessionId": self.session_id,
            "accountId": self.account_id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "quantity": self.quantity,
            "fastPeriod": self.fast_period,
            "slowPeriod": self.slow_period,
            "resolution": self.resolution,
            "pollInterval": self.poll_interval,
            "executionMode": self.execution_mode,
            "stopLossPercent": self.stop_loss_percent,
            "takeProfitPercent": self.take_profit_percent,
            "status": self.status,
            "latestSignal": self.latest_signal,
            "latestPrice": self.latest_price,
            "position": {
                "side": self.position_side,
                "entryPrice": self.entry_price,
            },
            "pnl": {
                "unrealizedPercent": self.unrealized_pnl_percent,
                "realizedPercent": self.realized_pnl_percent,
                "totalPercent": self.total_pnl_percent,
            },
            "executionCount": self.execution_count,
            "lastError": self.last_error,
            "lastExitReason": self.last_exit_reason,
            "lastExecution": self.last_execution,
            "latestAnalysis": self.latest_analysis,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
        }


class LocalStateStore:
    def __init__(self, file_path: str):
        self.path = Path(file_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def load(self) -> tuple[dict[str, TradeLockerSession], dict[str, BotRun]]:
        with self._lock:
            if not self.path.exists():
                return {}, {}
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}, {}

        sessions: dict[str, TradeLockerSession] = {}
        for session_payload in payload.get("sessions") or []:
            session = deserialize_session(session_payload)
            if session:
                sessions[session.session_id] = session

        runs: dict[str, BotRun] = {}
        for run_payload in payload.get("runs") or []:
            run = deserialize_run(run_payload)
            if run:
                runs[run.run_id] = run
        return sessions, runs

    def save(self, sessions: dict[str, TradeLockerSession], runs: dict[str, BotRun]) -> None:
        with self._lock:
            payload = {
                "sessions": [serialize_session_for_storage(session) for session in sessions.values()],
                "runs": [serialize_run_for_storage(run) for run in runs.values()],
            }
            data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            temp_path = self.path.with_suffix(".tmp")
            temp_path.write_text(data, encoding="utf-8")
            temp_path.replace(self.path)


class TradeLockerClient:
    def create_session(
        self,
        environment: str,
        username: str,
        password: str,
        server: str,
    ) -> TradeLockerSession:
        tokens = self._request_json(
            "POST",
            environment,
            "/auth/jwt/token",
            payload={"email": username, "password": password, "server": server},
        )
        access_token = tokens.get("accessToken")
        refresh_token = tokens.get("refreshToken")
        if not access_token:
            raise TradeLockerApiError(502, "TradeLocker did not return an access token.")

        accounts_response = self._request_json(
            "GET",
            environment,
            "/auth/jwt/all-accounts",
            access_token=access_token,
        )
        session = TradeLockerSession(
            session_id=str(uuid4()),
            environment=environment,
            username=username,
            server=server,
            access_token=access_token,
            refresh_token=refresh_token,
            accounts=self._normalize_accounts(accounts_response.get("accounts") or []),
        )
        return session

    def refresh_session(self, session: TradeLockerSession) -> TradeLockerSession:
        if not session.refresh_token:
            raise TradeLockerApiError(401, "TradeLocker session cannot be refreshed.")

        tokens = self._request_json(
            "POST",
            session.environment,
            "/auth/jwt/refresh",
            payload={"refreshToken": session.refresh_token},
        )
        access_token = tokens.get("accessToken")
        refresh_token = tokens.get("refreshToken")
        if not access_token:
            raise TradeLockerApiError(502, "TradeLocker refresh did not return an access token.")

        session.access_token = access_token
        session.refresh_token = refresh_token or session.refresh_token
        session.updated_at = time.time()
        return session

    def list_accounts(self, session: TradeLockerSession) -> list[dict[str, Any]]:
        response = self._request_with_session(session, "GET", "/auth/jwt/all-accounts")
        session.accounts = self._normalize_accounts(response.get("accounts") or [])
        session.updated_at = time.time()
        return session.accounts

    def get_market_snapshot(
        self,
        session: TradeLockerSession,
        account_id: int,
        symbol: str,
        resolution: str,
        bars: int,
    ) -> dict[str, Any]:
        account = self._find_account(session, account_id)
        instruments_response = self._request_with_session(
            session,
            "GET",
            f"/trade/accounts/{account_id}/instruments",
            acc_num=account["accNum"],
        )
        instrument = self._find_instrument(instruments_response.get("d", {}).get("instruments") or [], symbol)
        info_route_id = self._find_route_id(instrument, "INFO")
        trade_route_id = self._find_route_id(instrument, "TRADE")

        now_ms = int(time.time() * 1000)
        lookback_ms = resolution_to_millis(resolution) * max(bars, 10)
        history_response = self._request_with_session(
            session,
            "GET",
            "/trade/history",
            acc_num=account["accNum"],
            query={
                "tradableInstrumentId": instrument["tradableInstrumentId"],
                "routeId": info_route_id,
                "resolution": resolution,
                "from": now_ms - lookback_ms,
                "to": now_ms,
            },
        )

        close_values = extract_close_values(history_response.get("d", {}).get("barDetails") or [])
        if len(close_values) < max(3, bars):
            raise TradeLockerApiError(502, "TradeLocker did not return enough price history for this symbol.")

        return {
            "account": account,
            "instrumentId": int(instrument["tradableInstrumentId"]),
            "symbol": instrument.get("name") or symbol.upper(),
            "tradeRouteId": trade_route_id,
            "closeValues": close_values,
        }

    def place_market_order(
        self,
        session: TradeLockerSession,
        account_id: int,
        symbol: str,
        quantity: float,
        side: str,
    ) -> int | None:
        market_snapshot = self.get_market_snapshot(
            session=session,
            account_id=account_id,
            symbol=symbol,
            resolution="1m",
            bars=5,
        )
        account = market_snapshot["account"]
        response = self._request_with_session(
            session,
            "POST",
            f"/trade/accounts/{account_id}/orders",
            acc_num=account["accNum"],
            payload={
                "price": None,
                "qty": str(quantity),
                "routeId": market_snapshot["tradeRouteId"],
                "side": side,
                "tradableInstrumentId": str(market_snapshot["instrumentId"]),
                "type": "market",
                "validity": "IOC",
            },
        )
        order_id = response.get("d", {}).get("orderId")
        return int(order_id) if order_id is not None else None

    def _request_with_session(
        self,
        session: TradeLockerSession,
        method: str,
        path: str,
        *,
        acc_num: int | None = None,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self._request_json(
                method,
                session.environment,
                path,
                access_token=session.access_token,
                acc_num=acc_num,
                payload=payload,
                query=query,
            )
        except TradeLockerApiError as exc:
            if exc.status_code != 401 or not session.refresh_token:
                raise
            self.refresh_session(session)
            return self._request_json(
                method,
                session.environment,
                path,
                access_token=session.access_token,
                acc_num=acc_num,
                payload=payload,
                query=query,
            )

    def _request_json(
        self,
        method: str,
        environment: str,
        path: str,
        *,
        access_token: str | None = None,
        acc_num: int | None = None,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if environment not in TRADELOCKER_BASE_URLS:
            raise TradeLockerApiError(400, "Environment must be demo or live.")

        url = f"{TRADELOCKER_BASE_URLS[environment]}{path}"
        if query:
            url = f"{url}?{parse.urlencode(query)}"

        headers = {"Accept": "application/json"}
        developer_api_key = os.getenv("TRADELOCKER_DEVELOPER_API_KEY")
        if developer_api_key:
            headers["developer-api-key"] = developer_api_key
        if access_token:
            headers["Authorization"] = f"******"
        if acc_num is not None:
            headers["accNum"] = str(acc_num)

        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")

        api_request = request.Request(url, data=body, headers=headers, method=method)

        try:
            with request.urlopen(api_request, timeout=15) as response:
                raw_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")[:500]
            raise TradeLockerApiError(exc.code, "TradeLocker request failed.", details) from exc
        except error.URLError as exc:
            raise TradeLockerApiError(502, f"Could not reach TradeLocker: {exc.reason}") from exc

        try:
            return json.loads(raw_body or "{}")
        except json.JSONDecodeError as exc:
            raise TradeLockerApiError(502, "TradeLocker returned invalid JSON.") from exc

    def _normalize_accounts(self, accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_accounts: list[dict[str, Any]] = []
        for account in accounts:
            try:
                normalized_accounts.append(
                    {
                        "id": int(account["id"]),
                        "name": account.get("name") or f"Account {account['id']}",
                        "currency": account.get("currency") or "",
                        "accNum": int(account["accNum"]),
                        "accountBalance": float(account.get("accountBalance") or 0),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return normalized_accounts

    def _find_account(self, session: TradeLockerSession, account_id: int) -> dict[str, Any]:
        for account in session.accounts:
            if int(account["id"]) == int(account_id):
                return account
        raise TradeLockerApiError(404, "TradeLocker account was not found in the active session.")

    def _find_instrument(self, instruments: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
        normalized_symbol = normalize_symbol(symbol)
        for instrument in instruments:
            candidates = [
                instrument.get("name"),
                instrument.get("symbol"),
                instrument.get("ticker"),
            ]
            if any(normalize_symbol(candidate) == normalized_symbol for candidate in candidates if candidate):
                return instrument
        raise TradeLockerApiError(404, f"TradeLocker symbol '{symbol}' was not found for this account.")

    def _find_route_id(self, instrument: dict[str, Any], route_type: str) -> str:
        for route in instrument.get("routes") or []:
            if route.get("type") == route_type:
                return str(route.get("id"))
        raise TradeLockerApiError(502, f"TradeLocker did not return a {route_type} route for the symbol.")


def create_app(
    *,
    trade_client: TradeLockerClient | None = None,
    run_async: bool = True,
    state_file: str | None = None,
) -> Flask:
    app = Flask(__name__)
    client = trade_client or TradeLockerClient()
    default_state_file = os.path.join(app.root_path, "data", "state.json")
    state_store = LocalStateStore(state_file or default_state_file)
    sessions, runs = state_store.load()

    def persist_state() -> None:
        state_store.save(sessions, runs)

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.post("/api/tradelocker/session")
    def create_tradelocker_session():
        payload = flask_request.get_json(silent=True) or {}
        environment = payload.get("environment", "demo")
        username = (payload.get("username") or "").strip()
        password = payload.get("password") or ""
        server = (payload.get("server") or "").strip()

        if not username or not password or not server:
            return jsonify({"ok": False, "error": "Username, password, and server are required."}), 400

        try:
            session = client.create_session(environment, username, password, server)
        except TradeLockerApiError as exc:
            return error_response(exc)

        sessions[session.session_id] = session
        persist_state()
        return jsonify(
            {
                "ok": True,
                "message": "TradeLocker session created.",
                "session": serialize_session(session),
            }
        )

    @app.post("/api/tradelocker/test")
    def test_tradelocker_connection():
        return create_tradelocker_session()

    @app.get("/api/tradelocker/session/<session_id>")
    def get_tradelocker_session(session_id: str):
        session = sessions.get(session_id)
        if session is None:
            return jsonify({"ok": False, "error": "TradeLocker session not found."}), 404

        try:
            client.list_accounts(session)
        except TradeLockerApiError as exc:
            return error_response(exc)
        persist_state()

        return jsonify({"ok": True, "session": serialize_session(session)})

    @app.get("/api/tradelocker/session/<session_id>/accounts")
    def get_tradelocker_accounts(session_id: str):
        session = sessions.get(session_id)
        if session is None:
            return jsonify({"ok": False, "error": "TradeLocker session not found."}), 404

        try:
            accounts = client.list_accounts(session)
        except TradeLockerApiError as exc:
            return error_response(exc)
        persist_state()

        return jsonify({"ok": True, "accounts": accounts})

    @app.post("/api/fxreplay/test")
    def test_fxreplay_csv():
        uploaded_file = flask_request.files.get("file")
        strategy = (flask_request.form.get("strategy") or "rsi_macd").strip()
        symbol = (flask_request.form.get("symbol") or "EURUSD").strip()

        if uploaded_file is None or not uploaded_file.filename:
            return jsonify({"ok": False, "error": "Upload a CSV export from FX Replay."}), 400

        try:
            summary = summarize_replay_csv(uploaded_file.read())
        except ValueError:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "The CSV must be non-empty and include at least two rows with a close column.",
                    }
                ),
                400,
            )

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

    @app.post("/api/bot/start")
    def start_bot():
        payload = flask_request.get_json(silent=True) or {}
        session_id = (payload.get("sessionId") or "").strip()
        symbol = (payload.get("symbol") or "EURUSD").strip()
        strategy = (payload.get("strategy") or "moving_average").strip()
        execution_mode = (payload.get("executionMode") or "paper").strip().lower()

        session = sessions.get(session_id)
        if session is None:
            return jsonify({"ok": False, "error": "Create a TradeLocker session before starting the bot."}), 400

        try:
            account_id = int(payload.get("accountId"))
            quantity = float(payload.get("quantity") or 0.01)
            fast_period = int(payload.get("fastPeriod") or 5)
            slow_period = int(payload.get("slowPeriod") or 20)
            poll_interval = int(payload.get("pollInterval") or 15)
            stop_loss_value = payload.get("stopLossPercent", 0)
            take_profit_value = payload.get("takeProfitPercent", 0)
            stop_loss_percent = float(0 if stop_loss_value is None else stop_loss_value)
            take_profit_percent = float(0 if take_profit_value is None else take_profit_value)
        except (TypeError, ValueError):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": (
                            "Account, quantity, periods, poll interval, stop loss, and take profit "
                            "must be valid numbers."
                        ),
                    }
                ),
                400,
            )

        resolution = (payload.get("resolution") or "15m").strip()

        if execution_mode not in {"paper", "live"}:
            return jsonify({"ok": False, "error": "Execution mode must be paper or live."}), 400
        if resolution not in SUPPORTED_RESOLUTIONS:
            return jsonify({"ok": False, "error": "Unsupported resolution."}), 400
        if quantity <= 0:
            return jsonify({"ok": False, "error": "Quantity must be greater than zero."}), 400
        if fast_period < 2 or slow_period <= fast_period:
            return jsonify({"ok": False, "error": "Use a fast period >= 2 and a slow period larger than the fast period."}), 400
        if poll_interval < 5 or poll_interval > 300:
            return jsonify({"ok": False, "error": "Poll interval must be between 5 and 300 seconds."}), 400
        if stop_loss_percent < 0 or take_profit_percent < 0:
            return jsonify({"ok": False, "error": "Stop loss and take profit must be zero or positive."}), 400

        run = BotRun(
            run_id=str(uuid4()),
            session_id=session_id,
            account_id=account_id,
            symbol=symbol,
            strategy=strategy,
            quantity=quantity,
            fast_period=fast_period,
            slow_period=slow_period,
            resolution=resolution,
            poll_interval=poll_interval,
            execution_mode=execution_mode,
            stop_loss_percent=stop_loss_percent,
            take_profit_percent=take_profit_percent,
        )
        runs[run.run_id] = run
        persist_state()

        if run_async:
            worker = threading.Thread(
                target=run_bot_loop,
                args=(client, sessions, run, persist_state),
                daemon=True,
            )
            run.worker = worker
            worker.start()
        else:
            run.status = "running"
            try:
                execute_bot_iteration(client, sessions, run)
                persist_state()
            except TradeLockerApiError as exc:
                run.status = "error"
                run.last_error = exc.message
                persist_state()

        return jsonify({"ok": True, "message": "Bot runner started.", "run": run.to_dict()})

    @app.post("/api/bot/stop")
    def stop_bot():
        payload = flask_request.get_json(silent=True) or {}
        run_id = (payload.get("runId") or "").strip()
        run = runs.get(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "Bot run not found."}), 404

        run.stop_event.set()
        run.status = "stopped"
        run.updated_at = time.time()
        persist_state()
        return jsonify({"ok": True, "message": "Bot runner stopped.", "run": run.to_dict()})

    @app.get("/api/bot/status")
    def get_bot_status():
        run_id = (flask_request.args.get("runId") or "").strip()
        if run_id:
            run = runs.get(run_id)
            if run is None:
                return jsonify({"ok": False, "error": "Bot run not found."}), 404
            return jsonify({"ok": True, "run": run.to_dict()})

        return jsonify({"ok": True, "runs": [run.to_dict() for run in runs.values()]})

    @app.post("/api/bot/test")
    def test_bot_configuration():
        payload = flask_request.get_json(silent=True) or {}
        return jsonify(
            {
                "ok": True,
                "message": "Bot test configuration saved.",
                "testRun": {
                    "strategy": (payload.get("strategy") or "moving_average").strip(),
                    "symbol": (payload.get("symbol") or "EURUSD").strip(),
                    "source": (payload.get("source") or "TradeLocker").strip(),
                    "riskPercent": payload.get("riskPercent") or 1,
                    "status": "ready",
                },
            }
        )

    return app


def serialize_session(session: TradeLockerSession) -> dict[str, Any]:
    return {
        "sessionId": session.session_id,
        "environment": session.environment,
        "username": session.username,
        "server": session.server,
        "accounts": session.accounts,
        "createdAt": session.created_at,
        "updatedAt": session.updated_at,
    }


def serialize_session_for_storage(session: TradeLockerSession) -> dict[str, Any]:
    return {
        "sessionId": session.session_id,
        "environment": session.environment,
        "username": session.username,
        "server": session.server,
        "accessToken": session.access_token,
        "refreshToken": session.refresh_token,
        "accounts": session.accounts,
        "createdAt": session.created_at,
        "updatedAt": session.updated_at,
    }


def deserialize_session(payload: dict[str, Any]) -> TradeLockerSession | None:
    try:
        return TradeLockerSession(
            session_id=str(payload["sessionId"]),
            environment=str(payload["environment"]),
            username=str(payload["username"]),
            server=str(payload["server"]),
            access_token=str(payload["accessToken"]),
            refresh_token=payload.get("refreshToken"),
            accounts=list(payload.get("accounts") or []),
            created_at=float(payload.get("createdAt") or time.time()),
            updated_at=float(payload.get("updatedAt") or time.time()),
        )
    except (KeyError, TypeError, ValueError):
        return None


def serialize_run_for_storage(run: BotRun) -> dict[str, Any]:
    return {
        "runId": run.run_id,
        "sessionId": run.session_id,
        "accountId": run.account_id,
        "symbol": run.symbol,
        "strategy": run.strategy,
        "quantity": run.quantity,
        "fastPeriod": run.fast_period,
        "slowPeriod": run.slow_period,
        "resolution": run.resolution,
        "pollInterval": run.poll_interval,
        "executionMode": run.execution_mode,
        "stopLossPercent": run.stop_loss_percent,
        "takeProfitPercent": run.take_profit_percent,
        "status": run.status,
        "latestSignal": run.latest_signal,
        "latestPrice": run.latest_price,
        "positionSide": run.position_side,
        "entryPrice": run.entry_price,
        "unrealizedPnlPercent": run.unrealized_pnl_percent,
        "realizedPnlPercent": run.realized_pnl_percent,
        "totalPnlPercent": run.total_pnl_percent,
        "executionCount": run.execution_count,
        "lastExecutedSignal": run.last_executed_signal,
        "lastError": run.last_error,
        "lastExitReason": run.last_exit_reason,
        "lastExecution": run.last_execution,
        "latestAnalysis": run.latest_analysis,
        "startedAt": run.started_at,
        "updatedAt": run.updated_at,
    }


def deserialize_run(payload: dict[str, Any]) -> BotRun | None:
    try:
        run = BotRun(
            run_id=str(payload["runId"]),
            session_id=str(payload["sessionId"]),
            account_id=int(payload["accountId"]),
            symbol=str(payload["symbol"]),
            strategy=str(payload.get("strategy") or "moving_average"),
            quantity=float(payload.get("quantity") or 0.01),
            fast_period=int(payload.get("fastPeriod") or 5),
            slow_period=int(payload.get("slowPeriod") or 20),
            resolution=str(payload.get("resolution") or "15m"),
            poll_interval=int(payload.get("pollInterval") or 15),
            execution_mode=str(payload.get("executionMode") or "paper"),
            stop_loss_percent=max(float(payload.get("stopLossPercent") or 0.0), 0.0),
            take_profit_percent=max(float(payload.get("takeProfitPercent") or 0.0), 0.0),
            status=str(payload.get("status") or "stopped"),
            latest_signal=str(payload.get("latestSignal") or "hold"),
            latest_price=float(payload["latestPrice"]) if payload.get("latestPrice") is not None else None,
            position_side=str(payload.get("positionSide") or "flat"),
            entry_price=float(payload["entryPrice"]) if payload.get("entryPrice") is not None else None,
            unrealized_pnl_percent=float(payload.get("unrealizedPnlPercent") or 0.0),
            realized_pnl_percent=float(payload.get("realizedPnlPercent") or 0.0),
            total_pnl_percent=float(payload.get("totalPnlPercent") or 0.0),
            execution_count=int(payload.get("executionCount") or 0),
            last_executed_signal=payload.get("lastExecutedSignal"),
            last_error=payload.get("lastError"),
            last_exit_reason=payload.get("lastExitReason"),
            last_execution=payload.get("lastExecution"),
            latest_analysis=payload.get("latestAnalysis"),
            started_at=float(payload.get("startedAt") or time.time()),
            updated_at=float(payload.get("updatedAt") or time.time()),
        )
    except (KeyError, TypeError, ValueError):
        return None

    if run.status in {"running", "starting"}:
        run.status = "stopped"
    if run.position_side not in {"buy", "sell", "flat"}:
        run.position_side = "flat"
        run.entry_price = None
    update_unrealized_pnl(run)
    return run


def calculate_position_pnl_percent(side: str, entry_price: float, current_price: float) -> float:
    if entry_price == 0:
        return 0.0
    raw_move = ((current_price - entry_price) / entry_price) * 100
    return raw_move if side == "buy" else -raw_move


def update_unrealized_pnl(run: BotRun) -> None:
    if run.position_side not in {"buy", "sell"} or run.entry_price is None or run.latest_price is None:
        run.unrealized_pnl_percent = 0.0
    else:
        run.unrealized_pnl_percent = round(
            calculate_position_pnl_percent(run.position_side, run.entry_price, run.latest_price),
            5,
        )
    run.total_pnl_percent = round(run.realized_pnl_percent + run.unrealized_pnl_percent, 5)


def position_targets_hit(run: BotRun) -> tuple[bool, bool]:
    if run.position_side not in {"buy", "sell"} or run.entry_price is None or run.latest_price is None:
        return False, False
    pnl_percent = calculate_position_pnl_percent(run.position_side, run.entry_price, run.latest_price)
    hit_stop_loss = run.stop_loss_percent > 0 and pnl_percent <= -run.stop_loss_percent
    hit_take_profit = run.take_profit_percent > 0 and pnl_percent >= run.take_profit_percent
    return hit_stop_loss, hit_take_profit


def close_position(run: BotRun, reason: str, *, order_id: int | None, side: str) -> None:
    closed_pnl_percent = 0.0
    if run.position_side in {"buy", "sell"} and run.entry_price is not None and run.latest_price is not None:
        closed_pnl_percent = calculate_position_pnl_percent(run.position_side, run.entry_price, run.latest_price)
    run.realized_pnl_percent = round(run.realized_pnl_percent + closed_pnl_percent, 5)
    run.position_side = "flat"
    run.entry_price = None
    run.execution_count += 1
    run.last_executed_signal = side
    run.last_exit_reason = reason
    run.last_execution = {
        "side": side,
        "mode": run.execution_mode,
        "orderId": order_id,
        "price": run.latest_price,
        "timestamp": time.time(),
        "reason": reason,
        "closedPnlPercent": round(closed_pnl_percent, 5),
    }
    update_unrealized_pnl(run)
    run.updated_at = time.time()


def open_or_reverse_position(run: BotRun, signal: str, *, order_id: int | None) -> None:
    closed_pnl_percent = 0.0
    if run.position_side in {"buy", "sell"} and run.entry_price is not None and run.latest_price is not None:
        closed_pnl_percent = calculate_position_pnl_percent(run.position_side, run.entry_price, run.latest_price)
        run.realized_pnl_percent = round(run.realized_pnl_percent + closed_pnl_percent, 5)

    run.position_side = signal
    run.entry_price = run.latest_price
    run.execution_count += 1
    run.last_executed_signal = signal
    run.last_exit_reason = None
    run.last_execution = {
        "side": signal,
        "mode": run.execution_mode,
        "orderId": order_id,
        "price": run.latest_price,
        "timestamp": time.time(),
        "reason": "signal_change",
        "closedPnlPercent": round(closed_pnl_percent, 5),
    }
    update_unrealized_pnl(run)
    run.updated_at = time.time()


def run_bot_loop(
    client: TradeLockerClient,
    sessions: dict[str, TradeLockerSession],
    run: BotRun,
    persist_state: Callable[[], None],
) -> None:
    run.status = "running"
    run.updated_at = time.time()
    persist_state()
    while not run.stop_event.is_set():
        try:
            execute_bot_iteration(client, sessions, run)
            persist_state()
        except TradeLockerApiError as exc:
            run.status = "error"
            run.last_error = exc.message
            run.updated_at = time.time()
            persist_state()
            return

        if run.stop_event.wait(run.poll_interval):
            break

    if run.status != "error":
        run.status = "stopped"
        run.updated_at = time.time()
        persist_state()


def execute_bot_iteration(
    client: TradeLockerClient,
    sessions: dict[str, TradeLockerSession],
    run: BotRun,
) -> None:
    session = sessions.get(run.session_id)
    if session is None:
        raise TradeLockerApiError(404, "TradeLocker session is no longer available.")

    bars = max(run.slow_period + 2, 25)
    market_snapshot = client.get_market_snapshot(
        session=session,
        account_id=run.account_id,
        symbol=run.symbol,
        resolution=run.resolution,
        bars=bars,
    )
    close_values = market_snapshot["closeValues"]
    signal, fast_average, slow_average = moving_average_signal(
        close_values,
        fast_period=run.fast_period,
        slow_period=run.slow_period,
    )

    run.latest_signal = signal
    run.latest_price = round(close_values[-1], 5)
    run.latest_analysis = {
        "fastAverage": round(fast_average, 5),
        "slowAverage": round(slow_average, 5),
        "barsAnalyzed": len(close_values),
    }
    update_unrealized_pnl(run)
    run.updated_at = time.time()

    if run.position_side in {"buy", "sell"} and run.entry_price is not None:
        hit_stop_loss, hit_take_profit = position_targets_hit(run)
        if hit_stop_loss or hit_take_profit:
            close_reason = "stop_loss" if hit_stop_loss else "take_profit"
            close_side = "sell" if run.position_side == "buy" else "buy"
            order_id = None
            if run.execution_mode == "live":
                order_id = client.place_market_order(
                    session=session,
                    account_id=run.account_id,
                    symbol=run.symbol,
                    quantity=run.quantity,
                    side=close_side,
                )
            close_position(run, close_reason, order_id=order_id, side=close_side)
            return

    if signal == "hold" or signal == run.position_side:
        return

    order_id = None
    if run.execution_mode == "live":
        order_id = client.place_market_order(
            session=session,
            account_id=run.account_id,
            symbol=run.symbol,
            quantity=run.quantity,
            side=signal,
        )
    open_or_reverse_position(run, signal, order_id=order_id)


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


def extract_close_values(bar_details: list[Any]) -> list[float]:
    close_values: list[float] = []
    for bar in bar_details:
        close_value = None
        if isinstance(bar, dict):
            close_value = bar.get("c") if "c" in bar else bar.get("close")
        elif isinstance(bar, list) and len(bar) >= 5:
            close_value = bar[4]

        try:
            if close_value is not None:
                close_values.append(float(close_value))
        except (TypeError, ValueError):
            continue
    return close_values


def moving_average_signal(
    close_values: list[float],
    *,
    fast_period: int,
    slow_period: int,
) -> tuple[str, float, float]:
    fast_average = mean(close_values[-fast_period:])
    slow_average = mean(close_values[-slow_period:])
    if fast_average > slow_average:
        return "buy", fast_average, slow_average
    if fast_average < slow_average:
        return "sell", fast_average, slow_average
    return "hold", fast_average, slow_average


def resolution_to_millis(resolution: str) -> int:
    if resolution not in SUPPORTED_RESOLUTIONS:
        raise TradeLockerApiError(400, "Unsupported resolution.")
    return SUPPORTED_RESOLUTIONS[resolution]


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").replace("_", "").upper()


def error_response(exc: TradeLockerApiError):
    payload = {"ok": False, "error": exc.message}
    if exc.details:
        payload["details"] = exc.details
    return jsonify(payload), exc.status_code


app = create_app()


if __name__ == "__main__":
    app.run()
