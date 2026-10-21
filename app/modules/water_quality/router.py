"""Water quality endpoints (contract `water-quality`)."""

from app.modules.router_factory import build_router
from app.modules.water_quality.service import WaterQualityService

router = build_router("water-quality", WaterQualityService, entities=True)
