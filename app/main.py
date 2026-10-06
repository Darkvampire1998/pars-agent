import asyncio
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .db import initialize, transaction
from .risk import gate, market_levels
from .security import cipher, decrypt, digest, encrypt, hash_password, verify_password
from .strategies import CATALOG, analyze

ROOT = Path(__file__).parent
def uid(): return uuid4().hex
def js(v): return json.dumps(v, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
def now(): return time.time()
def live_allowed(): return os.environ.get("LIVE_TRADING_ALLOWED", "false").lower() == "true"

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Credentials(Model):
    email: str = Field(min_length=5, max_length=180, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=12, max_length=128)

class Config(Model):
    running: bool = False
    mode: Literal["signals", "demo", "live"] = "signals"
    strategies: list[Literal["ema_cross", "rsi_range", "donchian"]] = Field(default_factory=lambda: list(CATALOG), min_length=1, max_length=3)
    symbols: list[str] = Field(default_factory=lambda: ["EURUSD"], min_length=1, max_length=20)
    timeframe: Literal["M5", "M15", "H1"] = "M5"
    risk_pct: float = Field(default=0.5, ge=0.05, le=2)
    max_open_risk_pct: float = Field(default=2, ge=0.1, le=5)
    daily_loss_pct: float = Field(default=4, ge=0.5, le=5)
    total_loss_pct: float = Field(default=8, ge=1, le=10)
    max_positions: int = Field(default=3, ge=1, le=10)
    max_spread_points: int = Field(default=30, ge=1, le=10000)
    max_deviation_points: int = Field(default=10, ge=0, le=500)
    cost_buffer_pct: float = Field(default=20, ge=0, le=100)
    rr: float = Field(default=2, ge=1, le=5)
    @model_validator(mode="after")
    def valid(self):
        if self.risk_pct * (1 + self.cost_buffer_pct / 100) > self.max_open_risk_pct:
            raise ValueError("ریسک هر سفارش با ذخیره هزینه از ریسک همزمان بیشتر است")
        if self.daily_loss_pct >= self.total_loss_pct:
            raise ValueError("حد ضرر روزانه باید کمتر از حد ضرر کل باشد")
        if len(set(self.symbols)) != len(self.symbols) or any(not re.fullmatch(r"[A-Za-z0-9_.#-]{1,32}", s) for s in self.symbols):
            raise ValueError("نام نماد نامعتبر یا تکراری است")
        return self

class NewAccount(Model):
    name: str = Field(min_length=1, max_length=80)
    login: str = Field(pattern=r"^[0-9]{1,20}$")
    server: str = Field(min_length=1, max_length=100)
    initial_equity: float = Field(gt=0, le=1e12)

class Bar(Model):
    time: int = Field(gt=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    @model_validator(mode="after")
    def valid(self):
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close):
            raise ValueError("OHLC invalid")
        return self

class Deal(Model):
    ticket: str = Field(pattern=r"^[0-9]{1,24}$")
    time: float = Field(gt=0)
    symbol: str = Field(max_length=32)
    entry: Literal["in", "out", "inout", "out_by"]
    side: Literal["BUY", "SELL"]
    volume: float = Field(gt=0)
    price: float = Field(gt=0)
    profit: float
    commission: float
    swap: float

class Snapshot(Model):
    instance_id: str = Field(min_length=8, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    login: str = Field(pattern=r"^[0-9]{1,20}$")
    server: str = Field(min_length=1, max_length=100)
    symbol: str = Field(pattern=r"^[A-Za-z0-9_.#-]{1,32}$")
    timeframe: Literal["M5", "M15", "H1"]
    observed_at: float = Field(gt=0)
    quote_age: float = Field(ge=0)
    balance: float = Field(ge=0)
    equity: float = Field(ge=0)
    currency: str = Field(min_length=1, max_length=12)
    is_demo: bool
    trade_allowed: bool
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    point: float = Field(gt=0)
    digits: int = Field(ge=0, le=10)
    stops_level: int = Field(ge=0)
    open_risk: float = Field(ge=0)
    positions_count: int = Field(ge=0)
    unprotected_positions: int = Field(ge=0)
    pending_orders: int = Field(ge=0)
    bars: list[Bar] = Field(min_length=60, max_length=250)
    deals: list[Deal] = Field(default_factory=list, max_length=200)
    @model_validator(mode="after")
    def valid(self):
        if self.ask < self.bid or any(a.time >= b.time for a, b in zip(self.bars, self.bars[1:])):
            raise ValueError("Invalid quote or candle ordering")
        return self

class Result(Model):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal["executed", "rejected", "unknown"]
    ticket: str = Field(default="", max_length=24, pattern=r"^[0-9]*$")
    volume: float = Field(default=0, ge=0)
    price: float = Field(default=0, ge=0)
    reason: str = Field(default="", max_length=300)

class TelegramConfig(Model):
    token: str = Field(default="", max_length=150)
    chat_id: str = Field(pattern=r"^-?[0-9]{1,20}$")
    enabled: bool = True

def audit(db, user, account, event, data):
    db.execute("INSERT INTO audit(user_id,account_id,event,data,created) VALUES(?,?,?,?,?)", (user, account, event, js(data), now()))

def enqueue(db, user, message, key=None):
    db.execute("INSERT OR IGNORE INTO outbox(id,user_id,message,status,next_try) VALUES(?,?,?,'pending',?)", (key or uid(), user, message[:4000], now()))

def throttle(db, request, category, limit=15):
    ip = request.client.host if request.client else "unknown"
    if os.environ.get("TRUST_CADDY_CLIENT_IP") == "true":
        ip = request.headers.get("X-Pars-Client-IP", ip)
    key = digest(category + ":" + ip)
    row = db.execute("SELECT * FROM rate_limits WHERE key=?", (key,)).fetchone()
    if not row or row["reset"] < now():
        db.execute("INSERT OR REPLACE INTO rate_limits VALUES(?,1,?)", (key, now() + 60))
    elif row["count"] >= limit:
        raise HTTPException(429, "تعداد تلاش زیاد است؛ یک دقیقه دیگر تلاش کنید")
    else:
        db.execute("UPDATE rate_limits SET count=count+1 WHERE key=?", (key,))

def origin_check(request):
    origin = request.headers.get("origin")
    expected = os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
    if origin and origin != expected:
        raise HTTPException(403, "Origin rejected")

def user(request: Request):
    token = request.cookies.get("session", "")
    with transaction() as db:
        row = db.execute("SELECT s.*,u.email FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?", (digest(token), now())).fetchone()
        if not row:
            raise HTTPException(401, "ابتدا وارد شوید")
        if request.method not in ("GET", "HEAD"):
            origin_check(request)
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), row["csrf"]):
                raise HTTPException(403, "CSRF rejected")
        return dict(row)

