# Forecast validation on the NIWIS database

> Generated 2026-10-29 by `python scripts/forecast_validation.py` (read-only, Supabase). National series, built exactly as the API builds them; every model through the same rolling-origin backtest (4 origins, `min(h, 12)` months apart, each fitted only on data before its origin). **Nothing was tuned** for these numbers: the models run with the settings in `app/forecasting/` (DECISIONS D32–D34).

**Skill** = 1 − squared error ÷ squared error of the same-month climatology on the same backtest points: > 0 beats the long-term average, ≤ 0 does not (the page then says so). **MASE** < 1 beats the seasonal naive forecast. **Auto** = the lowest MASE among non-experimental models — what the page forecasts use. Experimental models (LSTM, Transformer) are scored for comparison only.

## Horizon 24 months

### Auto's choice

| Variable | Series | Auto picks | Skill vs climatology | MASE | Band coverage | Beats climatology | Run time (s) |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: |
| SPI-3 (—) | 2000-01 – 2026-08, 320 months | Climatology (same-month mean) | 0.00 | 0.77 | 78 % | **no** | 154.2 |
| SPI-6 (—) | 2000-01 – 2026-08, 320 months | Climatology (same-month mean) | 0.00 | 0.72 | 74 % | **no** | 35.8 |
| SPEI-3 (—) | 2000-01 – 2026-08, 320 months | SARIMA (order by AIC) | 0.02 | 0.75 | 95 % | yes | 42.9 |
| Area in drought (D1–D4) (%) | 2000-01 – 2026-08, 320 months | Climatology (same-month mean) | 0.00 | 0.84 | 88 % | **no** | 42.0 |
| Monthly rainfall (mm) | 2000-01 – 2026-08, 320 months | Climatology (same-month mean) | 0.00 | 0.88 | 89 % | **no** | 39.4 |
| SPI-1 (—) | 2000-01 – 2026-08, 320 months | Random Forest | -0.04 | 0.88 | 85 % | **no** | 43.9 |
| Temperature anomaly (°C) | 2000-01 – 2026-08, 320 months | SARIMA (order by AIC) | 0.00 | 0.68 | 84 % | yes | 39.9 |
| Hot days (Tmax > 35 °C) (days/month) | 2000-01 – 2026-08, 320 months | SARIMA (order by AIC) | 0.02 | 1.09 | 76 % | yes | 22.4 |
| NDVI (—) | 2001-01 – 2025-12, 300 months | Seasonal naive (same period last year) | 0.32 | 1.01 | 75 % | yes | 69.7 |
| VCI (%) | 2001-01 – 2025-12, 300 months | ETS / Holt-Winters | 0.28 | 1.07 | 93 % | yes | 51.3 |
| Monthly runoff (mm/month) | 2000-01 – 2026-08, 320 months | Climatology (same-month mean) | 0.00 | 0.91 | 89 % | **no** | 40.5 |
| Runoff anomaly (%) | 2000-01 – 2026-08, 320 months | Random Forest | 0.00 | 0.74 | 83 % | yes | 29.1 |
| River discharge (m³/s) | 2000-01 – 2026-08, 320 months | Climatology (same-month mean) | 0.00 | 1.18 | 86 % | **no** | 39.3 |
| gw-depth | — | **not forecast**: 38% of the 321 months between Jan 2000 and Sep 2026 have no value (at most 20% can be filled) | — | — | — | — | 0.0 |

### Skill against climatology, every model

