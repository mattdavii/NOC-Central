# Imagem da Central NexoPulse / NexoHub: igual no MiniPC (Coolify) e num VPS no futuro.
# Toda a configuração vem de variáveis de ambiente (DATABASE_URL, FLASK_SECRET_KEY, ...);
# o banco é acessado pelo endereço do Tailscale, nunca por "localhost".
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# Roda sem root: a Central só lê os próprios arquivos (o estado mora no PostgreSQL).
RUN useradd --system --uid 10001 --home /app nexo && chown -R nexo /app
USER nexo

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/healthz', timeout=8)"

# Mesmo comando do Procfile: um worker gevent (Socket.IO) atrás do proxy.
CMD ["sh", "-c", "exec gunicorn app:app --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 --bind 0.0.0.0:${PORT} --no-control-socket"]
