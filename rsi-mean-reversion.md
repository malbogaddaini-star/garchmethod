# RSI Mean-Reversion — BTC/USD, Daily

A fully objective, zero-discretion rule set. Every rule below reduces to arithmetic
on **completed daily bars**. Nothing is evaluated intrabar; nothing looks ahead. If you
can compute an RSI, an SMA, and an ATR, you can run this exactly as written — two people
following it will place identical orders.

> Companion to the GARCH method (Episode 1). GARCH answers *how much*. This answers
> *when* — a classic mean-reversion timing engine you can size with fixed-risk ATR (below)
> or drop the GARCH vol-targeting layer on top of.

---

## 0. Universe, clock, and account model

| Item | Rule |
|---|---|
| Instrument | BTC/USD |
| Timeframe | Daily bars, **UTC 00:00 close** (one price source, fixed; never mix venues mid-backtest) |
| Bar fields | Open `O_t`, High `H_t`, Low `L_t`, Close `C_t` of day *t* |
| Positions | **One at a time.** No pyramiding, no averaging down, no re-entry same bar |
| Signal timing | All indicators computed on the **close of day D**. Orders execute at the **open of day D+1** (`O_{D+1}`) |
| Direction | Primary spec is **long/flat** (spot). Symmetric short in §7 for perps/futures |
| Costs (for backtest) | Assume round-trip fee + slippage = 0.10% of notional per side. Fixed constant |

No decision is ever made from a live/forming bar. A signal is valid only once the daily
bar that produced it has closed.

---

## 1. Indicators (exact formulas)

All use Wilder's smoothing (RMA), *not* simple or exponential MA, so numbers match TradingView/Wilder.

### 1.1 RSI, length L = 14
```
Uₜ = max(Cₜ − Cₜ₋₁, 0)          # up-move
Dₜ = max(Cₜ₋₁ − Cₜ, 0)          # down-move
AvgUₜ = (AvgUₜ₋₁·(L−1) + Uₜ) / L   # Wilder RMA
AvgDₜ = (AvgDₜ₋₁·(L−1) + Dₜ) / L
   seed: AvgU, AvgD = simple mean of first L values of U, D
RSₜ   = AvgUₜ / AvgDₜ
RSIₜ  = 100 − 100/(1 + RSₜ)      # if AvgDₜ = 0 → RSIₜ = 100
```

### 1.2 Trend gate, SMA length = 200
```
SMA200ₜ = mean(Cₜ₋₁₉₉ … Cₜ)
```

### 1.3 Volatility for stops & sizing, ATR length = 14 (Wilder)
```
TRₜ  = max(Hₜ − Lₜ, |Hₜ − Cₜ₋₁|, |Lₜ − Cₜ₋₁|)
ATRₜ = (ATRₜ₋₁·(14−1) + TRₜ) / 14     # seed with mean of first 14 TR
```

The strategy requires ≥ 200 completed bars of history before it may take its first trade
(SMA200 must be defined). Bars 1–199: flat, no exceptions.

---

## 2. Fixed parameters

These are constants. They are *tuned by backtest, never by opinion in the moment.* Once
set, they do not change trade to trade.

| Symbol | Meaning | Value |
|---|---|---|
| `L` | RSI length | 14 |
| `OS` | Oversold entry threshold | 30 |
| `EXIT` | Mean-reversion exit level | 50 |
| `SMA_N` | Trend-gate length | 200 |
| `ATR_N` | ATR length | 14 |
| `k` | Stop distance in ATRs | 2.5 |
| `N_max` | Time stop (bars in trade) | 10 |
| `risk` | Equity risked per trade | 1.0% (0.010) |
| `lev_cap` | Max notional / equity | 1.0 (spot: no leverage) |

---

## 3. Entry rule (long)

Enter **only when flat**. On the close of day **D**, fire a long signal if **all** are true:

1. **Oversold cross-down** — RSI just entered oversold:
   `RSI₁₄(D) < 30` **and** `RSI₁₄(D−1) ≥ 30`
2. **Regime gate** — mean-revert with the primary trend, not against it:
   `C(D) > SMA200(D)`

**Execution:** buy at `O_{D+1}` (open of the next day).

Rationale for the two conditions, stated so they're not mistaken for discretion:
the cross-down (condition 1, not merely "RSI < 30") gives exactly **one** trigger per
oversold episode, so the rule can't re-fire arbitrarily. The SMA200 gate (condition 2)
restricts buying-the-dip to bull regimes, where dips revert; buying RSI < 30 in a
confirmed downtrend is catching a falling knife and is explicitly forbidden.

> **Toggle (declare before the backtest, then leave alone):** `GATE = ON/OFF`.
> `OFF` removes condition 2 → the pure classic RSI-30 long. `ON` is the default and
> recommended. This is a *configuration* choice fixed up front, not an in-trade decision.

---

## 4. Exit rule (long)

While in a long position, evaluate on the close of each day **D**. Exit at `O_{D+1}`
when **any** of the following is true — whichever comes first:

| # | Exit | Condition on close of D |
|---|---|---|
| E1 | **Target — reversion to mean** | `RSI₁₄(D) ≥ 50` |
| E2 | **Stop — volatility stop hit** | `C(D) ≤ stop_price` (see §5) |
| E3 | **Time stop — thesis expired** | bars held `≥ N_max = 10` |

Priority if two trigger on the same bar: **E2 (stop) > E3 (time) > E1 (target)** — the
protective exit always wins. All exits are market-on-next-open. There is no trailing,
no partial exit, no scaling out — one entry, one exit.

> **Variant (fixed up front):** replace E1's `≥ 50` with `≥ 70` to "let winners run" to
> overbought. Default is 50: the mean-reversion thesis is *revert to the mean*, and the
> mean of RSI is 50. Higher win rate, smaller average win.

