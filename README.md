# Upstox NSE Mother Candle Screener

Python/Streamlit screener for NSE F&O underlyings, Nifty 50, Nifty 500, all eligible NSE ordinary equities, or a validated custom NSE symbol list. Upstox is the exclusive OHLC provider; NSE Indices is used only for official current Nifty constituent CSVs. The application only reads data; it has no order, trading, portfolio, or webhook functionality.

## Install

Requires Python 3.10+.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
```

Do not commit `.env`. It is already in `.gitignore`.

## Configure an Upstox Developer App

1. Sign in to the [Upstox Developer Apps portal](https://account.upstox.com/developer/apps) and create an application.
2. Set a redirect URI, for example `http://127.0.0.1:8765/callback`, and use the exact same value in `.env`.
3. Add the resulting API key, API secret, redirect URI, and (optionally) a current access token to `.env`:

```ini
UPSTOX_API_KEY=...
UPSTOX_API_SECRET=...
UPSTOX_REDIRECT_URI=http://127.0.0.1:8765/callback
UPSTOX_ACCESS_TOKEN=...
```

The API key and secret are needed only for the interactive OAuth exchange. A valid `UPSTOX_ACCESS_TOKEN` is enough to scan. Access tokens expire at 03:30 IST on the following day, so renew the token when the application reports that it has expired.

### OAuth flow

If no `UPSTOX_ACCESS_TOKEN` exists, the program offers an OAuth browser flow. It opens the official Upstox login page, then asks you to paste the returned redirect URL (or its `code` parameter). It exchanges that single-use code for a token without ever asking for an Upstox password, PIN, or OTP. For convenience, a manually pasted token is kept only in memory for that run; add it to `.env` yourself if desired.

## Run

```powershell
python screener.py
```

Choose a timeframe, screening mode, stock universe, count mode, and output destination. The stock-universe default remains NSE F&O stocks. A small real-data smoke test is available after configuration:

```powershell
python screener.py --test
```

`--test` scans only `UPSTOX_TEST_SYMBOL_LIMIT` (default 5) dynamically discovered symbols from the selected universe.

For a reproducible, non-interactive five-symbol Daily/minimum-7 benchmark, use:

```powershell
python screener.py --benchmark --refresh-data
```

The timing report separates prompt/auth time, BOD discovery, NSE-calendar loading, the whole OHLC phase, cumulative HTTP latency, limiter sleep, screening, output, and wall-clock time.

## Streamlit web application

Install the requirements, configure a server-side Upstox Analytics Token, then run:

```powershell
.venv\Scripts\streamlit run streamlit_app.py
```

The page uses a form, so changing controls never starts a scan. It exposes current/historical mode, all timeframes and lookbacks, the five universes, inside-count range, optional small-body and green-candle filters, progress, cancellation between batches, a searchable/sortable table, and CSV download. It calls `inside_screener.scan` and `inside_screener.logic`; no screening formula is duplicated in the UI.

For local development, `.env` remains supported. For Streamlit, put only the token in the untracked `.streamlit/secrets.toml`:

```toml
UPSTOX_ACCESS_TOKEN = "..."
```

The token is read only by the Python server. The page never requests it from ordinary users, renders it, logs it, or includes it in a download.

### Cloud deployment

Streamlit Community Cloud can be a useful pilot for small or warm-cache scans. Add `UPSTOX_ACCESS_TOKEN` in the app's **Secrets** settings, never in Git. Its documented resource limits are shared and may change; generated runtime files are not guaranteed to persist across sessions, and inactive apps sleep. A 2,000-3,000-symbol, cold-cache historical scan can take long enough that an in-process job and local cache are not reliable durable infrastructure.

This repository therefore uses a deliberately conservative in-process job manager: one active scan per Python process and one shared Upstox request pace. It prevents duplicate clicks, shows queued/running/completed/cancelled/failed state, only cancels between complete batches, and treats per-symbol OHLC cache coverage as resume support. It never calls a cancelled job complete. For reliable multi-user or full-universe cloud operation, run the same Streamlit UI beside a persistent single worker and durable cache/job store with a globally coordinated Upstox rate limiter. Redis, Celery, and a database are not added until that deployment requirement exists.

