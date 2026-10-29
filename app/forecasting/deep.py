"""Deep learning (experimental): an LSTM and a small Transformer encoder in PyTorch.

Both see a 12-month lookback of the standardised series (plus the calendar month as sine / cosine, and the inputs when
given), predict one month and are applied recursively. Training: Adam, the last 15 % of the training windows held out
in time order for early stopping (patience 15), at most 200 epochs, a fixed seed, one CPU thread. With about 300
monthly points they rarely beat the statistical models; the comparison table shows that.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.forecasting.base import ForecastModel, ModelError, Prediction

LOOKBACK = 12
MAX_EPOCHS = 200
PATIENCE = 15
VAL_SHARE = 0.15


def _month_features(index: pd.DatetimeIndex) -> np.ndarray:
    m = index.month.to_numpy()
    return np.stack([np.sin(2 * np.pi * m / 12), np.cos(2 * np.pi * m / 12)], axis=1)


class _SeqModel(ForecastModel):
    group = "deep"
    min_obs = 60
    experimental = True
    supports_exog = True

    def _net(self, n_features: int) -> Any:
        raise NotImplementedError

    def _frame(self, z: np.ndarray, months: np.ndarray, x: np.ndarray | None) -> np.ndarray:
        cols = [z[:, None], months]
        if x is not None:
            cols.append(x)
        return np.concatenate(cols, axis=1).astype(np.float32)

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        import torch

        torch.manual_seed(self.seed)
        torch.set_num_threads(1)
        v = y.to_numpy(dtype=float)
        self.mu, self.sd = float(v.mean()), float(v.std()) or 1.0
        z = (v - self.mu) / self.sd
        self.cols = list(exog.columns) if exog is not None else []
        x = None
        if exog is not None:
            self.xmu, self.xsd = exog.mean().to_numpy(), exog.std().replace(0, 1.0).to_numpy()
            x = (exog.to_numpy(dtype=float) - self.xmu) / self.xsd
        months = _month_features(pd.DatetimeIndex(y.index))
        frame = self._frame(z, months, x)
        # sample t: the LOOKBACK rows before t → z[t]; the month (and inputs) of t enter as the last row's "next" info
        xs, ts = [], []
        for t in range(LOOKBACK, len(z)):
            win = frame[t - LOOKBACK : t].copy()
            xs.append(np.concatenate([win, self._next_row(months[t], x[t] if x is not None else None)[None, :]]))
            ts.append(z[t])
        if len(xs) < 30:
            raise ModelError("too few training windows")
        xa, ta = torch.tensor(np.asarray(xs)), torch.tensor(np.asarray(ts, dtype=np.float32))[:, None]
        n_val = max(5, int(len(xa) * VAL_SHARE))
        xtr, ttr, xva, tva = xa[:-n_val], ta[:-n_val], xa[-n_val:], ta[-n_val:]
        net = self._net(xa.shape[2])
        opt = torch.optim.Adam(net.parameters(), lr=3e-3)
        loss_fn = torch.nn.MSELoss()
        gen = torch.Generator().manual_seed(self.seed)
        best, best_state, waited, epochs = float("inf"), None, 0, 0
        for epoch in range(MAX_EPOCHS):
            net.train()
            perm = torch.randperm(len(xtr), generator=gen)
            for i in range(0, len(xtr), 32):
                idx = perm[i : i + 32]
                opt.zero_grad()
                loss = loss_fn(net(xtr[idx]), ttr[idx])
                loss.backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                val = float(loss_fn(net(xva), tva))
            epochs = epoch + 1
            if val < best - 1e-5:
                best, waited = val, 0
                best_state = {k: t.clone() for k, t in net.state_dict().items()}
            else:
                waited += 1
                if waited >= PATIENCE:
                    break
        if best_state is not None:
            net.load_state_dict(best_state)
        net.eval()
        self.net = net
        self.frame, self.z = frame, z
        self.fitted_params = {
            "lookback": LOOKBACK,
            "epochs": epochs,
            "validationMse": round(best, 4),
            "trainingWindows": len(xtr),
            "inputs": self.cols,
        }

    def _next_row(self, month: np.ndarray, x_now: np.ndarray | None) -> np.ndarray:
        """The row appended after the lookback: z unknown (0), the target month and its inputs."""
        cols = [np.zeros(1), month]
        if x_now is not None:
            cols.append(x_now)
        return np.concatenate(cols).astype(np.float32)

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        import torch

        if self.cols and (exog_future is None or len(exog_future) != steps):
            raise ModelError(f"{self.label} needs future values of its inputs")
        months = _month_features(self.future_index(steps))
        xf = None
        if self.cols and exog_future is not None:
            xf = (exog_future[self.cols].to_numpy(dtype=float) - self.xmu) / self.xsd
        frame = list(self.frame)
        out = []
        with torch.no_grad():
            for i in range(steps):
                win = np.asarray(frame[-LOOKBACK:])
                nxt = self._next_row(months[i], xf[i] if xf is not None else None)
                zhat = float(self.net(torch.tensor(np.concatenate([win, nxt[None, :]])[None, :, :]))[0, 0])
                out.append(zhat)
                row = nxt.copy()
                row[0] = zhat
                frame.append(row)
        return Prediction(np.asarray(out) * self.sd + self.mu)


class LSTM(_SeqModel):
    id = "lstm"
    label = "LSTM (experimental)"
    description = "Two LSTM layers (64 → 32 units, dropout 0.2) over a 12-month lookback; early stopping on a "
    "chronological validation split. Experimental: needs 60 months."

    def _net(self, n_features: int) -> Any:
        import torch
        from torch import nn

        class Net(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.l1 = nn.LSTM(n_features, 64, batch_first=True)
                self.d1 = nn.Dropout(0.2)
                self.l2 = nn.LSTM(64, 32, batch_first=True)
                self.d2 = nn.Dropout(0.2)
                self.out = nn.Linear(32, 1)

            def forward(self, x: torch.Tensor) -> torch.Tensor:
                h, _ = self.l1(x)
                h, _ = self.l2(self.d1(h))
                return self.out(self.d2(h[:, -1, :]))  # type: ignore[no-any-return]

        return Net()


class Transformer(_SeqModel):
    id = "transformer"
    label = "Transformer encoder (experimental)"
    description = "A small Transformer encoder (2 layers, 2 heads, width 32, learned positions) over a 12-month "
    "lookback; early stopping on a chronological validation split. Experimental: needs 60 months."

    def _net(self, n_features: int) -> Any:
        import torch
        from torch import nn

        class Net(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.inp = nn.Linear(n_features, 32)
                self.pos = nn.Parameter(torch.zeros(1, LOOKBACK + 1, 32))
                layer = nn.TransformerEncoderLayer(32, 2, dim_feedforward=64, dropout=0.1, batch_first=True)
                self.enc = nn.TransformerEncoder(layer, 2, enable_nested_tensor=False)
                self.out = nn.Linear(32, 1)

            def forward(self, x: torch.Tensor) -> torch.Tensor:
                h = self.enc(self.inp(x) + self.pos)
                return self.out(h[:, -1, :])  # type: ignore[no-any-return]

        return Net()
