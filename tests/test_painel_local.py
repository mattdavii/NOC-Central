"""Painel local do Edge (edge_local.py + templates/edge) e identificação de CFTV (cftv.py)."""
import io
import json
import time
from datetime import datetime, timedelta, timezone

import openpyxl
import pytest
from werkzeug.security import generate_password_hash

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
    monkeypatch.setattr(agent.fila_envio, 'DB', str(tmp_path / 'fila.db'))
    assert agent.enviar_alertas('https://central/api/v2/alertas_ia', [{'tipo': 'X', 'gravidade': 'OK', 'detalhes': 'y'}]) is False
    assert ev.listar()[0] == 2 and 'guardado para enviar' in ev.listar()[1][0]['detalhes']   # não levanta mais exceção


# ===================================================================== painel local (HTTP)
@pytest.fixture()
def edge(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, 'get_mac', lambda: MAC_EDGE)
    monkeypatch.chdir(tmp_path)          # sensor_local.db do teste
    acesso = edge_local.AcessoLocal('sensor_local.db')
    monkeypatch.setattr(agent, 'ACESSO_LOCAL', acesso)
    monkeypatch.setattr(agent._EstadoPainel, 'acesso', acesso)
    monkeypatch.delenv('NOC_EDGE_SENHA', raising=False)
    acesso.receber_da_central({'usuario': 'nexopulse', 'senha_hash': generate_password_hash('senha-do-painel', method='pbkdf2:sha256')})
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
    cli = agent.app.test_client()
    assert cli.post('/login', data={'usuario': 'nexopulse', 'senha': 'senha-do-painel'}).status_code == 302
    return cli, {}, '', acesso, ev


def test_painel_exige_login(edge):
    _, _, _, acesso, ev = edge
    anon = agent.app.test_client()
    r = anon.get('/')
    assert r.status_code == 302 and r.headers['Location'].startswith('/login')
    assert anon.get('/api/eventos').status_code == 401 and anon.get('/api/eventos').json['erro']
    assert anon.get('/static/sensor.css').status_code == 200                            # visual é público
    assert b'Painel local do sensor' in anon.get('/login').data
    assert anon.get('/?token=qualquer-coisa').status_code == 302                         # token não abre mais nada
    errado = anon.post('/login', data={'usuario': 'nexopulse', 'senha': 'errada'})
    assert errado.status_code == 401 and 'incorretos'.encode() in errado.data
    ok = anon.post('/login', data={'usuario': 'nexopulse', 'senha': 'senha-do-painel', 'proximo': '/relatorio?periodo=dia'})
    assert ok.status_code == 302 and ok.headers['Location'] == '/relatorio?periodo=dia'
    pagina = anon.get('/')
    assert pagina.status_code == 200 and b'card-hw' in pagina.data and b'/sair' in pagina.data
    assert [e['tipo'] for e in ev.listar(busca='painel local')[1]] == ['Acesso local', 'Acesso local']
    # escrita vinda de outro site é barrada; da própria página passa
    assert anon.post('/api/alvos', json={'ip': '1.1.1.1', 'descricao': 'x'}, headers={'Origin': 'http://malicioso.com'}).status_code == 403
    assert anon.post('/api/alvos', json={'ip': '1.1.1.1', 'descricao': 'x'}, headers={'Origin': 'http://localhost'}).status_code == 200
    # redirecionamento só para dentro do próprio sensor
    fora = agent.app.test_client().post('/login', data={'usuario': 'nexopulse', 'senha': 'senha-do-painel', 'proximo': '//malicioso.com'})
    assert fora.headers['Location'] == '/'
    # credencial nova da Central derruba as sessões abertas
    acesso.receber_da_central({'usuario': 'nexopulse', 'senha_hash': generate_password_hash('outra-senha-123', method='pbkdf2:sha256')})
    assert anon.get('/').status_code == 302
    anon.post('/sair')


def test_bloqueio_por_tentativas(edge):
    anon = agent.app.test_client()
    for _ in range(edge_local.MAX_FALHAS):
        anon.post('/login', data={'usuario': 'nexopulse', 'senha': 'errada'})
    r = anon.post('/login', data={'usuario': 'nexopulse', 'senha': 'senha-do-painel'})
    assert r.status_code == 401 and 'Muitas tentativas'.encode() in r.data      # nem a certa entra durante o bloqueio
    edge[3]._falhas.clear()


