"""PAM signal detectors (single source of truth). Logic is unchanged from w_pobifan_bt.py / pat2.py (17 Oct 2026);
only the plumbing changed:
  * data comes from the incremental store (env PAM_DATA, default /home/claude/data/store; see data_store.py)
  * load() also returns the open, and refuses series with unexplained weekly jumps > 40 % in the test window (A5)
  * every detector emits ALL signals; nothing is suppressed because an earlier trade under some exit model was still
    open (fix A3). One-position-per-stock is enforced by the portfolio engine, under the exit model being tested.
Patterns: 一 W底 (1.jpg), 二/三 破底翻 (2.jpg/3.jpg), 四 下飄旗形 (4.jpg), 十二 收斂三角形底部 (6.jpg)."""
import json, os, datetime as _dt

DATA = os.environ.get('PAM_DATA', '/home/claude/data/store')
START = '2016-10-14'
QQQ30 = 'NVDA AAPL MSFT GOOGL MU AMZN AMD META TSLA AVGO WMT INTC PLTR CSCO COST LRCX AMAT PANW NFLX TXN CRWD AMGN KLAC LIN SHOP MRVL SNDK ADI GILD QCOM'.split()
DOW30 = 'GS CAT MSFT AMGN UNH V TRV GOOGL AAPL JPM SHW AXP HD AMZN JNJ MCD CRM IBM CVX NVDA HON BA MMM PG MRK WMT CSCO DIS KO NKE'.split()
UNIV = sorted(set(QQQ30 + DOW30))
# weekly moves > 40 % inside the test window that were checked and are real (earnings / crash / squeeze), not split errors
KNOWN_JUMPS = {('AMD', '2016-04-18'), ('BA', '2020-03-16'), ('BA', '2020-03-23'), ('BA', '2020-06-01'), ('MRVL', '2023-05-22'),
               ('PLTR', '2020-11-23'), ('PLTR', '2024-02-05'), ('QCOM', '2019-04-15'), ('SHOP', '2020-04-13'), ('SHOP', '2022-03-14')}

def load(tk, data=None, check=True):
    d = json.load(open(os.path.join(data or DATA, tk + '.json')))
    T = [x[:10] for x in d['time']]; O, H, L, C, V = d['open'], d['high'], d['low'], d['close'], d['volume']
    wk = [_dt.date.fromisoformat(t).isocalendar()[:2] for t in T]
    T2, O2, H2, L2, C2, V2 = [], [], [], [], [], []
    for i in range(len(T)):                       # merge holiday weeks that IBKR splits into two bars
        if T2 and wk[i] == wk[i - 1]:
            H2[-1] = max(H2[-1], H[i]); L2[-1] = min(L2[-1], L[i]); C2[-1] = C[i]; V2[-1] += V[i]
        else:
            T2.append(T[i]); O2.append(O[i]); H2.append(H[i]); L2.append(L[i]); C2.append(C[i]); V2.append(V[i])
    T, O, H, L, C, V = T2, O2, H2, L2, C2, V2
    H = [max(h, o, c) for h, o, c in zip(H, O, C)]; L = [min(l, o, c) for l, o, c in zip(L, O, C)]
    if check:                                     # A5: hard split check inside the test window (from 2015 incl. warm-up)
        bad = [(tk, T[i]) for i in range(1, len(C)) if T[i] >= '2015-01-01' and abs(C[i] / C[i - 1] - 1) > 0.40 and (tk, T[i]) not in KNOWN_JUMPS]
        if bad: raise ValueError(f'unexplained >40% weekly move (split not adjusted?): {bad}')
    return T, O, H, L, C, V

def mas(C, n):
    out = [None] * len(C); s = 0
    for i, c in enumerate(C):
        s += c
        if i >= n: s -= C[i - n]
        if i >= n - 1: out[i] = s / n
    return out

def ma(C, i, n=30):
    return sum(C[i - n + 1:i + 1]) / n if i >= n - 1 else None

def ma_rising(C, i):
    m, m4 = ma(C, i), ma(C, i - 4)
    return m is not None and m4 is not None and m > m4

def vratio(V, i):
    a = V[max(0, i - 10):i]
    return V[i] / (sum(a) / len(a)) if a and sum(a) > 0 else None

