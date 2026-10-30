"""Tariff endpoints (contract `tariffs`)."""

from app.modules.router_factory import build_router
from app.modules.tariffs.service import TariffsService

router = build_router("tariffs", TariffsService, entities=True)
