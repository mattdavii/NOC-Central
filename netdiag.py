"""Testes de rede em camadas do NexoPulse Edge.

A cada janela (NOC_DIAG_JANELA, 60 s) mede, em paralelo:
- ICMP para o gateway local, o 1º salto do provedor (descoberto pela rota) e
  os alvos externos (1.1.1.1 e 8.8.8.8): latência média, jitter e perda;
- DNS, separado do ICMP: o resolvedor do sistema e consultas UDP diretas a
  servidores públicos, para separar "DNS do site falhou" de "sem internet".

A rota (mtr, traceroute ou tracepath, o que existir) roda periodicamente e
também quando a janela mostra degradação; comparada com a última rota boa,
indica qual salto piorou. Tudo configurável por variáveis NOC_DIAG_*.
"""
import concurrent.futures
import json
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import time
import urllib.request


def _lista(nome, padrao):
    return [v.strip() for v in os.environ.get(nome, padrao).split(",") if v.strip()]


def _num(nome, padrao, minimo):
    try:
        return max(minimo, float(os.environ.get(nome, padrao)))
    except ValueError:
        return float(padrao)


CONFIG = {
    "janela_s": _num("NOC_DIAG_JANELA", 60, 10),
    "ping_intervalo_s": _num("NOC_DIAG_PING_INTERVALO", 2, 1),
    "externos": _lista("NOC_DIAG_EXTERNOS", "1.1.1.1,8.8.8.8"),
    "dns_nomes": _lista("NOC_DIAG_DNS_NOMES", "cloudflare.com,google.com"),
    "dns_servidores": _lista("NOC_DIAG_DNS_SERVIDORES", "1.1.1.1,8.8.8.8"),
    "dns_timeout_s": _num("NOC_DIAG_DNS_TIMEOUT", 2, 0.5),
    "trace_alvo": os.environ.get("NOC_DIAG_TRACE_ALVO", "8.8.8.8"),
    "trace_intervalo_s": _num("NOC_DIAG_TRACE_INTERVALO", 900, 60),
    "trace_min_intervalo_s": _num("NOC_DIAG_TRACE_MIN", 120, 30),
    "redescobrir_salto_s": _num("NOC_DIAG_REDESCOBRIR_SALTO", 1800, 120),
    "gatilho_perda_pct": _num("NOC_DIAG_GATILHO_PERDA", 5, 0),
    "gatilho_latencia_ms": _num("NOC_DIAG_GATILHO_LATENCIA", 150, 1),
    "gatilho_jitter_ms": _num("NOC_DIAG_GATILHO_JITTER", 30, 1),
}
ALVO_VALIDO = re.compile(r"^[A-Za-z0-9_.:-]+$")
AMBIENTE_C = {**os.environ, "LC_ALL": "C", "LANG": "C"}  # saída das ferramentas em inglês, sempre


def _rodar(cmd, timeout):
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                           timeout=timeout, text=True, errors="replace", env=AMBIENTE_C)
        return r.returncode, r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return None, str(e)


# ---------------------------------------------------------------- ICMP
def parse_ping(saida):
    rtts = [float(v) for v in re.findall(r"time[=<]([\d.]+)\s*ms", saida)]
    m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", saida)
    enviados = int(m.group(1)) if m else len(rtts)
    recebidos = int(m.group(2)) if m else len(rtts)
    jitter = (sum(abs(b - a) for a, b in zip(rtts, rtts[1:])) / (len(rtts) - 1)) if len(rtts) > 1 else (0.0 if rtts else None)
    return {
        "enviados": enviados, "recebidos": recebidos,
        "perda_pct": round(100.0 * (enviados - recebidos) / enviados, 1) if enviados else 100.0,
        "latencia_ms": round(sum(rtts) / len(rtts), 1) if rtts else None,
        "jitter_ms": round(jitter, 1) if jitter is not None else None,
        "min_ms": round(min(rtts), 1) if rtts else None,
        "max_ms": round(max(rtts), 1) if rtts else None,
    }


def pingar(alvo, quantidade, intervalo):
    if not alvo or not ALVO_VALIDO.match(alvo) or alvo.startswith("-"):
        return {"enviados": 0, "recebidos": 0, "perda_pct": 100.0, "latencia_ms": None, "jitter_ms": None, "erro": "alvo inválido"}
    quantidade = max(1, int(quantidade))
    _, saida = _rodar(["ping", "-n", "-c", str(quantidade), "-i", str(intervalo), "-W", "1", alvo],
                      quantidade * intervalo + 5)
    return parse_ping(saida)


