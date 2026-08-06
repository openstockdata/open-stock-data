import numpy as np
import pandas as pd

import open_stock_data.indicators as indicators


def _prices(size: int = 80) -> tuple[pd.Series, pd.Series, pd.Series]:
    index = pd.date_range("2025-01-01", periods=size)
    close = pd.Series(np.linspace(10.0, 30.0, size) + np.sin(np.arange(size)), index=index)
    high = close + 1.5
    low = close - 1.0
    return high, low, close


def test_calc_cci_matches_rolling_reference_with_nan():
    high, low, close = _prices()
    high.iloc[35] = np.nan
    period = 20
    typical_price = (high + low + close) / 3
    mean = typical_price.rolling(period).mean()
    mean_deviation = typical_price.rolling(period).apply(
        lambda values: np.abs(values - values.mean()).mean(),
        raw=True,
    )
    expected = (typical_price - mean) / (0.015 * mean_deviation.replace(0, np.nan))

    pd.testing.assert_series_equal(
        indicators.calc_cci(high, low, close, period),
        expected,
    )


def test_add_technical_indicators_reuses_true_range(monkeypatch):
    high, low, close = _prices()
    volume = pd.Series(np.linspace(1000, 2000, len(close)), index=close.index)
    frame = pd.DataFrame({"high": high, "low": low, "close": close, "volume": volume})
    original = indicators._true_range
    calls = 0

    def counting_true_range(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(indicators, "_true_range", counting_true_range)
    indicators.add_technical_indicators(frame, close, low, high, volume)

    assert calls == 1
    pd.testing.assert_series_equal(frame["ATR"], original(high, low, close).rolling(14).mean(), check_names=False)
    pd.testing.assert_frame_equal(
        frame[["ADX", "+DI", "-DI"]],
        indicators.calc_adx(high, low, close),
    )
