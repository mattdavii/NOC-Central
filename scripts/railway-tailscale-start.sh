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

# Entre o Railway e a tailnet, pacotes WireGuard acima de ~1300 bytes se
# perdem: com o MTU padrão (1280) as respostas grandes do Postgres nunca
# chegavam e o psycopg2 congelava o worker. Um MTU menor reduz o MSS do TCP.
export TS_DEBUG_MTU="${TS_DEBUG_MTU:-1200}"

"$TS_DIR/tailscaled" --tun=userspace-networking --state=mem: --socket="$TS_SOCKET" \
  --socks5-server="127.0.0.1:${TS_SOCKS_PORT}" &

"$TS_DIR/tailscale" --socket="$TS_SOCKET" up --auth-key="$TS_AUTHKEY" \
  --hostname="${TS_HOSTNAME:-railway-nexopulse}" --timeout=60s

# O banco mora num PC pela tailnet: se ele suspender, reiniciar ou perder a
# rede, a Central não pode desistir. Antes, o container esperava 120 s e saía;
# depois de algumas saídas o Railway marcava CRASHED e não tentava mais, e a
# Central ficava fora até alguém reiniciar à mão. Agora ela espera o banco sem
# limite e, se o gunicorn cair (worker preso no banco), espera e sobe de novo.
esperar_banco() {
  until python scripts/ts_db_forward.py --wait "${TS_DB_WAIT:-120}"; do
    echo "[start] banco ainda inalcançável pela tailnet; continuo esperando"
  done
}

esperar_banco
python scripts/ts_db_forward.py &

gunicorn_pid=""
encerrar() {
  [ -n "$gunicorn_pid" ] && kill -TERM "$gunicorn_pid" 2>/dev/null && wait "$gunicorn_pid"
  exit 0
}
trap encerrar TERM INT

while true; do
  # Com o banco a ~200 ms pela tailnet, as migrações do boot passam dos 30 s
  # padrão do gunicorn e o worker seria morto em loop (WORKER TIMEOUT).
  gunicorn app:app --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
    -w 1 --bind "0.0.0.0:${PORT:-8000}" --no-control-socket --timeout "${GUNICORN_TIMEOUT:-180}" &
  gunicorn_pid=$!
  wait "$gunicorn_pid" || true
  gunicorn_pid=""
  echo "[start] gunicorn encerrou; aguardando o banco para subir de novo"
  sleep 5
  esperar_banco
done