---

## 5. Stop logic (exact)

The stop is fixed at entry and does not move (no trailing) for the primary spec.

```
entry_price = O_{D+1}                       # actual fill
ATR_entry   = ATR₁₄(D)                       # ATR from the signal bar's close
stop_price  = entry_price − k · ATR_entry    # k = 2.5
```

- The stop is evaluated **on the daily close** (E2 above): if `C(D) ≤ stop_price`, exit at
  `O_{D+1}`. Close-through (not intraday-touch) is chosen deliberately to ignore wick noise
  — it is fully objective and reduces stop-hunt whipsaw.
- **Intraday-touch variant (fixed up front):** place a resting stop-market at `stop_price`;
  exit the instant `L_t ≤ stop_price` at `stop_price` (model 0.10% slippage). Tighter risk,
  more stop-outs. Pick one *before* the backtest; do not switch per trade.

`stop_price` is also the denominator of the position-sizing formula (§6), so the stop and
the size are the same decision.

---

## 6. Position sizing (fully computable)

**Fixed-fractional risk.** Each trade risks exactly `risk = 1%` of current equity between
entry and stop. Size is therefore mechanical:

```
Equity        = current account equity in USD (mark-to-market at signal time)
risk_usd      = 0.010 · Equity
stop_distance = entry_price − stop_price = k · ATR_entry   # = 2.5 · ATR₁₄(D)

qty_btc       = risk_usd / stop_distance                    # units of BTC
notional      = qty_btc · entry_price

# no-leverage cap (spot):
if notional > lev_cap · Equity:            # lev_cap = 1.0
    qty_btc = (lev_cap · Equity) / entry_price
```

Interpretation: a wide-ATR (violent) market gives a large `stop_distance`, so `qty_btc`
shrinks automatically — smaller size in storms, larger in calm — for a **constant 1%
dollar risk** on every trade regardless of BTC's volatility.

**Optional GARCH overlay (composition):** multiply `qty_btc` by
`min(1, target_vol / forecast_vol)` from the GARCH skill to also cap *portfolio* vol, not
just per-trade risk. Both layers are computable; the ATR layer alone is fully self-contained.

Rounding: `qty_btc` floored to the exchange's lot size (a fixed constant, e.g. 1e-5 BTC).

---

## 7. Symmetric short (perps/futures only — optional)

Mirror image, same constants:

- **Entry (short):** flat, and on close of D: `RSI₁₄(D) > 70` **and** `RSI₁₄(D−1) ≤ 70`
  **and** `C(D) < SMA200(D)`. Sell at `O_{D+1}`.
- **Exit:** E1 `RSI₁₄(D) ≤ 50`; E2 `C(D) ≥ stop_price` where
  `stop_price = entry_price + 2.5·ATR_entry`; E3 time stop 10 bars.
- **Size:** `qty_btc = risk_usd / (stop_price − entry_price)`.

For a spot BTC account, run **long/flat only** (skip §7 entirely).

---

## 8. The whole thing as a daily decision (pseudocode)

Run once, after each daily bar closes.

```python
def on_daily_close(D, state, equity):
    rsi   = RSI14(D);  rsi_prev = RSI14(D-1)
    sma   = SMA200(D); atr = ATR14(D); close = C(D)

    if state.flat:
        oversold_cross = (rsi < 30) and (rsi_prev >= 30)
        regime_ok      = close > sma                      # GATE = ON
        if oversold_cross and regime_ok:
            state.pending = "BUY_AT_OPEN"                  # fills at O_{D+1}
        return

    # in a long position:
    bars_held = D - state.entry_bar
    hit_stop  = close <= state.stop_price                 # E2
    hit_time  = bars_held >= 10                            # E3
    hit_tgt   = rsi >= 50                                  # E1
    if hit_stop or hit_time or hit_tgt:                    # priority E2>E3>E1
        state.pending = "SELL_AT_OPEN"

def on_next_open(D1, state, equity):
    if state.pending == "BUY_AT_OPEN":
        entry = O(D1)
        stop  = entry - 2.5 * state.atr_at_signal
        qty   = (0.010 * equity) / (entry - stop)
        qty   = min(qty, (1.0 * equity) / entry)          # no leverage
        open_long(qty, entry, stop)
    elif state.pending == "SELL_AT_OPEN":
        close_long(at=O(D1))
    state.pending = None
```

That is the complete strategy. There is no input to it that is not a number computed from
past closed bars, and no branch that depends on judgment.

---

## 9. What is *not* allowed (so "no discretion" is enforceable)

- ❌ Overriding a stop, target, or time stop because a move "looks like" it'll come back.
- ❌ Averaging down / adding to a loser, or taking a second position in BTC.
- ❌ Changing any §2 parameter mid-run. Re-tuning happens on a fresh out-of-sample test, dated and logged — never live.
- ❌ Acting on an unclosed bar, a lower timeframe, or a different price source than the backtest used.
- ❌ Skipping a valid signal, or taking an invalid one, for any reason.

If a rule and your gut disagree, the rule wins. That is the entire point of writing it down.

---

## 10. Minimal backtest checklist

1. Pull ≥ 3 years of daily BTC/USD from **one** source; keep O/H/L/C.
2. Compute RSI14, SMA200, ATR14 with the exact formulas in §1.
3. Walk forward bar by bar; signals on close, fills on next open; deduct 0.10%/side.
4. Log every trade: entry/exit date, price, exit reason (E1/E2/E3), % risked, R multiple.
5. Report: CAGR, Sharpe, max drawdown, win rate, avg R, worst month — and compare against
   buy-and-hold BTC and against the `GATE = OFF` variant. Let the numbers pick the config;
   then freeze it.
