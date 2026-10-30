"""Green Drop endpoints (contract `green-drop`)."""

from app.modules.green_drop.service import GreenDropService
from app.modules.router_factory import build_router

router = build_router("green-drop", GreenDropService, entities=True)
