"""PAM weekly price store — incremental, saved in the project.

Project layout (all under claude/data/):
  manifest.json            readable index: ticker -> conid, exchange, first/last bar, bars, last close, updated
  weekly_store.b64         base64( lzma( json {ticker: {meta, ...bars}} ) )  - all weekly bars, IBKR "Last" (split-adjusted);
                           bars in format 2 (compact, lossless, from 21 Oct 2026) or format 1 (t,o,h,l,c,v lists) - unpack reads both

Commands (run from the folder holding this script):
  python3 data_store.py unpack STORE_DIR DATA_DIR      # project files -> DATA_DIR/<TK>.json (format used by the backtests)
  python3 data_store.py ingest DATA_DIR TK RAW [CONID] # merge an IBKR get_price_history JSON (file) into DATA_DIR/<TK>.json
  python3 data_store.py pack DATA_DIR STORE_DIR        # DATA_DIR -> STORE_DIR/weekly_store.b64 + manifest.json
  python3 data_store.py check DATA_DIR                 # split / gap / staleness report

Incremental update (weekly): for each ticker call IBKR get_price_history(contract_id=manifest conid, step=ONE_WEEK,
period=ONE_MONTH), save the raw JSON to a file, `ingest` it, then `pack` and write both files back to the project.
Merge rule: bars are keyed by week (ISO year-week). New bars replace stored bars of the same week (so a partial week
pulled mid-week is overwritten later). If the overlapping closes disagree by > 1.5 % on the median, the series was
re-adjusted (split): the stored history is rescaled by the median ratio and the event is logged in manifest 'events'.
"""
import json, lzma, base64, os, sys, statistics as st, datetime as dt

FIELDS = ('open', 'high', 'low', 'close', 'volume')

def _decimals(x):
    s = f'{x:.7g}'
    if 'e' in s: s = f'{x:.10f}'.rstrip('0')
    return len(s.split('.')[1]) if '.' in s else 0

def pk_rows(rows):
    """format 2 (21 Oct 2026): compact, lossless - prices as integers at the series' decimal precision, close
    delta-encoded, open/high/low relative to the close, dates as day steps, volumes exact (same as dailylog_store.py)"""
    if not rows: return dict(k=2, n=0, t0=None, dt=[], c=[], o=[], h=[], l=[], v=[])
    n = min(max(_decimals(r[k]) for r in rows for k in 'ohlc'), 6); sc = 10 ** n
    P = {k: [round(float(f'{r[k]:.7g}') * sc) for r in rows] for k in 'ohlc'}; c = P['c']
    ds = [dt.date.fromisoformat(r['t']).toordinal() for r in rows]
    return dict(k=2, n=n, t0=rows[0]['t'], dt=[ds[i] - ds[i - 1] for i in range(1, len(ds))],
                c=[c[0]] + [c[i] - c[i - 1] for i in range(1, len(c))], o=[a - b for a, b in zip(P['o'], c)],
                h=[a - b for a, b in zip(P['h'], c)], l=[a - b for a, b in zip(P['l'], c)], v=[int(round(r['v'])) for r in rows])

def unpk_rows(s):
    if s.get('k') != 2:                                      # format 1: plain lists t/o/h/l/c/v
        return [dict(t=s['t'][i], o=s['o'][i], h=s['h'][i], l=s['l'][i], c=s['c'][i], v=s['v'][i]) for i in range(len(s['t']))]
    if not s['t0']: return []
    sc = 10 ** s['n']; t = [s['t0']]; o = dt.date.fromisoformat(s['t0']).toordinal()
    for d in s['dt']: o += d; t.append(dt.date.fromordinal(o).isoformat())
    c, acc = [], 0
    for d in s['c']: acc += d; c.append(acc)
    return [dict(t=t[i], o=(s['o'][i] + c[i]) / sc, h=(s['h'][i] + c[i]) / sc, l=(s['l'][i] + c[i]) / sc, c=c[i] / sc, v=s['v'][i])
            for i in range(len(t))]

def wkey(t):
    return dt.date.fromisoformat(t[:10]).isocalendar()[:2]

def read_raw(path):
    d = json.load(open(path))
    n = len(d['time'])
    return [dict(t=d['time'][i][:10], o=d['open'][i], h=d['high'][i], l=d['low'][i], c=d['close'][i], v=d['volume'][i]) for i in range(n)]

def read_series(path):
    if not os.path.exists(path): return [], {}
    d = json.load(open(path))
    meta = d.get('meta', {})
    rows = [dict(t=t[:10], o=o, h=h, l=l, c=c, v=v) for t, o, h, l, c, v in zip(d['time'], d['open'], d['high'], d['low'], d['close'], d['volume'])]
    return rows, meta

def write_series(path, rows, meta):
    rows = sorted(rows, key=lambda r: r['t'])
    json.dump(dict(meta=meta, time=[r['t'] for r in rows], open=[r['o'] for r in rows], high=[r['h'] for r in rows],
                   low=[r['l'] for r in rows], close=[r['c'] for r in rows], volume=[r['v'] for r in rows]), open(path, 'w'))

