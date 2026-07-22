# /// script
# requires-python = ">=3.10"
# dependencies = ["pandas>=2.0", "numpy>=1.24", "matplotlib>=3.7"]
# ///
"""
rsi_backtest.py — offline, bar-by-bar backtest of the RSI mean-reversion spec.

The exact same rule set as pine-script/rsi-mean-reversion.pine and
rsi-mean-reversion.md, in pure pandas/numpy so you can run it against a CSV
without TradingView. Zero discretion: signals compute on the CLOSE of day D,
target/time exits and entries fill at the OPEN of day D+1, and the hard stop is
a resting intraday stop-market (§5 variant) — it fires the moment the low
touches entry − k·ATR that same bar, gapping through to the open when the bar
opens below it. Size is fixed-fractional 1%-risk on k·ATR, commission 0.1%/side.

Because the strategy is flat for the first 200 bars (SMA200 must exist), the
Wilder RSI/ATR are fully converged before the first trade — so the recursive
seed used here is numerically identical to TradingView's ta.rsi / ta.atr by
the time any order fires.

CSV: needs a date column and a close column. Open/High/Low are used when
present (columns open/high/low, any case); if only close is available they are
synthesized as O=H=L=C and a warning is printed (fills and TR degrade to
close-to-close — fine for a smoke test, not for a headline number).

Usage:
  uv run rsi_backtest.py --csv btcusd_daily.csv
  uv run rsi_backtest.py --csv btcusd_daily.csv --no-gate        # GATE = OFF variant
  uv run rsi_backtest.py --csv btcusd_daily.csv --chart eq.png --trades trades.csv
  uv run rsi_backtest.py --csv btcusd_daily.csv --json
"""

import argparse
import json
import sys

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = 365          # crypto trades 7 days/week
FEE_PER_SIDE = 0.001            # 0.10% commission per side (matches the Pine strategy)


# ------------------------------------------------------------------ data loading
def load_ohlc(csv: str) -> pd.DataFrame:
    df = pd.read_csv(csv)
    cols = {c.lower().strip(): c for c in df.columns}

    def pick(*names, required=False, default=None):
        for n in names:
            if n in cols:
                return cols[n]
        if required:
            sys.exit(f"CSV missing a required column (one of {names})")
        return default

    date_col = pick("date", "time", "timestamp", required=True)
    close_col = pick("close", "price", "priceusd", "adj close", "adj_close", required=True)
    open_col = pick("open")
    high_col = pick("high")
    low_col = pick("low")

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[date_col])
    out["close"] = pd.to_numeric(df[close_col], errors="coerce")
    if open_col and high_col and low_col:
        out["open"] = pd.to_numeric(df[open_col], errors="coerce")
        out["high"] = pd.to_numeric(df[high_col], errors="coerce")
        out["low"] = pd.to_numeric(df[low_col], errors="coerce")
    else:
        print("WARNING: no OHLC columns found — synthesizing O=H=L=C from close. "
              "Fills and ATR will use close-to-close only.", file=sys.stderr)
        out["open"] = out["close"]
        out["high"] = out["close"]
        out["low"] = out["close"]

    out = out.dropna().sort_values("date").reset_index(drop=True)
    out = out[out["close"] > 0].reset_index(drop=True)
    if len(out) < 210:
        sys.exit(f"Need >= ~210 daily bars (200 warmup + trades); got {len(out)}.")
    return out


# ------------------------------------------------------------------ indicators
def wilder_rma(x: pd.Series, length: int) -> pd.Series:
    """Wilder's RMA == ewm(alpha=1/length, adjust=False). Converges to ta.rsi/ta.atr."""
    return x.ewm(alpha=1.0 / length, adjust=False).mean()


