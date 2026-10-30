"""Water services endpoints (contract `services`)."""

from app.modules.router_factory import build_router
from app.modules.services.service import ServicesService

router = build_router("services", ServicesService, entities=True)
