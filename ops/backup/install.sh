#!/usr/bin/env bash
# Instala o backup diário como timer do systemd do usuário (sem root).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE="${RCLONE_REMOTE:-gdrive}"

command -v docker >/dev/null || { echo "docker não encontrado"; exit 1; }
command -v rclone >/dev/null || { echo "rclone não encontrado: instale com o gerenciador de pacotes (ex.: sudo pacman -S rclone)"; exit 1; }
if ! rclone listremotes | grep -qx "${REMOTE}:"; then
  echo "Remote '${REMOTE}' não existe no rclone. Crie com:"
  echo "  rclone config create ${REMOTE} drive scope=drive.file"
  echo "(abre o navegador para autorizar o Google Drive) e rode este instalador de novo."
  exit 1
fi

install -Dm755 "$DIR/nexopulse-backup.sh" "$HOME/.local/bin/nexopulse-backup"
install -Dm644 "$DIR/nexopulse-backup.service" "$HOME/.config/systemd/user/nexopulse-backup.service"
install -Dm644 "$DIR/nexopulse-backup.timer" "$HOME/.config/systemd/user/nexopulse-backup.timer"

systemctl --user daemon-reload
systemctl --user enable --now nexopulse-backup.timer

# Sem "linger", timers do usuário só rodam com sessão aberta.
if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
  echo
  echo "⚠️  Para o backup rodar mesmo sem você logado, execute uma vez:"
  echo "    sudo loginctl enable-linger $USER"
fi

echo
echo "✅ Backup diário instalado (03:30). Próxima execução:"
systemctl --user list-timers nexopulse-backup.timer --no-pager | sed -n 2p
echo "Rodar agora:  systemctl --user start nexopulse-backup && journalctl --user -u nexopulse-backup -n 20 --no-pager"
