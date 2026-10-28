"""Coletor da antena Starlink para o NexoPulse Edge.

Fala com a API gRPC local da antena (192.168.100.1:9200) pelo módulo
`starlink_grpc` do projeto starlink-grpc-tools (sparky8512), publicado no PyPI
como `starlink-grpc-core`. A API não é oficial e muda com o firmware, então:

- tudo aqui é lido de forma defensiva: campo ausente vira None, nunca exceção;
- a versão de firmware vai junto em toda leitura, para cruzar mudanças de
  comportamento com atualizações da antena;
- sem a biblioteca ou sem antena, `coletar()` devolve {"detectada": False} e o
  resto do Edge segue normalmente.

Modos (NOC_STARLINK): auto (padrão; tenta e marca "não detectada" se não houver
antena), off (não tenta) e mock (dados simulados, ver NOC_STARLINK_MOCK).
"""
import math
import os
import time

MODO = os.environ.get("NOC_STARLINK", "auto").strip().lower()
ALVO = os.environ.get("NOC_STARLINK_ALVO", "192.168.100.1:9200").strip()
INTERVALO = max(10, int(os.environ.get("NOC_STARLINK_INTERVALO", "30")))
# Sem antena, não vale tentar a cada ciclo: cada tentativa espera o timeout do gRPC.
INTERVALO_SEM_ANTENA = max(INTERVALO, int(os.environ.get("NOC_STARLINK_INTERVALO_SEM_ANTENA", "300")))
CENARIO_MOCK = os.environ.get("NOC_STARLINK_MOCK", "normal").strip().lower()
QUEDAS_JANELA_S = 3600  # quedas da última hora
MAX_QUEDAS = 20

try:  # dependência opcional (requirements-starlink.txt)
    import starlink_grpc
except Exception:  # pragma: no cover - depende do ambiente
    starlink_grpc = None

_contexto = None


def _num(valor, casas=None):
    try:
        if valor is None:
            return None
        valor = float(valor)
        if math.isnan(valor) or math.isinf(valor):
            return None
        return round(valor, casas) if casas is not None else valor
    except (TypeError, ValueError):
        return None


def desvio_angular(az, el, az_desejado, el_desejado):
    """Ângulo, em graus, entre a direção atual e a desejada (azimute/elevação)."""
    valores = [_num(v) for v in (az, el, az_desejado, el_desejado)]
    if any(v is None for v in valores):
        return None
    a1, e1, a2, e2 = (math.radians(v) for v in valores)
    cos_d = math.sin(e1) * math.sin(e2) + math.cos(e1) * math.cos(e2) * math.cos(a1 - a2)
    return round(math.degrees(math.acos(max(-1.0, min(1.0, cos_d)))), 2)


def _nome_enum(mensagem, campo):
    """Nome de um enum protobuf (ex.: OBSTRUCTED) sem depender das classes geradas."""
    try:
        numero = getattr(mensagem, campo)
        valor = mensagem.DESCRIPTOR.fields_by_name[campo].enum_type.values_by_number.get(numero)
        return valor.name if valor else str(numero)
    except Exception:
        return None


def _alinhamento(status_bruto, status):
    stats = getattr(status_bruto, "alignment_stats", None)
    az = _num(getattr(stats, "boresight_azimuth_deg", None)) if stats is not None else None
    el = _num(getattr(stats, "boresight_elevation_deg", None)) if stats is not None else None
    if az is None:
        az = _num(status.get("direction_azimuth"))
    if el is None:
        el = _num(status.get("direction_elevation"))
    az_d = _num(getattr(stats, "desired_boresight_azimuth_deg", None)) if stats is not None else None
    el_d = _num(getattr(stats, "desired_boresight_elevation_deg", None)) if stats is not None else None
    return {
        "azimute": _num(az, 2), "elevacao": _num(el, 2),
        "azimute_desejado": _num(az_d, 2), "elevacao_desejada": _num(el_d, 2),
        "tilt": _num(getattr(stats, "tilt_angle_deg", None), 2) if stats is not None else None,
        "desvio_graus": desvio_angular(az, el, az_d, el_d),
    }


def _quedas(historico, agora):
    saida = []
    for o in list(getattr(historico, "outages", None) or [])[-200:]:
        inicio_ns = getattr(o, "start_timestamp_ns", None)
        duracao_ns = getattr(o, "duration_ns", None)
        if not inicio_ns:
            continue
        # O relógio da antena é GPS (ns desde 06/01/1980), não Unix.
        inicio = inicio_ns / 1e9 + 315964800 - 18
        if agora - inicio > QUEDAS_JANELA_S or inicio > agora + 60:
            continue
        saida.append({"inicio": int(inicio), "duracao_s": _num((duracao_ns or 0) / 1e9, 1),
                      "causa": _nome_enum(o, "cause") or "DESCONHECIDA"})
    return saida[-MAX_QUEDAS:]


