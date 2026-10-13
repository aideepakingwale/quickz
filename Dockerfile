# Quickz - small, non-root image. Works on Render, Koyeb, Fly, Railway, any Docker host.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    QUICKZ_HOST=0.0.0.0 \
    QUICKZ_DATA=/data

WORKDIR /app
COPY requirements-prod.txt .
RUN pip install -r requirements-prod.txt

COPY quickz ./quickz
RUN useradd --create-home --uid 10001 quickz && mkdir -p /data && chown quickz:quickz /data
USER quickz

# Platforms inject $PORT (Render uses 10000); locally it defaults to 8000.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','8000'),timeout=4)"

# One process on purpose: live game state is held in memory, so do not run several replicas.
CMD ["python", "-m", "quickz"]
