"""Correções de identificação (CFTV) e de cobertura da varredura (rede inteira, outras VLANs)."""
import ipaddress
import json
from unittest.mock import mock_open, patch

import pytest

import cftv
import comunicacao as com
import database
import descoberta
import edge_local
import nexopulse_edge as agent

ONVIF_NVR = ('<d:ProbeMatch><d:Types>dn:NetworkVideoTransmitter</d:Types><d:Scopes>'
             'onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/hardware/DS-7608NI-K2 '
             'onvif://www.onvif.org/name/HIKVISION</d:Scopes></d:ProbeMatch>')


# ===================================================================== identificação
def test_porta_8000_sozinha_nao_e_cftv():
    assert cftv.classificar(None, [8000, 80]) is None              # servidor web, IHM, supervisório
    assert cftv.classificar('Hikvision', [8000])['confianca'] == 'alta'
    assert cftv.classificar(None, [8000, 554])['tipo'] == cftv.CFTV


def test_nvr_anunciado_como_camera_continua_nvr():
    info = cftv.interpretar_onvif(ONVIF_NVR)
    assert info['tipo'] == cftv.GRAVADOR
    c, r = descoberta.classificar({'marca': 'Hikvision', 'portas': [554, 8000], 'onvif': info})
    assert c['tipo'] == cftv.GRAVADOR and r is None
    pelo_nome = ONVIF_NVR.replace('DS-7608NI-K2', 'X1').replace('/name/HIKVISION', '/name/NVR%20Portaria')
    assert cftv.interpretar_onvif(pelo_nome)['tipo'] == cftv.GRAVADOR


def test_hikvision_que_nao_e_cftv():
    c, r = descoberta.classificar({'marca': 'Hikvision', 'portas': [80], 'upnp': {'modelo': 'DS-3E0510P-E'}})
    assert c is None and r['tipo'] == com.SWITCH
    c, r = descoberta.classificar({'marca': 'Hikvision', 'portas': [8000, 80], 'upnp': {'modelo': 'DS-K1T341AM'}})
    assert c['tipo'] == cftv.ACESSO and r is None


def test_camera_unifi_nao_vira_access_point():
    c, r = descoberta.classificar({'marca': 'Ubiquiti', 'portas': [22, 443], 'ubnt': {'modelo': 'UVC G4 Bullet', 'nome': 'Portão'}})
    assert c['tipo'] == cftv.CAMERA and c['modelo'] == 'UVC G4 Bullet' and r is None
    c, r = descoberta.classificar({'marca': 'Ubiquiti', 'portas': [22], 'ubnt': {'modelo': 'UNVR'}})
    assert c['tipo'] == cftv.GRAVADOR and r is None
    c, r = descoberta.classificar({'marca': 'Ubiquiti', 'portas': [22], 'ubnt': {'modelo': 'NanoStation 5AC loco'}})
    assert c is None and r['tipo'] == com.AP


# ===================================================================== varredura
@pytest.fixture()
def varredura_limpa(monkeypatch):
    monkeypatch.setattr(agent, '_varredura', {'fatia': {}, 'lista': None, 'extras': []})
    monkeypatch.setattr(agent, '_vistos_l2', {})
    monkeypatch.setattr(agent, 'redes_extras', lambda: [])
    monkeypatch.setattr(agent, 'MAX_NETWORK_SCAN_HOSTS', 512)
    return agent


def test_rede_grande_e_varrida_inteira_em_fatias(varredura_limpa, monkeypatch):
    rede, limitada = agent._rede_scan_segura('10.0.0.0/22', '10.0.1.5', agent.VARREDURA_MAX_HOSTS)
    assert str(rede) == '10.0.0.0/22' and not limitada             # antes: só o /24 do sensor
    pingados = []
    monkeypatch.setattr(agent, 'ping_silencioso', pingados.append)
    monkeypatch.setattr(agent.subprocess, 'check_output', lambda *a, **k: '')
    ctx = {'ip': '10.0.1.5', 'interface': 'eth0', 'cidr': '10.0.0.0/22', 'scan_rede': '10.0.0.0/22'}
    agent.get_topologia_arp(ctx, forcar_varredura=True)
    agent.get_topologia_arp(ctx, forcar_varredura=True)
    assert len(set(pingados)) == 1021 and '10.0.1.5' not in pingados      # 1022 hosts menos o sensor
    assert agent.dados_sensores['varredura']['ciclos'] == 2
    # /16 continua limitado (ao /20 em volta do sensor)
    rede, limitada = agent._rede_scan_segura('10.0.0.0/16', '10.0.1.5', agent.VARREDURA_MAX_HOSTS)
    assert limitada and rede.num_addresses == 4096 and ipaddress.ip_address('10.0.1.5') in rede


