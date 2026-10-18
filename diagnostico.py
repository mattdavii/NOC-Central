"""Motor de diagnóstico de rede da NexoPulse Central.

Funções puras: recebem a janela de medições do Edge (netdiag) e a leitura da
Starlink e devolvem o status por camada e os achados. Quem grava, abre e
fecha diagnósticos e manda Telegram é o app.py.

As camadas são avaliadas de baixo para cima (LAN → Starlink → provedor → DNS):
a camada mais baixa com problema explica as de cima, que ficam "desconhecido"
em vez de virar alertas falsos.
"""
import os
from datetime import datetime, timedelta, timezone

FUSO = timezone(timedelta(hours=-3))


def _lim(nome, padrao):
    try:
        return float(os.environ.get(f"DIAG_{nome.upper()}", padrao))
    except ValueError:
        return float(padrao)


def limiares():
    return {
        "lan_perda_pct": _lim("lan_perda_pct", 5), "lan_latencia_ms": _lim("lan_latencia_ms", 100),
        "prov_perda_pct": _lim("prov_perda_pct", 5), "prov_latencia_ms": _lim("prov_latencia_ms", 100),
        "prov_jitter_ms": _lim("prov_jitter_ms", 40),
        "ext_perda_pct": _lim("ext_perda_pct", 5), "ext_latencia_ms": _lim("ext_latencia_ms", 200),
        "sl_obstrucao_pct": _lim("sl_obstrucao_pct", 2), "sl_perda_pop_pct": _lim("sl_perda_pop_pct", 5),
        "sl_latencia_pop_ms": _lim("sl_latencia_pop_ms", 120), "sl_desvio_graus": _lim("sl_desvio_graus", 8),
        "dns_falha_pct": _lim("dns_falha_pct", 50), "dns_latencia_ms": _lim("dns_latencia_ms", 800),
        "correlacao_min_sites": _lim("correlacao_min_sites", 3), "correlacao_janela_s": _lim("correlacao_janela_s", 600),
        "confirmar_janelas": _lim("confirmar_janelas", 2), "resolver_janelas": _lim("resolver_janelas", 2),
        "starlink_validade_s": _lim("starlink_validade_s", 300),
    }


ALERTAS_ALINHAMENTO = {"mast_not_near_vertical", "motors_stuck"}
ALERTAS_STARLINK = {
    "motors_stuck": ("Motores da antena travados", "verificar se algo impede o movimento da antena; reiniciar pelo app Starlink"),
    "mast_not_near_vertical": ("Mastro fora do prumo", "conferir a fixação e o nível do mastro"),
    "thermal_throttle": ("Antena reduzindo desempenho por temperatura", "verificar sombra/ventilação e se há neve ou sujeira sobre a antena"),
    "thermal_shutdown": ("Antena desligada por temperatura", "verificar sombra/ventilação; ela volta sozinha ao esfriar"),
    "slow_ethernet_speeds": ("Cabo Ethernet negociando abaixo de 1 Gb/s", "verificar cabo, conectores e a porta do roteador"),
    "unexpected_location": ("Antena fora do endereço cadastrado", "conferir o endereço de serviço na conta Starlink"),
    "install_pending": ("Atualização de firmware pendente", "deixar a antena ligada para concluir a atualização"),
    "is_heating": ("Antena aquecendo (derretendo neve)", "nenhuma ação, só acompanhar"),
    "power_supply_thermal_throttle": ("Fonte superaquecida", "verificar ventilação da fonte/roteador Starlink"),
    "is_power_save_idle": ("Antena em modo economia", "conferir o agendamento de economia no app Starlink"),
}
ESTADOS_OK = {None, "", "CONNECTED", "OBSTRUCTED"}
ORDEM_GRAVIDADE = {"Crítica": 3, "Alerta": 2, "Aviso": 1}
CAUSAS = {
    "lan": "Problema na rede local/equipamento do site",
    "sem_gateway": "Problema na rede local/equipamento do site",
    "starlink_obstruida": "Starlink obstruída",
    "starlink_desalinhada": "Starlink desalinhada",
    "starlink_degradada": "Starlink com degradação",
    "starlink_alerta": "Alerta da antena Starlink",
    "provedor": "Instabilidade no provedor",
    "dns": "Falha de DNS",
    "sem_comunicacao": "Site sem comunicação",
    "queda_provedor": "Possível queda do provedor",
}
# Achados que, em vários sites do mesmo provedor ao mesmo tempo, indicam o provedor.
CORRELACIONAVEIS = {"provedor", "starlink_degradada", "sem_comunicacao"}


