"""Descoberta de equipamentos na rede local do sensor: o que cada um anuncia e quais portas responde.

Uma só passada alimenta os classificadores (cftv.py e comunicacao.py), para que cada equipamento
seja sondado uma vez só:
  - ONVIF WS-Discovery (multicast UDP 3702): câmeras e gravadores dizem fabricante, modelo e tipo;
  - UPnP/SSDP (multicast UDP 1900) + descrição XML: roteadores, ONUs, APs, NVRs;
  - MikroTik MNDP (broadcast UDP 5678): placa, versão do RouterOS e identidade;
  - Ubiquiti Discovery (broadcast UDP 10001): modelo, firmware e nome;
  - portas TCP típicas, testadas só com o aperto de mão TCP (nada é enviado).

Nunca toca em portas industriais (Modbus 502, S7 102, DNP3 20000, IEC 104 2404...): em rede de
usina elas são do SCADA e ficam de fora de propósito. NOC_CFTV=0 desliga a descoberta inteira
(nome mantido por compatibilidade); NOC_CFTV_PORTAS=0 desliga só o teste de portas.

Roda numa thread própria com cache por MAC; anotar() devolve o que já se sabe e agenda o resto.
Também acompanha os equipamentos de CFTV e de comunicação já identificados e gera eventos quando
um some da rede (eventos(), consumido pelo agente).
"""
import concurrent.futures
import ipaddress
import os
import queue
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid

import cftv
import comunicacao

try:
    import oui
except ImportError:  # sensor antigo sem a base do IEEE: segue com protocolos e portas
    oui = None

ATIVO = os.environ.get("NOC_CFTV", "1") != "0"
TESTAR_PORTAS = os.environ.get("NOC_CFTV_PORTAS", "1") != "0"

VALIDADE_S = 6 * 3600          # reavalia cada equipamento a cada 6 h
DESCOBERTA_S = 15 * 60         # sondas multicast/broadcast a cada 15 min
TIMEOUT_PORTA_S = 0.6
TIMEOUT_SONDA_S = 2.5
QUEDA_APOS_S = 180             # equipamento conhecido sem responder por 3 min = fora do ar
MAX_XML = 64 * 1024

PORTAS = {**cftv.PORTAS, **comunicacao.PORTAS}
assert not set(PORTAS) & {102, 502, 2404, 20000, 44818, 47808}, 'portas industriais não podem ser sondadas'

_cache = {}                    # mac -> (evidencias, consultado_em)
_pendentes = set()
_fila = queue.Queue()
_trava = threading.Lock()
_worker = None
_sondas = {'onvif': {}, 'ssdp': {}, 'mndp': {}, 'ubnt': {}, 'em': 0.0}
_vistos = {}                   # mac -> {'ip', 'nome', 'categoria', 'ultimo_ok', 'fora'}
_eventos = []


# ------------------------------------------------------------------ sondas
def _socket_udp(ip_local, porta=0, broadcast=False):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)  # nunca sai da rede local
    if broadcast:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    if ip_local and not broadcast:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip_local))
        except OSError:
            pass
    sock.bind(('' if broadcast or not ip_local else ip_local, porta))
    sock.settimeout(0.5)
    return sock


def _receber(sock, limite_s):
    fim = time.time() + limite_s
    while time.time() < fim:
        try:
            dados, (ip, _) = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        yield ip, dados[:MAX_XML]


def _privado(ip):
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _sondar(ip_local, destino, mensagem, porta_local=0, broadcast=False, limite_s=TIMEOUT_SONDA_S):
    try:
        sock = _socket_udp(ip_local, porta_local, broadcast)
    except OSError:
        return []
    try:
        sock.sendto(mensagem, destino)
        return [(ip, dados) for ip, dados in _receber(sock, limite_s) if ip != ip_local and _privado(ip)]
    except OSError:
        return []
    finally:
        sock.close()


def descobrir_onvif(ip_local):
    respostas = _sondar(ip_local, ('239.255.255.250', 3702), cftv.PROBE_ONVIF.format(id=uuid.uuid4()).encode())
    return {ip: cftv.interpretar_onvif(d.decode('utf-8', 'replace')) for ip, d in respostas if b'ProbeMatch' in d}