def test_equipamento_que_sai_da_tabela_arp_nao_some_na_hora(varredura_limpa, monkeypatch):
    monkeypatch.setattr(agent, 'ping_silencioso', lambda ip: None)
    monkeypatch.setattr(agent, 'ping', lambda ip: 0.5 if ip == '10.0.0.2' else 0)
    tabela = ['10.0.0.2 dev eth0 lladdr aa:bb:cc:dd:ee:02 REACHABLE\n10.0.0.3 dev eth0 lladdr aa:bb:cc:dd:ee:03 STALE']
    monkeypatch.setattr(agent.subprocess, 'check_output', lambda *a, **k: tabela[0])
    ctx = {'ip': '10.0.0.1', 'interface': 'eth0', 'cidr': '10.0.0.0/24'}
    assert [d['status'] for d in agent.get_topologia_arp(ctx, True)] == ['online', 'sem_icmp']
    tabela[0] = '10.0.0.2 dev eth0 lladdr aa:bb:cc:dd:ee:02 REACHABLE'      # o kernel descartou o .3
    lista = agent.get_topologia_arp(ctx, True)
    assert [(d['ip'], d['status']) for d in lista] == [('10.0.0.2', 'online'), ('10.0.0.3', 'desconhecido')]
    # entre varreduras: devolve a última lista, sem pingar todo mundo a cada 5 s
    monkeypatch.setattr(agent, 'ping', lambda ip: pytest.fail('não deveria pingar fora da varredura'))
    assert len(agent.get_topologia_arp(ctx, False)) == 2
    # passou da validade: sai da lista
    monkeypatch.setattr(agent, 'ping', lambda ip: 0.5 if ip == '10.0.0.2' else 0)
    agent._vistos_l2['10.0.0.3'] = (agent._vistos_l2['10.0.0.3'][0], 0)
    assert [d['ip'] for d in agent.get_topologia_arp(ctx, True)] == ['10.0.0.2']


def test_outra_vlan_entra_pelo_ping(varredura_limpa, monkeypatch):
    monkeypatch.setattr(agent, 'redes_extras', lambda: [ipaddress.ip_network('192.168.10.0/29')])
    monkeypatch.setattr(agent, 'ping_silencioso', lambda ip: None)
    monkeypatch.setattr(agent, 'ping', lambda ip: 3.0 if ip in ('192.168.10.2', '192.168.10.5') else 0)
    monkeypatch.setattr(agent.subprocess, 'check_output', lambda *a, **k: '')
    lista = agent.get_topologia_arp({'ip': '10.0.0.1', 'interface': 'eth0', 'cidr': '10.0.0.0/24'}, True)
    assert [(d['ip'], d['mac'], d['rede']) for d in lista] == [('192.168.10.2', None, '192.168.10.0/29'),
                                                               ('192.168.10.5', None, '192.168.10.0/29')]
    assert agent.dados_sensores['varredura']['extras'] == [{'rede': '192.168.10.0/29', 'hosts': 6, 'ciclos': 1}]


def test_validar_rede_extra():
    assert str(edge_local.validar_rede('192.168.10.7/24')) == '192.168.10.0/24'
    for invalida in ('8.8.8.0/24', '10.0.0.0/16', '127.0.0.0/24', '169.254.0.0/24', 'abc', 'fe80::/64'):
        assert edge_local.validar_rede(invalida) is None, invalida


def test_aviso_wsl_nat():
    with patch('builtins.open', mock_open(read_data='Linux version 5.15.153.1-microsoft-standard-WSL2')):
        assert 'mirrored' in agent.aviso_rede({'ip': '172.24.10.5', 'gateway': '172.24.0.1'})
        assert agent.aviso_rede({'ip': '192.168.0.10', 'gateway': '192.168.0.1'}) is None   # modo mirrored


def test_central_aceita_equipamento_de_outra_vlan(client, login):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, ip_sensor, ip_gateway, cliente_id) VALUES ('AA:BB:CC:00:00:99', 'Usina', '10.0.0.10', '10.0.0.1', 2)")
    conn.commit(); conn.close()
    camera = cftv.classificar(None, [554, 34567])
    lista = [{'ip': '192.168.10.2', 'mac': None, 'rede': '192.168.10.0/24', 'status': 'online', 'cftv': camera,
              'tipo_equipamento': camera['tipo']}]
    assert client.post('/api/v2/atualizar_dispositivos', json={'mac_id': 'AA:BB:CC:00:00:99', 'lista': lista}).status_code == 200
    dados = json.loads(login(1).get('/api/v2/levantamento/AA:BB:CC:00:00:99/json').data)
    d = next(x for x in dados['dispositivos'] if x['ip'] == '192.168.10.2')
    assert d['mac'] is None and d['rede_origem'] == '192.168.10.0/24' and d['cftv']['tipo'] == cftv.CFTV
    assert dados['escopo']['redes_adicionais'] == ['192.168.10.0/24']
    # sem MAC e sem rede de origem continua recusado
    ruim = [{'ip': '10.0.0.7', 'mac': None, 'status': 'online'}]
    assert client.post('/api/v2/atualizar_dispositivos', json={'mac_id': 'AA:BB:CC:00:00:99', 'lista': ruim}).status_code == 400
