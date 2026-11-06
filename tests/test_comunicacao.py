"""Equipamentos de comunicação (comunicacao.py) e a descoberta compartilhada (descoberta.py)."""
import json
import struct

import comunicacao as com
import cftv
import database
import descoberta

MAC_EDGE = 'AA:BB:CC:00:00:88'


def _mndp(**campos):
    tipos = {'identidade': 5, 'versao': 7, 'plataforma': 8, 'placa': 12, 'interface': 16}
    corpo = b''.join(struct.pack('>HH', tipos[k], len(v.encode())) + v.encode() for k, v in campos.items())
    return b'\x00\x00\x00\x01' + struct.pack('>HH', 10, 4) + struct.pack('<I', 3600) + corpo


def _ubnt(**campos):
    tipos = {'firmware': 0x03, 'nome': 0x0B, 'plataforma': 0x0C, 'modelo': 0x14}
    corpo = b''.join(bytes([tipos[k]]) + struct.pack('>H', len(v.encode())) + v.encode() for k, v in campos.items())
    return b'\x01\x00' + struct.pack('>H', len(corpo)) + corpo


def test_mndp_do_mikrotik():
    info = com.interpretar_mndp(_mndp(identidade='RB-Portaria', versao='7.15.3 (stable)', plataforma='MikroTik',
                                       placa='RB951Ui-2HnD', interface='ether2'))
    assert info == {'fabricante': 'MikroTik', 'modelo': 'RB951Ui-2HnD', 'firmware': 'RouterOS 7.15.3 (stable)',
                    'nome': 'RB-Portaria', 'interface': 'ether2', 'uptime_s': 3600}
    assert com.interpretar_mndp(b'\x00\x00\x00\x00') is None          # o próprio pedido de descoberta
    assert com.interpretar_mndp(b'\x00\x00\x00\x01\x00\x05\x00\xff' + b'x') is None   # TLV truncado


def test_descoberta_ubiquiti():
    info = com.interpretar_ubnt(_ubnt(firmware='XW.ar934x.v6.3.11', nome='PTP-Usina', plataforma='LBE5AC', modelo='LiteBeam 5AC Gen2'))
    assert info['modelo'] == 'LiteBeam 5AC Gen2' and info['firmware'] == 'XW.ar934x.v6.3.11' and info['nome'] == 'PTP-Usina'
    assert com.interpretar_ubnt(b'\x01\x00\x00\x00') is None           # o próprio pedido (sem campos)
    assert com.interpretar_ubnt(b'\x02\x06\x00\x00xxxx') is None        # outra versão do protocolo


def test_classificacao_de_comunicacao():
    mk = com.classificar('MikroTik', [8291, 22, 80], mndp=com.interpretar_mndp(_mndp(placa='CRS326-24G-2S+', versao='7.15')))
    assert mk['tipo'] == com.SWITCH and mk['confianca'] == 'alta' and mk['firmware'] == 'RouterOS 7.15'
    radio = com.classificar('Ubiquiti', [22, 80, 443], ubnt=com.interpretar_ubnt(_ubnt(modelo='NanoStation 5AC loco')))
    assert radio['tipo'] == com.AP and radio['modelo'] == 'NanoStation 5AC loco'
    gw = com.classificar('TP-Link', [80], papel='gateway')
    assert gw['tipo'] == com.ROTEADOR and gw['confianca'] == 'alta' and 'É o gateway da rede do sensor' in gw['evidencias']
    onu = com.classificar('Huawei', [80, 7547], upnp={'tipo_upnp': 'InternetGatewayDevice', 'modelo': 'EG8145V5'}, papel='gateway')
    assert onu['tipo'] == com.ONU and onu['modelo'] == 'EG8145V5'
    assert com.classificar('Teltonika', [22, 80])['tipo'] == com.LTE
    assert com.classificar('Starlink', [], papel='gateway')['tipo'] == com.STARLINK
    telnet = com.classificar('D-Link', [23, 80])
    assert telnet['riscos'] == ['Telnet aberto: a senha de gerência trafega sem criptografia']
    assert com.classificar('Huawei', [80]) is None                     # celular Huawei não vira equipamento de rede
    assert com.classificar(None, [22]) is None                         # um servidor Linux com SSH não é roteador
    assert com.classificar(None, [80], upnp={'modelo': 'RUT955'})['tipo'] == com.LTE


