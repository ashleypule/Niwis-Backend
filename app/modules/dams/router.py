from app.modules.dams.service import DamsService
from app.modules.router_factory import build_router

router = build_router("dams", DamsService, entities=True)
