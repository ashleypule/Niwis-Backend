# Issue fixes — before / after

> The seven issues in `../Niwis Frontend/docs/backend/BACKEND_ISSUES.md`, fixed on 2026-10-15. "Before" = the API
> running at the start of the work (commit `68b12e9`); "after" = the rebuilt Docker image. All requests are national
> scope with `period=12M`, as the frontend sends them; responses trimmed to the relevant keys. Root causes and audit
> results are in `PROGRESS.md`; contract changes in `CONTRACT_CHANGES.md` §3, §4, §5, §9, §10.

## #6 Seasonal change: values for 1981–2000 (critical)

`GET /api/v1/climate/breakdown?metric=seasonal-change&var=temperature`

```jsonc
// before: p0 is the year 2000 alone (the DB starts 2000-01-01; the window 1981–2000 was inclusive)
{"unit": "°C",
 "series": [{"key": "p0", "name": "1981–2000", "values": [21.58, 16.81, 12.66, 17.88]},
            {"key": "p1", "name": "2001–2020", "values": [22.95, 18.23, 12.32, 19.02]},
            {"key": "p2", "name": "2021–2026", "values": [22.98, 18.19, 12.57, 19.25]}],
 "unavailable": [{"field": "series.p0", "reason": "No data before 2000 in the database"}]}
// after
{"unit": "°C",
 "series": [{"key": "p0", "name": "1981–2000", "values": [null, null, null, null]},
            {"key": "p1", "name": "2001–2020", "values": [22.95, 18.23, 12.32, 19.02]},
            {"key": "p2", "name": "2021–2026", "values": [22.98, 18.19, 12.57, 19.25]}],
 "unavailable": [{"field": "series.p0",
                  "reason": "The database starts in 2000; the 1981–2000 period needs data from 1981"}]}
```

`GET /api/v1/climate/breakdown?metric=seasonal-change&var=rainfall`

```jsonc
// before: `var` ignored — the same temperatures, in °C, under the rainfall tab
{"unit": "°C", "source": ["4_1_temperature_change_master"],
 "series": [{"key": "p0", "values": [21.58, 16.81, 12.66, 17.88]}, …]}
// after: mean seasonal rainfall totals (mm) from 4_3
{"unit": "mm", "source": ["4_3_mean_annual_precipitation_master"],
 "series": [{"key": "p0", "name": "1981–2000", "values": [null, null, null, null]},
            {"key": "p1", "name": "2001–2020", "values": [223.0, 119.0, 48.0, 118.0]},
            {"key": "p2", "name": "2021–2025", "values": [259.0, 138.0, 45.0, 128.0]}],
 "unavailable": [{"field": "series.p0",
                  "reason": "The database starts in 2000; the 1981–2000 period needs data from 1981"}]}
```

Audit of the frontend's recorded responses before the fix (627): 16 violations, all this endpoint (both `var`s × 4
scopes). After re-recording: 0. Supabase, every endpoint × selector × 4 scopes: 1 004 requests, 0 problems.

## #1 `set` ignored on the vegetation indices series

`GET /api/v1/vegetation/timeseries?metric=indices&set=vci,vhi,tci`

```jsonc
// before: always NDVI / EVI / SAVI / MSAVI2
{"series": [{"key": "ndvi", "data": [… , 0.344, 0.389, 0.433]},
            {"key": "evi", "data": [… null]}, {"key": "savi", …}, {"key": "msavi2", …}],
 "unavailable": [{"field": "series.evi", …}, {"field": "series.savi", …}, {"field": "series.msavi2", …}]}
// after
{"axes": [{"unit": "%", "label": "Index value"}],
 "series": [{"key": "vci", "name": "VCI", "data": [… , 78.6, 83.5, 87.4]},
            {"key": "vhi", "name": "VHI", "data": [… , 86.9, 91.1, 93.3]},
            {"key": "tci", "name": "TCI", "data": [… , 95.3, 98.8, 99.2]}],
 "source": ["satellite_remote_sensing"]}
```

`GET /api/v1/vegetation/timeseries?metric=indices&set=ndwi,ndii`

```jsonc
// before: NDVI / EVI / SAVI / MSAVI2 (as above)
// after
{"series": [{"key": "ndwi", "name": "NDWI", "data": [… , -0.448, -0.458, -0.481]},
            {"key": "ndii", "name": "NDII", "data": [… , null, null, null]}],
 "unavailable": [{"field": "series.ndii",
                  "reason": "Only in the withheld 5.1 source (failed the NDVI plausibility check, docs/DATA_GAPS.md §5)"}]}
```

`GET /api/v1/vegetation/timeseries?metric=indices&set=vci,foo` (after)