def interpretar_upnp(xml):
    def campo(nome):
        m = re.search(rf'<{nome}>\s*([^<]{{1,160}}?)\s*</{nome}>', xml, re.I)
        return m.group(1).strip() if m else None
    modelo = ' '.join(p for p in (campo('modelName'), campo('modelNumber')) if p) or None
    if modelo:
        partes = modelo.split()
        if len(partes) == 2 and partes[0].lower() == partes[1].lower():
            modelo = partes[0]
    tipo = campo('deviceType')
    return {'fabricante': campo('manufacturer'), 'modelo': modelo, 'nome': campo('friendlyName'),
            'tipo_upnp': tipo.split(':')[-2] if tipo and tipo.count(':') >= 2 else None}


def descobrir_ssdp(ip_local):
    busca = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
             'MX: 2\r\nST: ssdp:all\r\n\r\n').encode()
    locais = {}
    for ip, dados in _sondar(ip_local, ('239.255.255.250', 1900), busca):
        m = re.search(rb'(?im)^location:\s*(\S+)', dados)
        if m and ip not in locais:
            locais[ip] = m.group(1).decode('ascii', 'ignore')
    achados = {}
    for ip, url in locais.items():
        alvo = urllib.parse.urlparse(url)
        if alvo.scheme != 'http' or alvo.hostname != ip:   # só a descrição servida pelo próprio equipamento
            continue
        try:
            with urllib.request.urlopen(url, timeout=2) as resposta:
                achados[ip] = interpretar_upnp(resposta.read(MAX_XML).decode('utf-8', 'replace'))
        except Exception:
            continue
    return achados


def descobrir_mndp(ip_local):
    # Pedido de descoberta do MikroTik: 4 bytes zerados para a porta 5678; as respostas vêm em broadcast.
    respostas = _sondar(ip_local, ('255.255.255.255', 5678), b'\x00\x00\x00\x00', porta_local=5678, broadcast=True)
    return {ip: info for ip, d in respostas if (info := comunicacao.interpretar_mndp(d))}


def descobrir_ubnt(ip_local):
    respostas = _sondar(ip_local, ('255.255.255.255', 10001), b'\x01\x00\x00\x00', broadcast=True)
    return {ip: info for ip, d in respostas if (info := comunicacao.interpretar_ubnt(d))}


def portas_abertas(ip, portas=PORTAS):
    def testar(porta):
        try:
            with socket.create_connection((ip, porta), timeout=TIMEOUT_PORTA_S):
                return porta
        except OSError:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        return sorted(p for p in executor.map(testar, portas) if p)


# ------------------------------------------------------------------ classificação
def _marca(mac):
    if not oui:
        return None
    registro, fonte = oui.fabricante(mac)
    return oui.marca(registro) if fonte == 'ieee' else None


def classificar(ev, papel=None):
    """(cftv, comunicacao) a partir das evidências; no máximo um dos dois.

    O modelo anunciado decide primeiro (câmera UniFi responde à descoberta da Ubiquiti, switch
    Hikvision tem marca de CFTV). Sem modelo decisivo, vence quem tem mais pontos; empate fica com
    comunicação se o equipamento é o gateway, senão com CFTV (um NVR com UPnP não é roteador)."""
    ubnt, upnp = ev.get('ubnt'), ev.get('upnp')
    anuncio = ubnt or ev.get('mndp') or upnp or {}
    if ubnt and cftv.tipo_pelo_modelo(ubnt.get('modelo')) in (cftv.CAMERA, cftv.GRAVADOR):
        upnp = {'fabricante': 'Ubiquiti', 'modelo': ubnt.get('modelo'), 'nome': ubnt.get('nome')}   # UniFi Protect
    c = cftv.classificar(ev.get('marca'), ev.get('portas', []), ev.get('onvif'), upnp)
    r = comunicacao.classificar(ev.get('marca'), ev.get('portas', []), ev.get('upnp'), ev.get('mndp'), ubnt, papel)
    modelo = (ev.get('onvif') or {}).get('modelo') or anuncio.get('modelo')
    if c and r:
        tipo_cftv = cftv.tipo_pelo_modelo(modelo)
        if tipo_cftv in (cftv.CAMERA, cftv.GRAVADOR, cftv.ACESSO):
            r = None
        elif comunicacao.tipo_pelo_modelo(modelo):
            c = None
        elif r['pontos'] > c['pontos'] or (r['pontos'] == c['pontos'] and papel == 'gateway'):
            c = None
        else:
            r = None
    return c, r


