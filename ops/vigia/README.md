# Vigia da NexoPulse

Roda **na máquina do banco** e testa, de fora da Central:

| Teste | Alerta quando |
|---|---|
| Central (`/healthz`) | o site não responde ou responde erro |
| Banco | o container do Postgres não responde |
| Tailscale / banco publicado | a tailnet parou ou a porta 5432 deixou de ser publicada (a Central não alcança o banco) |
| Disco | uso acima do limite (raiz e pasta do Docker) |
| Backup diário | o dump local mais novo é mais velho que o limite (padrão 26 h) |

Um problema só vira alerta depois de **N testes seguidos com falha** (padrão 2), avisa
uma vez no grupo NexoPulse e avisa de novo quando normaliza. O intervalo entre os
testes é de **2 a 5 minutos** (padrão 3).

## Tudo fica no banco

Intervalo, limites, estado dos testes e o destino dos alertas (bot e grupo master)
ficam nas tabelas `config_sistema` e `saude_estado`. A Central grava o destino a
cada boot, a partir das variáveis `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`; o
intervalo e os limites mudam na página **Servidor** da Central. Na máquina só
ficam o script e um cache (`~/.cache/nexopulse-vigia`, permissão 600) usado
apenas quando o banco não responde, para o vigia conseguir avisar justamente aí.

**Ao levar o banco para outra máquina (ex.: MiniPC):** restaure o banco, rode o
instalador abaixo lá e pronto. Nada para reconfigurar.

## Instalar

```bash
bash ops/vigia/install.sh
sudo loginctl enable-linger "$USER"   # uma vez, para rodar sem sessão aberta
```

Teste manual: `python3 ~/.local/bin/nexopulse-vigia --agora`.
Logs: `journalctl --user -u nexopulse-vigia -n 20 --no-pager`.

O timer dispara a cada minuto e o script decide, pelo intervalo do banco, se é hora
de testar. Se a máquina inteira desligar, o vigia some junto; para cobrir isso,
aponte um monitor externo (ex.: UptimeRobot) para `https://nexopulse.up.railway.app/healthz`.
