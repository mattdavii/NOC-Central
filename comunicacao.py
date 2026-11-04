"""Classificação de equipamentos de comunicação (roteador/firewall, switch, access point/rádio,
ONU/ONT, Starlink, roteador 4G/LTE, gateway industrial) a partir das evidências de descoberta.py.

Fontes, da mais forte para a mais fraca:
  - MikroTik MNDP e Ubiquiti Discovery: o equipamento diz modelo, firmware e nome;
  - UPnP: InternetGatewayDevice (roteador) e WFADevice (Wi-Fi);
  - portas de gerência: Winbox 8291 e API 8728/8729 (MikroTik), TR-069 7547 (CPE de provedor);
  - fabricante pelo MAC e papel de gateway da rede.

Pontuação: MNDP/Ubiquiti 4; Winbox/API 3; UPnP de rede 2; TR-069 2; fabricante de rede 2
(misto, como Huawei ou Intelbras, 1 e só com outro indício); gateway 2; modelo típico 2;
SSH/Telnet 1 só com outro indício. Abaixo de 2 não classifica; 4 ou mais é confiança alta.
"""
import re
import struct

PORTAS = {22: 'SSH', 23: 'Telnet', 8291: 'Winbox (MikroTik)', 8728: 'API MikroTik', 8729: 'API MikroTik (TLS)',
          7547: 'TR-069 (gerência do provedor)', 8443: 'HTTPS de gerência', 8080: 'HTTP alternativo'}

ROTEADOR, SWITCH, AP = 'Roteador/Firewall', 'Switch', 'Access Point/Rádio'
ONU, STARLINK, LTE = 'ONU/ONT', 'Starlink', 'Roteador 4G/LTE'
INDUSTRIAL, COMUNICACAO = 'Gateway industrial', 'Equipamento de comunicação'
TIPOS = (ROTEADOR, SWITCH, AP, ONU, STARLINK, LTE, INDUSTRIAL, COMUNICACAO)

# Marca curta (oui.marca) -> fabricante essencialmente de rede
MARCAS_REDE = {
    'MikroTik', 'Ubiquiti', 'Cisco', 'Cisco Meraki', 'Juniper', 'Fortinet', 'Ruijie', 'Cambium', 'Mimosa',
    'Ruckus', 'Extreme', 'Zyxel', 'Netgear', 'Aruba', 'Hirschmann', 'Teltonika', 'Moxa', 'Starlink',
    'FiberHome', 'Furukawa', 'Parks', 'Mercusys', 'Tenda', 'D-Link', 'TP-Link', 'Grandstream', 'Alcatel-Lucent',
}
# Também fazem celular, PLC, CFTV etc.: sozinha, a marca é só um indício
MARCAS_MISTAS = {'Huawei', 'ZTE', 'Nokia', 'Intelbras', 'Siemens', 'Advantech', 'Phoenix Contact', 'Sagemcom',
                 'Arcadyan', 'Askey', 'Sercomm'}
MARCAS_ONU = {'Huawei', 'ZTE', 'FiberHome', 'Nokia', 'Parks', 'Furukawa', 'Intelbras', 'Sagemcom', 'Askey'}

