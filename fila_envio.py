"""Fila persistente de medições do Edge para a Central (POST /api/v2/medicoes).

As medições ficam no SQLite local (tabela fila_envio do sensor_local.db) até a
Central confirmar o recebimento. Com o link fora, elas acumulam (até
NOC_FILA_MAX_ITENS ou NOC_FILA_MAX_DIAS, descartando as mais antigas) e são
enviadas em lotes quando a conexão volta. Cada item leva o próprio
`coletado_em`: a Central usa esse horário, não o da chegada.

Link secundário (ex.: 4G): `rotas_envio()` devolve a lista de destinos, tentados
em ordem. Hoje só a rota principal está ativa; com NOC_LINK_SECUNDARIO=1 e
NOC_CENTRAL_URL_SECUNDARIA definidos, a secundária entra como reserva. A saída
pela interface do 4G (NOC_INTERFACE_SECUNDARIA) ainda depende de rota de
política no sistema operacional; o envio não faz bind de interface.
"""
import json
import os
import sqlite3
import threading
import time
import urllib.request

DB = os.environ.get("NOC_FILA_DB", "sensor_local.db")
MAX_ITENS = max(100, int(os.environ.get("NOC_FILA_MAX_ITENS", "20000")))
MAX_DIAS = max(1, float(os.environ.get("NOC_FILA_MAX_DIAS", "7")))
LOTE = 100

_lock = threading.Lock()
_pronta = set()
_insercoes = 0


def _conectar():
    conn = sqlite3.connect(DB, timeout=10)
    if DB not in _pronta:
        conn.execute("CREATE TABLE IF NOT EXISTS fila_envio (id INTEGER PRIMARY KEY AUTOINCREMENT, coletado_em REAL NOT NULL, item TEXT NOT NULL)")
        conn.commit()
        _pronta.add(DB)
    return conn


def _aparar(conn):
    conn.execute("DELETE FROM fila_envio WHERE coletado_em < ?", (time.time() - MAX_DIAS * 86400,))
    conn.execute("DELETE FROM fila_envio WHERE id NOT IN (SELECT id FROM fila_envio ORDER BY id DESC LIMIT ?)", (MAX_ITENS,))


def enfileirar(item):
    global _insercoes
    item.setdefault("coletado_em", int(time.time()))
    with _lock:
        conn = _conectar()
        try:
            conn.execute("INSERT INTO fila_envio (coletado_em, item) VALUES (?, ?)",
                         (float(item["coletado_em"]), json.dumps(item, ensure_ascii=False)))
            _insercoes += 1
            if _insercoes % 100 == 0:
                _aparar(conn)
            conn.commit()
        finally:
            conn.close()


def pendentes():
    with _lock:
        conn = _conectar()
        try:
            return conn.execute("SELECT COUNT(*) FROM fila_envio").fetchone()[0]
        finally:
            conn.close()


def _proximo_lote():
    with _lock:
        conn = _conectar()
        try:
            linhas = conn.execute("SELECT id, item FROM fila_envio ORDER BY id LIMIT ?", (LOTE,)).fetchall()
        finally:
            conn.close()
    ids, itens = [], []
    for ident, bruto in linhas:
        ids.append(ident)
        try:
            itens.append(json.loads(bruto))
        except ValueError:
            pass  # registro corrompido: sai da fila junto com o lote
    return ids, itens


def _confirmar(ids):
    if not ids:
        return
    with _lock:
        conn = _conectar()
        try:
            conn.execute(f"DELETE FROM fila_envio WHERE id IN ({','.join('?' * len(ids))})", ids)
            conn.commit()
        finally:
            conn.close()


def rotas_envio(url_principal):
    rotas = [{"nome": "principal", "url": url_principal}]
    secundaria = os.environ.get("NOC_CENTRAL_URL_SECUNDARIA", "").strip()
    if os.environ.get("NOC_LINK_SECUNDARIO", "0") == "1" and secundaria:
        rotas.append({"nome": "secundaria", "url": secundaria, "interface": os.environ.get("NOC_INTERFACE_SECUNDARIA") or None})
    return rotas


def enviar_lote(rotas, mac, headers, extra=None, timeout=20):
    """Envia o lote mais antigo pela primeira rota que aceitar. Devolve quantos itens saíram da fila."""
    ids, itens = _proximo_lote()
    if not ids:
        return 0
    corpo = json.dumps({"mac_id": mac, "itens": itens, **(extra or {})}).encode("utf-8")
    ultimo_erro = None
    for rota in rotas:
        try:
            req = urllib.request.Request(rota["url"], data=corpo, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read()
            _confirmar(ids)
            return len(ids)
        except Exception as e:
            ultimo_erro = e
    raise ultimo_erro


def loop_envio(url, mac, headers_fn, extra_fn=None, intervalo=15, log=None):
    rotas = rotas_envio(url)
    falhando = False
    while True:
        try:
            while enviar_lote(rotas, mac, headers_fn(), extra_fn() if extra_fn else None) == LOTE:
                pass
            if falhando and log:
                log(None)
            falhando = False
        except Exception as e:
            if log and not falhando:  # registra a primeira falha, não uma a cada ciclo
                log(e)
            falhando = True
        time.sleep(intervalo if not falhando else min(60, intervalo * 2))
