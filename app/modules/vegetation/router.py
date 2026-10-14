"""Vegetation endpoints (contract `vegetation`)."""

from app.modules.router_factory import build_router
from app.modules.vegetation.service import VegetationService

router = build_router("vegetation", VegetationService)
