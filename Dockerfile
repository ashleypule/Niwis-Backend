# NIWIS backend API
FROM python:3.12-slim AS runtime

# MPLCONFIGDIR: forecasting workers import matplotlib (Prophet); /tmp is writable in the read-only container
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /srv

COPY requirements.txt .
# requirements.txt names the CPU-only PyTorch index (forecasting; no CUDA libraries)
# LightGBM (the Studio's gradient-boosting model, the AI page's feature importance) needs OpenMP at run time
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
RUN pip install -r requirements.txt

COPY app ./app
# Boundaries (reference/geo) and the contract inputs the app reads at runtime. The confidential spec is excluded by
# .dockerignore.
COPY reference/geo ./reference/geo

RUN useradd --create-home --uid 10001 niwis
USER niwis

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"

# X-Forwarded-For is handled only by app.middleware.RateLimitMiddleware (TRUST_PROXY_HEADERS / TRUSTED_PROXY_CIDRS),
# so uvicorn must not rewrite the client address itself; no Server header.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-server-header"]
