import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.db import transaction
from app.main import app, Config
from app.risk import gate
from app.strategies import analyze

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("LIVE_TRADING_ALLOWED", "false")
    # No external messages during tests. Queue contents are inspected directly.
    async def worker():
        import asyncio
        await asyncio.Future()
    monkeypatch.setattr("app.main.outbox_worker", worker)
    with TestClient(app) as c:
        yield c

def signup(client, email="ali@example.test"):
    r = client.post("/api/register", json={"email": email, "password": "A-strong-password-2026"})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf"]}

def new_account(client, h, login="12345"):
    r = client.post("/api/accounts", headers=h, json={"name": "Demo", "login": login, "server": "Broker-Demo", "initial_equity": 10000})
    assert r.status_code == 200, r.text
    v = r.json()
    # Existing protocol fixtures exercise legacy M5/priority admission. New-account
    # conservative defaults and adaptive filters have separate tests below.
    assert client.put(f"/api/accounts/{v['id']}/config",headers=h,json=Config().model_dump()).status_code==200
    return v["id"], {"Authorization": "Bearer " + v["bridge_key"]}

def snapshot():
    stamp = int(time.time()) - 900
    bars = [{"time": stamp - (119-i)*300, "open": 1.1, "high": 1.1002, "low": 1.0998, "close": 1.1} for i in range(120)]
    return {"instance_id": "terminal12345", "login": "12345", "server": "Broker-Demo", "symbol": "EURUSD", "timeframe": "M5", "observed_at": time.time(), "quote_age": 0,
            "balance": 10000, "equity": 10000, "currency": "USD", "is_demo": True, "trade_allowed": True, "bid": 1.1, "ask": 1.1001, "point": 0.00001,
            "digits": 5, "stops_level": 10, "open_risk": 0, "positions_count": 0, "unprotected_positions": 0, "pending_orders": 0, "bars": bars, "deals": []}

def ready(client):
    h = signup(client)
    aid, bh = new_account(client, h)
    s = snapshot()
    assert client.post("/api/bridge/poll", headers=bh, json=s).status_code == 200
    c = client.get("/api/accounts").json()[0]["config"]
    c.update({"running": True, "mode": "demo", "strategies": ["donchian"]})
    assert client.put(f"/api/accounts/{aid}/config", headers=h, json=c).status_code == 200
    s["bars"][-1].update({"time": s["bars"][-1]["time"]+300, "close": 1.2, "high": 1.2002})
    s.update({"bid": 1.2, "ask": 1.2001})
    return h, aid, bh, s

def prepared(client):
    h, aid, bh, s = ready(client)
    r = client.post("/api/bridge/poll", headers=bh, json=s)
    assert r.status_code == 200, r.text
    oid = r.json()["order_id"]
    assert oid
    return h, aid, bh, s, oid

def test_health_auth_and_password_storage(client):
    assert client.get("/api/health").json()["status"] == "ok"
    h = signup(client)
    assert client.get("/api/me").json()["email"] == "ali@example.test"
    with transaction() as db:
        assert "A-strong-password" not in db.execute("SELECT password FROM users").fetchone()[0]
    assert client.post("/api/logout", headers=h).status_code == 200
    assert client.get("/api/accounts").status_code == 401
    assert client.post("/api/login", json={"email": "ali@example.test", "password": "A-strong-password-2026"}).status_code == 200

def test_csrf_and_foreign_origin(client):
    h = signup(client)
    body = {"name": "Demo", "login": "123", "server": "Demo", "initial_equity": 10000}
    assert client.post("/api/accounts", json=body).status_code == 403
    assert client.post("/api/accounts", headers=h | {"Origin": "https://evil.test"}, json=body).status_code == 403

