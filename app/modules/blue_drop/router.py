"""Blue Drop endpoints (contract `blue-drop`)."""

from app.modules.blue_drop.service import BlueDropService
from app.modules.router_factory import build_router

router = build_router("blue-drop", BlueDropService, entities=True)
