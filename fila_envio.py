"""Fila de medições do Edge para a Central (POST /api/v2/medicoes).

Cada item leva o próprio `coletado_em`; a Central usa esse horário, não o da
chegada, então medições atrasadas continuam na posição certa da linha do tempo.
"""
import collections
import json
import threading
import time
import urllib.request

MAX_ITENS = 2000
LOTE = 100

_fila = collections.deque(maxlen=MAX_ITENS)
_lock = threading.Lock()


def enfileirar(item):
    item.setdefault("coletado_em", int(time.time()))
    with _lock:
        _fila.append(item)


def _proximo_lote():
    with _lock:
        return list(_fila)[:LOTE]


def _confirmar(quantidade):
    with _lock:
        for _ in range(min(quantidade, len(_fila))):
            _fila.popleft()


def enviar_lote(url, mac, headers, extra=None, timeout=20):
    lote = _proximo_lote()
    if not lote:
        return 0
    corpo = {"mac_id": mac, "itens": lote, **(extra or {})}
    req = urllib.request.Request(url, data=json.dumps(corpo).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()
    _confirmar(len(lote))
    return len(lote)


def loop_envio(url, mac, headers_fn, extra_fn=None, intervalo=15, log=None):
    falhando = False
    while True:
        try:
            while enviar_lote(url, mac, headers_fn(), extra_fn() if extra_fn else None) == LOTE:
                pass
            falhando = False
        except Exception as e:
            if log and not falhando:  # registra a primeira falha, não uma a cada ciclo
                log(e)
            falhando = True
        time.sleep(intervalo)