def zigzag(H, L, th):
    """causal zigzag: list of (idx, price, 'L'/'H', confirm_idx)"""
    piv = []; N = len(H); mode = None; ext = exi = None
    hi, hii, lo, loi = H[0], 0, L[0], 0
    for t in range(1, N):
        if mode is None:
            if H[t] > hi: hi, hii = H[t], t
            if L[t] < lo: lo, loi = L[t], t
            if H[t] >= lo * (1 + th) and loi < t: piv.append((loi, lo, 'L', t)); mode = 'H'; ext, exi = H[t], t
            elif L[t] <= hi * (1 - th) and hii < t: piv.append((hii, hi, 'H', t)); mode = 'L'; ext, exi = L[t], t
            continue
        if mode == 'H':
            if H[t] > ext: ext, exi = H[t], t
            elif L[t] <= ext * (1 - th): piv.append((exi, ext, 'H', t)); mode = 'L'; ext, exi = L[t], t
        else:
            if L[t] < ext: ext, exi = L[t], t
            elif H[t] >= ext * (1 + th): piv.append((exi, ext, 'L', t)); mode = 'H'; ext, exi = H[t], t
    return piv

# ------------------------------------------------------------------ 一 W底 (1.jpg)
def detect_w(T, H, L, C, V, th=0.07, tol=0.06, maxwait=26, start=START):
    piv = zigzag(H, L, th); N = len(C); out = []
    for k in range(3, len(piv)):
        i2, L2, ty, c2 = piv[k]
        if ty != 'L': continue
        ip, P, typ, _ = piv[k - 1]; i1, L1, ty1, _ = piv[k - 2]; i0, H0, ty0, _ = piv[k - 3]
        if typ != 'H' or ty1 != 'L' or ty0 != 'H': continue
        if not (L1 * (1 - tol) <= L2 <= L1 * (1 + tol)): continue
        if H0 < P * 0.97: continue
        base = i2 - i1
        if base < 4: continue
        r = None
        for t in range(max(c2, i2 + 1), min(N, i2 + maxwait + 1)):
            if C[t] < L2: break
            if C[t] > P: r = t; break
        if r is None or T[r] < start: continue
        h = P - (L1 + (L2 - L1) * (ip - i1) / (i2 - i1))
        out.append(dict(kind='W', i1=i1, ip=ip, i2=i2, r=r, L1=L1, P=P, L2=L2, base=base, entry=C[r], T1=P + h, T2=P + 2 * h, h=h,
                        vr=vratio(V, r), trend=ma_rising(C, r)))
    return out

def w_signals(T, H, L, C, V):
    """A3: every W breakout; no 'busy' suppression. Playbook stop = ❷ neckline, widened to 1% under the 2nd low when < 4% away."""
    out = []
    for e in sorted(detect_w(T, H, L, C, V), key=lambda x: x['r']):
        if e['entry'] >= e['T1']: continue        # gapped through T1 on the breakout close: no entry
        e['stop'] = e['P'] if e['entry'] / e['P'] - 1 >= 0.04 else e['L2'] * 0.99
        out.append(e)
    return out

# ------------------------------------------------------------------ 二 / 三 破底翻 (2.jpg / 3.jpg)
def detect_pobifan(T, H, L, C, V):
    N = len(C); K = 2; BOUNCE = 0.08; ZONE = 0.05; MAXUNDER = 0.15; RW = 8
    piv = [i for i in range(K, N - K) if L[i] == min(L[i - K:i + K + 1])]
    events = []; used = set()
    for i in piv:
        F = L[i]
        touches = [j for j in piv if abs(j - i) <= 52 and F <= L[j] <= F * (1 + ZONE)]
        bounced = None
        for t in range(i + 1, N):
            if H[t] >= F * (1 + BOUNCE): bounced = t; break
            if L[t] < F: break
        if bounced is None: continue
        b = next((t for t in range(bounced, N) if L[t] < F), None)
        if b is None: continue
        tch = [j for j in touches if j + K <= b and j < b]
        single = False
        if len(tch) < 2:
            if b - i >= 8 and max(H[i:b]) >= F * 1.15: single = True; tch = [i]
            else: continue
        first = min(tch)
        if i in used: continue
        r = next((t for t in range(b, min(N, b + RW)) if C[t] > F), None)
        S = min(L[b:(r + 1 if r is not None else min(N, b + RW))]); under = (F - S) / F
        if under > MAXUNDER or r is None:
            events.append(dict(kind='x', b=b, ex=None)); used.add(i); continue
        B = max(H[first:b]); base = b - first
        cls = '二 box' if len(tch) >= 3 and B / F - 1 < 0.30 else '三 W底'
        events.append(dict(kind=cls, F=F, fi=i, first=first, b=b, r=r, S=S, under=under, touches=len(tch), B=B, base=base,
                           entry=C[r], stop=S * 0.99, T2=B + (B - F), vb=vratio(V, b), vr=vratio(V, r), single=single, ex=None))
        used.add(i)
        for t in range(r + 1, N):                 # initial-stop week: detection-level de-dupe only (independent of exit model)
            if C[t] < S * 0.99: events[-1]['ex'] = t; break
            if C[t] > B and H[t] >= B + (B - F): break
    events.sort(key=lambda e: (e['b'], -e.get('touches', 0)))
    kept = []; last = None
    for e in events:
        traded = e['kind'] != 'x'
        if last is not None and e['b'] <= last['b'] + RW and not (last['ex'] is not None and last['ex'] <= e['b']): continue
        kept.append(e)
        if traded: last = e
    return [e for e in kept if e['kind'] != 'x']