def rsi_wilder(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    avg_up = wilder_rma(up, length)
    avg_down = wilder_rma(down, length)
    rs = avg_up / avg_down
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi[avg_down == 0] = 100.0            # all-up window -> RSI 100 (matches spec §1.1)
    return rsi


def atr_wilder(df: pd.DataFrame, length: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return wilder_rma(tr, length)


# ------------------------------------------------------------------ backtest core
def backtest(df: pd.DataFrame, *, L=14, os_level=30.0, exit_level=50.0, sma_n=200,
             atr_n=14, k=2.5, n_max=10, risk_pct=1.0, lev_cap=1.0,
             use_gate=True, init_capital=100_000.0, fee=FEE_PER_SIDE):
    n = len(df)
    date = df["date"].to_numpy()
    o = df["open"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)

    rsi = rsi_wilder(df["close"], L).to_numpy()
    sma = df["close"].rolling(sma_n).mean().to_numpy()
    atr = atr_wilder(df, atr_n).to_numpy()

    cash = init_capital
    qty = 0.0
    entry_price = entry_atr = stop = 0.0
    entry_bar = -1
    entry_comm = 0.0
    entry_date = None

    pending = None                      # next-open order: {'action','qty','atr'} or {'action','reason'}
    trades = []
    equity_curve = np.empty(n)

    def record_exit(exit_price, exit_i, reason):
        """Book a closed trade; returns net cash delta (proceeds − exit commission)."""
        exit_comm = exit_price * qty * fee
        stop_dist_ = entry_price - stop
        net = (exit_price - entry_price) * qty - entry_comm - exit_comm
        r_mult = net / (qty * stop_dist_) if stop_dist_ > 0 else np.nan
        trades.append({
            "entry_date": pd.Timestamp(entry_date).date().isoformat(),
            "exit_date": pd.Timestamp(date[exit_i]).date().isoformat(),
            "bars_held": exit_i - entry_bar,
            "entry": round(entry_price, 2),
            "exit": round(exit_price, 2),
            "stop": round(stop, 2),
            "qty": round(qty, 8),
            "reason": reason,
            "pnl_usd": round(net, 2),
            "r_multiple": round(r_mult, 3) if not np.isnan(r_mult) else None,
            "return_pct": round(100.0 * net / init_capital, 3),
        })
        return exit_price * qty - exit_comm

    for i in range(n):
        # --- 1. Fill orders pending from yesterday's close, at TODAY's open ------
        #        (entries, and target/time exits — all market-on-next-open)
        if pending is not None:
            if pending["action"] == "BUY":
                entry_price = o[i]
                qty = pending["qty"]
                entry_atr = pending["atr"]
                entry_comm = entry_price * qty * fee
                cash -= entry_price * qty + entry_comm
                stop = entry_price - k * entry_atr      # resting stop, fixed at fill
                entry_bar = i
                entry_date = date[i]
            elif pending["action"] == "SELL":           # E1 target / E3 time
                cash += record_exit(o[i], i, pending["reason"])
                qty = 0.0
            pending = None

        # --- 2. Resting intraday stop-market (E2) — checked LIVE on this bar -----
        #        Fires the instant the low touches the stop; a gap-open below the
        #        stop fills at the open. Pre-empts any target/time exit.
        if qty > 0.0 and i >= entry_bar and l[i] <= stop:
            fill = min(o[i], stop)                       # gap-through -> open
            cash += record_exit(fill, i, "E2 stop")
            qty = 0.0

        # --- 3. Evaluate signals on TODAY's close (day D; fills tomorrow) --------
        ready = i >= sma_n and not np.isnan(sma[i]) and not np.isnan(atr[i]) and not np.isnan(rsi[i - 1])
        if ready:
            if qty == 0.0:
                oversold_cross = rsi[i] < os_level and rsi[i - 1] >= os_level
                gate_ok = (not use_gate) or (c[i] > sma[i])
                if oversold_cross and gate_ok:
                    equity_now = cash                    # flat -> equity == cash
                    stop_dist = k * atr[i]
                    q = ((risk_pct / 100.0) * equity_now) / stop_dist if stop_dist > 0 else 0.0
                    cap_q = (lev_cap * equity_now) / c[i]  # no-leverage cap (close proxy)
                    q = min(q, cap_q)
                    if q > 0:
                        pending = {"action": "BUY", "qty": q, "atr": atr[i]}
            else:
                bars_held = i - entry_bar
                if rsi[i] >= exit_level:                  # E1 target
                    pending = {"action": "SELL", "reason": "E1 target"}
                elif bars_held >= n_max:                  # E3 time
                    pending = {"action": "SELL", "reason": "E3 time"}

        # --- 4. Mark-to-market equity at today's close --------------------------
        equity_curve[i] = cash + qty * c[i]

    eq = pd.Series(equity_curve, index=df["date"])
    return eq, pd.DataFrame(trades)


# ------------------------------------------------------------------ metrics
def metrics(eq: pd.Series, trades: pd.DataFrame, df: pd.DataFrame,
            init_capital=100_000.0) -> dict:
    ret = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    final = float(eq.iloc[-1])
    cagr = (final / init_capital) ** (1.0 / years) - 1.0
    sharpe = (ret.mean() / ret.std() * np.sqrt(PERIODS_PER_YEAR)) if ret.std() > 0 else 0.0
    dd = eq / eq.cummax() - 1.0
    max_dd = float(dd.min())

    wins = trades[trades["pnl_usd"] > 0] if len(trades) else trades
    win_rate = (len(wins) / len(trades)) if len(trades) else 0.0
    avg_r = float(trades["r_multiple"].dropna().mean()) if len(trades) else 0.0
    exposure = float((trades["bars_held"].sum() / len(df))) if len(trades) else 0.0

    # buy & hold, same capital, first-to-last close
    bh_final = init_capital * float(df["close"].iloc[-1] / df["close"].iloc[0])
    bh_cagr = (bh_final / init_capital) ** (1.0 / years) - 1.0
    bh_ret = df.set_index("date")["close"].pct_change().dropna()
    bh_dd = (df["close"] / df["close"].cummax() - 1.0).min()

    by_reason = trades["reason"].value_counts().to_dict() if len(trades) else {}

    return {
        "period": f"{eq.index[0].date()} -> {eq.index[-1].date()} ({years:.2f}y)",
        "final_equity": round(final, 2),
        "total_return_pct": round(100.0 * (final / init_capital - 1.0), 2),
        "cagr_pct": round(100.0 * cagr, 2),
        "sharpe": round(float(sharpe), 2),
        "max_drawdown_pct": round(100.0 * max_dd, 2),
        "num_trades": int(len(trades)),
        "win_rate_pct": round(100.0 * win_rate, 1),
        "avg_r_multiple": round(avg_r, 3),
        "time_in_market_pct": round(100.0 * exposure, 1),
        "exits_by_reason": by_reason,
        "buyhold_final_equity": round(bh_final, 2),
        "buyhold_cagr_pct": round(100.0 * bh_cagr, 2),
        "buyhold_max_drawdown_pct": round(100.0 * float(bh_dd), 2),
    }


# ------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(description="Offline backtest of the RSI mean-reversion spec.")
    ap.add_argument("--csv", required=True, help="Daily BTC/USD CSV (date, close[, open, high, low])")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--risk-pct", type=float, default=1.0, help="Equity risked per trade (%%)")
    ap.add_argument("--k", type=float, default=2.5, help="Stop distance in ATRs")
    ap.add_argument("--n-max", type=int, default=10, help="Time stop (bars held)")
    ap.add_argument("--os", type=float, default=30.0, help="Oversold entry level")
    ap.add_argument("--exit", type=float, default=50.0, dest="exit_level", help="RSI exit level")
    ap.add_argument("--no-gate", action="store_true", help="Disable SMA200 regime gate (classic RSI-30)")
    ap.add_argument("--fee", type=float, default=FEE_PER_SIDE, help="Commission per side (fraction)")
    ap.add_argument("--trades", help="Write per-trade log to this CSV path")
    ap.add_argument("--chart", help="Write equity-curve PNG to this path")
    ap.add_argument("--json", action="store_true", help="Print metrics as JSON only")
    args = ap.parse_args()

    df = load_ohlc(args.csv)
    eq, trades = backtest(
        df, os_level=args.os, exit_level=args.exit_level, k=args.k, n_max=args.n_max,
        risk_pct=args.risk_pct, use_gate=not args.no_gate,
        init_capital=args.capital, fee=args.fee,
    )
    m = metrics(eq, trades, df, init_capital=args.capital)

    if args.trades and len(trades):
        trades.to_csv(args.trades, index=False)
    if args.chart:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(eq.index, eq.values, label="RSI mean-reversion", color="#0a7d38", lw=1.5)
        bh = args.capital * (df.set_index("date")["close"] / df["close"].iloc[0])
        ax.plot(bh.index, bh.values, label="Buy & hold BTC", color="#8a8a8a", lw=1.0, alpha=0.8)
        ax.set_title(f"RSI Mean-Reversion BTC/USD — gate={'OFF' if args.no_gate else 'ON'}")
        ax.set_ylabel("Equity ($)")
        ax.legend()
        ax.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(args.chart, dpi=130)
        print(f"chart -> {args.chart}", file=sys.stderr)

    if args.json:
        print(json.dumps(m, indent=2))
        return

    print(f"\nRSI Mean-Reversion — BTC/USD daily  (gate={'OFF' if args.no_gate else 'ON'}, "
          f"{args.fee*100:.2f}%/side, ${args.capital:,.0f} start)")
    print("=" * 66)
    label = {
        "period": "Period", "final_equity": "Final equity ($)",
        "total_return_pct": "Total return (%)", "cagr_pct": "CAGR (%)",
        "sharpe": "Sharpe", "max_drawdown_pct": "Max drawdown (%)",
        "num_trades": "Trades", "win_rate_pct": "Win rate (%)",
        "avg_r_multiple": "Avg R multiple", "time_in_market_pct": "Time in market (%)",
        "exits_by_reason": "Exits by reason",
        "buyhold_final_equity": "Buy&hold final ($)", "buyhold_cagr_pct": "Buy&hold CAGR (%)",
        "buyhold_max_drawdown_pct": "Buy&hold max DD (%)",
    }
    for key, name in label.items():
        val = m[key]
        val = json.dumps(val) if isinstance(val, dict) else val
        print(f"  {name:<24} {val}")
    if len(trades):
        print(f"\n  first trade: {trades.iloc[0]['entry_date']}   "
              f"last trade: {trades.iloc[-1]['exit_date']}")
    print()


if __name__ == "__main__":
    main()
