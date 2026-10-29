"""Application-wide services, created once at startup and reached from routes through `get_state`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.config import Settings
from app.core.cache import Cache
from app.core.data import DataAccess
from app.core.spatial import Spatial
from app.db import Database
from app.meta.service import MetaService
from app.registry.registry import Registry


@dataclass
class AppState:
    settings: Settings
    db: Database
    registry: Registry
    cache: Cache
    meta: MetaService
    spatial: Spatial
    data: DataAccess
    forecast_jobs: Any = None  # app.forecasting.jobs.ForecastJobs (None: forecasting disabled)
    page_forecasts: Any = None  # app.forecasting.pages.PageForecasts


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.niwis
    return state
