# Migrar o banco para o MiniPC

Leva o banco da NexoPulse (TimescaleDB em Docker), o backup diário e o vigia do
notebook para o MiniPC. A Central (Railway) continua onde está; só passa a
buscar o banco no MiniPC.

- **Indisponibilidade:** ~10 minutos, só na virada. Os sensores guardam as medições
  nesse intervalo e enviam depois, com o horário original.
- **Nada se perde:** o dump final é feito com a Central desconectada, e a
  restauração confere a contagem de linhas de cada tabela contra a origem.
- **Configuração da saúde e do vigia:** fica no banco, vai junto sozinha.
- **Volta atrás:** o notebook fica intacto por 7 dias (veja *Rollback*).

Scripts em [`ops/minipc/`](../ops/minipc):

| Script | Onde roda | O que faz |
|---|---|---|
| `preparar.sh` | MiniPC | pacotes, SSH, Docker, Tailscale, **desliga a suspensão**, sobe o banco vazio e o publica na tailnet |
| `exportar.sh` | notebook | dump + contagem por tabela + checksum; `--enviar` copia pela tailnet com a senha do banco e a config do rclone; `--congelar` desconecta a Central antes do dump |
| `restaurar.sh` | MiniPC | confere checksum e versão, restaura no modo do TimescaleDB e compara tabela a tabela |
| `verificar.sh` | MiniPC | lista de conferência final (máquina, banco, rede, Central, backup, vigia) |

---

## 1. Preparar o MiniPC (sem impacto, faça antes)

No MiniPC, com Linux instalado (Debian/Ubuntu, Arch/CachyOS ou Fedora), como
usuário normal:

```bash
git clone https://github.com/mattdavii/NOC-Central.git ~/NOC-Central
cd ~/NOC-Central && git checkout work/railway-tailscale-db
bash ops/minipc/preparar.sh
```

- Na primeira vez ele te coloca no grupo `docker` e pede para **sair e entrar de
  novo**; rode o mesmo comando outra vez.
- Abra o link do Tailscale que aparecer para colocar o MiniPC na tailnet (nome
  `nexopulse-minipc`).
- Ele para avisando que falta o `~/nexopulse-db/.env`. É esperado: vem no passo 2.

## 2. Ensaio (sem impacto)

No **notebook**:

```bash
bash ops/minipc/exportar.sh --enviar SEU_USUARIO@nexopulse-minipc
```

No **MiniPC**:

```bash
bash ops/minipc/preparar.sh        # agora com o .env: sobe o banco e publica a 5432
bash ops/minipc/restaurar.sh ~/nexopulse-migracao/<pasta que o exportar mostrou>
```

Tem que terminar em **"✅ Banco restaurado e conferido: N tabelas iguais à origem."**
A Central segue usando o notebook; isso só prova que a mudança funciona.

## 3. Virada (~10 min fora do ar)

1. **Notebook — desligar vigia e backup** (senão o vigia daqui alerta a queda proposital):

   ```bash
   systemctl --user disable --now nexopulse-vigia.timer nexopulse-backup.timer
   ```

2. **Notebook — dump final com a Central desconectada e envio:**

   ```bash
   bash ops/minipc/exportar.sh --congelar --enviar SEU_USUARIO@nexopulse-minipc
   ```

   A partir daqui a Central fica esperando o banco (não cai) e os sensores guardam
   as medições.

3. **MiniPC — restaurar por cima do ensaio:**

   ```bash
   bash ops/minipc/restaurar.sh ~/nexopulse-migracao/<pasta nova> --substituir
   ```

4. **Railway — apontar a Central para o MiniPC:** no serviço `nexopulse-homolog`,
   troque a variável `TS_DB_HOST` pelo IP do MiniPC na tailnet (o `preparar.sh`
   mostra, ex.: `100.x.y.z`; ou `tailscale ip -4` no MiniPC) e aplique o deploy.
   `PGPASSWORD` e `DATABASE_URL` **não mudam** (a senha veio no `.env`).

5. **MiniPC — backup e vigia:**

   ```bash
   bash ops/backup/install.sh
   bash ops/vigia/install.sh
   systemctl --user start nexopulse-backup   # primeiro backup já da máquina nova
   ```

6. **MiniPC — conferir tudo:**

   ```bash
   bash ops/minipc/verificar.sh
   ```

   Tem que terminar em **"✅ MiniPC pronto e servindo a Central."** Na Central, a
   página **Servidor** deve mostrar o vigia reportando do `nexopulse-minipc`.

## 4. Depois da virada

- **Notebook:** pare o banco, mas **não apague** por 7 dias (é o rollback):

  ```bash
  cd ~/Projetos/nexopulse-db && docker compose stop
  ```

  Passados os 7 dias, se tudo estiver bem: `docker compose down` (o volume com os
  dados ainda fica; `docker volume rm nexopulse-db_nexopulse-pgdata` apaga de vez).
- **Sensor no MiniPC (opcional):** para monitorar a rede onde ele está:
  `curl -fsSL https://nexopulse.up.railway.app/instalar.sh | bash`.
- **Monitor externo (recomendado):** como o vigia some se o MiniPC desligar, um
  monitor gratuito (ex.: UptimeRobot) em `https://nexopulse.up.railway.app/healthz`
  cobre esse caso.

## Rollback (voltar para o notebook)

Só se algo der errado na virada. **Tudo o que for gravado no MiniPC depois da
virada se perde** ao voltar.

1. Notebook:

   ```bash
   cd ~/Projetos/nexopulse-db && docker compose start
   tailscale serve --bg --tcp 5432 tcp://localhost:5432
   systemctl --user enable --now nexopulse-vigia.timer nexopulse-backup.timer
   ```

2. Railway: `TS_DB_HOST` de volta para o IP do notebook (`100.64.213.122`) e deploy.
3. MiniPC: `systemctl --user disable --now nexopulse-vigia.timer nexopulse-backup.timer`.

## Problemas comuns

| Sintoma | Causa provável e o que fazer |
|---|---|
| `restaurar.sh` diz "imagem diferente da origem" | o `docker-compose.yml` do kit e o do notebook divergiram; use a mesma imagem (`timescale/timescaledb:2.21.3-pg16`) |
| `restaurar.sh` mostra ❌ em alguma tabela | exporte de novo (`exportar.sh`) e restaure com `--substituir`; veja o `restauracao.log` na pasta |
| Central não volta após trocar `TS_DB_HOST` | no MiniPC, `tailscale serve status` precisa listar a 5432; nos logs do Railway, procure `[ts-db-forward]` |
| `verificar.sh`: "Central conectada a ESTE banco" ❌ | o `TS_DB_HOST` ainda aponta para o notebook, ou o deploy não terminou |
| Backup falha no MiniPC | a config do rclone não veio: copie `~/.config/rclone/rclone.conf` do notebook (o `--enviar` já faz isso) |
