"""Fail-closed admission checks; MT5 repeats sizing with OrderCalcProfit."""
import math
import re

def exposure(symbol, side):
    match=re.match(r'^(AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD|XAU|XAG|BTC|ETH)(AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD)',symbol.upper())
    if not match: return set()
    sign=1 if side=='BUY' else -1
    return {(match[1],sign),(match[2],-sign)}

def position_gate(config, snapshot, side):
    positions=snapshot.get('positions')
    if snapshot['positions_count'] and positions is None and (config.get('one_position_per_symbol') or config.get('block_shared_currency')):
        return 'برای کنترل نماد/ارز مشترک، کانکتور را به‌روز کنید'
    for p in positions or []:
        if config.get('one_position_per_symbol') and p['symbol']==snapshot['symbol']:
            return 'پوزیشن این نماد از قبل باز است'
        if config.get('block_shared_currency') and exposure(p['symbol'],p['side']) & exposure(snapshot['symbol'],side):
            return 'جهت ریسک مشترک ارز/فلز با پوزیشن باز وجود دارد'
    return None

def gate(config, snapshot, baseline, daily_start, now):
    if not config["running"]:
        return "ایجنت متوقف است"
    if config["mode"] == "signals":
        return "حالت فقط تحلیل فعال است"
    if now - snapshot["received_at"] > 15 or abs(now - snapshot["observed_at"]) > 15:
        return "داده حساب یا قیمت قدیمی است"
    if not snapshot["trade_allowed"]:
        return "معامله در ترمینال یا حساب مجاز نیست"
    if snapshot["is_demo"] and config["mode"] != "demo":
        return "نوع حساب با حالت انتخاب شده مطابقت ندارد"
    if not snapshot["is_demo"] and config["mode"] != "live":
        return "حساب واقعی در حالت دمو قابل معامله نیست"
    equity = snapshot["equity"]
    if equity <= 0 or baseline <= 0 or daily_start <= 0:
        return "مبنای سرمایه معتبر نیست"
    if equity <= daily_start * (1 - config["daily_loss_pct"] / 100):
        return "حد ضرر روزانه رسیده است"
    if equity <= baseline * (1 - config["total_loss_pct"] / 100):
        return "حد ضرر کل رسیده است"
    if snapshot["pending_orders"]:
        return "سفارش معلق در حساب وجود دارد"
    if snapshot["unprotected_positions"]:
        return "پوزیشن بدون حد ضرر یا محاسبه ریسک نامعتبر است"
    if snapshot["positions_count"] >= config["max_positions"]:
        return "سقف تعداد پوزیشن رسیده است"
    budget = equity * config["risk_pct"] / 100
    # Include a configurable buffer for commission/slippage. Gaps can exceed it.
    reserved = budget * (1 + config["cost_buffer_pct"] / 100)
    if snapshot["open_risk"] + reserved > equity * config["max_open_risk_pct"] / 100:
        return "سقف ریسک همزمان اجازه ورود نمی‌دهد"
    if snapshot["open_risk"] + reserved >= equity - daily_start * (1 - config["daily_loss_pct"] / 100):
        return "ریسک سفارش از بودجه ضرر روزانه باقی‌مانده بیشتر است"
    if snapshot["open_risk"] + reserved >= equity - baseline * (1 - config["total_loss_pct"] / 100):
        return "ریسک سفارش از بودجه ضرر کل باقی‌مانده بیشتر است"
    spread = snapshot["ask"] - snapshot["bid"]
    if spread / snapshot["point"] > config["max_spread_points"]:
        return "اسپرد از سقف تنظیم شده بیشتر است"
    return None

def market_levels(idea, snapshot, rr):
    entry = snapshot["ask"] if idea["side"] == "BUY" else snapshot["bid"]
    sign = 1 if idea["side"] == "BUY" else -1
    distance = max(1.5 * idea["atr"], (snapshot["stops_level"] + 2) * snapshot["point"] + snapshot["ask"] - snapshot["bid"])
    sl, tp = entry - sign * distance, entry + sign * rr * distance
    if min(sl, tp, entry) <= 0:
        raise ValueError("invalid price")
    return entry, sl, tp
