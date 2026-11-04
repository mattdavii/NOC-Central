"""Identificação de equipamentos de CFTV (câmeras IP, NVR, DVR/XVR) na rede local do sensor.

Junta quatro evidências, da mais forte para a mais fraca:
  - ONVIF WS-Discovery (multicast UDP 3702): o próprio equipamento responde com fabricante,
    modelo, nome e tipo (câmera ou gravador). É o protocolo padrão do CFTV;
  - UPnP/SSDP (multicast UDP 1900) + descrição XML do equipamento: fabricante e modelo;
  - portas típicas, testadas só com conexão TCP (sem enviar dados): RTSP 554/8554,
    Hikvision SDK 8000, Dahua 37777, XMeye/iCSee 34567;
  - marca pelo prefixo do MAC (Hikvision, Dahua, Intelbras, Uniview, Axis...).

Tudo é somente leitura e de baixo impacto: duas sondas multicast por ciclo e, por equipamento,
um punhado de conexões TCP a cada VALIDADE_S. NOC_CFTV=0 desliga o módulo e NOC_CFTV_PORTAS=0
desliga só o teste de portas (redes OT mais sensíveis).

O resultado fica em cache por MAC e roda numa thread própria, como nomes_rede: anotar() devolve
o que já se sabe e agenda o resto. Também acompanha os equipamentos de CFTV já identificados e
avisa quando um some da rede (eventos(), consumido pelo agente).
"""
import os
import queue
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid
import ipaddress

try:
    import oui
except ImportError:  # sensor antigo sem a base do IEEE: segue só com ONVIF, SSDP e portas
    oui = None

ATIVO = os.environ.get("NOC_CFTV", "1") != "0"
TESTAR_PORTAS = os.environ.get("NOC_CFTV_PORTAS", "1") != "0"

VALIDADE_S = 6 * 3600          # reavalia cada equipamento a cada 6 h
DESCOBERTA_S = 15 * 60         # ONVIF e SSDP a cada 15 min
TIMEOUT_PORTA_S = 0.6
TIMEOUT_MULTICAST_S = 2.5
QUEDA_APOS_S = 180             # CFTV conhecido sem responder por 3 min = fora do ar
MAX_XML = 64 * 1024

PORTAS = {
    554: 'RTSP', 8554: 'RTSP (alternativa)', 8000: 'Hikvision SDK', 37777: 'Dahua TCP',
    34567: 'XMeye/iCSee', 80: 'HTTP', 443: 'HTTPS',
}
PORTAS_CFTV = {554, 8554, 8000, 37777, 34567}
PORTAS_PROPRIETARIAS = {8000: 'Hikvision', 37777: 'Dahua', 34567: 'XMeye/iCSee'}

# Marca curta (oui.marca) -> o fabricante é essencialmente de CFTV
MARCAS_CFTV = {
    'Hikvision', 'Dahua', 'EZVIZ', 'Uniview', 'Axis', 'Milesight', 'Tiandy', 'TVT', 'Hanwha Vision',
    'Hanwha (Samsung Techwin)', 'Reolink', 'Jovision', 'Vivotek', 'Bosch Security', 'Xiongmai (XMeye/iCSee)',
}
# Fabricam CFTV e também outras coisas (roteadores, ONUs): sozinha, a marca é só um indício
MARCAS_MISTAS = {'Intelbras', 'TP-Link'}

CAMERA, GRAVADOR, CFTV = 'Câmera IP', 'NVR/DVR', 'Equipamento de CFTV'

# Prefixos de modelo conhecidos -> tipo
_MODELOS = (
    (re.compile(r'^(I?DS-(7|8|9)\d|DS-6\d|HCVR|XVR|DH-XVR|DHI-XVR|NVR|DHI-NVR|DH-NVR|MHDX|NVD|INVD|IMHDX|DVR|HVR)', re.I), GRAVADOR),
    (re.compile(r'^(DS-2C|DS-2D|DS-2X|DS-2T|IDS-2C|IPC|DH-IPC|DHI-IPC|DH-SD|SD\d|VIP|VHD|VHL|IM\d|CS-|C\d[A-Z]?-|M\d{3,}|AXIS|P\d{4}|Q\d{4})', re.I), CAMERA),
)