def test_tenant_isolation_and_bridge_ownership(client):
    h = signup(client)
    aid, bh = new_account(client, h)
    signup(client, "other@example.test")
    assert client.get("/api/accounts").json() == []
    assert client.get(f"/api/accounts/{aid}/dashboard").status_code == 404
    assert client.get(f"/api/accounts/{aid}/report").status_code == 404
    s = snapshot(); s["login"] = "999"
    assert client.post("/api/bridge/poll", headers=bh, json=s).status_code == 403
    assert client.post("/api/bridge/poll", headers={"Authorization": "Bearer invalid"}, json=s).status_code == 401

def test_terminal_binding_and_duplicate_account(client):
    h = signup(client); aid, bh = new_account(client, h)
    s = snapshot()
    assert client.post("/api/bridge/poll", headers=bh, json=s).status_code == 200
    s["instance_id"] = "otherterminal"
    assert client.post("/api/bridge/poll", headers=bh, json=s).status_code == 409
    r = client.post("/api/accounts", headers=h, json={"name": "Duplicate", "login": "12345", "server": "Broker-Demo", "initial_equity": 10000})
    assert r.status_code == 409

def test_duplicate_bar_produces_one_order_and_claim_is_once(client):
    h, aid, bh, s, oid = prepared(client)
    for _ in range(3):
        assert client.post("/api/bridge/poll", headers=bh, json=s).json()["order_id"] == oid
    assert len(client.get(f"/api/accounts/{aid}/dashboard").json()["orders"]) == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(f"/api/bridge/claim/{oid}", headers=bh, json=s).status_code, range(2)))
    assert sorted(results) == [200, 409]
    assert client.post("/api/bridge/poll", headers=bh, json=s).json()["order_id"] == ""

def test_stop_cancels_prepared_and_blocks_claim(client):
    h, aid, bh, s, oid = prepared(client)
    assert client.post(f"/api/accounts/{aid}/stop", headers=h).status_code == 200
    assert client.post(f"/api/bridge/claim/{oid}", headers=bh, json=s).status_code == 409
    assert client.get(f"/api/accounts/{aid}/dashboard").json()["orders"][0]["status"] == "cancelled"

def test_risk_rechecked_at_claim(client):
    h, aid, bh, s, oid = prepared(client)
    s["equity"] = 9500
    assert client.post(f"/api/bridge/claim/{oid}", headers=bh, json=s).status_code == 409

def test_uncertain_order_blocks_other_symbols_until_manual_reconciliation(client):
    h, aid, bh, s, oid = prepared(client)
    assert client.post(f"/api/bridge/claim/{oid}", headers=bh, json=s).status_code == 200
    result = {"id": oid, "status": "unknown", "reason": "Broker timeout"}
    assert client.post("/api/bridge/result", headers=bh, json=result).status_code == 200
    c = client.get("/api/accounts").json()[0]["config"]; c["symbols"].append("XAUUSD")
    assert client.put(f"/api/accounts/{aid}/config", headers=h, json=c).status_code == 200
    second = copy.deepcopy(s); second["symbol"] = "XAUUSD"
    assert client.post("/api/bridge/poll", headers=bh, json=second).json()["order_id"] == ""
    checked = {"id": oid, "status": "rejected", "reason": "Checked broker history", "broker_checked": True}
    assert client.post(f"/api/accounts/{aid}/reconcile", headers=h, json=checked).status_code == 409
    client.post(f"/api/accounts/{aid}/stop", headers=h)
    assert client.post(f"/api/accounts/{aid}/reconcile", headers=h, json=checked).status_code == 200

def test_result_replay_is_idempotent_and_cross_account_rejected(client):
    h, aid, bh, s, oid = prepared(client)
    client.post(f"/api/bridge/claim/{oid}", headers=bh, json=s)
    result = {"id": oid, "status": "executed", "ticket": "87654", "volume": 0.1, "price": 1.2001}
    assert client.post("/api/bridge/result", headers=bh, json=result).status_code == 200
    assert client.post("/api/bridge/result", headers=bh, json=result).status_code == 200
    other, otherbh = new_account(client, h, "67890")
    assert client.post("/api/bridge/result", headers=otherbh, json=result).status_code == 404
    assert len([x for x in client.get("/api/telegram/queue").json() if x["id"] == "order:"+oid+":executed"]) == 1