def _coletar_real():
    global _contexto
    if starlink_grpc is None:
        return {"detectada": False, "motivo": "biblioteca starlink-grpc-core não instalada"}
    if _contexto is None:
        _contexto = starlink_grpc.ChannelContext(target=ALVO)
    try:
        status, _obstrucao, alertas = starlink_grpc.status_data(context=_contexto)
    except Exception as e:  # GrpcError, timeout, porta fechada...
        _contexto.close()
        return {"detectada": False, "motivo": f"antena não respondeu em {ALVO} ({type(e).__name__})"}

    agora = time.time()
    try:
        status_bruto = starlink_grpc.get_status(context=_contexto)
    except Exception:
        status_bruto = None
    try:
        historico = starlink_grpc.get_history(context=_contexto)
    except Exception:
        historico = None

    fracao = _num(status.get("fraction_obstructed"))
    perda_pop = _num(status.get("pop_ping_drop_rate"))
    return {
        "detectada": True,
        "modo": "real",
        "firmware": status.get("software_version"),
        "hardware": status.get("hardware_version"),
        "estado": status.get("state"),
        "uptime_s": _num(status.get("uptime"), 0),
        "obstrucao": {
            "fracao": fracao,
            "percentual": _num(fracao * 100, 2) if fracao is not None else None,
            "atual": bool(status.get("currently_obstructed")) if status.get("currently_obstructed") is not None else None,
            "duracao_media_s": _num(status.get("obstruction_duration"), 1),
            "intervalo_medio_s": _num(status.get("obstruction_interval"), 1),
        },
        "alinhamento": _alinhamento(status_bruto, status),
        # Lista genérica: alertas novos de firmware aparecem sem mudar o código.
        "alertas": sorted(nome[len("alert_"):] for nome, ativo in (alertas or {}).items() if ativo),
        "enlace": {
            "latencia_ms": _num(status.get("pop_ping_latency_ms"), 1),
            "perda_pct": _num(perda_pop * 100, 2) if perda_pop is not None else None,
        },
        "quedas": _quedas(historico, agora) if historico is not None else [],
    }


# ---------------------------------------------------------------- mock
CENARIOS_MOCK = {
    "normal": {"fracao": 0.004, "atual": False, "desvio": (0.5, 0.3), "alertas": [], "lat": 38.0, "perda": 0.002, "quedas": []},
    "obstruida": {"fracao": 0.06, "atual": True, "desvio": (0.5, 0.3), "alertas": [], "lat": 55.0, "perda": 0.09,
                  "quedas": [("OBSTRUCTED", 600, 12.5), ("OBSTRUCTED", 1500, 8.0)]},
    "desalinhada": {"fracao": 0.01, "atual": False, "desvio": (14.0, 9.0), "alertas": ["mast_not_near_vertical"],
                    "lat": 42.0, "perda": 0.01, "quedas": []},
    "degradada": {"fracao": 0.003, "atual": False, "desvio": (0.5, 0.3), "alertas": ["thermal_throttle"], "lat": 140.0,
                  "perda": 0.12, "quedas": [("NO_SCHEDULE", 900, 4.0), ("NO_DOWNLINK", 300, 6.0)]},
}


def _coletar_mock(cenario=None):
    c = CENARIOS_MOCK.get(cenario or CENARIO_MOCK, CENARIOS_MOCK["normal"])
    agora = time.time()
    az_d, el_d = 12.0, 65.0
    az, el = az_d + c["desvio"][0], el_d - c["desvio"][1]
    return {
        "detectada": True, "modo": "mock", "cenario": cenario or CENARIO_MOCK,
        "firmware": "mock-2026.10.0", "hardware": "rev4_mock", "estado": "OBSTRUCTED" if c["atual"] else "CONNECTED",
        "uptime_s": 86400,
        "obstrucao": {"fracao": c["fracao"], "percentual": round(c["fracao"] * 100, 2), "atual": c["atual"],
                      "duracao_media_s": 9.0 if c["quedas"] else None, "intervalo_medio_s": 700.0 if c["quedas"] else None},
        "alinhamento": {"azimute": az, "elevacao": el, "azimute_desejado": az_d, "elevacao_desejada": el_d,
                        "tilt": round(c["desvio"][1], 2), "desvio_graus": desvio_angular(az, el, az_d, el_d)},
        "alertas": list(c["alertas"]),
        "enlace": {"latencia_ms": c["lat"], "perda_pct": round(c["perda"] * 100, 2)},
        "quedas": [{"inicio": int(agora - atras), "duracao_s": dur, "causa": causa} for causa, atras, dur in c["quedas"]],
    }


def coletar():
    if MODO == "off":
        return None
    try:
        dados = _coletar_mock() if MODO == "mock" else _coletar_real()
    except Exception as e:  # nunca derruba o Edge
        dados = {"detectada": False, "motivo": f"falha inesperada no coletor ({type(e).__name__})"}
    dados["coletado_em"] = int(time.time())
    return dados


def proximo_intervalo(ultima_leitura):
    return INTERVALO if ultima_leitura and ultima_leitura.get("detectada") else INTERVALO_SEM_ANTENA


if __name__ == "__main__":
    import json
    print(json.dumps(coletar(), indent=2, ensure_ascii=False))