def pb_signals(T, H, L, C, V, start=START):
    """破底翻 under the playbook rules (shallow-spring 2nd close; R:R >= 1.4 to box top and >= 2 to T2)."""
    out = []
    for e in detect_pobifan(T, H, L, C, V):
        r = e['r']
        if T[r] < start: continue
        shallow = e['under'] <= 0.01 and (e['vr'] or 0) < 1.0
        if shallow:
            r2 = r + 1
            if r2 < len(C) and C[r2] > e['F']: r = r2
            else: continue
        entry = C[r]
        if entry <= e['stop']: continue
        rr1 = (e['B'] - entry) / (entry - e['stop']); rr2 = (e['T2'] - entry) / (entry - e['stop'])
        if rr1 < 1.4 or rr2 < 2: continue
        e.update(kind='PB', sub=e['kind'], r=r, entry=entry, rr1=rr1, rr2=rr2, trend=ma_rising(C, r))
        out.append(e)
    return out

# ------------------------------------------------------------------ 四 下飄旗形 (4.jpg) and 十二 收斂三角形底部 (6.jpg)
def detect_flag(T, H, L, C, start=START, X=1, th=0.07, pole_min=0.20):
    piv = zigzag(H, L, th); N = len(C); out = []
    for k in range(1, len(piv)):
        i0, L0, t0, _ = piv[k - 1]; i1, H1, t1, c1 = piv[k]
        if t0 != 'L' or t1 != 'H': continue
        pole = H1 - L0
        if H1 / L0 - 1 < pole_min or i1 - i0 > 26 * X: continue
        F = min(L[i1 + 1:c1 + 1]) if c1 > i1 else L[i1]
        for t in range(c1 + 1, min(N, i1 + 26 * X + 1)):
            if C[t] > H1:
                if t - i1 >= 2 * X and T[t] >= start:
                    entry = C[t]; T1 = F + pole; T2 = F + 2 * pole
                    if entry < T1:
                        out.append(dict(kind='FL', r=t, entry=entry, stop=F * 0.99, T1=T1, T2=T2, F=F, H1=H1, L0=L0, pole=pole / L0,
                                        retr=(H1 - F) / pole, base=t - i1, i1=i1, i0=i0, trend=ma_rising(C, t)))
                break
            F = min(F, L[t])
            if F < H1 - 0.5 * pole: break
    return out

def detect_triangle(T, H, L, C, start=START, X=1, th=0.07):
    piv = zigzag(H, L, th); N = len(C); out = []; used_t = set()
    for k in range(1, len(piv) - 3):
        (ih0, h0, a, _), (il1, l1, b, _), (ih1, h1, c, _), (il2, l2, d, _), (ih2, h2, e, ch2) = piv[k - 1], piv[k], piv[k + 1], piv[k + 2], piv[k + 3]
        if (a, b, c, d, e) != ('H', 'L', 'H', 'L', 'H'): continue
        if not (h0 > h1 and l2 > l1 and h2 < h1): continue
        su = (h2 - h1) / (ih2 - ih1); sl = (l2 - l1) / (il2 - il1)
        if su >= 0 or sl <= 0: continue
        up = lambda t: h1 + su * (t - ih1); lo = lambda t: l1 + sl * (t - il1)
        xa = (l1 - sl * il1 - h1 + su * ih1) / (su - sl)
        if xa <= ih2: continue
        last_low = l2
        for t in range(ch2 + 1, min(N, int(xa) + 1)):
            if C[t] < lo(t): break
            if C[t] > up(t):
                frac = (t - il1) / (xa - il1)
                if T[t] >= start and t not in used_t:
                    BL = up(t); hgt = h1 - l1; entry = C[t]; T1 = BL + hgt; T2 = BL + 2 * hgt
                    if entry < T1:
                        struct = last_low * 0.99; pb = BL if entry / BL - 1 >= 0.04 else struct
                        out.append(dict(kind='TR', r=t, entry=entry, stop=pb, T1=T1, T2=T2, BL=BL, frac=frac, base=t - il1, trend=ma_rising(C, t)))
                        used_t.add(t)
                break
            last_low = min(last_low, L[t]) if L[t] > lo(t) else last_low
    return out

def all_signals(tk, data=None):
    T, O, H, L, C, V = load(tk, data)
    return dict(T=T, O=O, H=H, L=L, C=C, V=V, sigs=w_signals(T, H, L, C, V) + pb_signals(T, H, L, C, V) +
                detect_flag(T, H, L, C) + detect_triangle(T, H, L, C))
