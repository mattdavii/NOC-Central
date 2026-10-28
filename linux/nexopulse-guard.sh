#!/usr/bin/env bash
# Guarda da atualização do NexoPulse Edge (ExecStartPre do serviço nexopulse-edge).
#
# Depois de uma atualização, o agente deixa .atualizacao/pendente e o apaga no
# primeiro contato com a Central. Se o serviço iniciar 3 vezes sem isso acontecer
# (versão nova quebrando ao abrir, ou sem conseguir falar com a Central em 10 min),
# esta guarda restaura a versão anterior e marca a nova como recusada; a Central
# não a oferece de novo a este sensor e avisa no Telegram.
# Nunca impede o serviço de subir: sai sempre com 0.
set -u
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ESTADO="$RAIZ/.atualizacao"
LIMITE=3

[[ -f "$ESTADO/pendente" ]] || exit 0
tentativas=$(( $(cat "$ESTADO/tentativas" 2>/dev/null || echo 0) + 1 ))
echo "$tentativas" > "$ESTADO/tentativas"
if (( tentativas < LIMITE )); then
  echo "nexopulse-guard: versão nova em teste (início $tentativas de $((LIMITE - 1)))"
  exit 0
fi
[[ -d "$ESTADO/anterior" ]] && cp -a "$ESTADO/anterior/." "$RAIZ/"
head -1 "$ESTADO/pendente" > "$ESTADO/recusado"
rm -f "$ESTADO/pendente" "$ESTADO/tentativas"
echo "nexopulse-guard: versão $(cat "$ESTADO/recusado") falhou $((LIMITE - 1)) vezes; versão anterior restaurada"
exit 0
