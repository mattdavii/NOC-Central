"""Painel local do Edge (edge_local.py + templates/edge) e identificação de CFTV (cftv.py)."""
import io
import json
import time
from datetime import datetime, timedelta, timezone

import jwt
import openpyxl
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import cftv
import descoberta
import database
import edge_local
import levantamento
import nexopulse_edge as agent

MAC_EDGE = 'AA:BB:CC:00:00:77'
HIK = '00:BC:99:12:34:56'      # Hangzhou Hikvision no registro IEEE
DAHUA = '08:ED:ED:12:34:56'    # Zhejiang Dahua


# ===================================================================== CFTV
ONVIF_HIK = """<?xml version="1.0"?><env:Envelope><env:Body><d:ProbeMatches><d:ProbeMatch>
<d:Types>dn:NetworkVideoTransmitter tds:Device</d:Types>
<d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/Profile/Streaming
onvif://www.onvif.org/hardware/DS-2CD2143G2-I onvif://www.onvif.org/name/HIKVISION%20DS-2CD2143G2-I
onvif://www.onvif.org/location/city/hangzhou</d:Scopes>
<d:XAddrs>http://192.168.0.64/onvif/device_service</d:XAddrs></d:ProbeMatch></d:ProbeMatches></env:Body></env:Envelope>"""


def test_onvif_traz_fabricante_modelo_e_tipo():
    info = cftv.interpretar_onvif(ONVIF_HIK)
    assert info['modelo'] == 'DS-2CD2143G2-I' and info['tipo'] == cftv.CAMERA
    assert info['fabricante'] == 'HIKVISION' and info['xaddrs'] == ['http://192.168.0.64/onvif/device_service']
    gravador = ONVIF_HIK.replace('video_encoder', 'Network_Video_Storage').replace('DS-2CD2143G2-I', 'DS-7608NI-K2')
    assert cftv.interpretar_onvif(gravador)['tipo'] == cftv.GRAVADOR


@pytest.mark.parametrize('modelo,tipo', [
    ('DS-2CD2143G2-I', cftv.CAMERA), ('DS-7608NI-K2', cftv.GRAVADOR), ('DH-XVR5108HS-I3', cftv.GRAVADOR),
    ('IPC-HFW2431S', cftv.CAMERA), ('VIP 3230 B', cftv.CAMERA), ('MHDX 1108', cftv.GRAVADOR), ('Archer C6', None)])
def test_tipo_pelo_modelo(modelo, tipo):
    assert cftv.tipo_pelo_modelo(modelo) == tipo


def test_classificacao_por_evidencias():
    forte = cftv.classificar('Dahua', [554, 37777, 80])
    assert forte['tipo'] == cftv.CFTV and forte['confianca'] == 'alta'
    assert 'Porta 37777 aberta (Dahua TCP)' in forte['evidencias']
    assert cftv.classificar('Intelbras', [80, 443]) is None          # Intelbras sozinha: roteador, ONU...
    assert cftv.classificar(None, [554]) is None                      # só RTSP não basta
    icsee = cftv.classificar(None, [34567, 554])
    assert icsee['confianca'] == 'media' and icsee['tipo'] == cftv.CFTV
    camera = cftv.classificar('Hikvision', [554, 8000], onvif=cftv.interpretar_onvif(ONVIF_HIK))
    assert camera['tipo'] == cftv.CAMERA and camera['modelo'] == 'DS-2CD2143G2-I' and camera['onvif']
    nvr_upnp = cftv.classificar('Hikvision', [], upnp={'modelo': 'DS-7608NI-K2', 'fabricante': 'Hikvision'})
    assert nvr_upnp['tipo'] == cftv.GRAVADOR


