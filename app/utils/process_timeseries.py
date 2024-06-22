import numpy as np
import pandas as pd
from skmap.io.process import WhittakerSmooth
from statsmodels.tsa.seasonal import STL


def extract_ts(df, dt_5days):
    ts, dates = [], []

    for dt1, dt2 in dt_5days:
        df_dt = df.loc[(df['date'] >= dt1) & (df['date'] <= dt2)]
        ts.append(df_dt[df_dt['Pixel_used'] >= 70]['NDVI_median'].mean())
        dates.append((dt2 - pd.DateOffset(days=2)).strftime('%Y-%m-%d'))

    ts = np.stack([np.stack([ts])])
    return ts, dates


def smooth_ts(ts):
    smoothed_ts = WhittakerSmooth(ts[0, 0, :], lmbd=10)
    return smoothed_ts


def decompose_ts(ts, season_size):
    res = STL(ts, period=season_size).fit()
    return res.trend


def process_timeseries(df, dt_5days, season_size):
    ts, dates = extract_ts(df, dt_5days)
    smoothed_ts = smooth_ts(ts)
    trend = decompose_ts(smoothed_ts, season_size)

    trend_data = pd.DataFrame({
        'date': dates,
        'NDVI': ts[0, 0, :],
        'smoothed_NDVI': smoothed_ts,
        'trend_NDVI': trend
    })
    return trend_data
