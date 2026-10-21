"""Groundwater endpoints (contract `groundwater`)."""

from app.modules.groundwater.service import GroundwaterService
from app.modules.router_factory import build_router

router = build_router("groundwater", GroundwaterService, entities=True)