def test_upnp_descricao():
    xml = ('<root><device><deviceType>urn:schemas-upnp-org:device:Basic:1</deviceType><friendlyName>NVR Portaria</friendlyName>'
           '<manufacturer>Hikvision</manufacturer><modelName>DS-7608NI-K2</modelName></device></root>')
    assert descoberta.interpretar_upnp(xml) == {'fabricante': 'Hikvision', 'modelo': 'DS-7608NI-K2', 'nome': 'NVR Portaria',
                                          'tipo_upnp': 'Basic'}


def test_anotar_usa_cache_e_avisa_queda(monkeypatch):
    descoberta._cache.clear(); descoberta._vistos.clear(); descoberta._eventos.clear()
    monkeypatch.setattr(descoberta, '_avaliar', lambda mac, ip, ip_local: {'marca': 'Hikvision', 'portas': [554, 8000]})
    descoberta.anotar([{'ip': '192.168.0.64', 'mac': HIK, 'status': 'online'}])
    descoberta._fila.join()
    lista = [{'ip': '192.168.0.64', 'mac': HIK, 'status': 'online'}]
    descoberta.anotar(lista)
    assert lista[0]['tipo_equipamento'] == cftv.CFTV and lista[0]['cftv']['confianca'] == 'alta'
    assert 'comunicacao' not in lista[0]
    # some da rede por mais que QUEDA_APOS_S: um evento crítico, uma vez; ao voltar, um OK
    descoberta._vistos[HIK]['ultimo_ok'] -= descoberta.QUEDA_APOS_S + 1
    descoberta.anotar([]); descoberta.anotar([])
    eventos = descoberta.eventos()
    assert [e[0] for e in eventos] == ['Queda de CFTV'] and eventos[0][2] == 'Crítica'
    descoberta.anotar([{'ip': '192.168.0.64', 'mac': HIK, 'status': 'online'}])
    assert [e[0] for e in descoberta.eventos()] == ['CFTV Restabelecido']
    descoberta._cache.clear(); descoberta._vistos.clear()


def test_central_guarda_cftv_e_exporta(client, login):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, ip_sensor, ip_gateway, cliente_id) VALUES (?, 'Portaria', '192.168.0.10', '192.168.0.1', 2)", (MAC_EDGE,))
    conn.commit(); conn.close()
    camera = cftv.classificar('Hikvision', [554, 8000], onvif=cftv.interpretar_onvif(ONVIF_HIK))
    lista = [{'ip': '192.168.0.64', 'mac': HIK, 'status': 'online', 'tipo_equipamento': cftv.CAMERA,
              'modelo': 'DS-2CD2143G2-I', 'cftv': dict(camera, evidencias=camera['evidencias'] + ['<script>'], extra='x')},
             {'ip': '192.168.0.65', 'mac': DAHUA, 'status': 'online', 'tipo_equipamento': 'Torradeira', 'cftv': 'lixo'}]
    assert client.post('/api/v2/atualizar_dispositivos', json={'mac_id': MAC_EDGE, 'lista': lista}).status_code == 200
    dados = json.loads(login(1).get(f'/api/v2/levantamento/{MAC_EDGE}/json').data)
    disp = {d['ip']: d for d in dados['dispositivos']}
    assert disp['192.168.0.64']['tipo'] == cftv.CAMERA and disp['192.168.0.64']['modelo'] == 'DS-2CD2143G2-I'
    assert 'extra' not in disp['192.168.0.64']['cftv'] and 'script' in disp['192.168.0.64']['cftv']['evidencias'][-1]
    assert '<' not in disp['192.168.0.64']['cftv']['evidencias'][-1]
    assert disp['192.168.0.65']['tipo'] is None and disp['192.168.0.65']['cftv'] is None
    assert dados['resumo']['cftv'] == 1 and dados['resumo']['por_tipo'] == {cftv.CAMERA: 1, 'Sensor NexoPulse': 1}


