"""
strategies/simulator.py — the rules "truth-teller".

`simulate_rules` is the generalized, per-trade R-multiple event simulator that the
whole Pardo scoring stack consumes. It is the faithful engine (PureEdge-style, full
control).

Generalizes `validation.cmr_native_holdout.simulate_cmr_native`:
  • setup / trigger are arbitrary boolean Series (from a RulesStrategy)
  • stop_spec  ∈ {'wick'} | {'atr', mult} | {'atr_trail', mult, arm_r}
  • exit_spec  ∈ any REGISTERED exit mode (see `strategies/exits.py`); the engine ships
    {'barriers', tp, timeout} | {'channel'} | {'atr_trail', mult} | {'reject_exit', reject_thr},
    and strategy-specific modes such as {'cot_neutral', legs} register themselves
    (npf.strategies.cot_exits). Default: 'barriers' — the only mode needing nothing but OHLC.
  • entry_fill ∈ 'close' | 'next_open'
  • friction: `cost_r` R deducted per trade (0 reproduces cmr_native exactly)
Emits the pure_edge trade-log columns; `pct_return` is in R (1R = entry→stop risk).
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from crucible_stack.engine.exits import ATR_STOP_MULT, DEFAULT_EXIT_MODE, get_exit
from crucible_stack.framework.strategy import TRADE_LOG_COLUMNS

__all__ = ["simulate_rules"]


def simulate_rules(df: pd.DataFrame, direction: str, is_equity: bool,
                   setup_mask: pd.Series, trigger_mask: pd.Series,
                   stop_spec: Dict[str, Any], exit_spec: Dict[str, Any],
                   entry_fill: str = 'next_open',
                   symbol: str = '', asset_class: str = 'Unknown',
                   cost_r: float = 0.0) -> pd.DataFrame:
    """Run one direction of a rules strategy over `df` → per-trade R-multiple log."""
    side = 1 if direction == 'long' else -1
    stop_mode = stop_spec.get('mode', 'wick')
    atr_mult = float(stop_spec.get('mult', ATR_STOP_MULT))
    arm_r = float(stop_spec.get('arm_r', 1.0))   # atr_trail: arm at k×R profit
    hard_r = stop_spec.get('hard_r')             # fail-safe intraday k×R stop (None = off)
    hard_r = float(hard_r) if hard_r is not None else None
    exit_mode = exit_spec.get('mode', DEFAULT_EXIT_MODE)
    exit_cls = get_exit(exit_mode)   # raises listing registered modes if unknown

    rows = df.to_dict('records')
    dates = df.index.tolist()
    setup = np.asarray(setup_mask.values, dtype=bool)
    trig = np.asarray(trigger_mask.values, dtype=bool)
    n = len(rows)

    trades = []
    i = 0
    while i < n - 1:
        if not (trig[i] and setup[i]):
            i += 1
            continue
        setup_close = rows[i]['Close']
        atr_i = rows[i].get('ATR', 0.0)

        # ── entry fill ───────────────────────────────────────────────────────
        if entry_fill == 'close':
            entry, entry_bar = setup_close, i
        else:
            entry, entry_bar = rows[i + 1]['Open'], i + 1

        # ── stop level + risk ────────────────────────────────────────────────
        # Risk is the ACTUAL entry-to-stop distance. With entry_fill='next_open'
        # the fill can gap away from the signal close, so measuring risk from the
        # setup close would mis-state the R-unit (a full stop-out must be −1R). For
        # entry_fill='close' entry == setup_close, so this is unchanged.
        if stop_mode == 'atr':
            if not (atr_i > 0):
                i += 1
                continue
            risk = atr_mult * atr_i
            stop = entry - risk if side > 0 else entry + risk
        else:  # 'wick' / 'atr_trail' — structural initial stop (setup bar Low/High)
            stop = rows[i]['Low'] if side > 0 else rows[i]['High']
            risk = (entry - stop) if side > 0 else (stop - entry)
            if not (risk > 0) or (atr_i > 0 and risk < 0.05 * atr_i):  # degenerate/gapped-past-stop
                i += 1
                continue

        # The exit rule is per-trade state plus a per-bar test; it is constructed here,
        # once `risk` is known, and consulted inside the loop. `tp_price` is recorded on
        # the trade row and only the barriers rule defines one.
        rule = exit_cls(side=side, entry=entry, risk=risk, atr_at_entry=atr_i,
                        direction=direction, is_equity=is_equity, spec=exit_spec)
        tp_price = getattr(rule, 'tp_price', np.nan)

        # ── trailing-stop state (stop_mode == 'atr_trail') ───────────────────
        # Keep the structural wick stop until +arm_r×R, then ride an ATR trail off
        # the best close so far. Ratchet only (max/min clamp) so the stop never
        # loosens below the initial wick stop — risk stays ≤ the initial R.
        trailing = stop_mode == 'atr_trail'
        run_ext = entry           # running extreme close since entry
        armed = False
        arm_gain = arm_r * risk   # profit (price units) needed to arm the trail

        # ── excursion tracking (price units; MFE ≥ 0, MAE ≤ 0) ───────────────
        # Running best favorable / worst adverse intrabar move since entry, plus a
        # snapshot at the 10th held bar for the pre-exit E-Ratio. Divided by risk
        # at append time so mfe/mae land in R units (same as pct_return).
        mfe_abs, mae_abs = 0.0, 0.0
        mfe10_abs = mae10_abs = None

        # ── exit loop ────────────────────────────────────────────────────────
        exit_price = exit_reason = None
        j = i + 1
        while j < n:
            bar = rows[j]
            held = j - entry_bar
            # accumulate excursion over every held bar, including the exit bar
            fav = (bar['High'] - entry) if side > 0 else (entry - bar['Low'])
            adv = (bar['Low'] - entry) if side > 0 else (entry - bar['High'])
            mfe_abs = max(mfe_abs, fav)
            mae_abs = min(mae_abs, adv)
            if held == 10:
                mfe10_abs, mae10_abs = mfe_abs, mae_abs
            # fail-safe intraday hard stop (checked on the bar Low/High, before the
            # close-based stop) — caps the disaster trades the close-based stop lets
            # gap through. Fills at the level, or at the open if the bar gapped past
            # it (an honest floor on the loss, ~ -hard_r×R except on a gap open).
            if hard_r is not None:
                hlv = entry - hard_r * risk if side > 0 else entry + hard_r * risk
                if side > 0 and bar['Low'] <= hlv:
                    exit_price, exit_reason = min(hlv, bar['Open']), 'hard'
                    break
                if side < 0 and bar['High'] >= hlv:
                    exit_price, exit_reason = max(hlv, bar['Open']), 'hard'
                    break
            # close-based stop breach (shared by both exit modes)
            if side > 0 and bar['Close'] < stop:
                exit_price, exit_reason = bar['Close'], 'stop'
                break
            if side < 0 and bar['Close'] > stop:
                exit_price, exit_reason = bar['Close'], 'stop'
                break

            # The exit rule runs exactly where the if/elif chain used to: after the
            # hard/close stop checks, before the trailing-stop ratchet. It may update
            # its own state on the way past — that is how the ATR trail ratchets.
            hit = rule.check(bar, held)
            if hit is not None:
                exit_price, exit_reason = hit
                break

            # Ratchet the ATR trail (after this bar's breach check, for the next
            # bar). No look-ahead: the stop that bar j was tested against was set
            # from bars < j.
            if trailing:
                c = bar['Close']
                run_ext = max(run_ext, c) if side > 0 else min(run_ext, c)
                if not armed and (c - entry if side > 0 else entry - c) >= arm_gain:
                    armed = True
                batr = bar.get('ATR', 0.0)
                if armed and batr > 0:
                    cand = run_ext - atr_mult * batr if side > 0 else run_ext + atr_mult * batr
                    stop = max(stop, cand) if side > 0 else min(stop, cand)
            j += 1
        if exit_price is None:
            exit_price, exit_reason, j = rows[-1]['Close'], 'eod', n - 1

        r = side * (exit_price - entry) / risk - cost_r
        # excursions in R (fall back to full-trade extent if it exited before bar 10)
        mfe10 = mfe_abs if mfe10_abs is None else mfe10_abs
        mae10 = mae_abs if mae10_abs is None else mae10_abs
        trades.append({
            'symbol': symbol, 'asset_class': asset_class,
            'entry_date': dates[entry_bar], 'exit_date': dates[j], 'side': side,
            'entry_price': entry, 'exit_price': exit_price,
            'tp_price': tp_price, 'sl_price': stop, 'exit_reason': exit_reason,
            'pct_return': r, 'mfe': mfe_abs / risk, 'mae': mae_abs / risk,
            'mfe_10': mfe10 / risk, 'mae_10': mae10 / risk,
            'bars_held': j - entry_bar, 'prob_success': np.nan,
        })
        i = j + 1

    return pd.DataFrame(trades, columns=TRADE_LOG_COLUMNS)
