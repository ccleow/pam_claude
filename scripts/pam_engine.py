"""PAM portfolio engine (one engine for every pattern and exit). Replaces port_bt.py / port2.py / etf_bt.portfolio.
$100k, weekly bars, risk RISK of equity on the stop distance, capped at CAP of equity, cash-limited, one position per stock.

Fixes vs the 17 Oct engines (all switchable so old and new can be compared like for like):
  A1  trail_fix   after the first target the exit level is max(initial structural stop, MA), and the MA trail is only armed
                  after a weekly close above the MA (old: MA only - the stop could drop below the spring low, or the trade
                  exited the week after a breakout made under a falling MA)
  A2  (same flag) MA undefined (new listing) = trail not armed -> the structural stop still protects (old: no exit at all)
  A3  w_busy=False W signals are no longer suppressed by a different exit model's open trade (old: busy from 'current' exits)
  A4  fill        'close' = act at the signal week's close (old) | 'next_open' = buy/sell at next week's open with `slip`
                  per side; intraweek target fills (high >= T1/T2) stay limit fills at the target
  B10 cash_yield  idle cash earns ~3-month T-bill (annual average by year, approximate)
Exit modes: current (playbook: half at first target, rest at T2 / close < entry) | trail (PB: full position on 30wMA after the
breakout; W: half at T1 + 40wMA; FL/TR: fl_style 'half40' or 'full30') | hold."""
import random, statistics as st, datetime as dt
import pam_signals as S

TBILL = {2016: .003, 2017: .009, 2018: .019, 2019: .021, 2020: .004, 2021: .0005, 2022: .020, 2023: .050, 2024: .050, 2025: .042, 2026: .038}
wkey = lambda t: dt.date.fromisoformat(t).isocalendar()[:2]
DATA = {}; SIGS = []

def prepare(univ=None):
    global DATA, SIGS, CAL, START_K
    DATA = {}; SIGS = []
    for tk in (univ or S.UNIV):
        a = S.all_signals(tk)
        T, C = a['T'], a['C']
        a.update(M30=S.mas(C, 30), M40=S.mas(C, 40), idx={wkey(t): i for i, t in enumerate(T)})
        DATA[tk] = a
        for e in a['sigs']:
            e['tk'] = tk; e['wk'] = wkey(T[e['r']]); SIGS.append(e)
        _mark_w_busy(tk)
    START_K = wkey(S.START)
    CAL = sorted({k for d in DATA.values() for k in d['idx'] if k >= START_K})
    return DATA, SIGS

def _mark_w_busy(tk):
    """old-engine replication only: flag W signals that the 17 Oct code dropped (a 'current'-exit W trade still open)."""
    d = DATA[tk]; C, H = d['C'], d['H']; N = len(C); busy = -1
    for e in sorted([s for s in d['sigs'] if s['kind'] == 'W'], key=lambda s: s['r']):
        e['old_busy'] = e['r'] <= busy
        if e['old_busy']: continue
        ex = N; hit = False
        for t in range(e['r'] + 1, N):
            if not hit:
                if C[t] < e['stop']: ex = t; break
                if H[t] >= e['T1']:
                    hit = True
                    if H[t] >= e['T2']: ex = t; break
                continue
            if H[t] >= e['T2'] or C[t] < e['entry']: ex = t; break
        busy = ex

def stats(eq):
    yrs = len(eq) / 52.18; c = (eq[-1] / eq[0]) ** (1 / yrs) - 1; pk = eq[0]; m = 0
    for v in eq: pk = max(pk, v); m = min(m, v / pk - 1)
    return c, m

