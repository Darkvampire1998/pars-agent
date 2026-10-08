"""Deterministic, closed-candle strategies. Scores are not win probabilities."""
from math import sqrt
from statistics import median

CATALOG = {
    "ema_cross": {"name": "تقاطع روند EMA", "description": "تقاطع EMA ۹ و ۲۱؛ توقف با ATR و هدف با نسبت سود به زیان."},
    "rsi_range": {"name": "بازگشت RSI در رنج", "description": "بازگشت RSI از ۳۰/۷۰ فقط در بازاری با شیب کم."},
    "donchian": {"name": "شکست کانال قیمت", "description": "بسته شدن کندل خارج از سقف یا کف ۲۰ کندل قبل."},
    "trend_pullback": {"name": "پول‌بک در روند", "description": "برگشت قیمت به EMA ۲۱ و ادامه جهت EMA ۵۰؛ فقط در روند معتبر."},
    "bollinger_reversion": {"name": "بازگشت باند در رنج", "description": "بازگشت از باند دو انحراف معیار با تأیید RSI؛ فقط در رنج."},
}

def indicators(bars):
    close = [b['close'] for b in bars]
    ranges, plus, minus = [], [], []
    for a, b in zip(bars, bars[1:]):
        ranges.append(max(b['high']-b['low'], abs(b['high']-a['close']), abs(b['low']-a['close'])))
        up, down = b['high']-a['high'], a['low']-b['low']
        plus.append(max(up, 0) if up > down else 0)
        minus.append(max(down, 0) if down > up else 0)
    atrs = [sum(ranges[i-13:i+1])/14 for i in range(13, len(ranges))]
    atr = atrs[-1] if atrs else 0
    # Wilder smoothing; ADX warm-up uses only observations already available.
    dx = []
    if len(ranges) >= 14:
        tr, p, m = sum(ranges[:14]), sum(plus[:14]), sum(minus[:14])
        for i in range(13, len(ranges)):
            if i > 13:
                tr = tr-tr/14+ranges[i]; p = p-p/14+plus[i]; m = m-m/14+minus[i]
            dx.append(100*abs(p-m)/(p+m) if p+m else 0)
    adx = sum(dx[:14])/14 if len(dx) >= 14 else 0
    for value in dx[14:]: adx = (adx*13+value)/14
    slow = ema(close, 21)
    slope = abs(slow[-1]-slow[-6])/atr if atr and len(slow) >= 6 else 0
    regime = 'trend' if adx >= 25 and slope >= .6 else ('range' if adx <= 20 and slope <= .6 else 'transition')
    typical = median(atrs[-50:-1]) if len(atrs) > 1 else atr
    shock = bool(atr and (bars[-1]['high']-bars[-1]['low'] > 3*atr or (typical and atr > 2.5*typical)))
    return {'atr': atr, 'adx': adx, 'regime': regime, 'volatility_shock': shock}

def select_candidate(ideas, policy='priority', min_votes=2):
    candidates = [idea for idea in ideas if idea['side'] != 'WAIT']
    if policy == 'adaptive':
        trend = {'ema_cross', 'donchian', 'trend_pullback'}
        ranging = {'rsi_range', 'bollinger_reversion'}
        candidates = [x for x in candidates if not x.get('volatility_shock') and
                      ((x.get('regime') == 'trend' and x['strategy'] in trend) or
                       (x.get('regime') == 'range' and x['strategy'] in ranging))]
    if len({x['side'] for x in candidates}) > 1:
        return None, 'راهبردها جهت‌های متضاد دارند'
    if policy == 'consensus' and len(candidates) < min_votes:
        return None, 'تعداد تأیید راهبردها کافی نیست'
    return (candidates[0], None) if candidates else (None, 'شرایط ورود و وضعیت بازار تأیید نشده است')

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
    elif strategy == 'trend_pullback':
        long = ema(close, 50)
        if fast[-1] > slow[-1] > long[-1] and bars[-2]['low'] <= slow[-2] and close[-1] > bars[-2]['high']:
            side, reason = 'BUY', 'پول‌بک به EMA ۲۱ و ادامه روند صعودی'
        elif fast[-1] < slow[-1] < long[-1] and bars[-2]['high'] >= slow[-2] and close[-1] < bars[-2]['low']:
            side, reason = 'SELL', 'پول‌بک به EMA ۲۱ و ادامه روند نزولی'
    elif strategy == 'bollinger_reversion':
        previous, current = close[-21:-1], close[-20:]
        pm, cm = sum(previous)/20, sum(current)/20
        ps = sqrt(sum((x-pm)**2 for x in previous)/20)
        cs = sqrt(sum((x-cm)**2 for x in current)/20)
        rs = rsi(close)
        if close[-2] < pm-2*ps and close[-1] >= cm-2*cs and rs[-1] > rs[-2] and rs[-1] < 50:
            side, reason = 'BUY', 'بازگشت به داخل باند پایین با تأیید RSI'
        elif close[-2] > pm+2*ps and close[-1] <= cm+2*cs and rs[-1] < rs[-2] and rs[-1] > 50:
            side, reason = 'SELL', 'بازگشت به داخل باند بالا با تأیید RSI'
    sign = 1 if side == "BUY" else -1
    return {"side": side, "reason": reason, "regime": regime, "atr": atr,
            "entry": close[-1], "sl": close[-1] - sign * 1.5 * atr,
            "tp": close[-1] + sign * 1.5 * atr * rr,
            "ema9": fast[-1], "ema21": slow[-1], "rsi": rsi(close)[-1]}