def merge(old, new):
    """returns merged rows, event(str or None). Keys by date; IBKR sometimes splits a holiday week in two bars (two dates
    in one ISO week) - those are kept as given (the backtest loader merges them)."""
    if not old: return new, None
    od = {r['t']: r for r in old}
    ov = [(od[r['t']]['c'], r['c']) for r in new if r['t'] in od and od[r['t']]['c'] and r['c']]
    event = None
    if ov:
        ratios = [n / o for o, n in ov[:-1] or ov]       # last bar may be a partial week
        m = st.median(ratios)
        if abs(m - 1) > 0.015:
            for r in old:
                for k in 'ohlc': r[k] = r[k] * m
                r['v'] = r['v'] / m
            event = f'rescaled stored history x{m:.4f} (overlap {len(ov)} bars)'
            od = {r['t']: r for r in old}
    elif new[0]['t'] > old[-1]['t'] and (dt.date.fromisoformat(new[0]['t']) - dt.date.fromisoformat(old[-1]['t'])).days > 8:
        event = f'GAP: stored ends {old[-1]["t"]}, new starts {new[0]["t"]} - pull a longer period'
    for r in new: od[r['t']] = r
    return sorted(od.values(), key=lambda r: r['t']), event

def cmd_ingest(data_dir, tk, raw, conid=None):
    os.makedirs(data_dir, exist_ok=True)
    p = os.path.join(data_dir, tk + '.json')
    old, meta = read_series(p)
    new = read_raw(raw)
    rows, ev = merge(old, new)
    if conid: meta['conid'] = int(conid)
    meta['updated'] = dt.date.today().isoformat()
    if ev: meta.setdefault('events', []).append(f"{meta['updated']}: {ev}")
    write_series(p, rows, meta)
    print(f'{tk}: {len(old)} -> {len(rows)} bars, {rows[0]["t"]}..{rows[-1]["t"]}, last close {rows[-1]["c"]}' + (f'  [{ev}]' if ev else ''))

def jumps(rows, th=0.40):
    return [(rows[i]['t'], round(rows[i]['c'] / rows[i - 1]['c'] - 1, 3)) for i in range(1, len(rows)) if rows[i - 1]['c'] and abs(rows[i]['c'] / rows[i - 1]['c'] - 1) > th]

def cmd_pack(data_dir, store_dir, keep_from='2012-01-01'):
    """bars before keep_from are dropped unless the series' meta sets its own 'keep_from' (25 Oct 2026: QQQ_ETF = 2007-01-01)"""
    os.makedirs(store_dir, exist_ok=True)
    bundle = {}; man = {'_about': 'PAM weekly bar store. Bars = IBKR get_price_history ONE_WEEK, source Last (split-adjusted, not dividend-adjusted). '
                                  'Bar date = first trading day of the week; close = that week\'s last close. Unpack with claude/scripts/data_store.py.',
                       '_packed': dt.date.today().isoformat(), 'tickers': {}}
    for f in sorted(os.listdir(data_dir)):
        if not f.endswith('.json') or f.startswith('raw_'): continue
        tk = f[:-5]; rows, meta = read_series(os.path.join(data_dir, f))
        rows = [r for r in rows if r['t'] >= meta.get('keep_from', keep_from)]   # per-series override (QQQ_ETF keeps 2007+)
        if not rows: continue
        rnd = lambda x: float(f'{x:.6g}')
        rows6 = [dict(t=r['t'], o=rnd(r['o']), h=rnd(r['h']), l=rnd(r['l']), c=rnd(r['c']), v=int(r['v'])) for r in rows]
        bundle[tk] = dict(meta=meta, **pk_rows(rows6))                 # format 2 (compact, lossless)
        man['tickers'][tk] = dict(conid=meta.get('conid'), exchange=meta.get('exchange', 'SMART'), first=rows[0]['t'], last=rows[-1]['t'],
                                  bars=len(rows), last_close=rows[-1]['c'], updated=meta.get('updated'), events=meta.get('events', []),
                                  jumps_gt40pct=jumps(rows))
    blob = base64.b64encode(lzma.compress(json.dumps(bundle, separators=(',', ':')).encode(), preset=9 | lzma.PRESET_EXTREME)).decode()
    lines = [blob[i:i + 120] for i in range(0, len(blob), 120)]          # short lines keep the doc readable by line tools
    open(os.path.join(store_dir, 'weekly_store.b64'), 'w').write('\n'.join(lines) + '\n')
    json.dump(man, open(os.path.join(store_dir, 'manifest.json'), 'w'), indent=1)
    print(f'packed {len(bundle)} tickers, store {len(blob)/1024:.0f} KB base64')