| Variable | Naive (last value) | Seasonal naive (same period last year) | Climatology (same-month mean) | 12-month moving average | ETS / Holt-Winters | SARIMA (order by AIC) | SARIMAX (with input variables) | Prophet | Random Forest | Gradient boosting (LightGBM) | LSTM (experimental) | Transformer encoder (experimental) | VAR (target + inputs) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SPI-3 | -3.95 | -1.04 | **0.00** | -1.73 | -0.01 | -0.11 | n/a | -0.45 | -0.08 | -0.43 | -0.11 | -0.21 | n/a |
| SPI-6 | -4.16 | -1.42 | **0.00** | -2.24 | -1.01 | -0.08 | n/a | -0.79 | -0.35 | -0.91 | -0.23 | -0.52 | n/a |
| SPEI-3 | -0.39 | -1.35 | 0.00 | -1.01 | -0.04 | **0.02** | n/a | -0.45 | -0.02 | -0.45 | -0.54 | -0.30 | n/a |
| Area in drought (D1–D4) | -7.08 | -1.46 | **0.00** | -1.54 | -1.96 | -0.33 | n/a | -0.65 | -0.09 | -0.24 | -0.07 | -0.25 | n/a |
| Monthly rainfall | -3.76 | -1.22 | **0.00** | -1.38 | -0.03 | -0.00 | n/a | -0.13 | -0.09 | -0.17 | -0.06 | -0.13 | n/a |
| SPI-1 | -2.47 | -0.96 | 0.00 | -0.80 | -0.04 | -0.01 | n/a | -0.16 | **-0.04** | -0.41 | 0.01 | -0.12 | n/a |
| Temperature anomaly | -0.08 | -1.37 | 0.00 | -0.19 | -0.17 | **0.00** | n/a | 0.01 | -0.04 | -0.29 | -0.00 | -0.07 | n/a |
| Hot days (Tmax > 35 °C) | -2.16 | -0.89 | 0.00 | -1.34 | -0.01 | **0.02** | n/a | 0.05 | -0.11 | -0.11 | -0.15 | -0.18 | n/a |
| NDVI | -2.51 | **0.32** | 0.00 | -1.88 | 0.11 | 0.13 | n/a | 0.06 | -0.16 | -0.33 | 0.14 | 0.32 | n/a |
| VCI | 0.20 | 0.15 | 0.00 | 0.18 | **0.28** | 0.12 | n/a | -0.12 | -0.31 | -0.41 | 0.04 | 0.16 | n/a |
| Monthly runoff | -3.38 | -1.23 | **0.00** | -1.25 | -0.01 | -0.01 | n/a | -0.13 | -0.05 | -0.05 | -0.04 | -0.01 | n/a |
| Runoff anomaly | -0.67 | -1.08 | 0.00 | -0.17 | -0.12 | -0.02 | n/a | -0.29 | **0.00** | -0.24 | 0.01 | -0.11 | n/a |
| River discharge | -1.60 | -0.83 | **0.00** | -0.72 | -0.00 | -0.22 | n/a | -0.02 | -0.12 | -0.06 | -0.18 | 0.05 | n/a |

### MASE, every model

| Variable | Naive (last value) | Seasonal naive (same period last year) | Climatology (same-month mean) | 12-month moving average | ETS / Holt-Winters | SARIMA (order by AIC) | SARIMAX (with input variables) | Prophet | Random Forest | Gradient boosting (LightGBM) | LSTM (experimental) | Transformer encoder (experimental) | VAR (target + inputs) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SPI-3 | 1.77 | 1.11 | 0.77 | 1.44 | 0.79 | 0.81 | n/a | 0.94 | 0.80 | 0.97 | 0.81 | 0.84 | n/a |
| SPI-6 | 1.61 | 1.18 | 0.72 | 1.41 | 1.10 | 0.76 | n/a | 1.02 | 0.83 | 1.02 | 0.85 | 0.92 | n/a |
| SPEI-3 | 0.90 | 1.17 | 0.75 | 1.12 | 0.76 | 0.75 | n/a | 0.94 | 0.76 | 0.92 | 0.92 | 0.82 | n/a |
| Area in drought (D1–D4) | 2.65 | 1.26 | 0.84 | 1.55 | 1.53 | 0.96 | n/a | 1.18 | 0.88 | 0.86 | 0.85 | 0.90 | n/a |
| Monthly rainfall | 1.91 | 1.24 | 0.88 | 1.51 | 0.89 | 0.88 | n/a | 0.94 | 0.92 | 0.93 | 0.90 | 0.91 | n/a |
| SPI-1 | 1.61 | 1.18 | 0.89 | 1.26 | 0.90 | 0.89 | n/a | 0.94 | 0.88 | 0.99 | 0.88 | 0.93 | n/a |
| Temperature anomaly | 0.72 | 1.10 | 0.70 | 0.77 | 0.76 | 0.68 | n/a | 0.73 | 0.70 | 0.79 | 0.70 | 0.71 | n/a |
| Hot days (Tmax > 35 °C) | 1.98 | 1.53 | 1.10 | 2.20 | 1.18 | 1.09 | n/a | 1.34 | 1.17 | 1.16 | 1.14 | 1.20 | n/a |
| NDVI | 2.43 | 1.01 | 1.24 | 2.20 | 1.21 | 1.13 | n/a | 1.18 | 1.28 | 1.40 | 1.14 | 0.99 | n/a |
| VCI | 1.17 | 1.17 | 1.25 | 1.16 | 1.07 | 1.13 | n/a | 1.32 | 1.46 | 1.55 | 1.22 | 1.15 | n/a |
| Monthly runoff | 1.87 | 1.28 | 0.91 | 1.52 | 0.91 | 0.91 | n/a | 0.97 | 0.91 | 0.93 | 0.93 | 0.89 | n/a |
| Runoff anomaly | 0.98 | 1.06 | 0.76 | 0.80 | 0.77 | 0.74 | n/a | 0.81 | 0.74 | 0.84 | 0.76 | 0.78 | n/a |
| River discharge | 2.03 | 1.69 | 1.18 | 1.96 | 1.27 | 1.42 | n/a | 1.26 | 1.27 | 1.21 | 1.31 | 1.12 | n/a |

