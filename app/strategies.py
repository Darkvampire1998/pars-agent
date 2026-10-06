"""Deterministic, closed-candle strategies. Scores are not win probabilities."""
from math import sqrt

CATALOG = {
    "ema_cross": {"name": "تقاطع روند EMA", "description": "تقاطع EMA ۹ و ۲۱؛ توقف با ATR و هدف با نسبت سود به زیان."},
    "rsi_range": {"name": "بازگشت RSI در رنج", "description": "بازگشت RSI از ۳۰/۷۰ فقط در بازاری با شیب کم."},
    "donchian": {"name": "شکست کانال قیمت", "description": "بسته شدن کندل خارج از سقف یا کف ۲۰ کندل قبل."},
}

def ema(values, period):
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + 2 / (period + 1) * (v - out[-1]))
    return out

def rsi(values, period=14):
    changes = [b - a for a, b in zip(values, values[1:])]
    gains = sum(max(v, 0) for v in changes[:period]) / period
    losses = sum(max(-v, 0) for v in changes[:period]) / period
    out = []
    for i, v in enumerate(changes):
        if i >= period:
            gains = (gains * (period - 1) + max(v, 0)) / period
            losses = (losses * (period - 1) + max(-v, 0)) / period
        if i >= period - 1:
            out.append(50 if gains == losses == 0 else (100 if losses == 0 else 100 - 100 / (1 + gains / losses)))
    return out

def analyze(bars, strategy, rr=2.0):
    if len(bars) < 60:
        return {"side": "WAIT", "reason": "حداقل ۶۰ کندل بسته لازم است", "regime": "unknown"}
    close = [b["close"] for b in bars]
    fast, slow = ema(close, 9), ema(close, 21)
    tr = [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"])) for a, b in zip(bars, bars[1:])]
    atr = sum(tr[-14:]) / 14
    if atr <= 0:
        return {"side": "WAIT", "reason": "نوسان معتبر نیست", "regime": "unknown"}
    regime = "trend" if abs(slow[-1] - slow[-6]) > 0.6 * atr else "range"
    side, reason = "WAIT", "شرایط ورود کامل نشده است"
    if strategy == "ema_cross":
        if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
            side, reason = "BUY", "تقاطع صعودی EMA ۹ و ۲۱ روی کندل بسته"
        elif fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
            side, reason = "SELL", "تقاطع نزولی EMA ۹ و ۲۱ روی کندل بسته"
    elif strategy == "rsi_range":
        rs = rsi(close)
        if regime == "range" and rs[-2] < 30 <= rs[-1]:
            side, reason = "BUY", "بازگشت RSI بالای ۳۰ در بازار رنج"
        elif regime == "range" and rs[-2] > 70 >= rs[-1]:
            side, reason = "SELL", "بازگشت RSI زیر ۷۰ در بازار رنج"
    elif strategy == "donchian":
        previous = bars[-21:-1]
        if close[-1] > max(b["high"] for b in previous):
            side, reason = "BUY", "شکست سقف ۲۰ کندل قبل با قیمت بسته شدن"
        elif close[-1] < min(b["low"] for b in previous):
            side, reason = "SELL", "شکست کف ۲۰ کندل قبل با قیمت بسته شدن"
    sign = 1 if side == "BUY" else -1
    return {"side": side, "reason": reason, "regime": regime, "atr": atr,
            "entry": close[-1], "sl": close[-1] - sign * 1.5 * atr,
            "tp": close[-1] + sign * 1.5 * atr * rr,
            "ema9": fast[-1], "ema21": slow[-1], "rsi": rsi(close)[-1]}