_cache = {}                    # mac -> (resultado, consultado_em)
_pendentes = set()
_fila = queue.Queue()
_trava = threading.Lock()
_worker = None
_descoberta = {'onvif': {}, 'ssdp': {}, 'em': 0.0}
_vistos = {}                   # mac -> {'ip', 'nome', 'ultimo_ok', 'fora'}
_eventos = []


# ------------------------------------------------------------------ descoberta multicast
def _socket_multicast(ip_local):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)  # nunca sai da rede local
    if ip_local:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip_local))
            sock.bind((ip_local, 0))
        except OSError:
            pass
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


_PROBE = ('<?xml version="1.0" encoding="UTF-8"?>'
          '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
          'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
          'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
          '<e:Header><w:MessageID>uuid:{id}</w:MessageID>'
          '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
          '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
          '</e:Header><e:Body><d:Probe/></e:Body></e:Envelope>')


def _escopos(texto):
    """'onvif://www.onvif.org/hardware/DS-2CD2143G2-I ...' -> {'hardware': ['DS-2CD2143G2-I'], ...}"""
    escopos = {}
    for bloco in re.findall(r'<[^>]*Scopes[^>]*>([^<]*)<', texto):
        for item in bloco.split():
            m = re.match(r'onvif://www\.onvif\.org/([^/]+)/(.+)', item, re.I)
            if m:
                escopos.setdefault(m.group(1).lower(), []).append(urllib.parse.unquote(m.group(2)).strip()[:120])
    return escopos


def interpretar_onvif(texto):
    """Fabricante, modelo, nome e tipo a partir de uma resposta ProbeMatch."""
    esc = _escopos(texto)
    tipos = ' '.join(esc.get('type', []) + re.findall(r'<[^>]*Types[^>]*>([^<]*)<', texto)).lower()
    hardware = (esc.get('hardware') or [None])[0]
    nome = (esc.get('name') or [None])[0]
    fabricante = (esc.get('mfr') or esc.get('manufacturer') or [None])[0]
    if not fabricante and nome:
        primeira = nome.split()[0]
        if primeira.isalpha() and len(primeira) > 2 and primeira.upper() != (hardware or '').upper():
            fabricante = primeira
    if any(t in tipos for t in ('recorder', 'nvr', 'storage', 'network_video_storage')):
        tipo = GRAVADOR
    elif any(t in tipos for t in ('video_encoder', 'networkvideotransmitter', 'ptz', 'video_analytics', 'camera')):
        tipo = CAMERA
    else:
        tipo = None
    if tipo is None and hardware:
        tipo = tipo_pelo_modelo(hardware)
    return {'fabricante': fabricante, 'modelo': hardware, 'nome': nome, 'tipo': tipo,
            'xaddrs': re.findall(r'https?://[^\s<]+', ' '.join(re.findall(r'<[^>]*XAddrs[^>]*>([^<]*)<', texto)))[:3]}


def descobrir_onvif(ip_local, limite_s=TIMEOUT_MULTICAST_S):
    achados = {}
    try:
        sock = _socket_multicast(ip_local)
    except OSError:
        return achados
    try:
        sock.sendto(_PROBE.format(id=uuid.uuid4()).encode(), ('239.255.255.250', 3702))
        for ip, dados in _receber(sock, limite_s):
            texto = dados.decode('utf-8', 'replace')
            if 'ProbeMatch' in texto:
                achados[ip] = interpretar_onvif(texto)
    except OSError:
        pass
    finally:
        sock.close()
    return achados


