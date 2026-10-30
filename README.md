# pam_claude — PAM backtest data stores

Large backtest price stores moved out of the PAM Claude project (30 Oct 2026) to free project knowledge space. Readable manifests, scripts and procedures stay in the PAM project (`claude/data/README.md`, `claude/scripts/`).

| File | What it is | Manifest (in PAM) |
|---|---|---|
| `data/weekly_store.b64` | Development store: 52-stock backtest universe (top-30 QQQ + top-30 Dow) + QQQ_ETF (from Apr 2007) + DIA_ETF. 54 series, format 2. | `claude/data/manifest.json` |
| `data/holdout/weekly_store.b64` | Holdout store (test 23): US-30 + SPY_ETF, HK-20 (HKD) + 2800_ETF. 52 series, raw/uncleaned. | `claude/data/holdout/manifest.json` |

Format: base64 of an LZMA (xz) compressed JSON `{ticker: {meta, t, o, h, l, c, v}}`. Unpack with `data_store.py unpack` (PAM `claude/scripts/data_store.py`).

SHA-256 at migration:
- `data/weekly_store.b64` 50531e4c7b5fdbab922ec772d02a88d69ec8f43cc8a4a43bbb9223f372199faa
- `data/holdout/weekly_store.b64` da68fdffecbd966d4c1a275494568fec6892e8d2b341b19ef1fd37ea4f1e0b40

## Restore in a session

```bash
git clone --depth 1 https://github.com/ccleow/pam_claude /home/claude/pam_claude
mkdir -p /home/claude/data/proj /home/claude/data/proj_holdout /home/claude/bt
cp /home/claude/pam_claude/data/weekly_store.b64 /home/claude/data/proj/
cp /home/claude/pam_claude/data/holdout/weekly_store.b64 /home/claude/data/proj_holdout/
# project_read claude/data/manifest.json -> /home/claude/data/proj/
# project_read claude/data/holdout/manifest.json -> /home/claude/data/proj_holdout/
# project_read claude/scripts/data_store.py (+ test scripts) -> /home/claude/bt/
python3 /home/claude/bt/data_store.py unpack /home/claude/data/proj /home/claude/data/store
```

## After an incremental update

`data_store.py pack` into `/home/claude/data/proj` (or `proj_holdout`), then copy the new `.b64` into this repo, commit and push. `project_write` only the updated `manifest.json` back to PAM.
