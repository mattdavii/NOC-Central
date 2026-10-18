#!/usr/bin/env bash
# Start do NOC-Central no Railway com o banco acessado pela tailnet (Tailscale).
#
# Variáveis no serviço do Railway:
#   TS_AUTHKEY        chave de autenticação do Tailscale (de preferência efêmera)
#   TS_DB_HOST        nome da máquina com o Postgres na tailnet (ex.: mateus-linux)
#   TS_DB_PORT        porta do Postgres nessa máquina (padrão 5432)
#   TS_DB_LOCAL_PORT  porta local usada no DATABASE_URL (padrão 15432)
#   DATABASE_URL      postgresql://usuario:senha@127.0.0.1:15432/banco
set -euo pipefail

TS_VERSION="${TS_VERSION:-1.104.1}"
TS_DIR=/tmp/tailscale
TS_SOCKET=/tmp/tailscaled.sock
export TS_SOCKS_PORT="${TS_SOCKS_PORT:-1055}"
export TS_DB_LOCAL_PORT="${TS_DB_LOCAL_PORT:-15432}"

: "${TS_AUTHKEY:?defina TS_AUTHKEY no Railway}"
: "${TS_DB_HOST:?defina TS_DB_HOST no Railway}"

if [ ! -x "$TS_DIR/tailscaled" ]; then
  # A imagem do Railpack não garante curl; baixa e extrai com Python.
  python - "$TS_VERSION" "$TS_DIR" <<'PY'
import io, os, sys, tarfile, urllib.request
version, dest = sys.argv[1], sys.argv[2]
url = f"https://pkgs.tailscale.com/stable/tailscale_{version}_amd64.tgz"
data = urllib.request.urlopen(url, timeout=60).read()
os.makedirs(dest, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(data)) as tar:
    for member in tar.getmembers():
        name = os.path.basename(member.name)
        if name in ("tailscale", "tailscaled"):
            member.name = name
            tar.extract(member, dest)
PY
fi

"$TS_DIR/tailscaled" --tun=userspace-networking --state=mem: --socket="$TS_SOCKET" \
  --socks5-server="127.0.0.1:${TS_SOCKS_PORT}" &

"$TS_DIR/tailscale" --socket="$TS_SOCKET" up --auth-key="$TS_AUTHKEY" \
  --hostname="${TS_HOSTNAME:-railway-nexopulse}" --timeout=60s

python scripts/ts_db_forward.py &

exec gunicorn app:app --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
  -w 1 --bind "0.0.0.0:${PORT:-8000}" --no-control-socket
