"""Test 25 - High-win v2 (四 flags + 一 W底, test-20 filters, half at T1 + 40wMA trail) on QQQ alone, Apr 2007 - Oct 2026.
Rules unchanged from tests 20-23; only the window and universe change. 2008-01 .. 2016-10-14 was never used to develop v2
(holdout); 2016-10-14 .. 2026-10 is the development window.
Outputs: per-trade stats, portfolio runs (v2 sizing 1.5 % / 15 %, and 100 % per trade), QQQ buy & hold, random-entry baseline."""
import statistics as st, csv, sys
sys.path.insert(0, '/home/claude/bt')
import pam_signals as S
S.START = '2008-01-02'
for f in ('detect_w', 'pb_signals', 'detect_flag', 'detect_triangle'):          # defaults were bound at import time
    fn = getattr(S, f); d = list(fn.__defaults__); d[-1 if f != 'detect_flag' and f != 'detect_triangle' else 0] = S.START
    fn.__defaults__ = tuple(d)
import pam_engine as E
E.TBILL.update({2007: .044, 2008: .014, 2009: .0015, 2010: .0014, 2011: .0005, 2012: .0009, 2013: .0006, 2014: .0003, 2015: .0005})
E.prepare(univ=['QQQ_ETF'])
whgt = lambda s: s['kind'] != 'W' or s['h'] / s['P'] >= 0.10
fpole = lambda s: s['kind'] != 'FL' or (s['pole'] >= 0.40 and s['trend'])
E.SIGS = [s for s in E.SIGS if s['kind'] in ('W', 'FL') and whgt(s) and fpole(s)]
RAW = [s for s in E.DATA['QQQ_ETF']['sigs'] if s['kind'] in ('W', 'FL')]
REAL = dict(trail_fix=True, w_busy=False, fill='next_open', slip=0.001, cash_yield=True)
BASE = dict(kinds=('W', 'FL'), mode='trail', fl_style='half40', w_trend_only=True)
HOLD_K = E.wkey('2016-10-14'); d = E.DATA['QQQ_ETF']; T = d['T']; C = d['C']; idx = d['idx']

def run(start=None, end=None, **kw):
    sigs = E.SIGS
    if end: E.SIGS = [s for s in sigs if s['wk'] < end]
    cal_all = E.CAL
    if end: E.CAL = [k for k in cal_all if k < end]
    eq, info = E.simulate(start=start, **BASE, **REAL, **kw)
    E.SIGS, E.CAL = sigs, cal_all
    return eq, info

def bh(start, end=None):
    ks = [k for k in E.CAL if k >= start and (end is None or k < end)]
    xs = [C[idx[k]] for k in ks]
    return [x / xs[0] * 1e5 for x in xs]

def rand_base(trades):
    """mean return of entering QQQ in ANY week of the same window and holding the same number of weeks"""
    out = []
    for t in trades:
        n = max(1, E.CAL.index(t['xwk']) - E.CAL.index(t['ewk'])) if t['xwk'] else len(E.CAL) - 1 - E.CAL.index(t['ewk'])
        lo = E.START_K if t['ewk'] < HOLD_K else HOLD_K
        ks = [k for k in E.CAL if k >= lo]
        rs = [C[idx[ks[j + n]]] / C[idx[ks[j]]] - 1 for j in range(len(ks) - n)]
        out.append(st.mean(rs) if rs else 0)
    return out

def pt(tr):
    if not tr: return 'n 0'
    r = [t['ret'] for t in tr]; rb = rand_base(tr); comp = 1
    for x in r: comp *= 1 + x
    return (f"n {len(r):2d} | win {sum(x > 0 for x in r) / len(r):4.0%} | avg {st.mean(r):+6.1%} | median {st.median(r):+6.1%} | "
            f"best {max(r):+6.1%} worst {min(r):+6.1%} | compounded {comp - 1:+7.1%} | random-entry same hold {st.mean(rb):+6.1%}")

rows = []
print(f"QQQ bars {T[0]}..{T[-1]}  raw W/FL signals {len(RAW)}  after v2 filters {len(E.SIGS)} "
      f"(W {sum(s['kind'] == 'W' for s in E.SIGS)}, FL {sum(s['kind'] == 'FL' for s in E.SIGS)})")
for lab, a, b in (('FULL 2008-01..2026-10', E.START_K, None), ('HOLDOUT 2008-01..2016-10', E.START_K, HOLD_K), ('DEV 2016-10..2026-10', HOLD_K, None)):
    print(f"\n## {lab}")
    _, info = run(start=a, end=b, RISK=1.0, CAP=1.0)
    tr = info['trades']; print('  per trade (100% in, v2 exits):', pt(tr))
    for k in ('W', 'FL'):
        print(f'    {k:2s}', pt([t for t in tr if t['kind'] == k]))
    for sz, kw in (('100% per trade', dict(RISK=1.0, CAP=1.0)), ('v2 sizing 1.5%/15%', dict(RISK=0.015, CAP=0.15))):
        eq, inf2 = run(start=a, end=b, **kw); c, m = E.stats(eq)
        print(f"  portfolio {sz:20s} CAGR {c:5.1%}  maxDD {m:5.0%}  total {eq[-1] / 1e5 - 1:+7.1%}  exposure {inf2['exposure']:4.0%}")
    eq = bh(a, b); c, m = E.stats(eq)
    print(f"  QQQ buy & hold (price only)      CAGR {c:5.1%}  maxDD {m:5.0%}  total {eq[-1] / 1e5 - 1:+7.1%}")
    if lab.startswith('FULL'): rows = tr

with open('/home/claude/test25_qqq_trades.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['pattern', 'entry_week', 'exit_week', 'exit_reason', 'return_pct'])
    for t in rows: w.writerow([t['kind'], '%d-W%02d' % t['ewk'], '%d-W%02d' % t['xwk'] if t['xwk'] else 'open', t['why'], round(t['ret'] * 100, 2)])
