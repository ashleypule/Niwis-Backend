"""Contract modules. Each implemented module is a package with router.py / service.py and is listed here (health reports
a module as live only when it is listed and its tables are reachable).

`DATABASE_VIEW_MODULES` serve only the generic database view of their tables (`/categories`, `/category/...`): their
pages show the database variables, and every other panel the "not yet available" state (health: `partial`).
"""

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
}
