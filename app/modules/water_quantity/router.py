"""Water quantity endpoints (contract `water-quantity`)."""

from app.modules.router_factory import build_router
from app.modules.water_quantity.service import WaterQuantityService

router = build_router("water-quantity", WaterQuantityService, entities=True)