See [Community Cloud resource and hibernation limits](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app), [secret management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management), and [runtime-file persistence limits](https://docs.streamlit.io/develop/concepts/configuration/serving-static-files).

## Pattern rule

Every completed candle can be a mother candle. Its inclusive range is `mother_low <= close <= mother_high`; wicks are deliberately ignored. Every subsequent completed close through the latest completed candle must remain in that range. Therefore results are active patterns only. A close outside the range invalidates a candidate; closes exactly on a boundary are valid.

The detector recognises overlapping active mothers, but final terminal, Streamlit, and CSV results keep only the **oldest mother candle** for each `Symbol + Timeframe`. Results remain sorted by `Inside Candles` descending.

### Optional small-body / doji-like filter

After choosing the timeframe and inside-candle count, choose whether to enable the optional small-body filter. When enabled, provide the `Range / Body` ratio threshold (default `2`) and then filter by either a minimum number or minimum percentage of qualifying inside candles.

For each inside candle only, the screener calculates `Range = High - Low`, `Body = abs(Close - Open)`, then classifies the candle when `Range / Body > threshold`. The comparison is strict: a ratio equal to the threshold does not qualify. Bullish and bearish bodies are treated identically. The mother candle is never included, qualifying small-body candles do not need to be consecutive, and the existing close-in-mother-range pattern rule is unchanged.

When `Body=0` and `Range>0`, a candle qualifies without division. A zero-range candle never qualifies. Other inconsistent OHLC relationships (for example, a high below the open or close) are rejected before screening. When the filter is disabled, result eligibility is identical to prior releases and the three new output columns show `N/A`.

Terminal and CSV results include `Small-Body Candles`, `Small-Body %`, and `Range/Body Ratio Threshold`.

### Optional green-candle percentage filter

Every qualifying active mother-candle sequence is independently classified after the original close-in-range rule passes. An inside candle is green only when `Close > Open`, red only when `Close < Open`, and neutral when `Close == Open`. The mother candle is excluded. The displayed `Green Candle %` is `Green Candle Count / Inside Candles * 100`; red and neutral rows remain in the denominator.

Enable the optional filter to require an inclusive minimum green percentage (decimal values from `0` to `100`, default `50`). Counts do not need to be consecutive. This classification is independent of the optional small-body filter; all enabled criteria are combined with logical AND. Terminal, CSV, and web output append `Green Candle Count`, `Red Candle Count`, `Neutral Candle Count`, and `Green Candle %`.

### Historical as-of screening

After choosing a timeframe, select either **Current** (the default) or **Historical** mode. Historical mode rebuilds the result from only the candles available through the requested cutoff; it does not take todayâ€™s active patterns and relabel them. The output includes the screening mode, requested cutoff, and the effective final completed candle actually used for each symbol.

Use these historical inputs by timeframe:

- Intraday, daily, weekly: `YYYY-MM-DD`
- Monthly: `YYYY-MM`
- Quarterly: `YYYY-Q1` through `YYYY-Q4`
- Half-yearly: `YYYY-H1` or `YYYY-H2`
- Yearly: `YYYY`

For a holiday or weekend daily request, the preceding available completed candle becomes the effective cutoff. Weekly requests use the latest fully completed week on or before the requested date. Monthly and higher-period requests use only source candles through the selected completed period; they do not aggregate later source data. Historical V3 requests and their local cache snapshots are date-anchored, and the selected timeframe lookback is measured backward from that cutoff.

Historical scans intentionally use the **current** selected Upstox universe. The application announces this in the terminal and writes it into the scan context; it does not claim to reconstruct historical F&O membership, historical listing membership, or delistings. This creates the usual survivorship limitation: an instrument eligible or listed only in the past may be absent, while an instrument eligible or listed only today may appear. No reliable historical universe snapshot is currently integrated.

### Stock universes and large scans

The terminal and Streamlit universe selectors provide five choices:

- **NSE F&O Stocks** — the existing default, resolved from NSE_FO equity underlyings to NSE_EQ keys.
- **All NSE Equity Stocks** — current BOD `segment=NSE_EQ`, `instrument_type=EQ` records, deduplicated by `instrument_key`. ETF/fund units are excluded using their `INF` ISIN prefix and ETF labels; other NSE_EQ instrument types, missing keys, and duplicate symbols are counted and reported.
- **Custom Symbol List** — a UTF-8 text file or CSV whose first column contains one NSE trading symbol per row. Every symbol is validated against the current eligible NSE equity universe; invalid and duplicate entries are reported rather than scanned.

Nifty 50 and Nifty 500 use the current official NSE Indices constituent CSVs. Their symbols are mapped through the current Upstox eligible NSE_EQ universe; the source URL, refresh time, listed count, and unresolved mappings are reported. Upstox documents `instrument_key` as the stable instrument identifier and specifically recommends filtering NSE equities by `segment=NSE_EQ` and `instrument_type=EQ`. The screener follows that schema, then applies the additional ETF/fund-unit safety exclusions described above.

Large scans use the existing four workers and shared 12-request/second pacer, but retrieve and screen `UPSTOX_SCAN_BATCH_SIZE` symbols at a time (default `100`). Only a batch of OHLC frames is retained in memory. Per-symbol OHLC caches also act as practical resume points: rerunning an interrupted scan reuses fresh completed-symbol caches; there is no separate mutable checkpoint file or cached result set.

## Data, timeframes, and completion

Upstox V3 native intervals are used for 5m, 15m, 30m, 60m, 90m, 2h, 3h, 4h, 5h, daily, weekly, and monthly. Quarterly, half-yearly, and yearly are constructed solely from **completed native monthly** OHLC candles with `Open=first`, `High=max`, `Low=min`, and `Close=last` on calendar boundaries.

All timestamps are converted to Asia/Kolkata. Current intraday and daily bars are discarded until their full interval / NSE session is complete. In **Current** mode, forming weekly, monthly, quarterly, half-yearly, and yearly candles are deliberately included: an intraperiod close outside a mother range invalidates the pattern immediately, and it can reappear if the current candle later closes back inside the range. Historical as-of scans continue to use completed higher-timeframe periods only. A final shortened hourly bar is accepted at the NSE session close, rather than incorrectly waiting for its nominal interval end. Set `UPSTOX_SESSION_OVERRIDES_JSON` for an announced non-standard current-day close.

The BOD cache lives at `.cache/upstox_nse_bod.json.gz`; OHLC caches live under `.cache/ohlc`. They are local, disposable caches only. The BOD source is refreshed daily (or on request); intraday data uses a short TTL, while a daily cache written after the NSE close is safely reused for the rest of the evening. Use `--refresh-data` to bypass OHLC caching for a live timing/data check. The client uses four workers, a shared 12 request/second pacing ceiling, retries with backoff, and per-symbol error isolation—well below Upstox’s standard 50 request/second limit.

### Timeframe-aware history depth

The terminal offers a lookback override in calendar days. Press Enter to use the timeframe default; alternatively set `UPSTOX_LOOKBACK_DAYS` to apply a configuration override. Historical mode always measures the range backward from its selected cutoff, never from today.

| Timeframe | Default lookback |
| --- | --- |
| 5m, 15m | 6 months |
| 30m through 5h | 1 year |
| Daily | 5 years |
| Weekly | 10 years |
| Monthly | 15 years |
| Quarterly, half-yearly | 20 years |
| Yearly | maximum available Upstox history |

Upstox V3 minute/hour history begins in January 2022; day/week/month history begins in January 2000. The requested start is clamped to those availability boundaries and reported. The client splits minute 1â€“15 ranges into 30-day windows, longer minute/hour ranges into quarter-sized windows, and daily requests into decade-sized windows; weekly/monthly source data is requested in one V3 window where the API permits it. Quarterly, half-yearly, and yearly candles continue to aggregate only completed native monthly candles.

The scan summary reports the requested range, effective per-symbol available-start range, completed-candle-count range, and categorized limitations. It distinguishes a user-configured lookback that is too short, a late first candle (recent listing or unavailable Upstox history), missing OHLC returned by Upstox, and genuine missing-session protections. A cache is reused only when it covers the entire requested period; a shorter cache is fetched again rather than presented as complete history.

NSE holiday coverage remains deliberately conservative. The built-in official fallback covers 2025â€“2026; if an older daily gap cannot be verified from the cached/current official calendar, the application reports the continuity restriction and excludes the older segment instead of inventing trading sessions. Weekly and monthly native histories do not require synthetic daily sessions for their own completion rules.

Same-session intraday gaps cause a symbol to be skipped because the number of consecutive candles cannot be verified. Daily gaps are checked against the official NSE Capital Market holiday feed, supplemented by official late-change circular fallbacks and the announced Nov. 8, 2026 Muhurat session. A known holiday is accepted; an absent expected trading day remains a hard continuity break and excludes older candles from screening. Weekends and holidays are never synthesised. The program supports explicit current-session overrides rather than fabricating missing candles.

## Tests

```powershell
python -m pytest -q
```

The automated suite covers active/invalidated and boundary patterns, wick handling, overlaps, optional small-body count/percentage filtering (including flat candles, invalid OHLC, input validation, CSV, and terminal output), current and historical no-lookahead screening, daily/intraday/weekly/monthly/quarterly/half-yearly/yearly cutoffs, timeframe-specific lookbacks, Upstox availability clamping, intraday request splitting, coverage-safe cache reuse, historical cache anchoring, incomplete periods, NSE_EQ ordinary-equity selection, ETF/derivative exclusion, duplicate handling, custom-symbol validation, insufficient/missing data, native period completion, OAuth redirect-code parsing, and F&O-to-equity instrument mapping.

## Official references

- [Upstox OAuth authentication](https://upstox.com/developer/api-documentation/authentication/)
- [Historical Candle Data V3](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/)
- [Intraday Candle Data V3](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/)
- [BOD instruments JSON](https://upstox.com/developer/api-documentation/instruments/)
- [Rate limits](https://upstox.com/developer/api-documentation/rate-limiting/)
- [NSE equity trading holidays](https://www.nseindia.com/resources/exchange-communication-holidays?apiversion=v2)
- [NSE 2026 Muhurat equity-session circular](https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf)
- [Nifty 50 constituents](https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv)
- [Nifty 500 constituents](https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv)
- [Streamlit Community Cloud deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
