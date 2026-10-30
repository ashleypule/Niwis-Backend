"""Contract modules. Each implemented module is a package with router.py / service.py and is listed here (health reports
a module as live only when it is listed and its tables are reachable).

`DATABASE_VIEW_MODULES` serve only the generic database view of their tables (`/categories`, `/category/...`): their
pages show the database variables, and every other panel the "not yet available" state (health: `partial`).
"""

# Served without tables of their own (DECISIONS D47): the modules each one reads (health lists them as live with
# the tables those modules read); an empty tuple means the registry and the tables' statistics
DERIVED_MODULES: dict[str, tuple[str, ...]] = {
    "ai": ("drought", "surface-water", "groundwater", "water-quality"),  # page forecasts; the Studio reads any table
    "alerts": (
        "drought",
        "vegetation",
        "water-quality",
        "groundwater-reserve",
        "green-drop",
        "blue-drop",
        "services",
        "rwqo",
        "iwrm",
        "tariffs",
    ),
    "data-integration": (),
    "governance": (),
}
# Served in part through other modules' endpoints (health: `partial`)
PARTIAL_MODULES: dict[str, str] = {
    "overview": "its panels come from their modules (rainfall, drought, dams, runoff, groundwater, water quality) and "
    "its forecast panel from the page forecasts; the Overview's own summary has no table",
}
IMPLEMENTED_MODULES: set[str] = {
    "rainfall",
    "drought",
    "climate",
    "vegetation",
    "runoff",
    "dams",
    "surface-water",
    "groundwater",
    "groundwater-reserve",
    "water-quality",
    "water-quantity",
    "iwrm",
    "green-drop",
    "blue-drop",
    "rwqo",
    "alerts",
    "data-integration",
    "governance",
    "services",
    "tariffs",
}
DATABASE_VIEW_MODULES: set[str] = {
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
}
