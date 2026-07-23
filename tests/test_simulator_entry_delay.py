"""entry_delay on simulate_rules: latency / adverse-execution stress.

Two guarantees:
  1. delay=0 reproduces the historical fill exactly (backward compatible).
  2. delay=d fills d bars later, so the entry moves d bars into the future.

Flat prices keep the exit trivial (no stop/target is ever hit, the trade rides to
end-of-data), so these assertions isolate the ENTRY, which is what entry_delay moves.
"""
import pandas as pd

from crucible_stack.engine.simulator import simulate_rules

STOP = {"mode": "atr", "mult": 2.0}
EXIT = {"mode": "barriers", "tp": 2.0, "timeout": 100}


def _flat_df(n=12, price=100.0, atr=1.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"Open": price, "High": price, "Low": price, "Close": price, "ATR": atr},
        index=idx,
    )


def _one_signal(df, at):
    m = pd.Series(False, index=df.index)
    m.iloc[at] = True
    return m, m  # setup == trigger


def test_entry_delay_zero_matches_the_default_call():
    df = _flat_df()
    setup, trig = _one_signal(df, at=3)
    base = simulate_rules(df, "long", False, setup, trig, STOP, EXIT)
    d0 = simulate_rules(df, "long", False, setup, trig, STOP, EXIT, entry_delay=0)
    pd.testing.assert_frame_equal(base, d0)


def test_entry_delay_shifts_the_fill_one_bar_later():
    df = _flat_df()
    setup, trig = _one_signal(df, at=3)
    d0 = simulate_rules(df, "long", False, setup, trig, STOP, EXIT, entry_delay=0)
    d1 = simulate_rules(df, "long", False, setup, trig, STOP, EXIT, entry_delay=1)
    assert len(d0) == 1 and len(d1) == 1
    # next_open fill: one extra bar of delay moves the entry exactly one bar later
    delta = d1["entry_date"].iloc[0] - d0["entry_date"].iloc[0]
    assert delta == pd.Timedelta(days=1)


def test_entry_delay_running_off_the_end_drops_the_trade():
    # signal on the second-to-last bar: a next-open fill needs bar i+1 (the last bar,
    # ok at delay 0) but i+2 does not exist, so delay=1 produces no trade.
    df = _flat_df(n=6)
    setup, trig = _one_signal(df, at=4)   # i = 4, n = 6, so i+1 = 5 exists, i+2 does not
    d0 = simulate_rules(df, "long", False, setup, trig, STOP, EXIT, entry_delay=0)
    d1 = simulate_rules(df, "long", False, setup, trig, STOP, EXIT, entry_delay=1)
    assert len(d0) == 1
    assert len(d1) == 0
