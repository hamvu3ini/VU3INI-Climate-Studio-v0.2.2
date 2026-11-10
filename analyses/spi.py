"""Standardised Precipitation Index (McKee et al. 1993), computed locally.

Rainfall accumulated over n months is fitted to a gamma distribution per
calendar month over the baseline years, with the probability of zero rain
handled separately, then transformed to a standard normal value.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def spi_series(df: pd.DataFrame, col: str, n: int, base_y0: int, base_y1: int) -> pd.DataFrame:
    """df: columns 'date' (month starts) and `col` (monthly totals). Returns df with an 'SPI' column."""
    d = df.sort_values("date").reset_index(drop=True).copy()
    d["acc"] = d[col].rolling(n, min_periods=n).sum()
    d["SPI"] = np.nan
    for m in range(1, 13):
        sel = d["date"].dt.month == m
        base = d.loc[sel & d["date"].dt.year.between(base_y0, base_y1), "acc"].dropna()
        vals = d.loc[sel, "acc"]
        if len(base) < 10:
            continue
        q = (base == 0).mean()
        pos = base[base > 0]
        if len(pos) < 5:
            continue
        a, loc, scale = stats.gamma.fit(pos, floc=0)
        cdf = q + (1 - q) * stats.gamma.cdf(vals.clip(lower=0), a, loc=loc, scale=scale)
        cdf = np.clip(cdf, 1e-6, 1 - 1e-6)
        d.loc[sel, "SPI"] = stats.norm.ppf(cdf)
    d["SPI"] = d["SPI"].round(2)
    return d.drop(columns=["acc"])