# ---------------------------------------------------------------- DNS
def _pacote_dns(nome):
    ident = random.randint(0, 0xFFFF)
    cabecalho = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in nome.strip(".").split(".")) + b"\0"
    return ident, cabecalho + qname + struct.pack(">HH", 1, 1)


def consulta_dns(servidor, nome, timeout, porta=53):
    """Consulta A direta por UDP. Devolve o tempo em ms ou levanta OSError/ValueError."""
    ident, pacote = _pacote_dns(nome)
    familia = socket.AF_INET6 if ":" in servidor else socket.AF_INET
    with socket.socket(familia, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        inicio = time.monotonic()
        s.sendto(pacote, (servidor, porta))
        while True:
            resposta, _ = s.recvfrom(4096)
            if len(resposta) >= 12 and struct.unpack(">H", resposta[:2])[0] == ident:
                break
        ms = (time.monotonic() - inicio) * 1000
    flags, _, respostas = struct.unpack(">HHH", resposta[2:8])
    if flags & 0x000F:
        raise ValueError(f"rcode {flags & 0x000F}")
    if respostas == 0:
        raise ValueError("sem resposta")
    return ms


def resolver_sistema(nome, timeout=None):
    inicio = time.monotonic()
    socket.getaddrinfo(nome, 443, proto=socket.IPPROTO_TCP)
    return (time.monotonic() - inicio) * 1000


def testar_dns(nomes, servidores, timeout):
    resultado = []
    for servidor in ["sistema"] + list(servidores):
        tempos, falhas, erro = [], 0, None
        for nome in nomes:
            try:
                ms = resolver_sistema(nome) if servidor == "sistema" else consulta_dns(servidor, nome, timeout)
                tempos.append(ms)
            except (OSError, ValueError, UnicodeError) as e:
                falhas += 1
                erro = str(e)[:120]
        resultado.append({"servidor": servidor, "consultas": len(nomes), "falhas": falhas,
                          "tempo_ms": round(sum(tempos) / len(tempos), 1) if tempos else None,
                          **({"erro": erro} if erro else {})})
    return resultado


def somar_dns(medidas):
    """Junta as rodadas de DNS de uma janela num resumo por servidor."""
    total = {}
    for rodada in medidas:
        for m in rodada:
            t = total.setdefault(m["servidor"], {"servidor": m["servidor"], "consultas": 0, "falhas": 0, "_tempos": []})
            t["consultas"] += m["consultas"]
            t["falhas"] += m["falhas"]
            if m.get("tempo_ms") is not None:
                t["_tempos"].append(m["tempo_ms"])
            if m.get("erro"):
                t["erro"] = m["erro"]
    saida = []
    for t in total.values():
        tempos = t.pop("_tempos")
        t["tempo_ms"] = round(sum(tempos) / len(tempos), 1) if tempos else None
        t["falha_pct"] = round(100.0 * t["falhas"] / t["consultas"], 1) if t["consultas"] else None
        saida.append(t)
    return saida


# ---------------------------------------------------------------- rota
def parse_traceroute(saida):
    saltos = []
    for linha in saida.splitlines():
        m = re.match(r"^\s*(\d+)\s+(.*)$", linha)
        if not m:
            continue
        resto = m.group(2)
        ip = next(iter(re.findall(r"\d{1,3}(?:\.\d{1,3}){3}", resto)), None)
        rtts = [float(v) for v in re.findall(r"([\d.]+)\s*ms", resto)]
        sondas = len(rtts) + resto.count("*")
        saltos.append({"ttl": int(m.group(1)), "ip": ip,
                       "rtt_ms": round(sum(rtts) / len(rtts), 1) if rtts else None,
                       "perda_pct": round(100.0 * (sondas - len(rtts)) / sondas, 1) if sondas else 100.0})
    return saltos


def parse_tracepath(saida):
    por_ttl = {}
    for linha in saida.splitlines():
        m = re.match(r"^\s*(\d+)\??:\s+(\S+)(?:\s+([\d.]+)ms)?", linha)
        if not m or m.group(2) == "[LOCALHOST]":
            continue
        ttl, host, rtt = int(m.group(1)), m.group(2), m.group(3)
        salto = por_ttl.setdefault(ttl, {"ttl": ttl, "ip": None, "_rtts": [], "_sondas": 0})
        salto["_sondas"] += 1
        if host != "no" and re.match(r"^[\d.]+$", host):
            # tracepath repete o salto anterior quando ajusta o PMTU; vale o 1º endereço do TTL.
            salto["ip"] = salto["ip"] or host
            if rtt:
                salto["_rtts"].append(float(rtt))
    saltos = []
    for ttl in sorted(por_ttl):
        s = por_ttl[ttl]
        rtts, sondas = s.pop("_rtts"), s.pop("_sondas")
        s["rtt_ms"] = round(sum(rtts) / len(rtts), 1) if rtts else None
        s["perda_pct"] = round(100.0 * (sondas - len(rtts)) / sondas, 1) if sondas else 100.0
        saltos.append(s)
    return saltos


def parse_mtr_json(saida):
    try:
        hubs = json.loads(saida)["report"]["hubs"]
    except (ValueError, KeyError, TypeError):
        return []
    saltos = []
    for h in hubs:
        host = h.get("host")
        saltos.append({"ttl": int(h.get("count", len(saltos) + 1)),
                       "ip": host if host and host != "???" else None,
                       "rtt_ms": h.get("Avg") if host != "???" else None,
                       "perda_pct": h.get("Loss%", 100.0)})
    return saltos


def rota(alvo):
    if not ALVO_VALIDO.match(alvo or "") or alvo.startswith("-"):
        return {"ferramenta": None, "saltos": [], "erro": "alvo inválido"}
    if shutil.which("mtr"):
        codigo, saida = _rodar(["mtr", "-n", "-r", "-c", "5", "-j", alvo], 90)
        saltos = parse_mtr_json(saida)
        if saltos:
            return {"ferramenta": "mtr", "saltos": saltos}
    if shutil.which("traceroute"):
        _, saida = _rodar(["traceroute", "-n", "-q", "3", "-w", "2", "-m", "20", alvo], 120)
        return {"ferramenta": "traceroute", "saltos": parse_traceroute(saida)}
    if shutil.which("tracepath"):
        _, saida = _rodar(["tracepath", "-n", "-m", "20", alvo], 120)
        return {"ferramenta": "tracepath", "saltos": parse_tracepath(saida)}
    return {"ferramenta": None, "saltos": [], "erro": "nenhuma ferramenta de rota instalada (mtr, traceroute ou tracepath)"}


def candidatos_primeiro_salto(saltos, gateway):
    """Saltos depois do gateway local, na ordem: o primeiro é o equipamento do provedor."""
    vistos, saida = {gateway}, []
    for s in saltos:
        ip = s.get("ip")
        if ip and ip not in vistos:
            vistos.add(ip)
            saida.append(ip)
    return saida


def comparar_rotas(base, atual, limiar_perda=30.0, limiar_ms=50.0):
    """Primeiro salto que piorou em relação à última rota boa (ou None)."""
    if not base or not atual:
        return None
    antes = {s["ttl"]: s for s in base}
    for s in atual:
        b = antes.get(s["ttl"])
        if not b:
            continue
        perda_subiu = (s.get("perda_pct") or 0) - (b.get("perda_pct") or 0) >= limiar_perda
        rtt_a, rtt_b = s.get("rtt_ms"), b.get("rtt_ms")
        rtt_subiu = rtt_a is not None and rtt_b is not None and rtt_a - rtt_b >= max(limiar_ms, rtt_b)
        sumiu = b.get("ip") and not s.get("ip") and all(not x.get("ip") for x in atual if x["ttl"] >= s["ttl"])
        if perda_subiu or rtt_subiu or sumiu:
            return {"ttl": s["ttl"], "ip": s.get("ip") or b.get("ip"),
                    "antes": {"rtt_ms": rtt_b, "perda_pct": b.get("perda_pct")},
                    "depois": {"rtt_ms": rtt_a, "perda_pct": s.get("perda_pct")},
                    "motivo": "rota termina aqui" if sumiu else ("perda" if perda_subiu else "latência")}
    return None


# ---------------------------------------------------------------- utilidades
def gateway_padrao():
    _, saida = _rodar(["ip", "-4", "route", "show", "default"], 5)
    m = re.search(r"default via (\d{1,3}(?:\.\d{1,3}){3})", saida or "")
    return m.group(1) if m else None


def degradado(janela, cfg=CONFIG):
    for a in janela.get("alvos", []):
        if a["papel"] not in ("provedor", "externo"):
            continue
        if (a.get("perda_pct") or 0) >= max(cfg["gatilho_perda_pct"], 0.1) \
                or (a.get("latencia_ms") or 0) >= cfg["gatilho_latencia_ms"] \
                or (a.get("jitter_ms") or 0) >= cfg["gatilho_jitter_ms"]:
            return True
    return False


_provedor_cache = (0, None)


def provedor():
    """Nome do provedor pela consulta do IP público (cache de 6 h). NOC_PROVEDOR fixa à mão."""
    global _provedor_cache
    manual = os.environ.get("NOC_PROVEDOR", "").strip()
    if manual:
        return {"nome": manual[:80], "origem": "manual"}
    if os.environ.get("NOC_PROVEDOR_LOOKUP", "1") != "1":
        return None
    quando, valor = _provedor_cache
    if time.time() - quando < 21600:
        return valor
    try:
        with urllib.request.urlopen("https://ipwho.is/?fields=success,connection", timeout=5) as resp:
            dados = json.load(resp)
        con = dados.get("connection") or {}
        valor = {"nome": str(con.get("isp") or con.get("org") or "")[:80] or None, "asn": con.get("asn"), "origem": "ip"} \
            if dados.get("success") else None
    except (OSError, ValueError):
        valor = valor  # mantém o último conhecido
    _provedor_cache = (time.time(), valor)
    return valor


# ---------------------------------------------------------------- ciclo
class Monitor:
    def __init__(self, cfg=CONFIG, gateway_fn=gateway_padrao):
        self.cfg = cfg
        self.gateway_fn = gateway_fn
        self.primeiro_salto = None
        self.salto_descoberto_em = 0
        self.ultima_rota = 0
        self.rota_base = None  # última rota de uma janela sem degradação

    def _descobrir_salto(self, gateway, saltos):
        for ip in candidatos_primeiro_salto(saltos, gateway)[:3]:
            # Muitos roteadores de borda não respondem ping; só serve o que responde.
            if pingar(ip, 3, 1).get("recebidos"):
                return ip
        return None

    def _rota(self, motivo, gateway, base):
        r = rota(self.cfg["trace_alvo"])
        self.ultima_rota = time.time()
        item = {"tipo": "rota", "coletado_em": int(self.ultima_rota), "motivo": motivo, "alvo": self.cfg["trace_alvo"], **r}
        if r["saltos"] and (not self.primeiro_salto or time.time() - self.salto_descoberto_em > self.cfg["redescobrir_salto_s"]):
            self.primeiro_salto = self._descobrir_salto(gateway, r["saltos"])
            self.salto_descoberto_em = time.time()
        item["pior_salto"] = comparar_rotas(base, r["saltos"]) if motivo == "degradacao" else None
        return item

    def ciclo(self):
        cfg = self.cfg
        gateway = self.gateway_fn()
        itens = []
        if self.primeiro_salto is None and time.time() - self.ultima_rota > cfg["trace_min_intervalo_s"]:
            itens.append(self._rota("descoberta", gateway, None))
        inicio = time.time()

        alvos = [("gateway", gateway)] if gateway else []
        if self.primeiro_salto:
            alvos.append(("provedor", self.primeiro_salto))
        alvos += [("externo", ip) for ip in cfg["externos"]]
        quantidade = max(1, int(cfg["janela_s"] / cfg["ping_intervalo_s"]))

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(alvos) + 1) as ex:
            futuros = {ex.submit(pingar, alvo, quantidade, cfg["ping_intervalo_s"]): (papel, alvo) for papel, alvo in alvos}
            # DNS em rodadas espalhadas pela janela, enquanto os pings correm.
            rodadas_dns = []
            for i in range(3):
                time.sleep(max(0.0, inicio + i * cfg["janela_s"] / 3 - time.time()))
                rodadas_dns.append(testar_dns(cfg["dns_nomes"], cfg["dns_servidores"], cfg["dns_timeout_s"]))
            medidos = []
            for f in concurrent.futures.as_completed(futuros):
                papel, alvo = futuros[f]
                medidos.append({"papel": papel, "alvo": alvo, **f.result()})
        ordem = {"gateway": 0, "provedor": 1, "externo": 2}
        janela = {"tipo": "rede", "inicio": int(inicio), "coletado_em": int(time.time()), "janela_s": int(time.time() - inicio),
                  "gateway": gateway, "alvos": sorted(medidos, key=lambda a: (ordem[a["papel"]], a["alvo"])),
                  "dns": somar_dns(rodadas_dns)}
        if not gateway:
            janela["sem_gateway"] = True
        itens.append(janela)

        ruim = degradado(janela, cfg)
        desde = time.time() - self.ultima_rota
        if ruim and desde >= cfg["trace_min_intervalo_s"]:
            itens.append(self._rota("degradacao", gateway, self.rota_base))
        elif not ruim and desde >= cfg["trace_intervalo_s"]:
            item = self._rota("periodica", gateway, None)
            self.rota_base = item["saltos"] or self.rota_base
            itens.append(item)
        elif not ruim and self.rota_base is None and itens[0].get("tipo") == "rota":
            self.rota_base = itens[0]["saltos"] or None
        return itens