def simulate(kinds=('PB', 'W'), mode='trail', univ=None, w_trend_only=True, tr_book_only=False, fl_filter=None, fl_style='half40',
             trail_fix=True, w_busy=False, fill='close', slip=0.0, cash_yield=False, RISK=0.01, CAP=0.10, seed=None, start=None, weeks=None, cash_ret=None):
    rnd = random.Random(seed) if seed is not None else None
    sk = start or START_K; cal = [k for k in CAL if k >= sk]
    if weeks: cal = cal[:weeks]
    sigs = [s for s in SIGS if s['kind'] in kinds and (univ is None or s['tk'] in univ) and s['wk'] >= sk
            and not (s['kind'] == 'W' and w_trend_only and not s['trend'])
            and not (s['kind'] == 'W' and w_busy and s.get('old_busy'))
            and not (s['kind'] == 'TR' and tr_book_only and not (0.5 <= s['frac'] <= 0.75))
            and not (s['kind'] == 'FL' and fl_filter and not fl_filter(s))]
    by = {}
    for s in sigs: by.setdefault(s['wk'], []).append(s)
    cash = 1e5; pos = {}; last = {}; eq = []; trades = []; expo = []
    nx = (lambda d, i: d['O'][i + 1] if i + 1 < len(d['O']) else d['C'][i]) if fill == 'next_open' else None

    for k in cal:
        if cash_ret is not None: cash *= 1 + cash_ret.get(k, 0.0)          # idle cash parked in an index (sweep)
        elif cash_yield: cash *= (1 + TBILL.get(k[0], 0.03)) ** (1 / 52)
        for tk in list(pos):
            p = pos[tk]; d = DATA[tk]; i = d['idx'].get(k)
            if i is None or i < p['i0']: continue
            C, H = d['C'][i], d['H'][i]; last[tk] = C

            def sell(frac, px, why, at_close=True):
                nonlocal cash
                if at_close and fill == 'next_open': px = nx(d, i)
                px *= (1 - slip) if at_close else 1
                q = p['q'] * frac; cash += q * px; p['q'] -= q; p['real'] += q * px
                if p['q'] <= 1e-9:
                    trades.append(dict(tk=tk, kind=p['kind'], ewk=p['ewk'], xwk=k, ret=p['real'] / p['cost'] - 1, why=why)); del pos[tk]

            if i == p['i0'] and fill == 'next_open':      # entry bar: bought at its open; only the stop applies at its close
                if C < p['stop']: sell(1, C, 'stop')
                continue
            if i <= p['i0'] and fill == 'close': continue
            kd = p['kind']; style = 'pb' if kd == 'PB' else ('half40' if kd == 'W' else fl_style)
            if p['stage'] == 'pre':
                if C < p['stop']: sell(1, C, 'stop'); continue
                hit = (C > p['B']) if kd == 'PB' else (H >= p['T1'])
                if hit:
                    p['stage'] = 'post'; p['pi'] = i
                    if mode == 'current':
                        if kd != 'PB' and H >= p['T2']: sell(1, p['T2'], 'T2', at_close=False); continue
                        if kd == 'PB': sell(0.5, C, 'half')
                        else: sell(0.5, p['T1'], 'half', at_close=False)
                    elif mode == 'trail' and style == 'half40':
                        sell(0.5, p['T1'], 'half', at_close=False)
                    if tk in pos and mode == 'trail':
                        lv = d['M40'][i] if style == 'half40' else d['M30'][i]
                        p['armed'] = lv is not None and C > lv
                continue
            if mode == 'current':
                if H >= p['T2']: sell(1, p['T2'], 'T2', at_close=False); continue
                if i > p['pi'] and C < p['entry']: sell(1, C, 'BE'); continue
            elif mode == 'trail':
                lv = d['M40'][i] if style == 'half40' else d['M30'][i]
                if trail_fix:
                    if not p['armed'] and lv is not None and C > lv: p['armed'] = True
                    level = max(p['stop'], lv) if (p['armed'] and lv is not None) else p['stop']
                    if C < level: sell(1, C, 'trail' if p['armed'] else 'stop-post'); continue
                else:
                    if lv is not None and C < lv: sell(1, C, 'trail'); continue
        for tk in pos:
            i = DATA[tk]['idx'].get(k)
            if i is not None: last[tk] = DATA[tk]['C'][i]
        equity = cash + sum(p['q'] * last.get(tk, p['entry']) for tk, p in pos.items())
        order = (lambda s: rnd.random()) if rnd else (lambda s: ({'PB': 0, 'W': 1, 'FL': 2, 'TR': 3}.get(s['kind'], 4), s['tk']))
        for s in sorted(by.get(k, []), key=order):
            if s['tk'] in pos: continue
            d = DATA[s['tk']]; r = s['r']
            if 'fill_i' in s:                       # pre-computed fill (e.g. limit order on a pullback): exact price, no slippage
                px = s['fill_px']; i0 = s['fill_i']
            elif fill == 'next_open':
                if r + 1 >= len(d['C']): continue
                px = d['O'][r + 1] * (1 + slip); i0 = r + 1
            else:
                px = s['entry']; i0 = r
            rps = px - s['stop']
            if rps <= 0: continue                   # opened below the stop: skip
            val = min(equity * RISK / (rps / px), equity * CAP)
            if cash < 0.25 * val or cash < 0.005 * equity: continue
            val = min(val, cash); cash -= val
            pos[s['tk']] = dict(kind=s['kind'], i0=i0, q=val / px, entry=px, stop=s['stop'], T1=s.get('T1'), T2=s['T2'], B=s.get('B'),
                                stage='pre', cost=val, real=0.0, ewk=k, armed=False)
            last[s['tk']] = px
        equity = cash + sum(p['q'] * last.get(tk, p['entry']) for tk, p in pos.items())
        eq.append(equity); expo.append(1 - cash / equity)
    for tk, p in pos.items():
        trades.append(dict(tk=tk, kind=p['kind'], ewk=p['ewk'], xwk=None, ret=(p['real'] + p['q'] * last[tk]) / p['cost'] - 1, why='open'))
    return eq, dict(trades=trades, exposure=st.mean(expo) if expo else 0)

def robust(n=100, **cfg):
    cs, ms, wins, nt = [], [], [], []
    for sd in range(n):
        eq, info = simulate(seed=sd, **cfg); c, m = stats(eq); cs.append(c); ms.append(m)
        tr = info['trades']; nt.append(len(tr)); wins.append(sum(t['ret'] > 0 for t in tr) / max(1, len(tr)))
    cs.sort(); ms.sort()
    return dict(cagr=st.median(cs), p5=cs[int(.05 * n)], p95=cs[int(.95 * n) - 1], dd=st.median(ms), worst=ms[0], win=st.median(wins), trades=st.median(nt))

def rolling(**cfg):
    starts = [CAL[i] for i in range(0, len(CAL) - 261, 13)]
    cs, ms = [], []
    for k in starts:
        eq, _ = simulate(start=k, weeks=261, **cfg); c, m = stats(eq); cs.append(c); ms.append(m)
    return dict(med=st.median(cs), mn=min(cs), mx=max(cs), dd=st.median(ms), worst=min(ms), n=len(cs))

def bench(tk, start=None):
    d = DATA.get(tk) or dict(zip(('T', 'O', 'H', 'L', 'C', 'V'), S.load(tk, check=False)))
    m = {wkey(t): c for t, c in zip(d['T'], d['C'])}
    xs = []; lc = None
    for k in CAL:
        if start and k < start: continue
        if k in m: lc = m[k]
        xs.append(lc)
    return [x / xs[0] * 1e5 for x in xs]
