"""Contract modules. Each implemented module is a package with router.py / service.py / schemas.py and is listed here
(health reports a module as live only when it is listed and its tables are reachable)."""

IMPLEMENTED_MODULES: set[str] = {"rainfall"}
