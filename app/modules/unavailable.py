"""501 DATA_UNAVAILABLE for every contract path the database cannot serve.

The frontend calls `/api/v1/<module>/<kind>` for all 25 modules of the contract. A production build talks only to
this API (no sample data), so a module without tables must answer with the contract's
"not available" convention and a specific reason — never a bare 404, which the frontend shows as an error. Registered
after every module router; paths of live modules (their routers define what exists), unknown modules and unknown kinds
stay 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.core.errors import DataUnavailable, NotFound
from app.modules import DATABASE_VIEW_MODULES, IMPLEMENTED_MODULES

# The frontend's module ids (src/config/modules.ts) that call module endpoints; `reports` composes other pages.
CONTRACT_MODULES = (
    "overview",
    "drought",
    "climate",
    "vegetation",
    "rainfall",
    "runoff",
    "dams",
    "surface-water",
    "groundwater",
    "groundwater-reserve",
    "water-quality",
    "green-drop",
    "blue-drop",
    "rwqo",
    "iwrm",
    "water-quantity",
    "services",
    "tariffs",
    "settlements",
    "ai",
    "alerts",
    "data-integration",
    "governance",
    "roadmap",
)
KINDS = ("summary", "timeseries", "forecast", "breakdown", "table", "matrix", "map", "entities", "categories")
_VIEW = "Only the database variables of this module are served (Database variables section, /{module}/categories); "
REASONS: dict[str, str] = {
    "overview": "This Overview panel has no table in the database; Overview panels whose data exist come from their "
    "module (rainfall, drought, dams, runoff, groundwater, water quality).",
    "green-drop": _VIEW + "Green Drop assessment scores, risk ratings and plant registers are not in the database "
    "(the 13.x tables hold effluent quality, works operations and weather).",
    "blue-drop": _VIEW + "Blue Drop assessment scores, risk ratings and supply-system registers are not in the "
    "database (the 14.x tables hold drinking-water quality and operations).",
    "rwqo": _VIEW + "Resource water quality objectives per site and compliance against them are not in the database "
    "(the 15.x tables hold district values without RWQO targets).",
    "iwrm": _VIEW + "Water-use licences, allocations and catchment-management records are not in the database "
    "(the 16.x tables hold district resource indicators).",
    "water-quantity": "No water-balance, demand or supply tables are in the database.",
    "services": "No water-services access or backlog tables (households, standpipes, RDP levels) are in the database.",
    "tariffs": "No tariff or water-pricing tables are in the database.",
    "settlements": "No settlement register or population tables are in the database.",
    "ai": "No model-output tables are in the database. NIWIS makes its own statistical forecasts: the forecast keys "
    "drought, streamflow and groundwater and the models table are served from them, and any variable can be forecast "
    "in the Forecasting Studio (/api/v1/forecast/*); this panel has no forecast product.",
    "alerts": "No alert service: alerts are not stored in the database and no alert rules run on the live data yet.",
    "data-integration": "Feed-level integration metrics are not recorded in the database; table coverage, row counts "
    "and freshness are served by /api/v1/meta/sources, /meta/coverage and /meta/variables.",
    "governance": "Data-governance scores, policies and stewardship records are not in the database; table coverage "
    "and freshness are served by /api/v1/meta/*.",
    "roadmap": "The programme plan and its progress are not in the database.",
}

router = APIRouter(prefix="/api/v1", tags=["unavailable"], dependencies=[Depends(get_current_user)])


def reason(module: str, kind: str) -> str:
    if module in REASONS:
        return REASONS[module].replace("{module}", module)
    return f"The {module} module serves no {kind} datasets: the database has no table for them."


@router.get("/alerts", include_in_schema=False)
async def alerts() -> None:
    raise DataUnavailable(REASONS["alerts"], missing=["alerts"])


def _unavailable(module: str, kind: str) -> None:
    # a live module's router defines what it serves; anything else there is a wrong path
    if module not in CONTRACT_MODULES or module in IMPLEMENTED_MODULES or kind not in KINDS:
        raise NotFound("Not found")
    if kind == "categories" and module in IMPLEMENTED_MODULES | DATABASE_VIEW_MODULES:
        raise NotFound("Not found")  # served by the category router; anything else under it is a wrong path
    raise DataUnavailable(reason(module, kind), missing=[f"{module}/{kind}"])


@router.get("/{module}/{kind}", include_in_schema=False)
async def unavailable(module: str, kind: str) -> None:
    _unavailable(module, kind)


@router.get("/{module}/{kind}/{rest:path}", include_in_schema=False)
async def unavailable_item(module: str, kind: str, rest: str) -> None:
    _unavailable(module, kind)  # entities/{id}, category/{table}/{group}
