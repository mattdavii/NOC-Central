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
        pedido = b'GET /login HTTP/1.0\r\nHost: 127.0.0.1:10010\r\n\r\n'
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


# Portas que o Chrome/Edge recusam abrir (ERR_UNSAFE_PORT); a ponte já caiu nessa com a 10080.
PORTAS_BLOQUEADAS_NOS_NAVEGADORES = {1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79, 87, 95, 101, 102,
                                     103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135, 137, 139, 143, 161, 179, 389, 427,
                                     465, 512, 513, 514, 515, 526, 530, 531, 532, 540, 548, 554, 556, 563, 587, 601, 636, 989,
                                     990, 993, 995, 1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566, 6665,
                                     6666, 6667, 6668, 6669, 6679, 6697, 10080}


def test_porta_da_ponte_abre_no_navegador():
    texto = (RAIZ / 'windows' / 'ponte-painel-local.ps1').read_text(encoding='utf-8-sig')
    porta = int(re.search(r'\[int\]\$PortaLocal = (\d+)', texto).group(1))
    assert porta not in PORTAS_BLOQUEADAS_NOS_NAVEGADORES