### Fit time per model (s, all origins + no refit)

| Variable | Naive (last value) | Seasonal naive (same period last year) | Climatology (same-month mean) | 12-month moving average | ETS / Holt-Winters | SARIMA (order by AIC) | SARIMAX (with input variables) | Prophet | Random Forest | Gradient boosting (LightGBM) | LSTM (experimental) | Transformer encoder (experimental) | VAR (target + inputs) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SPI-3 | 0.0 | 0.0 | 0.1 | 0.0 | 4.7 | 11.4 | n/a | 31.7 | 43.8 | 3.5 | 47.5 | 11.4 | n/a |
| SPI-6 | 0.0 | 0.0 | 0.0 | 0.0 | 0.8 | 11.7 | n/a | 1.0 | 3.9 | 0.3 | 6.8 | 11.3 | n/a |
| SPEI-3 | 0.0 | 0.0 | 0.0 | 0.0 | 1.1 | 13.9 | n/a | 1.6 | 4.4 | 0.4 | 7.9 | 8.3 | n/a |
| Area in drought (D1–D4) | 0.0 | 0.0 | 0.0 | 0.0 | 3.2 | 5.4 | n/a | 1.8 | 5.5 | 0.5 | 12.1 | 13.4 | n/a |
| Monthly rainfall | 0.0 | 0.0 | 0.0 | 0.0 | 1.7 | 7.6 | n/a | 1.9 | 5.8 | 0.5 | 10.3 | 11.7 | n/a |
| SPI-1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.8 | 16.5 | n/a | 2.2 | 5.0 | 0.4 | 7.1 | 10.9 | n/a |
| Temperature anomaly | 0.0 | 0.0 | 0.0 | 0.0 | 0.8 | 14.6 | n/a | 4.0 | 5.3 | 0.4 | 4.2 | 7.6 | n/a |
| Hot days (Tmax > 35 °C) | 0.0 | 0.0 | 0.0 | 0.0 | 0.8 | 4.4 | n/a | 1.6 | 4.4 | 0.3 | 4.2 | 5.4 | n/a |
| NDVI | 0.0 | 0.0 | 0.0 | 0.0 | 0.8 | 21.9 | n/a | 6.2 | 5.0 | 0.3 | 13.6 | 17.6 | n/a |
| VCI | 0.0 | 0.0 | 0.0 | 0.0 | 1.7 | 19.2 | n/a | 4.5 | 5.8 | 0.4 | 7.2 | 12.2 | n/a |
| Monthly runoff | 0.0 | 0.0 | 0.0 | 0.0 | 1.3 | 11.4 | n/a | 2.6 | 4.4 | 0.3 | 6.2 | 10.8 | n/a |
| Runoff anomaly | 0.0 | 0.0 | 0.0 | 0.0 | 1.2 | 7.7 | n/a | 2.0 | 4.8 | 0.4 | 5.3 | 6.0 | n/a |
| River discharge | 0.0 | 0.0 | 0.0 | 0.0 | 0.4 | 14.7 | n/a | 0.9 | 5.2 | 0.4 | 6.2 | 8.4 | n/a |

