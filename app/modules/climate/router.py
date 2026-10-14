"""Climate endpoints (contract `climate`)."""

from app.modules.climate.service import ClimateService
from app.modules.router_factory import build_router

router = build_router("climate", ClimateService)
