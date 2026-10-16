"""Settings loaded from the environment / .env (pydantic-settings). Secrets are SecretStr and never logged."""

from __future__ import annotations

import ipaddress
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # --- database (read-only use) -------------------------------------------------------------------------------
    supabase_db_url: SecretStr | None = None
    db_pool_min: int = 1
    db_pool_max: int = 5
    db_statement_timeout_ms: int = 10_000
    db_connect_timeout_s: float = 15.0

    # --- HTTP ---------------------------------------------------------------------------------------------------
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:5174"]
    )
    rate_limit_per_minute: int = 600  # per client IP (one dashboard visit makes ~45 requests); 0 disables
    # networks never rate-limited, comma-separated CIDRs (e.g. the DWS network); default none
    rate_limit_trusted_cidrs: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # read the client IP from X-Forwarded-For; only behind a reverse proxy that sets it (else it can be spoofed)
    trust_proxy_headers: bool = False
    environment: str = "development"
    # app.core.response_checks on every module response: "log" (warn), "raise" (fail the request; tests), "off"
    response_checks: Literal["off", "log", "raise"] = "log"
    log_level: str = "INFO"

    # --- data behaviour ------------------------------------------------------------------------------------------
    stale_after_days: int = 90  # a value is "isStale" when its asOf is this many days older than the newest DB date
    # headline drought class = the worst class that, with all worse classes, covers at least this % of the area
    drought_class_area_pct: float = Field(20.0, gt=0, le=100)
    max_series_points: int = 2_000
    # pre-load every category chart group (national, 12M) at startup and before its cache expires (BACKEND_ISSUES #13)
    prewarm_categories: bool = True
    cache_ttl_summary_s: int = 15 * 60
    cache_ttl_map_s: int = 15 * 60
    cache_ttl_long_series_s: int = 60 * 60
    cache_ttl_meta_s: int = 60 * 60

    @field_validator("cors_origins", "rate_limit_trusted_cidrs", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("rate_limit_trusted_cidrs")
    @classmethod
    def _valid_cidrs(cls, v: list[str]) -> list[str]:
        for cidr in v:
            ipaddress.ip_network(cidr, strict=False)  # ValueError → refuse to start on a malformed network
        return v

    @property
    def registered_tables(self) -> dict[str, list[str]]:
        """NIWIS_SUPABASE_TABLES_MODULE_<n> variables: the tables the data team has registered, by module number."""
        from dotenv import dotenv_values

        env = {**{k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None}, **os.environ}
        out: dict[str, list[str]] = {}
        for key, val in env.items():
            m = re.fullmatch(r"NIWIS_SUPABASE_TABLES_MODULE_(\d+)", key)
            if m:
                out[m.group(1)] = [t.strip() for t in val.split(",") if t.strip()]
        return dict(sorted(out.items()))


@lru_cache
def get_settings() -> Settings:
    return Settings()