def _ruim(alvo, perda, latencia, jitter=None):
    if not alvo:
        return False
    return ((alvo.get("perda_pct") or 0) >= perda
            or (alvo.get("latencia_ms") is not None and alvo["latencia_ms"] >= latencia)
            or (jitter is not None and alvo.get("jitter_ms") is not None and alvo["jitter_ms"] >= jitter))


def _metricas(prefixo, alvo):
    return {f"{prefixo}_{k}": alvo.get(k) for k in ("perda_pct", "latencia_ms", "jitter_ms") if alvo and alvo.get(k) is not None}


def _achado(chave, camada, gravidade, confianca, evidencias, acao, **extra):
    return {"chave": chave, "camada": camada, "causa": CAUSAS[chave], "gravidade": gravidade, "confianca": confianca,
            "evidencias": evidencias, "acao": acao, **extra}


def _starlink(sl, lim):
    achados = []
    if not sl or not sl.get("detectada"):
        return achados
    obs = sl.get("obstrucao") or {}
    ali = sl.get("alinhamento") or {}
    enl = sl.get("enlace") or {}
    alertas = set(sl.get("alertas") or [])
    quedas = sl.get("quedas") or []
    quedas_obs = [q for q in quedas if q.get("causa") == "OBSTRUCTED"]
    pct = obs.get("percentual")
    perda_pop, lat_pop = enl.get("perda_pct"), enl.get("latencia_ms")
    enlace_ruim = (perda_pop is not None and perda_pop >= lim["sl_perda_pop_pct"]) or \
                  (lat_pop is not None and lat_pop >= lim["sl_latencia_pop_ms"])
    ev_enlace = {k: v for k, v in (("pop_perda_pct", perda_pop), ("pop_latencia_ms", lat_pop)) if v is not None}

    if (pct is not None and pct >= lim["sl_obstrucao_pct"]) or obs.get("atual"):
        forte = pct is not None and pct >= lim["sl_obstrucao_pct"] and (quedas_obs or enlace_ruim)
        ev = {"obstrucao_pct": pct, "obstruida_agora": obs.get("atual"), "quedas_por_obstrucao_1h": len(quedas_obs), **ev_enlace}
        achados.append(_achado("starlink_obstruida", "starlink",
                               "Crítica" if (pct or 0) >= 3 * lim["sl_obstrucao_pct"] or len(quedas_obs) >= 3 else "Alerta",
                               "alta" if forte else "média", ev,
                               "verificar árvores ou estruturas no campo de visão da antena (app Starlink → Verificar obstruções)",
                               obstrucao_pct=pct))

    desvio = ali.get("desvio_graus")
    mastro = alertas & ALERTAS_ALINHAMENTO
    if (desvio is not None and desvio >= lim["sl_desvio_graus"]) or mastro:
        ev = {"desvio_graus": desvio, "tilt": ali.get("tilt"), "azimute": ali.get("azimute"),
              "azimute_desejado": ali.get("azimute_desejado"), "elevacao": ali.get("elevacao"),
              "elevacao_desejada": ali.get("elevacao_desejada"), "alertas": sorted(mastro)}
        achados.append(_achado("starlink_desalinhada", "starlink", "Alerta",
                               "alta" if mastro and desvio is not None and desvio >= lim["sl_desvio_graus"] else "média",
                               {k: v for k, v in ev.items() if v not in (None, [])},
                               "conferir a fixação e o prumo do mastro" if mastro else "conferir o apontamento e a fixação da antena",
                               desvio_graus=desvio))

    estado = sl.get("estado")
    obstruida = any(a["chave"] == "starlink_obstruida" for a in achados)
    if (enlace_ruim or estado not in ESTADOS_OK) and not obstruida:
        causas_quedas = sorted({q.get("causa") for q in quedas if q.get("causa")})
        ev = {**ev_enlace, **({"estado": estado} if estado not in ESTADOS_OK else {}),
              **({"quedas_1h": len(quedas), "causas_quedas": causas_quedas} if quedas else {})}
        achados.append(_achado("starlink_degradada", "starlink", "Alerta", "alta" if enlace_ruim and quedas else "média", ev,
                               "acompanhar as quedas no app Starlink; sem obstrução nem alerta na antena, abrir chamado na Starlink"))

    outros = sorted(alertas - ALERTAS_ALINHAMENTO)
    if outros:
        nomes = [ALERTAS_STARLINK.get(a, (a.replace("_", " "), ""))[0] for a in outros]
        acoes = [ALERTAS_STARLINK[a][1] for a in outros if a in ALERTAS_STARLINK]
        achados.append(_achado("starlink_alerta", "starlink", "Aviso", "alta", {"alertas": outros},
                               "; ".join(acoes) or "consultar o app Starlink", descricao=", ".join(nomes)))
    return achados


