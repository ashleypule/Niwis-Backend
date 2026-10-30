"""Compare the forecast blocks served on the pages (national) with docs/forecast_validation.json (same variable,
24-month Auto run)."""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8")
S = os.path.dirname(os.path.abspath(__file__))
val = {r["variable"]: r for r in json.load(open(r"C:\Users\Baloyi PA\Desktop\Niwis Backend\docs\forecast_validation.json", encoding="utf-8"))["results"] if r["horizon"] == 24}
print("validation variables:", sorted(val))
PAGES = [  # (variable id, module dir, file)
    ("spi-3", "drought", "forecast_spi.json"), ("spi-6", "drought", "forecast_spi_scale-6.json"), ("spei-3", "drought", "forecast_spei.json"),
    ("drought-area", "drought", "forecast_area.json"), ("rainfall", "rainfall", "forecast_rainfall.json"), ("spi-1", "rainfall", "forecast_spi_scale-1.json"),
    ("temp-anomaly", "climate", "forecast_temp-anomaly.json"), ("hot-days", "climate", "forecast_hot-days.json"), ("ndvi", "vegetation", "forecast_ndvi.json"),
    ("vci", "vegetation", "forecast_vci.json"), ("runoff", "runoff", "forecast_runoff.json"), ("runoff-anomaly", "runoff", "forecast_runoff-anomaly.json"),
    ("runoff-flow", "runoff", "forecast_flow.json"), ("flow", "surface-water", "forecast_flow.json"), ("gw-depth", "groundwater", "forecast_level.json"),
    ("wqi", "water-quality", "forecast_wqi.json"), ("ecoli", "water-quality", "forecast_ecoli-catchment.json"),
]
for var, mod, f in PAGES:
    p = os.path.join(S, "api", mod, "national", f)
    if not os.path.exists(p):
        print(var, "no dump"); continue
    b = json.load(open(p, encoding="utf-8"))
    fc = b.get("forecast")
    if not fc:
        print(var, "no forecast block", str(b)[:80]); continue
    v = val.get(var)
    obs = next((s for s in b["series"] if s["key"] == "obs"), None)
    last = obs["data"][-1] if obs else None
    doc = f"doc: {v['auto']} mase {v['autoMase']:.2f} skill {v['autoSkill']:.2f} last {v['last']} h{v['horizon']}" if v else "doc: (not in validation)"
    print(f"{var:15s} page: {fc['model']:16s} mase {fc['mase']:.2f} skill {fc['skill']:.2f} dataAsOf {fc['dataAsOf']} asOf {b.get('asOf')} obs_last {last}  | {doc}")
