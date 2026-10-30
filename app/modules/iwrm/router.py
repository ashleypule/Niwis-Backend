"""IWRM endpoints (contract `iwrm`)."""

from app.modules.iwrm.service import IwrmService
from app.modules.router_factory import build_router

router = build_router("iwrm", IwrmService)
