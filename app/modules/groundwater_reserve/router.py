"""Groundwater reserve endpoints (contract `groundwater-reserve`)."""

from app.modules.groundwater_reserve.service import GroundwaterReserveService
from app.modules.router_factory import build_router

router = build_router("groundwater-reserve", GroundwaterReserveService, entities=True)
