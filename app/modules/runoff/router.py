"""Runoff endpoints (contract `runoff`)."""

from app.modules.router_factory import build_router
from app.modules.runoff.service import RunoffService

router = build_router("runoff", RunoffService, entities=True)
