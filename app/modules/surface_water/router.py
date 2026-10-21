"""Surface water endpoints (contract `surface-water`)."""

from app.modules.router_factory import build_router
from app.modules.surface_water.service import SurfaceWaterService

router = build_router("surface-water", SurfaceWaterService, entities=True)