def test_credencial_da_central_e_do_ambiente(tmp_path, monkeypatch):
    acesso = edge_local.AcessoLocal(str(tmp_path / 'c.db'))
    monkeypatch.delenv('NOC_EDGE_SENHA', raising=False)
    assert acesso.credencial() == (None, None, None)
    monkeypatch.setenv('NOC_EDGE_SENHA', 'instalacao-123'); monkeypatch.setenv('NOC_EDGE_USUARIO', 'campo')
    assert acesso.credencial()[0::2] == ('campo', 'ambiente') and acesso.conferir('1.2.3.4', 'campo', 'instalacao-123')
    assert not acesso.receber_da_central({'usuario': 'x', 'senha_hash': 'texto-puro'})      # só aceita hash
    assert acesso.receber_da_central({'usuario': 'nexopulse', 'senha_hash': generate_password_hash('da-central-1', method='pbkdf2:sha256')})
    assert acesso.credencial()[0::2] == ('nexopulse', 'central')                           # a da Central vale mais
    assert not acesso.conferir('1.2.3.4', 'campo', 'instalacao-123') and acesso.conferir('1.2.3.4', 'nexopulse', 'da-central-1')
    v = acesso.versao()
    assert not acesso.receber_da_central(edge_local.ler_config(str(tmp_path / 'c.db'), 'painel_credencial')) and acesso.versao() == v


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


def test_painel_configura_redes_extras(edge):
    cli, h, *_ = edge
    assert cli.get('/api/config/redes_extras', headers=h).json['redes'] == []
    r = cli.post('/api/config/redes_extras', headers=h, json={'redes': '192.168.10.0/24\n192.168.20.9/24\n192.168.10.0/24'})
    assert r.status_code == 200 and r.json['redes'] == ['192.168.10.0/24', '192.168.20.0/24']
    assert cli.get('/api/config/redes_extras', headers=h).json['redes'] == ['192.168.10.0/24', '192.168.20.0/24']
    assert [str(x) for x in agent.redes_extras()] == ['192.168.10.0/24', '192.168.20.0/24']
    ruim = cli.post('/api/config/redes_extras', headers=h, json={'redes': ['8.8.8.0/24']})
    assert ruim.status_code == 400 and '8.8.8.0/24' in ruim.json['erro']
    assert agent.app.test_client().post('/api/config/redes_extras', json={'redes': []}).status_code == 401


def test_central_manda_credencial_do_painel(client, monkeypatch):
    import app as central
    monkeypatch.setattr(central, 'PAINEL_LOCAL', {'usuario': 'nexopulse', 'senha_hash': 'pbkdf2:sha256:1$x$y'})
    r = client.post('/api/v2/report_data', json={'mac_id': MAC_EDGE, 'nome_local': 'Teste'})
    assert r.status_code == 200 and r.json['painel_local']['usuario'] == 'nexopulse'
    assert 'senha' not in json.dumps(r.json).replace('senha_hash', '')       # nunca a senha em texto
    monkeypatch.setattr(central, 'PAINEL_LOCAL', None)
    assert 'painel_local' not in client.post('/api/v2/report_data', json={'mac_id': MAC_EDGE, 'nome_local': 'Teste'}).json


def test_painel_entrega_dados_para_o_app_de_campo(edge):
    cli, _, _, _, ev = edge
    ev.registrar('Queda de Alvo', 'Google DNS (8.8.8.8) parou!', 'Crítica')
    ev.registrar('Alvo Restaurado', 'Google DNS (8.8.8.8) voltou.', 'OK')
    ev.registrar('Conectividade Central', 'Não consegue mais reportar telemetria à central: timeout', 'Crítica')
    ev.registrar('Speedtest', '187 Mbps', 'OK')                       # não é ocorrência de rede
    r = cli.get('/api/campo')
    assert r.status_code == 200
    d = r.json
    assert d['formato'] == 'nexopulse.edge-campo' and d['sensor']['mac_id'] == MAC_EDGE
    camera = [x for x in d['dispositivos'] if x['ip'] == '192.168.0.64'][0]
    assert camera['mac'] == HIK and camera['marca']['valor'] == 'Hikvision' and camera['modelo'] == 'DS-2CD2143G2-I'
    assert [o['tipo'] for o in d['ocorrencias']] == ['Conectividade Central', 'Alvo Restaurado', 'Queda de Alvo']
    assert d['quedas'] == 2
    # sem sessão, o app recebe 401 em JSON (e entra pelo /login como o navegador)
    anonimo = cli.application.test_client()
    assert anonimo.get('/api/campo').status_code == 401
