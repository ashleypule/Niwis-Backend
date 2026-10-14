"""Drought endpoints (contract `drought`)."""

from app.modules.drought.service import DroughtService
from app.modules.router_factory import build_router

router = build_router("drought", DroughtService)