_MODELOS = (
    (re.compile(r'^(RUTX?\d|TRB\d|TCR\d|ICR-?\d|RUTM)', re.I), LTE),
    (re.compile(r'^(NPORT|MGATE|EKI-1|ADAM-|FL COMSERVER|TCF-|IOLOGIK)', re.I), INDUSTRIAL),
    (re.compile(r'^(HG8|EG8|HG6|HS8|F6\d\d|AN55\d\d|G-\d{3}|ONU|ONT|PON|1\d0 ?GF|ONT-|WITHUS)', re.I), ONU),
    (re.compile(r'^(CRS|CSS|USW|US-\d|ES-\d|EDGESWITCH|TL-S[GF]|SG\d|SF\d|DGS|DES-|GS\d|JL\d|WS-C|C9[23]\d|EX\d{4}|S5\d{3}|'
                r'S[67]\d{3}|RSP|SCALANCE X|EDS-|SW\d|MS\d{3})', re.I), SWITCH),
    (re.compile(r'^(SXT|LHG|LDF|GROOVE|DISC|MANTBOX|NETMETAL|WAP|CAP|UAP|U6|U7|UAL|NANOSTATION|NSM|NS-|LOCO|LBE|PBE|'
                r'ROCKET|LITEBEAM|POWERBEAM|NANOBEAM|AIRMAX|LAP|NBE|EAP|CPE\d|WBS|DECO|WOM|APC|AP ?\d{3}|EPMP|FORCE|'
                r'PTP|PMP|MR\d|AIR-|IAP|WA\d|UNIFI AP|U-LR)', re.I), AP),
    (re.compile(r'^(RB\d|RB[A-Z]|HEX|HAP|CCR|CHR|L009|ER-|ERX|EDGEROUTER|UDM|USG|UCG|UXG|UDR|ARCHER|TL-WR|TL-ER|ER\d{3,}|'
                r'FG-|FORTIGATE|ISR|RV\d|MX\d|SRX|ACTION|RG ?\d|RF ?\d|W[456]-|IWR|DIR-|TWIBI|EX\d{3}\b)', re.I), ROTEADOR),
)


def tipo_pelo_modelo(modelo):
    if not modelo:
        return None
    texto = modelo.strip()
    candidatos = [texto] + ([texto.split(None, 1)[1]] if ' ' in texto else [])
    for candidato in candidatos:
        for padrao, tipo in _MODELOS:
            if padrao.match(candidato):
                return tipo
    return None


# ------------------------------------------------------------------ MikroTik MNDP (UDP 5678)
def interpretar_mndp(dados):
    """Anúncio MNDP: cabeçalho de 4 bytes e TLVs (tipo e tamanho em 16 bits big-endian)."""
    if len(dados) < 8:
        return None
    campos, pos = {}, 4
    while pos + 4 <= len(dados):
        tipo, tamanho = struct.unpack('>HH', dados[pos:pos + 4])
        valor = dados[pos + 4:pos + 4 + tamanho]
        pos += 4 + tamanho
        if len(valor) != tamanho:
            break
        campos[tipo] = valor
    texto = lambda t: campos[t].decode('utf-8', 'replace').strip()[:120] if t in campos else None
    if not any(t in campos for t in (5, 7, 8, 12)):
        return None
    uptime = struct.unpack('<I', campos[10])[0] if len(campos.get(10, b'')) == 4 else None
    plataforma = texto(8) or 'MikroTik'
    return {'fabricante': 'MikroTik' if 'mikrotik' in plataforma.lower() else plataforma,
            'modelo': texto(12), 'firmware': f'RouterOS {texto(7)}' if texto(7) else None,
            'nome': texto(5), 'interface': texto(16), 'uptime_s': uptime}


# ------------------------------------------------------------------ Ubiquiti Discovery (UDP 10001)
def interpretar_ubnt(dados):
    """Resposta v1: versão, comando, tamanho (16 bits) e TLVs (tipo 8 bits, tamanho 16 bits)."""
    if len(dados) < 8 or dados[0] != 1 or dados[1] != 0:
        return None
    campos, pos = {}, 4
    fim = min(len(dados), 4 + struct.unpack('>H', dados[2:4])[0])
    while pos + 3 <= fim:
        tipo, tamanho = dados[pos], struct.unpack('>H', dados[pos + 1:pos + 3])[0]
        campos.setdefault(tipo, dados[pos + 3:pos + 3 + tamanho])
        pos += 3 + tamanho
    texto = lambda t: campos[t].decode('utf-8', 'replace').strip()[:120] if campos.get(t) else None
    modelo = texto(0x14) or texto(0x15) or texto(0x0C)
    if not (modelo or texto(0x03) or texto(0x0B)):
        return None
    return {'fabricante': 'Ubiquiti', 'modelo': modelo, 'firmware': texto(0x03), 'nome': texto(0x0B),
            'essid': texto(0x0D)}