def _privado(ip):
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def interpretar_upnp(xml):
    def campo(nome):
        m = re.search(rf'<{nome}>\s*([^<]{{1,160}}?)\s*</{nome}>', xml, re.I)
        return m.group(1).strip() if m else None
    modelo = ' '.join(p for p in (campo('modelName'), campo('modelNumber')) if p) or None
    if modelo:
        partes = modelo.split()
        if len(partes) == 2 and partes[0].lower() == partes[1].lower():
            modelo = partes[0]
    return {'fabricante': campo('manufacturer'), 'modelo': modelo, 'nome': campo('friendlyName'),
            'tipo_upnp': (campo('deviceType') or '').split(':')[-2] if campo('deviceType') else None}


def descobrir_ssdp(ip_local, limite_s=TIMEOUT_MULTICAST_S):
    locais = {}
    try:
        sock = _socket_multicast(ip_local)
    except OSError:
        return {}
    try:
        busca = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
                 'MX: 2\r\nST: ssdp:all\r\n\r\n').encode()
        sock.sendto(busca, ('239.255.255.250', 1900))
        for ip, dados in _receber(sock, limite_s):
            m = re.search(rb'(?im)^location:\s*(\S+)', dados)
            if m and ip not in locais:
                locais[ip] = m.group(1).decode('ascii', 'ignore')
    except OSError:
        pass
    finally:
        sock.close()
    achados = {}
    for ip, url in locais.items():
        alvo = urllib.parse.urlparse(url)
        # Só a descrição servida pelo próprio equipamento, na rede local.
        if alvo.scheme != 'http' or alvo.hostname != ip or not _privado(ip):
            continue
        try:
            with urllib.request.urlopen(url, timeout=2) as resposta:
                achados[ip] = interpretar_upnp(resposta.read(MAX_XML).decode('utf-8', 'replace'))
        except Exception:
            continue
    return achados


# ------------------------------------------------------------------ portas e classificação
def portas_abertas(ip, portas=PORTAS):
    abertas = []
    for porta in portas:
        try:
            with socket.create_connection((ip, porta), timeout=TIMEOUT_PORTA_S):
                abertas.append(porta)  # só o aperto de mão TCP; nada é enviado
        except OSError:
            continue
    return abertas


def tipo_pelo_modelo(modelo):
    if not modelo:
        return None
    texto = modelo.strip().split()[-1] if len(modelo.split()) > 1 and modelo.split()[0].isalpha() else modelo.strip()
    for padrao, tipo in _MODELOS:
        if padrao.match(texto) or padrao.match(modelo.strip()):
            return tipo
    return None


def classificar(marca=None, portas=(), onvif=None, upnp=None):
    """Junta as evidências. Devolve None se nada indica CFTV."""
    evidencias, pontos = [], 0
    onvif, upnp = onvif or {}, upnp or {}
    if onvif:
        pontos += 3
        evidencias.append('Responde ONVIF (protocolo padrão de CFTV)')
    for porta in sorted(set(portas) & set(PORTAS_PROPRIETARIAS)):
        pontos += 2
        evidencias.append(f'Porta {porta} aberta ({PORTAS[porta]})')
    if set(portas) & {554, 8554}:
        pontos += 1
        evidencias.append('RTSP aberto (fluxo de vídeo)')
    if marca in MARCAS_CFTV:
        pontos += 2
        evidencias.append(f'Fabricante de CFTV pelo MAC ({marca})')
    elif marca in MARCAS_MISTAS and pontos:
        pontos += 1
        evidencias.append(f'Fabricante que também faz CFTV ({marca})')
    modelo = onvif.get('modelo') or upnp.get('modelo')
    tipo_modelo = tipo_pelo_modelo(modelo)
    if tipo_modelo and not onvif:
        pontos += 2
        evidencias.append(f'Modelo típico de CFTV ({modelo})')
    if pontos < 2:
        return None
    tipo = onvif.get('tipo') or tipo_modelo or CFTV
    confianca = 'alta' if pontos >= 4 else 'media' if pontos >= 3 else 'baixa'
    return {
        'tipo': tipo,
        'confianca': confianca,
        'fabricante': onvif.get('fabricante') or upnp.get('fabricante') or marca,
        'modelo': modelo,
        'nome': onvif.get('nome') or upnp.get('nome'),
        'portas': [f'{p} ({PORTAS[p]})' for p in sorted(portas)],
        'onvif': bool(onvif),
        'evidencias': evidencias,
    }


