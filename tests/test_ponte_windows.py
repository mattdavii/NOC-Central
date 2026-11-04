"""Ponte do painel local no Windows: o ajudante que roda no Ubuntu liga a entrada/saída padrão ao painel."""
import http.server
import re
import subprocess
import sys
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _ajudante(tmp_path):
    texto = (RAIZ / 'windows' / 'ponte-painel-local.ps1').read_text(encoding='utf-8-sig')
    codigo = re.search(r"#!/usr/bin/env python3\r?\n.*?# NEXOPULSE-PONTE-FIM", texto, re.S).group(0)
    arquivo = tmp_path / 'nexopulse-ponte'
    arquivo.write_text(codigo.replace('\r\n', '\n'), encoding='utf-8')
    return arquivo


class _Painel(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        corpo = ('painel ' + self.path).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *args):
        pass


def test_ajudante_leva_o_pedido_ao_painel_e_devolve_a_resposta(tmp_path):
    servidor = http.server.ThreadingHTTPServer(('0.0.0.0', 0), _Painel)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        pedido = b'GET /login HTTP/1.0\r\nHost: 127.0.0.1:10080\r\n\r\n'
        saida = subprocess.run([sys.executable, str(_ajudante(tmp_path)), str(servidor.server_address[1])],
                               input=pedido, capture_output=True, timeout=20).stdout
        assert saida.startswith(b'HTTP/1.0 200') and saida.endswith(b'painel /login')
    finally:
        servidor.shutdown()


def test_ajudante_sai_com_erro_quando_o_painel_nao_responde(tmp_path):
    r = subprocess.run([sys.executable, str(_ajudante(tmp_path)), '9'], input=b'', capture_output=True, timeout=30)
    assert r.returncode == 1


def test_central_serve_a_ponte(client):
    r = client.get('/ponte-painel-windows.ps1')
    assert r.status_code == 200 and 'Install-Ponte' in r.get_data(as_text=True)