# ===================================================================== registro de eventos
def test_eventos_filtros_e_retencao(tmp_path, monkeypatch):
    ev = edge_local.Eventos(str(tmp_path / 'local.db'))
    ev.iniciar()
    for i in range(5):
        ev.registrar('Queda de Alvo', f'Câmera {i} parou', 'Crítica' if i % 2 else 'OK')
    ev.registrar('Teste', 'x', 'Inventada')
    total, linhas = ev.listar(gravidade='Crítica')
    assert total == 2 and all(l['gravidade'] == 'Crítica' for l in linhas)
    assert ev.listar(busca='Câmera 3')[0] == 1
    assert ev.listar(gravidade=None)[1][0]['gravidade'] == 'Alerta'       # gravidade desconhecida vira Alerta
    hoje = datetime.now().date().isoformat()
    assert ev.listar(de=hoje, ate=hoje)[0] == 6
    # retenção: o que passou de RETENCAO_DIAS sai na limpeza
    with ev._conectar() as conn:
        conn.execute("UPDATE logs_locais SET data_hora = datetime('now', '-200 days') WHERE id <= 2")
        ev._limpar(conn)
    assert ev.listar()[0] == 4
    assert ev.resumo()['por_gravidade'] == {'Crítica': 1, 'OK': 2, 'Alerta': 1}


def test_periodo_da_requisicao():
    hoje = datetime.now().date()
    assert edge_local.periodo_da_requisicao({})[2] == 'Últimos 7 dias'
    assert edge_local.periodo_da_requisicao({'periodo': 'dia', 'data': '2026-10-02'}) == ('2026-10-02', '2026-10-02', 'Dia 02/10/2026')
    de, ate, texto = edge_local.periodo_da_requisicao({'periodo': 'periodo', 'de': '2026-10-30', 'ate': '2026-10-01'})
    assert (de, ate) == ('2026-10-01', '2026-10-30') and texto == 'Período 01/10/2026 a 30/10/2026'
    assert edge_local.periodo_da_requisicao({'periodo': 'dia', 'data': 'ontem'})[1] == hoje.isoformat()


def test_alerta_enviado_fica_no_registro_local(monkeypatch, tmp_path):
    ev = edge_local.Eventos(str(tmp_path / 'a.db')); ev.iniciar()
    monkeypatch.setattr(agent, 'EVENTOS_LOCAIS', ev)
    monkeypatch.setattr(agent.urllib.request, 'urlopen', lambda *a, **k: None)
    agent.enviar_alertas('https://central/api/v2/alertas_ia', [{'tipo': 'Queda de Alvo', 'gravidade': 'Crítica', 'detalhes': 'NVR parou'}])
    assert ev.listar()[1][0]['detalhes'] == 'NVR parou'

    def falha(*a, **k): raise OSError('sem internet')
    monkeypatch.setattr(agent.urllib.request, 'urlopen', falha)
    with pytest.raises(OSError):
        agent.enviar_alertas('https://central/api/v2/alertas_ia', [{'tipo': 'X', 'gravidade': 'OK', 'detalhes': 'y'}])
    assert ev.listar()[0] == 1     # quem chamou registra com o motivo da falha