def test_cftv_e_comunicacao_nao_se_misturam():
    # NVR Intelbras com UPnP: CFTV vence (mais pontos) e não aparece como roteador
    nvr = {'marca': 'Intelbras', 'portas': [554, 37777, 80], 'upnp': {'tipo_upnp': 'Basic', 'modelo': 'MHDX 1108'}}
    c, r = descoberta.classificar(nvr)
    assert c['tipo'] == cftv.GRAVADOR and r is None
    # roteador Intelbras que é o gateway: comunicação
    c, r = descoberta.classificar({'marca': 'Intelbras', 'portas': [80, 443], 'upnp': {'tipo_upnp': 'InternetGatewayDevice'}}, 'gateway')
    assert c is None and r['tipo'] == com.ROTEADOR


def test_portas_industriais_nunca_sao_testadas():
    assert not set(descoberta.PORTAS) & {102, 502, 2404, 20000, 44818, 47808, 4840}


def test_queda_de_comunicacao_ignora_gateway(monkeypatch):
    descoberta._cache.clear(); descoberta._vistos.clear(); descoberta._eventos.clear()
    evid = {'08:55:31:00:00:01': {'marca': 'MikroTik', 'portas': [8291]}, '08:55:31:00:00:02': {'marca': 'MikroTik', 'portas': [8291]}}
    for mac, e in evid.items():
        descoberta._cache[mac] = (e, 9e18)       # já avaliado e dentro da validade
    lista = [{'ip': '10.0.0.1', 'mac': '08:55:31:00:00:01', 'status': 'online'},
             {'ip': '10.0.0.2', 'mac': '08:55:31:00:00:02', 'status': 'online'}]
    descoberta.anotar(lista, gateway_ip='10.0.0.1')
    assert lista[0]['comunicacao']['tipo'] == com.ROTEADOR and lista[1]['tipo_equipamento'] == com.COMUNICACAO
    assert set(descoberta._vistos) == {'08:55:31:00:00:02'}
    descoberta._vistos['08:55:31:00:00:02']['ultimo_ok'] -= descoberta.QUEDA_APOS_S + 1
    descoberta.anotar([], gateway_ip='10.0.0.1')
    assert [e[0] for e in descoberta.eventos()] == ['Queda de Equipamento de Comunicação']
    descoberta._cache.clear(); descoberta._vistos.clear()


def test_central_guarda_comunicacao_e_exporta(client, login):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, ip_sensor, ip_gateway, cliente_id) VALUES (?, 'Usina', '10.0.0.10', '10.0.0.1', 2)", (MAC_EDGE,))
    conn.commit(); conn.close()
    mk = com.classificar('MikroTik', [8291, 23], mndp=com.interpretar_mndp(_mndp(placa='RB4011iGS+', versao='7.15', identidade='Borda')), papel='gateway')
    lista = [{'ip': '10.0.0.1', 'mac': '08:55:31:00:00:01', 'status': 'online', 'tipo_equipamento': mk['tipo'], 'modelo': mk['modelo'],
              'firmware': mk['firmware'], 'comunicacao': dict(mk, extra='x')},
             {'ip': '10.0.0.9', 'mac': '08:55:31:00:00:09', 'status': 'online', 'comunicacao': {'tipo': 'Geladeira'}}]
    assert client.post('/api/v2/atualizar_dispositivos', json={'mac_id': MAC_EDGE, 'lista': lista}).status_code == 200
    dados = json.loads(login(1).get(f'/api/v2/levantamento/{MAC_EDGE}/json').data)
    disp = {d['ip']: d for d in dados['dispositivos']}
    rb = disp['10.0.0.1']
    assert rb['tipo'] == com.ROTEADOR and rb['modelo'] == 'RB4011iGS+' and rb['firmware'] == 'RouterOS 7.15'
    assert rb['comunicacao']['riscos'] and 'extra' not in rb['comunicacao']
    assert disp['10.0.0.9']['comunicacao'] is None and dados['resumo']['comunicacao'] == 1
    csv = login(1).get(f'/api/v2/levantamento/{MAC_EDGE}/csv').data.decode('utf-8-sig')
    assert 'Categoria;Tipo;Modelo;Firmware' in csv and 'Comunicação;Roteador/Firewall;RB4011iGS+;RouterOS 7.15' in csv


def test_sem_icmp_nao_e_queda():
    descoberta._cache.clear(); descoberta._vistos.clear(); descoberta._eventos.clear()
    descoberta._cache['08:55:31:00:00:03'] = ({'marca': 'MikroTik', 'portas': [8291]}, 9e18)
    descoberta.anotar([{'ip': '10.0.0.3', 'mac': '08:55:31:00:00:03', 'status': 'online'}])
    descoberta._vistos['08:55:31:00:00:03']['ultimo_ok'] -= descoberta.QUEDA_APOS_S + 1
    descoberta.anotar([{'ip': '10.0.0.3', 'mac': '08:55:31:00:00:03', 'status': 'sem_icmp'}])
    assert descoberta.eventos() == []
    descoberta._cache.clear(); descoberta._vistos.clear()