# ------------------------------------------------------------------ classificação
def classificar(marca=None, portas=(), upnp=None, mndp=None, ubnt=None, papel=None):
    """Junta as evidências. Devolve None se nada indica equipamento de comunicação."""
    evidencias, riscos, pontos = [], [], 0
    portas, upnp = set(portas), upnp or {}
    anuncio = mndp or ubnt or {}
    if mndp:
        pontos += 4
        evidencias.append('Anuncia-se por MNDP (descoberta do MikroTik)')
    if ubnt:
        pontos += 4
        evidencias.append('Responde à descoberta da Ubiquiti')
    tipo_upnp = (upnp.get('tipo_upnp') or '').lower()
    if tipo_upnp in ('internetgatewaydevice', 'wandevice', 'wfadevice', 'landevice'):
        pontos += 2
        evidencias.append(f"Anuncia-se por UPnP como {upnp['tipo_upnp']}")
    if portas & {8291, 8728, 8729}:
        pontos += 3
        evidencias.append('Gerência MikroTik aberta (' + ', '.join(PORTAS[p] for p in sorted(portas & {8291, 8728, 8729})) + ')')
    if 7547 in portas:
        pontos += 2
        evidencias.append('TR-069 aberto (equipamento gerenciado pelo provedor)')
    if marca in MARCAS_REDE:
        pontos += 2
        evidencias.append(f'Fabricante de equipamentos de rede pelo MAC ({marca})')
    elif marca in MARCAS_MISTAS and (pontos or papel == 'gateway'):
        pontos += 1
        evidencias.append(f'Fabricante que também faz equipamentos de rede ({marca})')
    if papel == 'gateway':
        pontos += 2
        evidencias.append('É o gateway da rede do sensor')
    modelo = anuncio.get('modelo') or upnp.get('modelo')
    tipo_modelo = tipo_pelo_modelo(modelo)
    if tipo_modelo and not anuncio:
        pontos += 2
        evidencias.append(f'Modelo típico de {tipo_modelo} ({modelo})')
    if portas & {22, 23} and pontos:
        pontos += 1
        evidencias.append('Gerência por ' + ' e '.join(PORTAS[p] for p in sorted(portas & {22, 23})))
    if 23 in portas:
        riscos.append('Telnet aberto: a senha de gerência trafega sem criptografia')
    if pontos < 2:
        return None

    if marca == 'Starlink':
        tipo = STARLINK
    elif tipo_modelo:
        tipo = tipo_modelo
    elif tipo_upnp == 'wfadevice':
        tipo = AP
    elif tipo_upnp in ('internetgatewaydevice', 'wandevice'):
        tipo = ONU if (7547 in portas and marca in MARCAS_ONU) else ROTEADOR
    elif 7547 in portas and marca in MARCAS_ONU:
        tipo = ONU
    elif marca == 'Teltonika':
        tipo = LTE
    elif marca in ('Moxa', 'Advantech'):
        tipo = INDUSTRIAL
    elif ubnt:
        tipo = AP
    elif mndp or papel == 'gateway':
        tipo = ROTEADOR
    else:
        tipo = COMUNICACAO
    return {
        'tipo': tipo,
        'confianca': 'alta' if pontos >= 4 else 'media' if pontos >= 3 else 'baixa',
        'pontos': pontos,
        'fabricante': anuncio.get('fabricante') or upnp.get('fabricante') or marca,
        'modelo': modelo,
        'firmware': anuncio.get('firmware'),
        'nome': anuncio.get('nome') or upnp.get('nome'),
        'portas': [f'{p} ({PORTAS.get(p) or {80: "HTTP", 443: "HTTPS"}.get(p, "TCP")})'
                   for p in sorted(portas & (set(PORTAS) | {80, 443}))],
        'evidencias': evidencias,
        'riscos': riscos,
    }