def cmd_unpack(store_dir, data_dir):
    os.makedirs(data_dir, exist_ok=True)
    blob = ''.join(open(os.path.join(store_dir, 'weekly_store.b64')).read().split())
    bundle = json.loads(lzma.decompress(base64.b64decode(blob)))
    for tk, d in bundle.items():
        rows = unpk_rows(d)                                               # reads format 1 and format 2
        json.dump(dict(meta=d['meta'], time=[r['t'] for r in rows], open=[r['o'] for r in rows], high=[r['h'] for r in rows],
                       low=[r['l'] for r in rows], close=[r['c'] for r in rows], volume=[r['v'] for r in rows]), open(os.path.join(data_dir, tk + '.json'), 'w'))
    print(f'unpacked {len(bundle)} tickers to {data_dir}')

def cmd_clean(src_dir, dst_dir, k=1.5):
    """copy series to dst_dir with bad ticks clamped: high > k*max(open,close) -> max(open,close); low < min(open,close)/k -> min(open,close).
    The stored (raw) series are never modified, so later syncs merge cleanly; backtests read the cleaned copy."""
    os.makedirs(dst_dir, exist_ok=True); k = float(k)
    for f in sorted(os.listdir(src_dir)):
        if not f.endswith('.json') or f.startswith('raw_'): continue
        d = json.load(open(os.path.join(src_dir, f))); n = 0
        for i in range(len(d['close'])):
            hi = max(d['open'][i], d['close'][i]); lo = min(d['open'][i], d['close'][i])
            if d['high'][i] > k * hi: d['high'][i] = hi; n += 1
            if d['low'][i] < lo / k: d['low'][i] = lo; n += 1
        json.dump(d, open(os.path.join(dst_dir, f), 'w'))
        if n: print(f'{f[:-5]}: {n} ticks clamped')

def cmd_check(data_dir, today=None):
    today = dt.date.fromisoformat(today) if today else dt.date.today()
    for f in sorted(os.listdir(data_dir)):
        if not f.endswith('.json') or f.startswith('raw_'): continue
        rows, meta = read_series(os.path.join(data_dir, f))
        if not rows: continue
        stale = (today - dt.date.fromisoformat(rows[-1]['t'])).days
        gaps = [rows[i]['t'] for i in range(1, len(rows)) if (dt.date.fromisoformat(rows[i]['t']) - dt.date.fromisoformat(rows[i - 1]['t'])).days > 10]
        j = jumps(rows)
        flag = ' STALE' if stale > 13 else ''
        print(f"{f[:-5]:6s} {rows[0]['t']}..{rows[-1]['t']} n={len(rows):4d}{flag}" + (f' gaps={gaps[:3]}' if gaps else '') + (f' JUMPS={j}' if j else ''))

def cmd_sync(data_dir, transcript=None, manifest=None):
    """Ingest every IBKR get_price_history ONE_WEEK result found in this session's transcript whose contract_id is in the
    manifest. Each tool call is linked to its own result by tool_use_id, so parallel calls cannot be mixed up. Works for
    inline results and for results the harness saved to a file ('Output has been saved to <path>')."""
    import glob, re
    if not transcript:
        transcript = max(glob.glob('/root/.claude/projects/*/*.jsonl'), key=os.path.getmtime)
    man = json.load(open(manifest or os.path.join(data_dir, '..', 'proj', 'manifest.json')))['tickers']
    by_conid = {v['conid']: tk for tk, v in man.items() if v.get('conid')}
    calls = {}; done = 0
    for line in open(transcript):
        try: rec = json.loads(line)
        except Exception: continue
        content = (rec.get('message') or {}).get('content')
        if not isinstance(content, list): continue
        for c in content:
            if c.get('type') == 'tool_use' and c.get('name', '').endswith('get_price_history'):
                inp = c.get('input', {})
                if inp.get('step') == 'ONE_WEEK' and not inp.get('outside_rth') and int(inp.get('contract_id', 0)) in by_conid:
                    calls[c['id']] = by_conid[int(inp['contract_id'])]
            elif c.get('type') == 'tool_result' and c.get('tool_use_id') in calls:
                cc = c.get('content'); text = ''.join(x.get('text', '') for x in cc) if isinstance(cc, list) else (cc or '')
                m = re.search(r'saved to (\S+\.txt)', text)
                src = m.group(1) if m else None
                if not src and text.startswith('{"chart_step"'):
                    src = os.path.join(os.path.dirname(os.path.abspath(data_dir)), '_sync_tmp.json'); open(src, 'w').write(text)
                if src and not os.path.exists(src):
                    print(f'skip {calls[c["tool_use_id"]]}: result file no longer exists ({src})')
                elif src:
                    cmd_ingest(data_dir, calls[c['tool_use_id']], src); done += 1
    print(f'sync: {done} results ingested from {transcript}')

if __name__ == '__main__':
    a = sys.argv[1:]
    {'ingest': lambda: cmd_ingest(*a[1:]), 'pack': lambda: cmd_pack(*a[1:]), 'unpack': lambda: cmd_unpack(*a[1:]),
     'check': lambda: cmd_check(*a[1:]), 'clean': lambda: cmd_clean(*a[1:]), 'sync': lambda: cmd_sync(*a[1:])}[a[0]]()
