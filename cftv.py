"""Classificação de equipamentos de CFTV (câmeras IP, NVR, DVR/XVR) a partir das evidências que
descoberta.py coleta: ONVIF, UPnP, portas típicas e marca pelo MAC.

Pontuação: ONVIF 3; porta proprietária (Hikvision 8000, Dahua 37777, XMeye/iCSee 34567) 2 cada;
RTSP 1; fabricante de CFTV pelo MAC 2 (fabricante misto, como Intelbras, 1 e só com outro indício);
modelo típico de CFTV 2. Abaixo de 2 pontos não é CFTV; 4 ou mais é confiança alta.
"""
import re
import urllib.parse

PORTAS = {
    554: 'RTSP', 8554: 'RTSP (alternativa)', 8000: 'Hikvision SDK', 37777: 'Dahua TCP',
    34567: 'XMeye/iCSee', 80: 'HTTP', 443: 'HTTPS',
}
PORTAS_PROPRIETARIAS = {8000: 'Hikvision', 37777: 'Dahua', 34567: 'XMeye/iCSee'}

# Marca curta (oui.marca) -> o fabricante é essencialmente de CFTV
MARCAS_CFTV = {
    'Hikvision', 'Dahua', 'EZVIZ', 'Uniview', 'Axis', 'Milesight', 'Tiandy', 'TVT', 'Hanwha Vision',
    'Hanwha (Samsung Techwin)', 'Reolink', 'Jovision', 'Vivotek', 'Bosch Security', 'Xiongmai (XMeye/iCSee)',
}
# Fabricam CFTV e também outras coisas (roteadores, ONUs): sozinha, a marca é só um indício
MARCAS_MISTAS = {'Intelbras', 'TP-Link'}

CAMERA, GRAVADOR, CFTV = 'Câmera IP', 'NVR/DVR', 'Equipamento de CFTV'
TIPOS = (CAMERA, GRAVADOR, CFTV)

_MODELOS = (
    (re.compile(r'^(I?DS-(7|8|9)\d|DS-6\d|HCVR|XVR|DH-XVR|DHI-XVR|NVR|DHI-NVR|DH-NVR|MHDX|NVD|INVD|IMHDX|DVR|HVR)', re.I), GRAVADOR),
    (re.compile(r'^(DS-2C|DS-2D|DS-2X|DS-2T|IDS-2C|IPC|DH-IPC|DHI-IPC|DH-SD|SD\d|VIP|VHD|VHL|IM\d|CS-|C\d[A-Z]?-|M\d{3,}|AXIS|P\d{4}|Q\d{4})', re.I), CAMERA),
)

PROBE_ONVIF = ('<?xml version="1.0" encoding="UTF-8"?>'
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
        tipo = tipo_pelo_modelo(hardware)
    return {'fabricante': fabricante, 'modelo': hardware, 'nome': nome, 'tipo': tipo,
            'xaddrs': re.findall(r'https?://[^\s<]+', ' '.join(re.findall(r'<[^>]*XAddrs[^>]*>([^<]*)<', texto)))[:3]}


def tipo_pelo_modelo(modelo):
    if not modelo:
        return None
    for candidato in (modelo.strip(), modelo.strip().split()[-1]):
        for padrao, tipo in _MODELOS:
            if padrao.match(candidato):
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
    return {
        'tipo': onvif.get('tipo') or tipo_modelo or CFTV,
        'confianca': 'alta' if pontos >= 4 else 'media' if pontos >= 3 else 'baixa',
        'pontos': pontos,
        'fabricante': onvif.get('fabricante') or upnp.get('fabricante') or marca,
        'modelo': modelo,
        'nome': onvif.get('nome') or upnp.get('nome'),
        'portas': [f'{p} ({PORTAS.get(p, "TCP")})' for p in sorted(set(portas) & set(PORTAS))],
        'onvif': bool(onvif),
        'evidencias': evidencias,
    }