def _dns(janela, lim):
    medidas = {d.get("servidor"): d for d in janela.get("dns") or []}
    sistema = medidas.pop("sistema", None)

    def ruim(d):
        return d is not None and ((d.get("falha_pct") or 0) >= lim["dns_falha_pct"]
                                  or (d.get("tempo_ms") is not None and d["tempo_ms"] >= lim["dns_latencia_ms"]))
    if not ruim(sistema):
        return None
    publicos_ruins = bool(medidas) and all(ruim(d) for d in medidas.values())
    ev = {"dns_sistema_falha_pct": sistema.get("falha_pct"), "dns_sistema_ms": sistema.get("tempo_ms"),
          **{f"dns_{s}_falha_pct": d.get("falha_pct") for s, d in medidas.items()}}
    if publicos_ruins:
        return _achado("dns", "dns", "Alerta", "média", ev,
                       "resolução falha até nos servidores públicos: verificar firewall/bloqueio da porta 53 no roteador",
                       detalhe="todos os servidores DNS falhando")
    return _achado("dns", "dns", "Alerta", "alta" if medidas else "média", ev,
                   "trocar o DNS do roteador/DHCP para 1.1.1.1 e 8.8.8.8 ou reiniciar o roteador do site",
                   detalhe="DNS do site falhando; servidores públicos respondem")