## Horizon 3 months

### Auto's choice

| Variable | Series | Auto picks | Skill vs climatology | MASE | Band coverage | Beats climatology | Run time (s) |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: |
| SPI-3 (—) | 2000-01 – 2026-08, 320 months | Gradient boosting (LightGBM) | 0.51 | 0.69 | 58 % | yes | 34.9 |
| SPI-6 (—) | 2000-01 – 2026-08, 320 months | Gradient boosting (LightGBM) | 0.84 | 0.38 | 58 % | yes | 40.7 |
| SPEI-3 (—) | 2000-01 – 2026-08, 320 months | Gradient boosting (LightGBM) | 0.43 | 0.59 | 67 % | yes | 47.1 |
| Area in drought (D1–D4) (%) | 2000-01 – 2026-08, 320 months | Seasonal naive (same period last year) | 0.28 | 0.71 | 75 % | yes | 25.2 |
| Monthly rainfall (mm) | 2000-01 – 2026-08, 320 months | Gradient boosting (LightGBM) | 0.43 | 0.60 | 75 % | yes | 31.3 |
| SPI-1 (—) | 2000-01 – 2026-08, 320 months | Seasonal naive (same period last year) | 0.29 | 0.67 | 67 % | yes | 35.8 |
| Temperature anomaly (°C) | 2000-01 – 2026-08, 320 months | Climatology (same-month mean) | 0.00 | 0.54 | 42 % | **no** | 30.3 |
| Hot days (Tmax > 35 °C) (days/month) | 2000-01 – 2026-08, 320 months | Climatology (same-month mean) | 0.00 | 0.56 | 75 % | **no** | 26.7 |
| NDVI (—) | 2001-01 – 2025-12, 300 months | ETS / Holt-Winters | 0.68 | 0.99 | 75 % | yes | 42.0 |
| VCI (%) | 2001-01 – 2025-12, 300 months | Naive (last value) | 0.85 | 0.59 | 58 % | yes | 44.8 |
| Monthly runoff (mm/month) | 2000-01 – 2026-08, 320 months | SARIMA (order by AIC) | 0.05 | 0.74 | 67 % | yes | 45.1 |
| Runoff anomaly (%) | 2000-01 – 2026-08, 320 months | 12-month moving average | 0.20 | 0.73 | 58 % | yes | 24.9 |
| River discharge (m³/s) | 2000-01 – 2026-08, 320 months | Seasonal naive (same period last year) | 0.35 | 1.39 | 67 % | yes | 30.0 |
| gw-depth | — | **not forecast**: 38% of the 321 months between Jan 2000 and Sep 2026 have no value (at most 20% can be filled) | — | — | — | — | 0.0 |

### Skill against climatology, every model