# ------------------------------------------------------------------ cache, worker e quedas
def _marca(mac):
    if not oui:
        return None
    registro, fonte = oui.fabricante(mac)
    return oui.marca(registro) if fonte == 'ieee' else None


def _avaliar(mac, ip, ip_local):
    agora = time.time()
    if agora - _descoberta['em'] > DESCOBERTA_S:
        _descoberta.update(onvif=descobrir_onvif(ip_local), ssdp=descobrir_ssdp(ip_local), em=agora)
    marca = _marca(mac)
    onvif, upnp = _descoberta['onvif'].get(ip), _descoberta['ssdp'].get(ip)
    # Portas: um teste por equipamento a cada VALIDADE_S; pula MAC privado (celular, notebook),
    # que não é CFTV e não precisa ser incomodado.
    privado = bool(oui and oui.mac_privado(mac))
    portas = portas_abertas(ip) if TESTAR_PORTAS and not privado else []
    return classificar(marca, portas, onvif, upnp), upnp


def _trabalhar():
    while True:
        mac, ip, ip_local = _fila.get()
        try:
            resultado = _avaliar(mac, ip, ip_local)
        except Exception:
            resultado = (None, None)
        with _trava:
            _cache[mac] = (resultado, time.time())
            _pendentes.discard(mac)
        _fila.task_done()


def _garantir_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_trabalhar, name='cftv', daemon=True)
        _worker.start()


def _acompanhar(dispositivos):
    """Registra quedas e retornos dos equipamentos de CFTV já identificados."""
    agora = time.time()
    presentes = {d.get('mac'): d for d in dispositivos}
    for mac, d in presentes.items():
        if d.get('cftv'):
            info = _vistos.setdefault(mac, {'fora': False, 'ultimo_ok': agora})
            info.update(ip=d.get('ip'), nome=d.get('modelo') or d['cftv'].get('tipo'))
    for mac, info in _vistos.items():
        d = presentes.get(mac)
        ok = bool(d and d.get('status') == 'online')
        if ok:
            if info['fora']:
                _eventos.append(('CFTV Restabelecido', f"{info['nome']} ({info['ip']}) voltou a responder.", 'OK'))
            info.update(fora=False, ultimo_ok=agora)
        elif not info['fora'] and agora - info['ultimo_ok'] > QUEDA_APOS_S:
            info['fora'] = True
            _eventos.append(('Queda de CFTV', f"{info['nome']} ({info['ip']}) não responde há mais de "
                             f"{QUEDA_APOS_S // 60} min.", 'Crítica'))


def eventos():
    """Eventos (tipo, detalhes, gravidade) desde a última chamada."""
    with _trava:
        lista = _eventos[:]
        _eventos.clear()
    return lista


def anotar(dispositivos, ip_local=None):
    """Acrescenta 'cftv', 'tipo_equipamento' e 'modelo' a cada dispositivo já avaliado."""
    if not ATIVO:
        return dispositivos
    agora = time.time()
    _garantir_worker()
    with _trava:
        for d in dispositivos:
            mac, ip = d.get('mac'), d.get('ip')
            if not mac or not ip:
                continue
            resultado, consultado = _cache.get(mac, (None, 0))
            cftv, upnp = resultado if resultado else (None, None)
            if cftv:
                d['cftv'] = cftv
                d['tipo_equipamento'] = cftv['tipo']
                if cftv.get('modelo'):
                    d['modelo'] = cftv['modelo']
            elif upnp and upnp.get('modelo'):
                d['modelo'] = upnp['modelo']
            if agora - consultado > VALIDADE_S and mac not in _pendentes:
                _pendentes.add(mac)
                _fila.put((mac, ip, ip_local))
        _acompanhar(dispositivos)
    return dispositivos