def avaliar(janela, starlink=None, lim=None):
    """Janela do Edge (+ Starlink) → {"camadas": {...}, "achados": [...]}."""
    lim = lim or limiares()
    alvos = janela.get("alvos") or []
    gw = next((a for a in alvos if a.get("papel") == "gateway"), None)
    prov = next((a for a in alvos if a.get("papel") == "provedor"), None)
    ext = [a for a in alvos if a.get("papel") == "externo"]
    achados = []
    camadas = {"lan": "ok", "starlink": None, "provedor": "ok", "dns": "ok"}

    # 1. LAN
    lan_ruim = False
    if janela.get("sem_gateway") or not gw:
        lan_ruim = True
        achados.append(_achado("sem_gateway", "lan", "Crítica", "alta", {"gateway": None},
                               "verificar cabo/Wi-Fi do sensor e se o roteador do site está ligado", detalhe="sem rota padrão (gateway)"))
    elif _ruim(gw, lim["lan_perda_pct"], lim["lan_latencia_ms"]):
        lan_ruim = True
        ext_tambem = bool(ext) and all(_ruim(e, lim["ext_perda_pct"], lim["ext_latencia_ms"]) for e in ext)
        forte = (gw.get("perda_pct") or 0) >= 3 * lim["lan_perda_pct"] or ext_tambem
        achados.append(_achado("lan", "lan", "Crítica" if (gw.get("perda_pct") or 0) >= 50 else "Alerta",
                               "alta" if forte else "média", {**_metricas("gateway", gw), "gateway": gw.get("alvo")},
                               "verificar cabo, switch, Wi-Fi e roteador do site"))

    # 2. Starlink (leitura direta da antena, vale mesmo com a LAN ruim)
    sl_achados = _starlink(starlink, lim)
    achados += sl_achados
    if starlink and starlink.get("detectada"):
        camadas["starlink"] = "ok"

    # 3. Provedor
    sl_explica = any(a["chave"] in ("starlink_obstruida", "starlink_degradada") for a in sl_achados)
    ext_ruins = [e for e in ext if _ruim(e, lim["ext_perda_pct"], lim["ext_latencia_ms"])]
    ext_todos_ruins = bool(ext) and len(ext_ruins) == len(ext)
    if lan_ruim:
        camadas["provedor"] = camadas["dns"] = "desconhecido"
    else:
        prov_ruim = _ruim(prov, lim["prov_perda_pct"], lim["prov_latencia_ms"], lim["prov_jitter_ms"])
        ev = {**_metricas("gateway", gw), **_metricas("provedor", prov),
              **{f"externo_{e['alvo']}_perda_pct": e.get("perda_pct") for e in ext}}
        if sl_explica and (prov_ruim or ext_todos_ruins):
            camadas["provedor"] = "desconhecido"  # a Starlink já explica a degradação
        elif prov_ruim:
            achados.append(_achado("provedor", "provedor", "Crítica" if (prov.get("perda_pct") or 0) >= 50 else "Alerta",
                                   "alta" if ext_todos_ruins else "média", {**ev, "primeiro_salto": prov.get("alvo")},
                                   "abrir chamado no provedor com estas medições (gateway OK, perda/latência a partir do 1º salto)"))
        elif ext_todos_ruins:
            detalhe = "1º salto do provedor não identificado" if not prov else "degradação depois do 1º salto (rota do provedor)"
            achados.append(_achado("provedor", "provedor", "Alerta", "média" if prov else "baixa", ev,
                                   "abrir chamado no provedor; anexar a rota (traceroute) desta janela", detalhe=detalhe))

        # 4. DNS: só faz sentido com a internet respondendo por ICMP
        if ext and not ext_todos_ruins:
            dns = _dns(janela, lim)
            if dns:
                achados.append(dns)
        else:
            camadas["dns"] = "desconhecido"

    for a in achados:
        estado = "critico" if a["gravidade"] == "Crítica" else "alerta"
        atual = camadas.get(a["camada"])
        if atual in (None, "ok", "desconhecido") or (atual == "alerta" and estado == "critico"):
            camadas[a["camada"]] = estado
    return {"camadas": camadas, "achados": achados}


def starlink_valida(sl, referencia, lim=None):
    """Leitura da Starlink recente o bastante para julgar esta janela."""
    lim = lim or limiares()
    return bool(sl) and abs((sl.get("coletado_em") or 0) - referencia) <= lim["starlink_validade_s"]


