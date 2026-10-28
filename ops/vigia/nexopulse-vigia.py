#!/usr/bin/env python3
"""Vigia da NexoPulse: testa a Central e a máquina do banco de fora da Central.

Roda pelo timer do systemd do usuário a cada minuto, mas só testa de fato no
intervalo gravado no banco (config_sistema.vigia_intervalo_min, 2 a 5 min).
Configuração, estado e destino dos alertas ficam no banco; o cache local
(~/.cache/nexopulse-vigia) só é usado quando o banco não responde, para o vigia
conseguir avisar justamente nessa hora.

Variáveis opcionais: NEXO_DB_CONTAINER (nexopulse-db), NEXO_DB_USER (nexopulse),
NEXO_DB_NAME (nexopulse), BACKUP_LOCAL_DIR (~/.local/share/nexopulse-backup).
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

CONTAINER = os.environ.get("NEXO_DB_CONTAINER", "nexopulse-db")
DB_USER = os.environ.get("NEXO_DB_USER", "nexopulse")
DB_NAME = os.environ.get("NEXO_DB_NAME", "nexopulse")
BACKUP_DIR = os.path.expanduser(os.environ.get("BACKUP_LOCAL_DIR", "~/.local/share/nexopulse-backup"))
CACHE_DIR = os.path.expanduser(os.environ.get("NEXO_VIGIA_CACHE", "~/.cache/nexopulse-vigia"))
CACHE = os.path.join(CACHE_DIR, "cache.json")
PADRAO = {"vigia_intervalo_min": "3", "falhas_para_alertar": "2", "disco_alerta_pct": "85", "disco_critico_pct": "95",
          "backup_max_horas": "26", "central_url": "https://nexopulse.up.railway.app"}
HOST = socket.gethostname()


def log(msg):
    print(f"[nexopulse-vigia] {msg}", flush=True)


def rodar(cmd, timeout=20, entrada=None):
    try:
        r = subprocess.run(cmd, input=entrada, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return None, "", str(e)


def psql(sql):
    """Executa SQL no banco pelo container. Devolve as linhas (lista de listas) ou levanta RuntimeError."""
    codigo, saida, erro = rodar(["docker", "exec", "-i", CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME,
                                 "-At", "-F", "\t", "-v", "ON_ERROR_STOP=1"], 30, sql)
    if codigo != 0:
        raise RuntimeError((erro or saida or "psql falhou").strip()[:200])
    return [linha.split("\t") for linha in saida.splitlines() if linha]


def q(valor):
    return "NULL" if valor is None else "'" + str(valor).replace("'", "''") + "'"


def ler_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def gravar_cache(dados):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(dados, f)
    os.chmod(tmp, 0o600)  # guarda o token do bot
    os.replace(tmp, CACHE)


def num(cfg, chave):
    try:
        return float(cfg.get(chave, PADRAO.get(chave)))
    except (TypeError, ValueError):
        return float(PADRAO.get(chave, 0))


# ---------------------------------------------------------------- verificações
def check(chave, rotulo, estado, valor, detalhe=""):
    return {"chave": chave, "rotulo": rotulo, "estado": estado, "valor": valor, "detalhe": detalhe}


def verificar_banco(banco_ok, erro):
    if banco_ok:
        return check("banco", "Banco de dados", "ok", "respondendo")
    return check("banco", "Banco de dados", "critico", "sem resposta", erro or "o container do banco não respondeu")


def verificar_central(url):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=20) as r:
            corpo = json.loads(r.read() or b"{}")
        if corpo.get("status") == "ok":
            return check("central", "Central (site)", "ok", f"no ar · v{corpo.get('central_version', '?')}")
        return check("central", "Central (site)", "critico", "resposta inesperada")
    except urllib.error.HTTPError as e:
        return check("central", "Central (site)", "critico", f"HTTP {e.code}", url)
    except Exception as e:
        return check("central", "Central (site)", "critico", "fora do ar", f"{type(e).__name__}: {e}"[:160])


def verificar_tailscale():
    if not shutil.which("tailscale"):
        return [check("tailscale", "Tailscale", "na", "não instalado")]
    codigo, saida, _ = rodar(["tailscale", "status", "--json"])
    try:
        estado = json.loads(saida).get("BackendState") if codigo == 0 else None
    except ValueError:
        estado = None
    saida_ts = check("tailscale", "Tailscale", "ok" if estado == "Running" else "critico", estado or "parado",
                     "" if estado == "Running" else "a Central não alcança o banco sem a tailnet")
    _, serve, _ = rodar(["tailscale", "serve", "status"])
    publicado = ":5432" in serve
    return [saida_ts, check("tailscale_serve", "Banco publicado na tailnet", "ok" if publicado else "critico",
                            "porta 5432" if publicado else "não publicado",
                            "" if publicado else "rode: tailscale serve --bg --tcp 5432 tcp://localhost:5432")]


def verificar_disco(cfg):
    pontos = {"/"}
    codigo, saida, _ = rodar(["docker", "info", "-f", "{{.DockerRootDir}}"])
    if codigo == 0 and saida.strip():
        pontos.add(saida.strip())
    pior = None
    for p in pontos:
        try:
            u = shutil.disk_usage(p)
        except OSError:
            continue
        pct = 100.0 * u.used / u.total
        if pior is None or pct > pior[1]:
            pior = (p, pct, u.free)
    if not pior:
        return check("disco", "Disco", "na", "sem leitura")
    p, pct, livre = pior
    estado = "critico" if pct >= num(cfg, "disco_critico_pct") else "alerta" if pct >= num(cfg, "disco_alerta_pct") else "ok"
    return check("disco", "Disco", estado, f"{pct:.0f}% em {p}", f"{livre / 2**30:.1f} GB livres")


def verificar_backup(cfg):
    dumps = sorted(glob.glob(os.path.join(BACKUP_DIR, "nexopulse-*.dump")), key=os.path.getmtime)
    if not dumps:
        return check("backup", "Backup diário", "alerta", "nenhum backup local", f"esperado em {BACKUP_DIR}")
    horas = (time.time() - os.path.getmtime(dumps[-1])) / 3600
    atrasado = horas > num(cfg, "backup_max_horas")
    return check("backup", "Backup diário", "alerta" if atrasado else "ok", f"último há {horas:.0f} h",
                 "o backup diário não está rodando (journalctl --user -u nexopulse-backup)" if atrasado else os.path.basename(dumps[-1]))


# ---------------------------------------------------------------- alertas
def telegram(cfg, texto):
    token, chat = cfg.get("telegram_bot_token"), cfg.get("telegram_chat_id")
    if not token or not chat:
        log("sem destino de alerta no banco (telegram_bot_token/telegram_chat_id); alerta não enviado")
        return False
    dados = urllib.parse.urlencode({"chat_id": chat, "text": texto, "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=dados, timeout=20) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        log(f"falha ao enviar alerta: {type(e).__name__}")
        return False


def aplicar(checks, anteriores, cfg):
    limite = max(1, int(num(cfg, "falhas_para_alertar")))
    agora = time.time()
    novos = {}
    for c in checks:
        ant = anteriores.get(c["chave"], {})
        ruim = c["estado"] in ("alerta", "critico")
        falhas = int(ant.get("falhas") or 0) + 1 if ruim else 0
        notificado = int(ant.get("notificado") or 0)
        desde = float(ant.get("desde") or agora) if ruim else None
        if ruim and not notificado and falhas >= limite:
            icone = "🚨" if c["estado"] == "critico" else "⚠️"
            texto = f"{icone} VIGIA NEXOPULSE ({HOST})\n\n{c['rotulo']}: {c['valor']}" + (f"\n{c['detalhe']}" if c["detalhe"] else "")
            notificado = 1 if telegram(cfg, texto) else 0
        elif not ruim and notificado:
            if telegram(cfg, f"✅ VIGIA NEXOPULSE ({HOST})\n\n{c['rotulo']} normalizado: {c['valor']}"):
                notificado = 0
        novos[c["chave"]] = {**c, "falhas": falhas, "notificado": notificado, "desde": desde, "atualizado_em": agora}
    return novos


def main():
    cache = ler_cache()
    cfg = {**PADRAO, **cache.get("config", {})}
    banco_ok, erro_banco = True, None
    try:
        cfg.update({chave: valor for chave, valor in psql("SELECT chave, valor FROM config_sistema")})
    except RuntimeError as e:
        banco_ok, erro_banco = False, str(e)

    agora = time.time()
    intervalo = 60 * min(5, max(2, num(cfg, "vigia_intervalo_min")))
    if "--agora" not in sys.argv and agora - float(cache.get("ultima_execucao", 0)) < intervalo - 20:
        return 0

    anteriores = dict(cache.get("estado", {}))
    if banco_ok:
        try:
            colunas = ["chave", "falhas", "notificado", "desde"]
            for linha in psql("SELECT chave, falhas, notificado, desde FROM saude_estado WHERE origem='vigia'"):
                anteriores[linha[0].removeprefix("vigia:")] = dict(zip(colunas, linha))
        except RuntimeError:
            pass

    checks = [verificar_banco(banco_ok, erro_banco), verificar_central(cfg.get("central_url") or PADRAO["central_url"]),
              *verificar_tailscale(), verificar_disco(cfg), verificar_backup(cfg)]
    estado = aplicar(checks, anteriores, cfg)

    if banco_ok:
        sql = ["BEGIN;"]
        for c in estado.values():
            sql.append(f"""INSERT INTO saude_estado (chave, origem, rotulo, estado, valor, detalhe, desde, falhas, notificado, atualizado_em)
                VALUES ({q('vigia:' + c['chave'])}, 'vigia', {q(c['rotulo'])}, {q(c['estado'])},
                {q(c['valor'])}, {q(c['detalhe'])}, {q(c['desde'])}, {c['falhas']}, {c['notificado']}, {c['atualizado_em']})
                ON CONFLICT (chave) DO UPDATE SET rotulo=excluded.rotulo, estado=excluded.estado, valor=excluded.valor, detalhe=excluded.detalhe,
                desde=excluded.desde, falhas=excluded.falhas, notificado=excluded.notificado, atualizado_em=excluded.atualizado_em;""")
        for chave, valor in (("vigia_ultimo_batimento", agora), ("vigia_host", HOST)):
            sql.append(f"""INSERT INTO config_sistema (chave, valor, atualizado_em) VALUES ({q(chave)}, {q(valor)}, {agora})
                ON CONFLICT (chave) DO UPDATE SET valor=excluded.valor, atualizado_em=excluded.atualizado_em;""")
        sql.append("COMMIT;")
        try:
            psql("\n".join(sql))
        except RuntimeError as e:
            log(f"não gravou o estado no banco: {e}")

    gravar_cache({"config": cfg, "estado": {k: {x: v[x] for x in ("falhas", "notificado", "desde")} for k, v in estado.items()},
                  "ultima_execucao": agora})
    log(" · ".join(f"{c['chave']}={c['estado']}" for c in checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