def bridge(request: Request):
    token = request.headers.get("authorization", "")
    if not token.startswith("Bearer "):
        raise HTTPException(401, "Bridge key required")
    with transaction() as db:
        row = db.execute("SELECT * FROM accounts WHERE key_hash=?", (digest(token[7:]),)).fetchone()
        if not row:
            raise HTTPException(401, "Bridge key invalid")
        return row["id"]

def owned(db, aid, user_id):
    row = db.execute("SELECT * FROM accounts WHERE id=? AND user_id=?", (aid, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "حساب پیدا نشد")
    return dict(row)

def public_account(a):
    a = dict(a)
    a.pop("key_hash", None)
    a.pop("user_id", None)
    a["config"] = json.loads(a["config"])
    a["snapshot"] = json.loads(a["snapshot"]) if a["snapshot"] else None
    a["connected"] = bool(a["last_seen"] and now() - a["last_seen"] < 20)
    return a

def report(db, aid, since, until):
    rows = db.execute("SELECT data FROM deals WHERE account_id=? AND time>=? AND time<?", (aid, since, until)).fetchall()
    items = [json.loads(x[0]) for x in rows]
    net = sum(x["profit"] + x["commission"] + x["swap"] for x in items)
    exits = [x for x in items if x["entry"] in ("out", "inout", "out_by")]
    return {"net_realized": round(net, 2), "exit_deals": len(exits), "deals": items,
            "note": "خالص معاملات دریافت‌شده از ترمینال؛ خروج جزئی یک دیل جداست. کارمزد و سواپ منظور شده است."}

async def outbox_worker():
    while True:
        try:
            # A single worker is required: use one uvicorn process (see deployment).
            with transaction() as db:
                rows = [dict(r) for r in db.execute("SELECT o.*,t.token,t.chat_id,t.enabled FROM outbox o LEFT JOIN telegram t ON t.user_id=o.user_id WHERE o.status='pending' AND o.next_try<=? LIMIT 10", (now(),)).fetchall()]
                dt = datetime.now(timezone(timedelta(hours=3.5)))
                midnight = datetime(dt.year, dt.month, dt.day, tzinfo=dt.tzinfo).timestamp()
                report_day = (dt.date() - timedelta(days=1)).isoformat()
                for a in db.execute("SELECT a.* FROM accounts a JOIN telegram t ON t.user_id=a.user_id WHERE t.enabled=1 AND a.last_seen IS NOT NULL").fetchall():
                    key = "daily:" + a["id"] + report_day
                    if not db.execute("SELECT 1 FROM outbox WHERE id=?", (key,)).fetchone():
                        stats = report(db, a["id"], midnight - 86400, midnight)
                        enqueue(db, a["user_id"], f"گزارش روزانه {a['name']} | {report_day}\nخالص دیل‌های دریافت‌شده: {stats['net_realized']}\nتعداد دیل خروج: {stats['exit_deals']}", key)
            for r in rows:
                if not r["enabled"] or not r["token"]:
                    with transaction() as db:
                        db.execute("UPDATE outbox SET status='skipped' WHERE id=?", (r["id"],))
                    continue
                try:
                    token = decrypt(r["token"])
                    async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                        res = await client.post("https://api.telegram.org/bot" + token + "/sendMessage", json={"chat_id": r["chat_id"], "text": r["message"]})
                    ok = res.status_code == 200 and res.json().get("ok") is True
                    error = "" if ok else f"Telegram HTTP {res.status_code}"
                except Exception:
                    ok, error = False, "Telegram delivery failed"  # never log URL/token
                with transaction() as db:
                    attempts = r["attempts"] + 1
                    db.execute("UPDATE outbox SET status=?,attempts=?,next_try=?,last_error=? WHERE id=?", ("sent" if ok else ("failed" if attempts >= 5 else "pending"), attempts, now() + min(300, 2 ** attempts * 5), error, r["id"]))
            with transaction() as db:
                db.execute("DELETE FROM sessions WHERE expires<?", (now(),))
                db.execute("DELETE FROM rate_limits WHERE reset<?", (now() - 60,))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # failures leave durable work pending; health/queue visible in panel
        await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app):
    cipher()
    initialize()
    task = asyncio.create_task(outbox_worker())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Pars Agent", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)

