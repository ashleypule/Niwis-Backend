"""The warm-up's patience and the silence of a real Prophet fit (DECISIONS D49)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.db import patient
from tests.conftest import requires_db

ROOT = Path(__file__).parents[1]
FIT = """
import numpy as np, pandas as pd
from app.forecasting.statistical import Prophet
y = pd.Series(np.arange(48.0), index=pd.date_range("2015-01-01", periods=48, freq="MS"))
Prophet()._fit(y, None)
"""


@pytest.mark.db
@requires_db
async def test_only_the_warm_up_has_the_longer_statement_timeout(client_factory: Any) -> None:
    async for c in client_factory():
        db = c._transport.app.state.niwis.db  # type: ignore[attr-defined]
        sql = "SELECT current_setting('statement_timeout') AS t, current_setting('transaction_read_only') AS ro"
        assert (await db.fetch(sql))[0]["t"] == "10s"
        with patient():
            row = (await db.fetch(sql))[0]
        assert (row["t"], row["ro"]) == ("1min", "on")
        assert (await db.fetch(sql))[0]["t"] == "10s"


def test_a_prophet_fit_in_a_fresh_process_prints_nothing_about_chains_or_plotly() -> None:
    done = subprocess.run([sys.executable, "-c", FIT], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-1500:]
    out = done.stdout + done.stderr
    assert "Chain [1]" not in out
    assert "plotly" not in out