| Variable | Naive (last value) | Seasonal naive (same period last year) | Climatology (same-month mean) | 12-month moving average | ETS / Holt-Winters | SARIMA (order by AIC) | SARIMAX (with input variables) | Prophet | Random Forest | Gradient boosting (LightGBM) | LSTM (experimental) | Transformer encoder (experimental) | VAR (target + inputs) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SPI-3 | -1.24 | 0.59 | 0.00 | -0.71 | -0.30 | 0.26 | n/a | -0.15 | 0.40 | **0.51** | 0.20 | 0.09 | n/a |
| SPI-6 | -0.49 | 0.64 | 0.00 | -0.77 | 0.61 | 0.67 | n/a | -0.30 | 0.79 | **0.84** | 0.63 | 0.63 | n/a |
| SPEI-3 | 0.67 | 0.56 | 0.00 | 0.64 | -0.22 | 0.36 | n/a | -0.38 | 0.41 | **0.43** | -0.17 | 0.24 | n/a |
| Area in drought (D1–D4) | -0.95 | **0.28** | 0.00 | -0.63 | -0.42 | -0.33 | n/a | -0.21 | 0.13 | 0.01 | -0.03 | 0.22 | n/a |
| Monthly rainfall | -1.25 | 0.05 | 0.00 | -1.68 | -0.06 | 0.05 | n/a | -0.07 | 0.23 | **0.43** | -0.11 | -0.03 | n/a |
| SPI-1 | -0.72 | **0.29** | 0.00 | -0.71 | -0.03 | 0.01 | n/a | -0.19 | 0.12 | -0.06 | 0.09 | 0.05 | n/a |
| Temperature anomaly | -1.38 | -0.72 | **0.00** | 0.08 | -0.22 | 0.10 | n/a | 0.02 | -0.30 | -0.78 | -0.24 | -0.28 | n/a |
| Hot days (Tmax > 35 °C) | -3.63 | -4.52 | **0.00** | -3.64 | -0.44 | -0.40 | n/a | -0.70 | -1.57 | -3.18 | -3.50 | -2.23 | n/a |
| NDVI | -0.85 | 0.16 | 0.00 | -0.53 | **0.68** | 0.56 | n/a | 0.72 | 0.36 | 0.44 | 0.56 | 0.56 | n/a |
| VCI | **0.85** | 0.23 | 0.00 | 0.63 | 0.83 | 0.71 | n/a | 0.45 | 0.65 | 0.66 | 0.76 | 0.65 | n/a |
| Monthly runoff | -0.99 | -0.01 | 0.00 | -1.42 | -0.09 | **0.05** | n/a | -0.00 | 0.13 | -0.01 | 0.23 | -0.15 | n/a |
| Runoff anomaly | -0.68 | -0.17 | 0.00 | **0.20** | -0.00 | 0.16 | n/a | -0.38 | 0.06 | -0.29 | 0.07 | 0.04 | n/a |
| River discharge | -0.47 | **0.35** | 0.00 | -0.40 | 0.27 | 0.15 | n/a | 0.24 | -0.02 | -0.50 | 0.09 | 0.15 | n/a |

### MASE, every model

| Variable | Naive (last value) | Seasonal naive (same period last year) | Climatology (same-month mean) | 12-month moving average | ETS / Holt-Winters | SARIMA (order by AIC) | SARIMAX (with input variables) | Prophet | Random Forest | Gradient boosting (LightGBM) | LSTM (experimental) | Transformer encoder (experimental) | VAR (target + inputs) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SPI-3 | 1.63 | 0.71 | 0.94 | 1.65 | 1.11 | 0.78 | n/a | 1.02 | 0.72 | 0.69 | 0.80 | 0.95 | n/a |
| SPI-6 | 1.45 | 0.73 | 1.01 | 1.51 | 0.60 | 0.58 | n/a | 1.19 | 0.46 | 0.38 | 0.59 | 0.56 | n/a |
| SPEI-3 | 0.60 | 0.65 | 0.86 | 0.64 | 0.99 | 0.64 | n/a | 1.13 | 0.59 | 0.59 | 0.94 | 0.71 | n/a |
| Area in drought (D1–D4) | 1.02 | 0.71 | 0.95 | 1.13 | 1.14 | 1.13 | n/a | 1.06 | 0.91 | 1.00 | 0.93 | 0.71 | n/a |
| Monthly rainfall | 1.10 | 0.72 | 0.73 | 1.54 | 0.76 | 0.69 | n/a | 0.76 | 0.69 | 0.60 | 0.71 | 0.77 | n/a |
| SPI-1 | 1.24 | 0.67 | 0.90 | 1.34 | 0.91 | 0.93 | n/a | 0.97 | 0.87 | 0.95 | 0.84 | 0.89 | n/a |
| Temperature anomaly | 0.82 | 0.72 | 0.54 | 0.58 | 0.66 | 0.57 | n/a | 0.60 | 0.66 | 0.77 | 0.63 | 0.66 | n/a |
| Hot days (Tmax > 35 °C) | 1.22 | 1.29 | 0.56 | 1.61 | 0.70 | 0.68 | n/a | 1.06 | 0.87 | 1.12 | 1.26 | 1.03 | n/a |
| NDVI | 2.86 | 2.00 | 2.28 | 2.52 | 0.99 | 1.31 | n/a | 1.00 | 1.67 | 1.40 | 1.29 | 1.36 | n/a |
| VCI | 0.59 | 2.14 | 2.45 | 1.34 | 0.66 | 1.15 | n/a | 1.76 | 1.29 | 1.20 | 0.99 | 1.35 | n/a |
| Monthly runoff | 1.11 | 0.79 | 0.78 | 1.53 | 0.84 | 0.74 | n/a | 0.78 | 0.78 | 0.81 | 0.67 | 0.83 | n/a |
| Runoff anomaly | 1.14 | 0.75 | 0.85 | 0.73 | 0.81 | 0.74 | n/a | 0.97 | 0.81 | 0.95 | 0.80 | 0.82 | n/a |
| River discharge | 2.26 | 1.39 | 1.58 | 2.16 | 1.50 | 1.40 | n/a | 1.39 | 1.69 | 2.19 | 1.40 | 1.34 | n/a |