@app.middleware("http")
async def headers(request, call_next):
    if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > 400_000:
        return PlainTextResponse("Payload too large", status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response

@app.get("/")
def index(): return FileResponse(ROOT / "static/index.html")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

@app.get("/api/health")
def health():
    with transaction() as db:
        db.execute("SELECT 1")
    return {"status": "ok", "version": "0.1.0"}

def session_response(db, user_id, response):
    raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(raw), user_id, csrf, now() + 12 * 3600))
    response.set_cookie("session", raw, httponly=True, secure=os.environ.get("COOKIE_SECURE", "true") == "true", samesite="strict", max_age=12 * 3600, path="/")
    return {"csrf": csrf}

@app.post("/api/register")
def register(c: Credentials, request: Request, response: Response):
    origin_check(request)
    if os.environ.get("REGISTRATION_ENABLED", "true") != "true":
        raise HTTPException(403, "ثبت نام بسته است")
    # Commit rate limit even on invalid credentials or conflict.
    with transaction() as db: throttle(db, request, "register", 5)
    with transaction() as db:
        user_id = uid()
        try:
            db.execute("INSERT INTO users VALUES(?,?,?,?)", (user_id, c.email.lower(), hash_password(c.password), now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "ثبت نام با این مشخصات امکان‌پذیر نیست")
        return session_response(db, user_id, response)

@app.post("/api/login")
def login(c: Credentials, request: Request, response: Response):
    origin_check(request)
    with transaction() as db: throttle(db, request, "login")
    with transaction() as db:
        row = db.execute("SELECT * FROM users WHERE email=?", (c.email.lower(),)).fetchone()
        # Same expensive path for unknown users.
        stored = row["password"] if row else "0" * 32 + ":" + "0" * 64
        if not verify_password(c.password, stored) or not row:
            raise HTTPException(401, "ایمیل یا رمز عبور نادرست است")
        return session_response(db, row["id"], response)

@app.get("/api/me")
def me(u=Depends(user)):
    with transaction() as db:
        tg = db.execute("SELECT chat_id,enabled FROM telegram WHERE user_id=?", (u["user_id"],)).fetchone()
    return {"email": u["email"], "csrf": u["csrf"], "live_allowed": live_allowed(), "telegram": dict(tg) if tg else None, "strategies": CATALOG}

@app.post("/api/logout")
def logout(response: Response, u=Depends(user)):
    with transaction() as db: db.execute("DELETE FROM sessions WHERE token=?", (u["token"],))
    response.delete_cookie("session", path="/")
    return {"ok": True}

@app.get("/api/accounts")
def accounts(u=Depends(user)):
    with transaction() as db:
        return [public_account(a) for a in db.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY rowid", (u["user_id"],)).fetchall()]

@app.post("/api/accounts")
def add_account(data: NewAccount, u=Depends(user)):
    raw, aid = secrets.token_urlsafe(36), uid()
    with transaction() as db:
        if db.execute("SELECT COUNT(*) FROM accounts WHERE user_id=?", (u["user_id"],)).fetchone()[0] >= 10:
            raise HTTPException(400, "سقف ۱۰ حساب برای هر کاربر")
        try:
            db.execute("INSERT INTO accounts(id,user_id,name,login,server,key_hash,config,baseline) VALUES(?,?,?,?,?,?,?,?)", (aid, u["user_id"], data.name, data.login, data.server, digest(raw), js(Config().model_dump()), data.initial_equity))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "این حساب قبلاً متصل شده است")
        audit(db, u["user_id"], aid, "account.created", {})
    return {"id": aid, "bridge_key": raw}

@app.put("/api/accounts/{aid}/config")
def configure(aid: str, config: Config, u=Depends(user)):
    if config.mode == "live" and not live_allowed():
        raise HTTPException(403, "مدیر سرور هنوز معامله واقعی را فعال نکرده است")
    with transaction() as db:
        a = owned(db, aid, u["user_id"])
        snapshot = json.loads(a["snapshot"]) if a["snapshot"] else None
        if config.running and config.mode != "signals" and (not snapshot or now() - a["last_seen"] > 20):
            raise HTTPException(409, "ابتدا ترمینال متاتریدر را متصل کنید")
        db.execute("UPDATE accounts SET config=? WHERE id=?", (js(config.model_dump()), aid))
        db.execute("UPDATE orders SET status='cancelled',updated=? WHERE account_id=? AND status='prepared'", (now(), aid))
        audit(db, u["user_id"], aid, "config.updated", config.model_dump())
    return {"ok": True}

@app.post("/api/accounts/{aid}/stop")
def stop(aid: str, u=Depends(user)):
    with transaction() as db:
        a = owned(db, aid, u["user_id"])
        c = json.loads(a["config"]); c["running"] = False
        db.execute("UPDATE accounts SET config=? WHERE id=?", (js(c), aid))
        db.execute("UPDATE orders SET status='cancelled',updated=? WHERE account_id=? AND status='prepared'", (now(), aid))
        audit(db, u["user_id"], aid, "agent.stopped", {})
    return {"ok": True, "note": "ورود جدید متوقف شد؛ پوزیشن‌های موجود با SL/TP خود ادامه دارند"}

@app.post("/api/accounts/{aid}/rotate-key")
def rotate(aid: str, u=Depends(user)):
    raw = secrets.token_urlsafe(36)
    with transaction() as db:
        a = owned(db, aid, u["user_id"])
        c = json.loads(a["config"]); c["running"] = False
        if db.execute("SELECT 1 FROM orders WHERE account_id=? AND status IN ('claimed','unknown')", (aid,)).fetchone():
            raise HTTPException(409, "ابتدا سفارش نامشخص را با بروکر تطبیق دهید")
        db.execute("UPDATE accounts SET key_hash=?,config=?,instance_id=NULL WHERE id=?", (digest(raw), js(c), aid))
        db.execute("UPDATE orders SET status='cancelled',updated=? WHERE account_id=? AND status='prepared'", (now(), aid))
        audit(db, u["user_id"], aid, "bridge.rotated", {})
    return {"bridge_key": raw}

@app.get("/api/accounts/{aid}/dashboard")
def dashboard(aid: str, u=Depends(user)):
    with transaction() as db:
        a = owned(db, aid, u["user_id"])
        return {"account": public_account(a), "markets": [json.loads(r[0]) for r in db.execute("SELECT data FROM markets WHERE account_id=?", (aid,)).fetchall()],
                "ideas": [json.loads(r[0]) for r in db.execute("SELECT data FROM ideas WHERE account_id=? ORDER BY created DESC LIMIT 30", (aid,)).fetchall()],
                "orders": [dict(r) | {"data": json.loads(r["data"]), "result": json.loads(r["result"]) if r["result"] else None} for r in db.execute("SELECT * FROM orders WHERE account_id=? ORDER BY created DESC LIMIT 50", (aid,)).fetchall()],
                "audit": [dict(r) for r in db.execute("SELECT event,created FROM audit WHERE account_id=? ORDER BY id DESC LIMIT 30", (aid,)).fetchall()]}

@app.get("/api/accounts/{aid}/report")
def account_report(aid: str, u=Depends(user)):
    with transaction() as db:
        owned(db, aid, u["user_id"])
        result = report(db, aid, now() - 30 * 86400, now())
        result["deals"] = result["deals"][-500:]
        return result

@app.put("/api/telegram")
def save_telegram(c: TelegramConfig, u=Depends(user)):
    if c.token and not re.fullmatch(r"[0-9]{5,16}:[A-Za-z0-9_-]{20,100}", c.token):
        raise HTTPException(400, "توکن تلگرام معتبر نیست")
    with transaction() as db:
        old = db.execute("SELECT token FROM telegram WHERE user_id=?", (u["user_id"],)).fetchone()
        token = encrypt(c.token) if c.token else (old[0] if old else None)
        if not token: raise HTTPException(400, "توکن ربات لازم است")
        db.execute("INSERT OR REPLACE INTO telegram VALUES(?,?,?,?)", (u["user_id"], token, c.chat_id, c.enabled))
        audit(db, u["user_id"], None, "telegram.updated", {"enabled": c.enabled})
    return {"ok": True}

@app.get("/api/telegram/queue")
def queue(u=Depends(user)):
    with transaction() as db:
        return [dict(r) for r in db.execute("SELECT id,status,attempts,last_error FROM outbox WHERE user_id=? ORDER BY rowid DESC LIMIT 15", (u["user_id"],)).fetchall()]

@app.post("/api/telegram/test")
def telegram_test(u=Depends(user)):
    with transaction() as db:
        if not db.execute("SELECT 1 FROM telegram WHERE user_id=? AND enabled=1", (u["user_id"],)).fetchone():
            raise HTTPException(400, "ابتدا تلگرام را تنظیم و فعال کنید")
        enqueue(db, u["user_id"], "اتصال گزارش‌های Pars Agent برقرار است. این پیام تست اتصال است.")
    return {"ok": True, "note": "در صف ارسال قرار گرفت؛ وضعیت ارسال را بررسی کنید"}

@app.post("/api/accounts/{aid}/telegram-report")
def telegram_report(aid: str, u=Depends(user)):
    with transaction() as db:
        a = owned(db, aid, u["user_id"])
        if not db.execute("SELECT 1 FROM telegram WHERE user_id=? AND enabled=1", (u["user_id"],)).fetchone():
            raise HTTPException(400, "تلگرام فعال نیست")
        stats = report(db, aid, now() - 86400, now())
        enqueue(db, u["user_id"], f"گزارش ۲۴ ساعت {a['name']}\nخالص دیل‌ها: {stats['net_realized']}\nتعداد دیل خروج: {stats['exit_deals']}")
    return {"ok": True}

@app.get("/api/bridge/config")
def bridge_config(aid=Depends(bridge)):
    with transaction() as db:
        a = db.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
        return json.loads(a["config"])

def accept_snapshot(db, aid, s):
    a = dict(db.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone())
    c = json.loads(a["config"])
    if s.login != a["login"] or s.server != a["server"]:
        raise HTTPException(403, "حساب ترمینال با حساب ثبت‌شده تطابق ندارد")
    if a["instance_id"] and a["instance_id"] != s.instance_id:
        raise HTTPException(409, "این حساب به یک ترمینال دیگر متصل است؛ ابتدا توقف و تعویض کلید انجام دهید")
    if s.symbol not in c["symbols"] or s.timeframe != c["timeframe"]:
        raise HTTPException(400, "نماد یا تایم‌فریم در تنظیمات حساب فعال نیست")
    if abs(now() - s.observed_at) > 15 or s.quote_age > 15:
        raise HTTPException(409, "ساعت ترمینال یا قیمت قدیمی است")
    data = s.model_dump()
    data["received_at"] = now()
    day = datetime.now(timezone.utc).date().isoformat()
    if a["day"] != day:
        previous = json.loads(a["snapshot"]) if a["snapshot"] else {}
        a["day_start"] = max(s.balance, s.equity, previous.get("equity", 0))
        a["day"] = day
    db.execute("UPDATE accounts SET snapshot=?,last_seen=?,day=?,day_start=?,instance_id=? WHERE id=?", (js({k: v for k, v in data.items() if k not in ("bars", "deals")}), now(), a["day"], a["day_start"], s.instance_id, aid))
    market = {"symbol": s.symbol, "timeframe": s.timeframe, "bars": data["bars"], "bid": s.bid, "ask": s.ask, "received_at": now()}
    db.execute("INSERT OR REPLACE INTO markets VALUES(?,?,?,?)", (aid, s.symbol, s.timeframe, js(market)))
    for deal in s.deals:
        d = deal.model_dump()
        inserted = db.execute("INSERT OR IGNORE INTO deals VALUES(?,?,?,?,?)", (aid, d["ticket"], d["time"], d["symbol"], js(d))).rowcount
        if inserted and now() - d["time"] < 120:
            net = d["profit"] + d["commission"] + d["swap"]
            enqueue(db, a["user_id"], f"دیل متاتریدر | {a['name']}\n{d['symbol']} {d['side']} · {d['entry']}\nحجم: {d['volume']} · قیمت: {d['price']}\nخالص: {net:.2f} {s.currency}", "deal:" + aid + ":" + d["ticket"])
    return a, c, data

def admission(db, a, c, data, exclude=None):
    if c["mode"] == "live" and not live_allowed(): return "معامله واقعی در سرور بسته است"
    if db.execute("SELECT 1 FROM orders WHERE account_id=? AND status IN ('claimed','unknown','prepared') AND id!=?", (a["id"], exclude or "")).fetchone():
        return "سفارش در انتظار اجرا یا تطبیق با بروکر است"
    return gate(c, data, a["baseline"], a["day_start"], now())

@app.post("/api/bridge/poll")
def poll(s: Snapshot, aid=Depends(bridge)):
    with transaction() as db:
        a, c, data = accept_snapshot(db, aid, s)
        ideas = []
        for strategy in c["strategies"]:
            idea = analyze(data["bars"], strategy, c["rr"])
            idea.update({"id": uid(), "symbol": s.symbol, "timeframe": s.timeframe, "strategy": strategy, "candle": s.bars[-1].time, "created": now()})
            if idea["side"] != "WAIT":
                idea["entry"], idea["sl"], idea["tp"] = market_levels(idea, data, c["rr"])
            inserted = db.execute("INSERT OR IGNORE INTO ideas VALUES(?,?,?,?,?,?,?,?)", (idea["id"], aid, s.symbol, s.timeframe, s.bars[-1].time, strategy, js(idea), now())).rowcount
            if inserted: ideas.append(idea)
        error = admission(db, a, c, data)
        candidates = [x for x in ideas if x["side"] != "WAIT"]
        if len({x["side"] for x in candidates}) > 1:
            error = "استراتژی‌ها جهت‌های متضاد دارند"
        if candidates and not error:
            idea = candidates[0]
            order = {"id": uid(), "symbol": s.symbol, "side": idea["side"], "entry": idea["entry"], "sl": idea["sl"], "tp": idea["tp"], "risk_money": s.equity * c["risk_pct"] / 100, "risk_pct": c["risk_pct"], "max_spread_points": c["max_spread_points"], "max_deviation_points": c["max_deviation_points"], "expires_at": now() + 12,
                     "max_open_risk_pct": c["max_open_risk_pct"], "max_positions": c["max_positions"], "cost_buffer_pct": c["cost_buffer_pct"], "daily_floor": a["day_start"] * (1 - c["daily_loss_pct"] / 100), "total_floor": a["baseline"] * (1 - c["total_loss_pct"] / 100), "mode": c["mode"]}
            db.execute("INSERT INTO orders VALUES(?,?,?,'prepared',?,?,?,NULL)", (order["id"], aid, idea["id"], js(order), now(), now()))
        # Return an existing unclaimed order only on its originating symbol; never resend claimed.
        row = db.execute("SELECT data FROM orders WHERE account_id=? AND status='prepared'", (aid,)).fetchone()
        order = json.loads(row[0]) if row else None
        if order and order["expires_at"] < now():
            db.execute("UPDATE orders SET status='expired',updated=? WHERE id=?", (now(), order["id"])); order = None
        if order and order["symbol"] != s.symbol: order = None
        return {"order_id": order["id"] if order else "", "reason": error or ("سفارش آماده است" if order else "شرایط ورود تکمیل نیست")}

@app.post("/api/bridge/claim/{oid}")
def claim(oid: str, s: Snapshot, aid=Depends(bridge)):
    with transaction() as db:
        a, c, data = accept_snapshot(db, aid, s)
        row = db.execute("SELECT * FROM orders WHERE id=? AND account_id=?", (oid, aid)).fetchone()
        if not row or row["status"] != "prepared": raise HTTPException(409, "سفارش قبلاً مصرف یا لغو شده است")
        order = json.loads(row["data"])
        if order["symbol"] != s.symbol or order["expires_at"] < now(): raise HTTPException(409, "سفارش منقضی یا نماد متفاوت است")
        error = admission(db, a, c, data, oid)
        if error: raise HTTPException(409, error)
        # Config changes cancel prepared orders. Verify drift before handing off.
        entry = s.ask if order["side"] == "BUY" else s.bid
        if abs(entry - order["entry"]) / s.point > order["max_deviation_points"]:
            raise HTTPException(409, "قیمت از محدوده مجاز لغزش خارج شده است")
        db.execute("UPDATE orders SET status='claimed',updated=? WHERE id=?", (now(), oid))
        audit(db, a["user_id"], aid, "order.claimed", {"id": oid})
        return order

@app.post("/api/bridge/result")
def result(r: Result, aid=Depends(bridge)):
    with transaction() as db:
        a = db.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
        row = db.execute("SELECT * FROM orders WHERE id=? AND account_id=?", (r.id, aid)).fetchone()
        if not row: raise HTTPException(404, "سفارش پیدا نشد")
        if row["status"] in ("executed", "rejected"):
            if row["result"] == js(r.model_dump()): return {"ok": True}
            raise HTTPException(409, "نتیجه قطعی قبلاً ثبت شده است")
        if row["status"] not in ("claimed", "unknown"): raise HTTPException(409, "سفارش ارسال نشده است")
        db.execute("UPDATE orders SET status=?,result=?,updated=? WHERE id=?", (r.status, js(r.model_dump()), now(), r.id))
        audit(db, a["user_id"], aid, "order." + r.status, {"id": r.id, "ticket": r.ticket})
        enqueue(db, a["user_id"], f"سفارش {a['name']} · {r.status}\nشناسه: {r.id}\nتیکت: {r.ticket or 'نامشخص'}\nحجم: {r.volume}\n{r.reason}", "order:" + r.id + ":" + r.status)
    return {"ok": True}

class Reconcile(Result):
    broker_checked: Literal[True]

@app.post("/api/accounts/{aid}/reconcile")
def reconcile(aid: str, r: Reconcile, u=Depends(user)):
    if r.status == "unknown": raise HTTPException(400, "نتیجه قطعی بروکر لازم است")
    with transaction() as db:
        a = owned(db, aid, u["user_id"])
        if json.loads(a["config"])["running"]: raise HTTPException(409, "ابتدا ایجنت را متوقف کنید")
        row = db.execute("SELECT * FROM orders WHERE id=? AND account_id=? AND status IN ('claimed','unknown')", (r.id, aid)).fetchone()
        if not row: raise HTTPException(404, "سفارش معلق پیدا نشد")
        db.execute("UPDATE orders SET status=?,result=?,updated=? WHERE id=?", (r.status, js(r.model_dump(exclude={"broker_checked"})), now(), r.id))
        audit(db, u["user_id"], aid, "order.manually_reconciled", r.model_dump())
    return {"ok": True}
