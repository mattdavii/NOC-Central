#!/usr/bin/env bash
# Libera as ações de root do NexoPulse Edge ("Atualizar sistema" e "Reiniciar máquina").
#
# Instala o ajudante nexopulse-admin (só update-system, reboot, restart-agent e
# start-service) e uma regra de sudo que deixa o usuário do sensor rodar SÓ ele, sem
# senha. Com isso a Central pode atualizar e reiniciar esta máquina (cada uso ainda
# pede a senha do Administrador Master na Central).
#
#   bash linux/liberar-root.sh             libera (o instalador chama com NEXOPULSE_ROOT=1)
#   bash linux/liberar-root.sh --desfazer  remove o ajudante e a regra
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AJUDANTE="${NEXOPULSE_ADMIN_DESTINO:-/usr/local/sbin/nexopulse-admin}"
REGRA="${NEXOPULSE_SUDOERS_DESTINO:-/etc/sudoers.d/nexopulse-edge}"
USUARIO="$(id -un)"

if [[ "${EUID}" -eq 0 ]]; then
  echo "Rode como o usuário do sensor (o script pede sudo quando precisa)."
  exit 1
fi

if [[ "${1:-}" == "--desfazer" ]]; then
  sudo rm -f "$REGRA" "$AJUDANTE"
  echo "✅ Ações de root removidas: a Central não atualiza nem reinicia mais esta máquina."
  exit 0
fi

regra_tmp="$(mktemp)"
trap 'rm -f "$regra_tmp"' EXIT
echo "${USUARIO} ALL=(root) NOPASSWD: ${AJUDANTE}" > "$regra_tmp"
# Valida antes de pôr no lugar: uma regra inválida em sudoers.d quebraria o sudo.
sudo visudo -cf "$regra_tmp" >/dev/null
sudo install -o root -g root -m 0755 "$RAIZ/linux/nexopulse-admin" "$AJUDANTE"
sudo install -o root -g root -m 0440 "$regra_tmp" "$REGRA"
sudo visudo -c >/dev/null
echo "✅ Ações de root liberadas para ${USUARIO} (só ${AJUDANTE}). Para desfazer: bash $RAIZ/linux/liberar-root.sh --desfazer"
