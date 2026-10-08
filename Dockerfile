FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DB_PATH=/data/agent.sqlite
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home agent && mkdir /data && chown agent:agent /data
COPY --chown=agent:agent app ./app
COPY --chown=agent:agent mt5 ./mt5
# Bundle Windows SDK wheels once in CI; the connector installs offline with Python 3.12 x64.
RUN pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 --implementation cp --abi cp312 --dest /app/connector-wheels -r mt5/requirements.txt
USER agent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log"]
