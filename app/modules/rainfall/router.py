"""Rainfall endpoints (contract `rainfall`)."""

from app.modules.rainfall.service import RainfallService
from app.modules.router_factory import build_router

router = build_router("rainfall", RainfallService, entities=True)
