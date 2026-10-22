#!/usr/bin/env bash
# Instala o vigia como timer do systemd do usuário (sem root), na máquina do banco.
# Configuração e estado ficam no banco: numa máquina nova, basta rodar de novo.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER="${NEXO_DB_CONTAINER:-nexopulse-db}"

command -v python3 >/dev/null || { echo "python3 não encontrado"; exit 1; }
command -v docker >/dev/null || { echo "docker não encontrado"; exit 1; }
docker inspect "$CONTAINER" >/dev/null 2>&1 || { echo "container do banco '$CONTAINER' não encontrado (defina NEXO_DB_CONTAINER)"; exit 1; }

install -Dm755 "$DIR/nexopulse-vigia.py" "$HOME/.local/bin/nexopulse-vigia"
install -Dm644 "$DIR/nexopulse-vigia.service" "$HOME/.config/systemd/user/nexopulse-vigia.service"
install -Dm644 "$DIR/nexopulse-vigia.timer" "$HOME/.config/systemd/user/nexopulse-vigia.timer"

systemctl --user daemon-reload
systemctl --user enable --now nexopulse-vigia.timer

if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
  echo
  echo "⚠️  Para o vigia rodar mesmo sem você logado, execute uma vez:"
  echo "    sudo loginctl enable-linger $USER"
fi

echo
echo "==> Primeiro teste:"
python3 "$HOME/.local/bin/nexopulse-vigia" --agora
echo
echo "✅ Vigia instalado. Intervalo e limites: página Servidor da Central (ficam no banco)."
echo "Logs: journalctl --user -u nexopulse-vigia -n 20 --no-pager"