### Fit time per model (s, all origins + no refit)

| Variable | Naive (last value) | Seasonal naive (same period last year) | Climatology (same-month mean) | 12-month moving average | ETS / Holt-Winters | SARIMA (order by AIC) | SARIMAX (with input variables) | Prophet | Random Forest | Gradient boosting (LightGBM) | LSTM (experimental) | Transformer encoder (experimental) | VAR (target + inputs) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SPI-3 | 0.0 | 0.0 | 0.0 | 0.0 | 1.6 | 7.8 | n/a | 0.8 | 2.1 | 0.3 | 9.5 | 12.7 | n/a |
| SPI-6 | 0.0 | 0.0 | 0.0 | 0.0 | 1.2 | 15.9 | n/a | 0.9 | 2.4 | 0.3 | 6.7 | 13.1 | n/a |
| SPEI-3 | 0.0 | 0.0 | 0.0 | 0.0 | 0.9 | 17.5 | n/a | 0.9 | 2.1 | 0.3 | 9.4 | 15.9 | n/a |
| Area in drought (D1–D4) | 0.0 | 0.0 | 0.0 | 0.0 | 2.6 | 5.8 | n/a | 0.9 | 2.3 | 0.2 | 3.6 | 7.9 | n/a |
| Monthly rainfall | 0.0 | 0.0 | 0.0 | 0.0 | 2.0 | 5.9 | n/a | 0.8 | 2.0 | 0.3 | 4.9 | 15.1 | n/a |
| SPI-1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.8 | 17.6 | n/a | 0.8 | 2.1 | 0.3 | 3.8 | 10.2 | n/a |
| Temperature anomaly | 0.0 | 0.0 | 0.0 | 0.0 | 0.6 | 13.5 | n/a | 0.8 | 2.2 | 0.3 | 3.7 | 9.1 | n/a |
| Hot days (Tmax > 35 °C) | 0.0 | 0.0 | 0.0 | 0.0 | 0.8 | 4.6 | n/a | 0.9 | 2.2 | 0.3 | 6.6 | 11.2 | n/a |
| NDVI | 0.0 | 0.0 | 0.0 | 0.0 | 0.8 | 20.1 | n/a | 0.9 | 2.0 | 0.2 | 8.5 | 9.2 | n/a |
| VCI | 0.0 | 0.0 | 0.0 | 0.0 | 1.6 | 22.2 | n/a | 0.8 | 1.9 | 0.3 | 5.4 | 12.6 | n/a |
| Monthly runoff | 0.0 | 0.0 | 0.0 | 0.0 | 1.4 | 20.6 | n/a | 0.9 | 2.0 | 0.2 | 4.7 | 13.9 | n/a |
| Runoff anomaly | 0.0 | 0.0 | 0.0 | 0.0 | 1.3 | 8.5 | n/a | 0.8 | 2.1 | 0.3 | 2.9 | 8.9 | n/a |
| River discharge | 0.0 | 0.0 | 0.0 | 0.0 | 0.3 | 14.1 | n/a | 0.8 | 2.0 | 0.2 | 4.8 | 5.2 | n/a |

