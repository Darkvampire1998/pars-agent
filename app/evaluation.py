"""Chronological, next-open OHLC evaluation. No tuning or profit promises."""
from .strategies import analyze, indicators, select_candidate

def simulate(bars, strategies, policy, rr, point, spread, slippage, commission_bps, risk_pct, min_votes=2):
    position, trades = None, []
    equity, peak, max_dd, blocked_until = 100.0, 100.0, 0.0, 0
    for i in range(120, len(bars)):
        bar = bars[i]
        if position:
            sign, entry, distance, stop, target, opened = position
            exit_price, reason = None, None
            # Opening gaps execute at the worse open; SL wins ambiguous OHLC bars.
            if sign*(bar['open']-stop) <= 0: exit_price, reason = bar['open'], 'gap_stop'
            elif (bar['low'] <= stop if sign == 1 else bar['high'] >= stop): exit_price, reason = stop, 'stop'
            elif (bar['high'] >= target if sign == 1 else bar['low'] <= target): exit_price, reason = target, 'target'
            elif i-opened >= 30 or i == len(bars)-1: exit_price, reason = bar['close'], 'time_exit'
            if exit_price is not None:
                costs = ((spread+2*slippage)*point + (entry+exit_price)*commission_bps/10000)/distance
                net_r = sign*(exit_price-entry)/distance-costs
                equity *= max(0, 1+net_r*risk_pct/100)
                peak = max(peak, equity); max_dd = max(max_dd, (peak-equity)/peak*100)
                trades.append({'entry_time': bars[opened]['time'], 'exit_time': bar['time'], 'side': 'BUY' if sign == 1 else 'SELL', 'net_r': round(net_r, 5), 'exit': reason})
                position, blocked_until = None, i+3
        if position or i < blocked_until or i == len(bars)-1: continue
        window = bars[max(0, i-250):i]  # The current bar is never part of the signal.
        context = indicators(window)
        ideas = [analyze(window, strategy, rr) | context | {'strategy': strategy} for strategy in strategies]
        candidate, _ = select_candidate(ideas, policy, min_votes)
        if not candidate or candidate['atr'] <= 0 or spread*point/candidate['atr'] > .15: continue
        sign = 1 if candidate['side'] == 'BUY' else -1
        entry, distance = bar['open'], 1.5*candidate['atr']
        position = (sign, entry, distance, entry-sign*distance, entry+sign*rr*distance, i)
        # Evaluate the entry candle as well; do not skip its SL/TP.
        stop_hit = bar['low'] <= position[3] if sign == 1 else bar['high'] >= position[3]
        target_hit = bar['high'] >= position[4] if sign == 1 else bar['low'] <= position[4]
        if stop_hit or target_hit:
            exit_price, reason = (position[3], 'stop') if stop_hit else (position[4], 'target')
            costs = ((spread+2*slippage)*point+(entry+exit_price)*commission_bps/10000)/distance
            net_r = sign*(exit_price-entry)/distance-costs
            equity *= max(0, 1+net_r*risk_pct/100)
            peak = max(peak, equity); max_dd = max(max_dd, (peak-equity)/peak*100)
            trades.append({'entry_time': bar['time'], 'exit_time': bar['time'], 'side': candidate['side'], 'net_r': round(net_r, 5), 'exit': reason})
            position, blocked_until = None, i+3
    wins = sum(max(0, x['net_r']) for x in trades)
    losses = -sum(min(0, x['net_r']) for x in trades)
    return {'trades': len(trades), 'win_rate_pct': round(100*sum(x['net_r']>0 for x in trades)/len(trades),2) if trades else None,
            'profit_factor': round(wins/losses,3) if losses else None,
            'expectancy_r': round(sum(x['net_r'] for x in trades)/len(trades),4) if trades else None,
            'return_pct': round(equity-100,3), 'max_drawdown_pct': round(max_dd,3), 'sample': trades[-100:]}

def evaluate(bars, **settings):
    split = max(150, int(len(bars)*.7))
    development = simulate(bars[:split], **settings)
    # 120 preceding bars warm indicators; new entries start exactly at the split.
    holdout = simulate(bars[split-120:], **settings)
    stress = simulate(bars[split-120:], **(settings | {'spread': settings['spread']*2, 'slippage': settings['slippage']*2}))
    return {'development': development, 'holdout': holdout, 'cost_stress': stress,
            'split_time': bars[split]['time'], 'parameters_tuned': False,
            'assessment': 'insufficient' if holdout['trades'] < 30 else ('unfavorable' if holdout['expectancy_r'] <= 0 or stress['expectancy_r'] is None or stress['expectancy_r'] <= 0 else 'needs_demo_validation'),
            'note': 'بخش آخر ۳۰٪ داده از بخش توسعه جداست؛ پارامترها بهینه نشده‌اند. ورود در باز شدن کندل بعد، هزینه رفت‌وبرگشت و اولویت SL در کندل مبهم منظور شده است. داده OHLC جای تیک، شرایط بروکر، آزمون دمو و تحلیل آماری چندآزمونی را نمی‌گیرد.'}
