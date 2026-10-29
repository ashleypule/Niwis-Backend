"""Forecasting engine: statistical forecasts of any live numeric catalogue column (docs/DECISIONS.md D29–D40).

    series.py       build the target series from the database (catalogue aggregation rule, area weighting)
    base.py         the model interface (`fit`, `predict`) every model implements
    baselines.py    naive, seasonal naive, climatology, 12-month moving average
    statistical.py  ETS, SARIMA, SARIMAX, Prophet
    ml.py           Random Forest and LightGBM on lag features
    deep.py         LSTM and Transformer encoder (experimental, PyTorch)
    multivariate.py VAR on standardised monthly series
    registry.py     every model, listed by `GET /api/v1/forecast/models`
    validation.py   gap filling inside a training window, rolling-origin backtest, metrics, intervals
    engine.py       one run: backtest, model selection (Auto), refit on all data, forecast, classes
    jobs.py         background worker processes, result cache, per-IP and queue limits, timeouts
    pages.py        precomputed Auto forecasts for the module pages' forecast endpoints
    router.py       `/api/v1/forecast/*`

Nothing is ever written to the database: forecasts live in memory and are recomputed when the data change.
"""