def _avaliar(mac, ip, ip_local):
    agora = time.time()
    if agora - _sondas['em'] > DESCOBERTA_S:
        _sondas.update(onvif=descobrir_onvif(ip_local), ssdp=descobrir_ssdp(ip_local),
                       mndp=descobrir_mndp(ip_local), ubnt=descobrir_ubnt(ip_local), em=agora)
    privado = bool(oui and oui.mac_privado(mac))
    return {
        'marca': _marca(mac),
        # celular e notebook (MAC privado) não são infraestrutura e não precisam ser incomodados
        'portas': portas_abertas(ip) if TESTAR_PORTAS and not privado else [],
        'onvif': _sondas['onvif'].get(ip), 'upnp': _sondas['ssdp'].get(ip),
        'mndp': _sondas['mndp'].get(ip), 'ubnt': _sondas['ubnt'].get(ip),
    }


def _trabalhar():
    while True:
        mac, ip, ip_local = _fila.get()
        try:
            evidencias = _avaliar(mac, ip, ip_local)
        except Exception:
            evidencias = {}
        with _trava:
            _cache[mac] = (evidencias, time.time())
            _pendentes.discard(mac)
        _fila.task_done()


def _garantir_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_trabalhar, name='descoberta', daemon=True)
        _worker.start()


CATEGORIAS = {'cftv': ('Queda de CFTV', 'CFTV Restabelecido'),
              'comunicacao': ('Queda de Equipamento de Comunicação', 'Equipamento de Comunicação Restabelecido')}


def _acompanhar(dispositivos, gateway_ip):
    """Quedas e retornos dos equipamentos identificados. O gateway fica de fora: a queda dele já é
    o alerta de rede local do agente e o diagnóstico por camadas da Central."""
    agora = time.time()
    presentes = {d.get('mac') or f"ip:{d.get('ip')}": d for d in dispositivos}
    for mac, d in presentes.items():
        categoria = 'cftv' if d.get('cftv') else 'comunicacao' if d.get('comunicacao') else None
        if categoria and d.get('ip') != gateway_ip:
            info = _vistos.setdefault(mac, {'fora': False, 'ultimo_ok': agora})
            info.update(ip=d.get('ip'), categoria=categoria,
                        nome=d.get('modelo') or d[categoria].get('nome') or d[categoria].get('tipo'))
    for mac, info in _vistos.items():
        d = presentes.get(mac)
        queda, volta = CATEGORIAS[info['categoria']]
        # 'sem_icmp' = está na tabela ARP, só não responde a ping: presente, não é queda.
        if d and d.get('status') in ('online', 'sem_icmp'):
            if info['fora']:
                _eventos.append((volta, f"{info['nome']} ({info['ip']}) voltou a responder.", 'OK'))
            info.update(fora=False, ultimo_ok=agora)
        elif not info['fora'] and agora - info['ultimo_ok'] > QUEDA_APOS_S:
            info['fora'] = True
            _eventos.append((queda, f"{info['nome']} ({info['ip']}) não responde há mais de "
                             f"{QUEDA_APOS_S // 60} min.", 'Crítica'))


def eventos():
    """Eventos (tipo, detalhes, gravidade) desde a última chamada."""
    with _trava:
        lista = _eventos[:]
        _eventos.clear()
    return lista


def anotar(dispositivos, ip_local=None, gateway_ip=None):
    """Acrescenta 'cftv' ou 'comunicacao', 'tipo_equipamento', 'modelo' e 'firmware' a cada
    dispositivo já avaliado, e agenda os que ainda não foram (ou venceram a validade)."""
    if not ATIVO:
        return dispositivos
    agora = time.time()
    _garantir_worker()
    with _trava:
        for d in dispositivos:
            # Equipamento de outra VLAN (roteado) não tem MAC visível: a chave é o IP.
            ip = d.get('ip')
            mac = d.get('mac') or (f'ip:{ip}' if ip else None)
            if not mac or not ip:
                continue
            evidencias, consultado = _cache.get(mac, (None, 0))
            if evidencias:
                c, r = classificar(evidencias, 'gateway' if ip == gateway_ip else None)
                achado = c or r
                if c:
                    d['cftv'] = c
                if r:
                    d['comunicacao'] = r
                if achado:
                    d['tipo_equipamento'] = achado['tipo']
                    if achado.get('modelo'):
                        d['modelo'] = achado['modelo']
                    if achado.get('firmware'):
                        d['firmware'] = achado['firmware']
                elif (evidencias.get('upnp') or {}).get('modelo'):
                    d['modelo'] = evidencias['upnp']['modelo']
            if agora - consultado > VALIDADE_S and mac not in _pendentes:
                _pendentes.add(mac)
                _fila.put((mac, ip, ip_local))
        _acompanhar(dispositivos, gateway_ip)
    return dispositivos
