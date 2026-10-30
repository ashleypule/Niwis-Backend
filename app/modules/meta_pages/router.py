"""Data Integration and Data Governance endpoints (contract `data-integration`, `governance`)."""

from app.modules.meta_pages.service import DataIntegrationService, GovernanceService
from app.modules.router_factory import build_router

data_integration_router = build_router("data-integration", DataIntegrationService)
governance_router = build_router("governance", GovernanceService)