```json
{"error": "Unknown set value(s) ['foo']", "code": "BAD_REQUEST",
 "issues": [{"param": "set", "value": "vci,foo", "invalid": ["foo"],
             "allowed": ["ndvi", "vci", "vhi", "tci", "ndwi", "lst", "sm", "evi", "savi", "msavi2", "vpi", "asi", "svi", "ndii"]}]}
```

Every other panel selector was ignored as well and is now honoured (`CONTRACT_CHANGES.md` §10). Two of them used to
return data under the wrong label: `index-trend?index=evi` / `savi` returned NDVI (now 501, withheld), and
`indicator-trends?scenario=rcp85` on the projections page returned observed 2020–2025 values (now 501, no
projections).

## #5 All-null series without an `unavailable` entry

`GET /api/v1/climate/timeseries?metric=indicator-trends`

```jsonc
// before
{"series": [… {"key": "drought", "name": "Drought frequency", "data": [["2020-01-01", null], ["2021-01-01", null], …]} …]}
// (no "unavailable")
// after: same series, plus the reason, computed from the data
{"unavailable": [{"field": "series.drought",
  "reason": "The number of drought months (SPI-3 ≤ −1.5) was 0 in 2020 (the base year) in this scope (lowest area-weighted SPI-3 that year: -1.06), so a % change from 2020 is undefined"}]}
```

## #7 Annual data flagged stale

`GET /api/v1/climate/timeseries?metric=extremes`, `GET /api/v1/climate/table?metric=statistics`,
`GET /api/v1/rainfall/breakdown?metric=seasonal` — identical before / after for all three:

```jsonc
// before
{"asOf": "2025-01-01", "isStale": true}
// after (newest record 2026-09-06; 2025 is the last complete year)
{"asOf": "2025-01-01", "periodEnd": "2025-12-31", "isStale": false}
```

Satellite / hydrology months (2025-12) stay stale: `periodEnd` 2025-12-31 is 249 days before the newest record.

## #2, #3 Cascade row keys

`GET /api/v1/drought/table?metric=cascade` (meteorological and hydrological rows)

```jsonc
// before
{"id": "meteorological", "stage": "Meteorological (SPI-6)", "value": 0.44, "class": "No drought", "severity": 8.0, "asOf": "2026-08-01"}
{"id": "hydrological", "stage": "Hydrological (dam storage % FSC)", "value": 63.6, "class": "No alert", "severity": 36.0, "asOf": "2025-12-01"}
// after
{"id": "meteorological", "stage": "Meteorological (SPI-6)", "metric": "spi", "value": "SPI-6 0.44", "valueNum": 0.44,
 "cls": "No drought", "scale": "drought", "bar": 0.0, "class": "No drought", "severity": 8.0, "asOf": "2026-08-01"}
{"id": "hydrological", "stage": "Hydrological (dam storage % FSC)", "metric": "storagePct", "value": "63.6 % FSC",
 "valueNum": 63.6, "cls": "moderate", "scale": "storage", "bar": 50.0, "class": "No alert", "severity": 36.0, "asOf": "2025-12-01"}
```

`GET /api/v1/climate/table?metric=signal-chain` (first row)

```jsonc
// before
{"id": "temp", "signal": "Temperature", "change": "+0.21 °C (12 months)", "severity": null}
// after
{"id": "temp", "signal": "Temperature", "change": "+0.21 °C (12 months)", "severity": null,
 "stage": "Temperature", "metric": "tmean", "value": "+0.21 °C (12 months)", "cls": null, "scale": "risk", "bar": null}
// unavailable: rows.severity, rows.cls, rows.bar — "No agreed severity thresholds for these signals (none in thresholds.ts)"
```

## #4 Rate limit

Default 600 / min per IP (was 120); `/api/v1/health` exempt; `RATE_LIMIT_TRUSTED_CIDRS`; `X-Forwarded-For` only with
`TRUST_PROXY_HEADERS=true`. `npm run fixtures:record` (629 requests) and `npm run contract:check` (223 requests)
both ran without a 429.

## Frontend checks (no frontend code changed)

| | Before (2026-10-14) | After (2026-10-15) |
| --- | --- | --- |
| `npm run fixtures:record` | 627 fixtures | 627 fixtures, 0 failed |
| `npm run contract:check` | 148 ok · 12 structural differences · 63 unavailable · 0 schema failures · 0 errors | 148 ok · 8 structural differences · 67 unavailable · **0 schema failures · 0 errors** |

Gone: both cascade tables (#2, #3), the three vegetation `set` tabs (#1), `indicator-trends?scenario` (now 501) and
`affected?level=catchment` (now 501). The remaining 8 are informational: all-null series that now carry an
`unavailable` entry (EVI / SAVI / MSAVI2, NDII, pan evaporation / VPD, borehole level, and the "Negative anomaly"
bars where no month in the window was below normal), plus the column keys of `drought/affected` and
`climate/provinces`, which differ from the mock and were not part of `BACKEND_ISSUES.md`.
