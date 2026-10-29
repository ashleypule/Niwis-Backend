"""Settings loaded from the environment / .env (pydantic-settings). Secrets are SecretStr and never logged."""

from __future__ import annotations

import ipaddress
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
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
    # browser origins allowed to call the API (GET only, no credentials); empty = "*" (the data are public, and the
    # frontend's domain is not fixed yet). Set it to the frontend's domain(s) once known.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    rate_limit_per_minute: int = 600  # per client IP (one dashboard visit makes ~45 requests); 0 disables
    # networks never rate-limited, comma-separated CIDRs (e.g. the DWS network); default none
    rate_limit_trusted_cidrs: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # read the client IP from X-Forwarded-For; only behind a reverse proxy that sets it (else it can be spoofed)
    trust_proxy_headers: bool = False
    # with TRUST_PROXY_HEADERS: the reverse proxies (CIDRs) whose X-Forwarded-For is believed; empty = any peer
    trusted_proxy_cidrs: Annotated[list[str], NoDecode] = Field(default_factory=list)
    max_request_body_bytes: int = 16_384  # every endpoint is a GET; larger (or chunked) bodies get 413
    max_query_length: int = 4_096  # longer query strings get 414
    environment: str = Field("development", validation_alias=AliasChoices("ENV", "ENVIRONMENT", "environment"))
    # ENV=production refuses to start unless this is true: the operator confirms that SUPABASE_DB_URL connects as
    # the read-only role of migrations/proposed/001_readonly_api_role.sql (not as postgres / service role)
    db_readonly_role_confirmed: bool = False
    # app.core.response_checks on every module response: "log" (warn), "raise" (fail the request; tests), "off"
    response_checks: Literal["off", "log", "raise"] = "log"
    log_level: str = "INFO"

    # --- data behaviour ------------------------------------------------------------------------------------------
    stale_after_days: int = 90  # a value is "isStale" when its asOf is this many days older than the newest DB date
    # headline drought class = the worst class that, with all worse classes, covers at least this % of the area
    drought_class_area_pct: float = Field(20.0, gt=0, le=100)
    max_series_points: int = 2_000
    # pre-load every category chart group (period 12M) at startup and before its cache expires (BACKEND_ISSUES #13),
    # for each scope listed: "national", "province=WC", "municipality=DC32", "wma=WMA5" (#14: frontend test scopes)
    prewarm_categories: bool = True
    prewarm_scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["national", "province=WC", "municipality=DC32", "wma=WMA5"]
    )
    cache_ttl_summary_s: int = 15 * 60
    cache_ttl_map_s: int = 15 * 60
    cache_ttl_long_series_s: int = 60 * 60
    cache_ttl_meta_s: int = 60 * 60

    # --- forecasting (app/forecasting; DECISIONS D29–D40) ---------------------------------------------------------
    forecast_enabled: bool = True
    forecast_workers: int = 2  # worker processes training at once (0 = in-process, tests only: no hard timeout)
    forecast_timeout_s: int = 120  # a run still training after this is stopped
    forecast_runs_per_hour: int = 10  # new runs per client IP (cached results are free); 0 disables
    forecast_queue_limit: int = 20  # Studio runs queued or running at once; more → 503 with Retry-After
    forecast_retention_h: int = 24  # finished runs are discarded after this
    forecast_pages: bool = True  # precompute the module pages' forecasts (Auto, 24 months) at startup / new data
    forecast_page_provinces: bool = True  # … for the nine provinces too (else South Africa only)
    forecast_page_check_s: int = 30 * 60  # how often to check whether the newest data date changed

    @field_validator("cors_origins", "rate_limit_trusted_cidrs", "trusted_proxy_cidrs", "prewarm_scopes", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("rate_limit_trusted_cidrs", "trusted_proxy_cidrs")
    @classmethod
    def _valid_cidrs(cls, v: list[str]) -> list[str]:
        for cidr in v:
            ipaddress.ip_network(cidr, strict=False)  # ValueError → refuse to start on a malformed network
        return v

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in ("production", "prod")

    @property
    def cors_allow_origins(self) -> list[str]:
        return self.cors_origins or ["*"]

    def check_production(self) -> None:
        """Refuse to start in production without the operator's confirmation of the read-only database role."""
        if self.is_production and not self.db_readonly_role_confirmed:
            raise RuntimeError(
                "ENV=production requires DB_READONLY_ROLE_CONFIRMED=true: connect SUPABASE_DB_URL as the read-only "
                "role (migrations/proposed/001_readonly_api_role.sql, DEPLOYMENT.md) and then set it"
            )

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