def correlacionar(ativos, lim=None, provedores_com_grupo=()):
    """Diagnósticos ativos de vários sites → {provedor: [membros]} que indicam queda do provedor.

    `ativos`: dicts com sensor_mac, provedor, chave e inicio. Abre um grupo com
    N+ sites cujo início cabe na janela de correlação; um grupo já aberto
    continua enquanto houver 2+ sites afetados.
    """
    lim = lim or limiares()
    por_provedor = {}
    for a in ativos:
        nome = (a.get("provedor") or "").strip()
        if a.get("chave") in CORRELACIONAVEIS and nome:
            por_provedor.setdefault(nome.lower(), []).append(a)
    grupos = {}
    abertos = {p.lower() for p in provedores_com_grupo}
    for chave, membros in por_provedor.items():
        por_sensor = {}
        for m in sorted(membros, key=lambda m: m["inicio"]):
            por_sensor.setdefault(m["sensor_mac"], m)
        lista = sorted(por_sensor.values(), key=lambda m: m["inicio"])
        if chave in abertos and len(lista) >= 2:
            grupos[lista[0]["provedor"]] = lista
            continue
        for i in range(len(lista)):
            janela = [m for m in lista[i:] if m["inicio"] - lista[i]["inicio"] <= lim["correlacao_janela_s"]]
            if len(janela) >= lim["correlacao_min_sites"]:
                grupos[lista[0]["provedor"]] = lista
                break
    return grupos


# ---------------------------------------------------------------- textos
def hora(ts):
    dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(FUSO)
    hoje = datetime.now(FUSO).date()
    return dt.strftime("%Hh%M") if dt.date() == hoje else dt.strftime("%d/%m %Hh%M")


def _n(v, casas=1):
    return "?" if v is None else (f"{v:.{casas}f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))


def mensagem(d, site, provedor=None):
    """Texto para o técnico de campo, a partir de um diagnóstico (achado + inicio)."""
    ev = d.get("evidencias") or {}
    desde = hora(d["inicio"])
    chave = d["chave"]
    if chave == "starlink_obstruida":
        return f"Starlink do site {site} com {_n(ev.get('obstrucao_pct'))}% de obstrução desde {desde} — {d['acao']}."
    if chave == "starlink_desalinhada":
        extra = " (mastro fora do prumo)" if "mast_not_near_vertical" in (ev.get("alertas") or []) else ""
        return f"Starlink do site {site} desalinhada{extra}: {_n(ev.get('desvio_graus'))}° fora do apontamento ideal desde {desde} — {d['acao']}."
    if chave == "starlink_degradada":
        return (f"Starlink do site {site} com {_n(ev.get('pop_perda_pct'))}% de perda e {_n(ev.get('pop_latencia_ms'))} ms "
                f"até o PoP desde {desde} — {d['acao']}.")
    if chave == "starlink_alerta":
        return f"Starlink do site {site} com alerta: {d.get('descricao') or ', '.join(ev.get('alertas') or [])} — {d['acao']}."
    if chave in ("lan", "sem_gateway"):
        if chave == "sem_gateway":
            return f"Site {site} sem gateway desde {desde}: o sensor perdeu a rede local — {d['acao']}."
        return f"Rede local do site {site} com {_n(ev.get('gateway_perda_pct'))}% de perda até o gateway desde {desde} — {d['acao']}."
    if chave == "provedor":
        nome = f"Provedor {provedor}" if provedor else "Provedor"
        if "provedor_perda_pct" in ev or "provedor_latencia_ms" in ev:
            return (f"{nome} instável no site {site} desde {desde}: {_n(ev.get('provedor_perda_pct'))}% de perda e "
                    f"{_n(ev.get('provedor_latencia_ms'))} ms no 1º salto, com a rede local OK — {d['acao']}.")
        return f"{nome} instável no site {site} desde {desde}: internet com perda, rede local OK ({d.get('detalhe')}) — {d['acao']}."
    if chave == "dns":
        return f"Falha de DNS no site {site} desde {desde}: a internet responde, mas os nomes não resolvem ({d.get('detalhe')}) — {d['acao']}."
    if chave == "sem_comunicacao":
        return f"Site {site} sem comunicação com a Central desde {desde} — verificar energia, link e o sensor no local."
    if chave == "queda_provedor":
        return (f"Possível queda do provedor {provedor}: {ev.get('sites')} sites afetados desde {desde} "
                f"({', '.join(ev.get('nomes') or [])}) — acionar o provedor; não é falha local dos sites.")
    return f"{d.get('causa')} no site {site} desde {desde}."