# ===================================================================== painel local (HTTP)
@pytest.fixture()
def edge(monkeypatch, tmp_path):
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    publica = chave.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(agent, 'CHAVE_PUBLICA_JWT', publica)
    monkeypatch.setattr(agent, 'get_mac', lambda: MAC_EDGE)
    monkeypatch.chdir(tmp_path)          # sensor_local.db do teste
    ev = edge_local.Eventos('sensor_local.db')
    monkeypatch.setattr(agent, 'EVENTOS_LOCAIS', ev)
    agent.init_local_db()
    camera = cftv.classificar('Hikvision', [554, 8000], onvif=cftv.interpretar_onvif(ONVIF_HIK))
    monkeypatch.setitem(agent.dados_sensores, 'topologia', [
        {'ip': '192.168.0.1', 'mac': 'F4:F2:6D:AA:BB:CC', 'status': 'online', 'latencia': 1.0, 'nome': 'Desconhecido', 'fabricante': 'Desconhecido'},
        {'ip': '192.168.0.64', 'mac': HIK, 'status': 'online', 'latencia': 2.0, 'nome': 'Desconhecido', 'fabricante': 'Desconhecido',
         'cftv': camera, 'tipo_equipamento': camera['tipo'], 'modelo': camera['modelo']},
    ])
    monkeypatch.setitem(agent.dados_sensores, 'gateway_ip', '192.168.0.1')
    monkeypatch.setitem(agent.dados_sensores, 'meu_ip', '192.168.0.10')
    agent.app.config['TESTING'] = True
    token = jwt.encode({'mac': MAC_EDGE, 'exp': datetime.now(timezone.utc) + timedelta(hours=1)}, chave, algorithm='RS256')
    outro = jwt.encode({'mac': 'AA:AA:AA:AA:AA:AA', 'exp': datetime.now(timezone.utc) + timedelta(hours=1)}, chave, algorithm='RS256')
    return agent.app.test_client(), {'X-Auth-Token': token}, token, outro, ev


def test_painel_exige_token_do_proprio_sensor(edge):
    cli, _, token, outro, _ = edge
    assert cli.get('/').status_code == 401 and b'Acesso local' in cli.get('/').data      # página, não JSON
    assert cli.get('/api/eventos').status_code == 401 and cli.get('/api/eventos').json['erro']
    assert cli.get('/?token=' + outro).status_code == 401                               # token de outro sensor
    assert cli.get('/static/sensor.css').status_code == 200                             # visual é público
    pagina = cli.get('/?token=' + token)
    assert pagina.status_code == 200 and b'card-hw' in pagina.data and b'/static/sensor.css' in pagina.data


def test_painel_exporta_levantamento_com_cftv(edge):
    cli, h, *_ = edge
    dados = json.loads(cli.get('/api/levantamento.json', headers=h).data)
    assert dados['gerado_por'].startswith('NexoPulse Edge') and dados['resumo']['cftv'] == 1
    camera = next(d for d in dados['dispositivos'] if d['ip'] == '192.168.0.64')
    assert camera['marca']['valor'] == 'Hikvision' and camera['modelo'] == 'DS-2CD2143G2-I' and camera['tipo'] == cftv.CAMERA
    assert any(d['papel'] == 'sensor' for d in dados['dispositivos'])
    csv = cli.get('/api/levantamento.csv', headers=h)
    assert csv.status_code == 200 and 'DS-2CD2143G2-I'.encode() in csv.data and 'attachment' in csv.headers['Content-Disposition']
    livro = openpyxl.load_workbook(io.BytesIO(cli.get('/api/levantamento.xlsx', headers=h).data))
    assert 'Modelo' in [c.value for c in livro['Dispositivos'][1]]
    assert cli.get('/api/levantamento.pdf', headers=h).status_code == 400


def test_painel_eventos_e_relatorio(edge):
    cli, h, token, _, ev = edge
    ev.registrar('Queda de CFTV', 'DS-2CD2143G2-I (192.168.0.64) não responde', 'Crítica')
    ev.registrar('CFTV Restabelecido', 'voltou', 'OK')
    r = cli.get('/api/eventos?gravidade=Crítica', headers=h).json
    assert r['total'] == 1 and r['eventos'][0]['tipo'] == 'Queda de CFTV'
    csv = cli.get('/api/eventos.csv', headers=h)
    assert csv.status_code == 200 and b'Queda de CFTV' in csv.data
    rel = cli.get('/relatorio?periodo=ultimos&dias=1&token=' + token)
    assert rel.status_code == 200
    html = rel.data.decode()
    assert 'Equipamentos de CFTV' in html and 'DS-2CD2143G2-I' in html and 'Últimos 1 dias' in html
    assert cli.get('/api/v2/sensor_data/x', headers=h).json == {'host_info': None}