def test_stale_quote_nan_and_unordered_bars_rejected(client):
    h = signup(client); aid, bh = new_account(client, h)
    s = snapshot(); s["quote_age"] = 60
    assert client.post("/api/bridge/poll", headers=bh, json=s).status_code == 409
    s = snapshot(); s["bars"][-1]["time"] = s["bars"][-2]["time"]
    assert client.post("/api/bridge/poll", headers=bh, json=s).status_code == 422
    s = snapshot(); s["equity"] = "NaN"
    assert client.post("/api/bridge/poll", headers=bh, json=s).status_code == 422

def test_live_requires_server_switch(client):
    h, aid, bh, s = ready(client)
    c = client.get("/api/accounts").json()[0]["config"]; c["mode"] = "live"
    assert client.put(f"/api/accounts/{aid}/config", headers=h, json=c).status_code == 403

def test_deals_deduplicate_and_include_costs(client):
    h = signup(client); aid, bh = new_account(client, h)
    s = snapshot(); s["deals"] = [{"ticket": "111", "time": time.time()-30, "symbol": "EURUSD", "entry": "out", "side": "SELL", "volume": 0.1, "price": 1.1, "profit": 50, "commission": -3, "swap": -2}]
    for _ in range(3): assert client.post("/api/bridge/poll", headers=bh, json=s).status_code == 200
    r = client.get(f"/api/accounts/{aid}/report").json()
    assert r["net_realized"] == 45
    assert r["exit_deals"] == 1
    assert len(r["deals"]) == 1

def test_telegram_token_encrypted_and_never_returned(client):
    h = signup(client)
    token = "123456789:" + "x" * 30
    assert client.put("/api/telegram", headers=h, json={"token": token, "chat_id": "123456"}).status_code == 200
    assert token not in client.get("/api/me").text
    with transaction() as db:
        assert token not in db.execute("SELECT token FROM telegram").fetchone()[0]
    assert client.post("/api/telegram/test", headers=h).status_code == 200
    assert client.get("/api/telegram/queue").json()[0]["status"] == "pending"

@pytest.mark.parametrize("field,value", [("unprotected_positions",1),("pending_orders",1),("positions_count",3),("open_risk",195),("trade_allowed",False),("is_demo",False),("equity",9200),("quote_age",40)])
def test_entry_blockers_do_not_create_orders(client, field, value):
    h, aid, bh, s = ready(client); s[field] = value
    r = client.post("/api/bridge/poll", headers=bh, json=s)
    assert r.status_code in (200,409)
    assert client.get(f"/api/accounts/{aid}/dashboard").json()["orders"] == []

def test_strategy_no_future_leak_and_actual_breakout():
    s = snapshot(); bars = s["bars"]
    assert analyze(bars,"donchian")["side"] == "WAIT"
    bars[-1].update({"close": 1.2, "high": 1.2001})
    a = analyze(bars,"donchian")
    assert a["side"] == "BUY" and a["sl"] < a["entry"] < a["tp"]
    assert analyze(bars[:-1],"donchian")["side"] == "WAIT"

def test_config_validation_and_security_headers(client):
    h = signup(client); aid,bh = new_account(client,h)
    c = client.get("/api/accounts").json()[0]["config"]; c["risk_pct"] = 2; c["max_open_risk_pct"] = 1
    assert client.put(f"/api/accounts/{aid}/config", headers=h, json=c).status_code == 422
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"

def test_login_throttles_failed_attempts(client):
    signup(client)
    body = {"email": "ali@example.test", "password": "incorrect-password"}
    for _ in range(15): assert client.post("/api/login", json=body).status_code == 401
    assert client.post("/api/login", json=body).status_code == 429
