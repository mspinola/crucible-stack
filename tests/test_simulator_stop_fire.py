"""stop_spec['fire'] on simulate_rules: how the structural stop fires.

  'close'    (default) the stop fires on a close through the level, filling at that close.
  'intraday' a resting stop order: fires when the bar touches the level, filling at the
             level, or at the open when the bar gaps through it.

Hand-built bars. Long side: the setup bar (index 2) has Low 98, so the wick stop is 98;
the next-open fill on bar 3 is 100, so risk is 2 and one R is 2 points. The barriers
exit is set wide (tp 100R, timeout 100) so it never interferes; a trade not stopped
rides to end of data.
"""
import pandas as pd
import pytest

from crucible_stack.engine.simulator import simulate_rules

EXIT = {"mode": "barriers", "tp": 100.0, "timeout": 100}


def _df(stop_bar, side="long"):
    """Flat 100 bars, a setup bar at 2 whose wick sits 2 points out, the fill bar at 3,
    `stop_bar` (O, H, L, C) at 4, then a run to 110 (long) or 90 (short)."""
    if side == "long":
        setup = (100, 100, 98, 100)
        run = [(102, 104, 101, 104), (104, 110, 104, 110)]
    else:
        setup = (100, 102, 100, 100)
        run = [(98, 99, 96, 96), (96, 96, 90, 90)]
    rows = [(100, 100, 100, 100)] * 2 + [setup, (100, 100, 100, 100), stop_bar] + run
    idx = pd.date_range("2020-01-01", periods=len(rows), freq="D")
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["ATR"] = 1.0
    return df


def _run(df, side="long", **stop):
    m = pd.Series(False, index=df.index)
    m.iloc[2] = True
    t = simulate_rules(df, side, False, m, m, {"mode": "wick", **stop}, EXIT)
    assert len(t) == 1
    return t.iloc[0]


def test_default_is_close_and_unchanged():
    df = _df((100, 100, 97, 99))
    a = _run(df)
    b = _run(df, fire="close")
    assert a["pct_return"] == b["pct_return"] and a["exit_reason"] == b["exit_reason"]


def test_a_wick_that_closes_back_above_rides_under_close_and_stops_intraday():
    # bar 4 trades to 97 (through the 98 stop) and closes at 99, back above it
    df = _df((100, 100, 97, 99))
    close = _run(df, fire="close")
    intra = _run(df, fire="intraday")
    assert close["exit_reason"] == "eod" and close["pct_return"] == pytest.approx(5.0)
    assert intra["exit_reason"] == "stop" and intra["pct_return"] == pytest.approx(-1.0)


def test_a_close_through_fills_at_the_close_vs_at_the_level():
    df = _df((100, 100, 96, 97))
    assert _run(df, fire="close")["pct_return"] == pytest.approx(-1.5)
    assert _run(df, fire="intraday")["pct_return"] == pytest.approx(-1.0)


def test_an_intraday_gap_through_the_level_fills_at_the_open():
    df = _df((96, 99, 95, 99))
    intra = _run(df, fire="intraday")
    assert intra["exit_reason"] == "stop" and intra["pct_return"] == pytest.approx(-2.0)


def test_a_touch_fires_the_intraday_stop():
    df = _df((100, 100, 98, 99))
    assert _run(df, fire="intraday")["exit_reason"] == "stop"
    assert _run(df, fire="close")["exit_reason"] == "eod"


def test_short_side_mirrors():
    df = _df((100, 103, 100, 101), side="short")
    close = _run(df, side="short", fire="close")
    intra = _run(df, side="short", fire="intraday")
    assert close["exit_reason"] == "eod" and close["pct_return"] == pytest.approx(5.0)
    assert intra["exit_reason"] == "stop" and intra["pct_return"] == pytest.approx(-1.0)


def test_an_unknown_fire_mode_raises():
    with pytest.raises(ValueError, match="fire"):
        _run(_df((100, 100, 97, 99)), fire="open")
