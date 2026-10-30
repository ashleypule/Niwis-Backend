"""RWQO endpoints (contract `rwqo`)."""

from app.modules.router_factory import build_router
from app.modules.rwqo.service import RwqoService

router = build_router("rwqo", RwqoService, entities=True)
