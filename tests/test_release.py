import io
import json
import re
import time
import urllib.error
from pathlib import Path
from unittest.mock import patch
import pytest
import database
import notifications as tg
import app as central
from location import parse_location


def test_legacy_report_and_migration(client, login):
    payload={'mac_id':'AA:01','ip_local':'192.168.1.5','cpu_usage':15,'ping_global':'{"Google":0.4}'}
    assert client.post('/api/v2/report_data',json=payload).status_code == 200
    login()
    data=client.get('/api/v2/sensor_data/AA:01').json
    assert data['latitude'] is None and data['lat'] is None and data['agent_version'] is None
    assert client.get('/sensor/AA:01').status_code == 200
    for route in ('/','/usuarios','/api/v2/graficos_ping/AA:01','/api/v2/graficos/AA:01','/api/v2/historico/AA:01','/api/v2/logs_globais'):
        assert client.get(route).status_code == 200,route
    database.init_db();database.init_db()
    assert client.get('/api/v2/sensor_data/AA:01').json['cpu_usage']==15
    info = client.get('/api/v2/agent_versao').json
    assert info['versao'] == central.CENTRAL_VERSION and info['platform'] == 'linux'
    assert 'url_download' not in info
    assert client.get('/healthz').json['central_version']=='2.2.0-rc1'


def test_dashboard_uses_real_online_sensor_measurements(client, login):
    first = {'Google': 10, 'Cloudflare': 20, 'AWS': 0, 'Quad9': 40}
    second = {'Google': 30, 'Cloudflare': 0, 'AWS': 0, 'Quad9': 60}
    stale = {'Google': 999, 'Cloudflare': 999, 'AWS': 999, 'Quad9': 999}
    for mac, pings in (('linux-01', first), ('windows-01', second), ('offline-01', stale)):
        response = client.post('/api/v2/report_data', json={
            'mac_id': mac, 'ping_global': json.dumps(pings),
        })
        assert response.status_code == 200

    conn = database.get_db()
    conn.execute("UPDATE sensores SET status='offline' WHERE mac_id='offline-01'")
    conn.commit(); conn.close()

    login()
    response = client.get('/api/v2/mapa_sensores')
    assert response.status_code == 200
    connectivity = response.json['connectivity']
    assert connectivity['Google'] == {
        'status': 'measured', 'latency_ms': 20.0,
        'sample_count': 2, 'no_icmp_count': 0,
    }
    assert connectivity['Cloudflare'] == {
        'status': 'measured', 'latency_ms': 20.0,
        'sample_count': 1, 'no_icmp_count': 1,
    }
    assert connectivity['AWS'] == {
        'status': 'no_icmp', 'latency_ms': None,
        'sample_count': 0, 'no_icmp_count': 2,
    }
    assert connectivity['Quad9']['latency_ms'] == 50.0


def test_login_version_is_beside_credentials(client):
    page = client.get('/login').get_data(as_text=True)
    assert '<div class="login-header">' in page
    assert '<span class="version-badge">v2.2.0-rc1</span>' in page
    assert 'class="product-bar"' not in page


def test_nexopulse_brand_is_local_and_consistent(client, login):
    login_page = client.get('/login').get_data(as_text=True)
    assert '<title>NexoHub - Acesso</title>' in login_page
    assert 'PORTAL DO CLIENTE' in login_page and 'MD Soluções Tecnológicas' in login_page
    assert '/static/brand/nexohub/logo-escuro.svg' in login_page
    assert '/static/brand/nexohub/favicon.svg' in login_page

    manifest = client.get('/static/manifest.json').json
    assert manifest['name'] == 'NexoHub'
    assert manifest['short_name'] == 'NexoHub'
    assert manifest['id'] == '/'
    assert manifest['scope'] == '/'
    assert manifest['start_url'] == '/'
    assert all(icon['src'].startswith('/static/brand/nexohub/') for icon in manifest['icons'])
    assert {'any', 'maskable'} == {icon['purpose'] for icon in manifest['icons']}

    root_manifest = client.get('/manifest.json')
    assert root_manifest.status_code == 200
    assert 'no-cache' in root_manifest.headers['Cache-Control']
    service_worker = client.get('/sw.js')
    assert service_worker.status_code == 200
    assert service_worker.headers['Service-Worker-Allowed'] == '/'
    assert 'no-cache' in service_worker.headers['Cache-Control']

    login()
    for route in ('/', '/tv', '/sensores', '/usuarios'):
        page = client.get(route)
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert 'NexoPulse' in html
        assert '/static/brand/' in html

    root = Path(__file__).resolve().parents[1]
    for template in (
        'index.html', 'login.html', 'sensor.html', 'sensores.html',
        'usuarios.html', 'tv.html', 'sensor_virtual.html', 'relatorio.html',
    ):
        central.app.jinja_env.get_template(template)
        source = (root / 'templates' / template).read_text(encoding='utf-8')
        assert 'name="viewport"' in source

    product_css = (root / 'static/product.css').read_text(encoding='utf-8')
    assert 'html[data-theme="light"]' in product_css
    assert '@media(max-width:480px)' in product_css

    brand_js = (root / 'static/brand.js').read_text(encoding='utf-8')
    assert "register('/sw.js?v=nexopulse-2'" in brand_js
    assert "updateViaCache: 'none'" in brand_js
    assert 'existing.unregister()' in brand_js

    # O agente é só Linux: não há mais executável Windows empacotado.
    assert not (root / 'agente_v2.spec').exists()

    for relative in (
        'static/brand/nexopulse/logo-escuro.svg',
        'static/brand/nexopulse/logo-claro.svg',
        'static/brand/nexopulse/simbolo-escuro.svg',
        'static/brand/nexopulse/icon-192.png',
        'static/brand/nexopulse/icon-512.png',
        'static/brand/nexopulse/maskable-192.png',
        'static/brand/nexopulse/maskable-512.png',
        'static/brand/nexopulse/apple-touch-icon.png',
    ):
        assert (root / relative).stat().st_size > 1000


def test_location_precision_and_old_agent(client,login):
    client.post('/api/v2/report_data',json={'mac_id':'A'})
    login()
    config={'mac_id':'A','nome':'Sensor','latitude':0,'longitude':0,'accuracy_m':8,'location_source':'browser'}
    assert client.post('/api/v2/configurar_sensor',json=config).status_code==200
    client.post('/api/v2/report_data',json={'mac_id':'A','latitude':30,'longitude':40,'location_source':'ip','agent_version':'2.2.0-rc1'})
    d=client.get('/api/v2/sensor_data/A').json
    assert d['lat']==0 and d['latitude']==0 and d['accuracy_m']==8 and d['location_source']=='browser'
    assert d['location_updated_at'] and d['agent_version']=='2.2.0-rc1'
    assert client.post('/api/v2/configurar_sensor',json={'mac_id':'A','nome':'Renamed'}).status_code==200
    assert client.get('/api/v2/sensor_data/A').json['accuracy_m']==8
    config.update(latitude=None,longitude=None)
    assert client.post('/api/v2/configurar_sensor',json=config).status_code==200
    assert client.get('/api/v2/sensor_data/A').json['latitude'] is None


@pytest.mark.parametrize('data',[{'lat':91,'lon':0},{'lat':0,'lon':181},{'lat':float('nan'),'lon':0},{'lat':1},{'lat':True,'lon':1},{'lat':0,'lon':0,'accuracy_m':-1},{'lat':1,'lon':2,'location_source':'browser'}])
def test_invalid_location(data):
    with pytest.raises(ValueError):parse_location(data)


def test_tenant_access(client,login):
    client.post('/api/v2/report_data',json={'mac_id':'foreign'})
    conn=database.get_db();conn.execute("UPDATE sensores SET cliente_id=3 WHERE mac_id='foreign'");conn.commit();conn.close()
    login(2,'Cliente')
    for route in ('/sensor/foreign','/api/v2/sensor_data/foreign','/api/v2/gerar_token_local/foreign','/api/v2/dispositivos/foreign'):
        assert client.get(route).status_code==403
    assert client.post('/api/v2/configurar_sensor',json={'mac_id':'foreign','nome':'hack','lat':1,'lon':1}).status_code==403
    assert client.put('/api/v2/usuarios/3/info',json={}).status_code==403
    assert client.post('/api/v2/usuarios',json={'role':'Administrador Master'}).status_code==403
    assert client.post('/api/v2/testar_telegram/0').status_code==403
    assert client.get('/api/v2/status_telegram/2').status_code==200
    assert client.get('/api/v2/mapa_sensores').json['sensores']==[]


def test_ingest_validation_and_compatibility(client,monkeypatch):
    for payload in ({},[],{'mac_id':{}},{'mac_id':''}):
        assert client.post('/api/v2/report_data',json=payload).status_code==400
    assert client.post('/api/v2/report_data',json={'mac_id':'old-windows'}).status_code==200
    monkeypatch.setattr(central,'NOC_SENSOR_API_KEY','test-key')
    assert client.post('/api/v2/report_data',json={'mac_id':'keyless'}).status_code==401
    assert client.post('/api/v2/report_data',json={'mac_id':'keyed'},headers={'X-NOC-Sensor-Key':'test-key'}).status_code==200


def test_individual_sensor_credential_rollout(client, login, monkeypatch):
    monkeypatch.setattr(central, 'NOC_SENSOR_API_KEY', '')
    monkeypatch.setattr(central, 'NOC_SENSOR_AUTH_REQUIRED', False)
    assert client.post('/api/v2/report_data', json={'mac_id': 'linux-01'}).status_code == 200

    login()
    created = client.post('/api/v2/sensor_credential/linux-01')
    assert created.status_code == 201
    token = created.json['sensor_api_key']
    assert len(token) >= 32 and created.json['key_prefix'] == token[:8]
    conn = database.get_db()
    stored = conn.execute('SELECT * FROM sensor_credentials WHERE sensor_mac=?', ('linux-01',)).fetchone()
    conn.close()
    assert stored['key_hash'] != token and stored['revoked_at'] is None
    sensors_page = client.get('/sensores').get_data(as_text=True)
    assert token not in sensors_page and created.json['key_prefix'] in sensors_page

    # Provisionar a chave não derruba agentes Windows/Linux antigos.
    with client.session_transaction() as browser_session:
        browser_session.clear()
    assert client.post('/api/v2/report_data', json={'mac_id': 'linux-01'}).status_code == 200

    monkeypatch.setattr(central, 'NOC_SENSOR_AUTH_REQUIRED', True)
    assert client.post('/api/v2/report_data', json={'mac_id': 'linux-01'}).status_code == 401
    assert client.post('/api/v2/report_data', json={'mac_id': 'linux-01'}, headers={
        'X-NOC-Sensor-ID': 'linux-01', 'X-NOC-Sensor-Key': 'wrong',
    }).status_code == 401
    assert client.post('/api/v2/report_data', json={'mac_id': 'linux-01'}, headers={
        'X-NOC-Sensor-ID': 'linux-01', 'X-NOC-Sensor-Key': token,
    }).status_code == 200
    assert client.post('/api/v2/report_data', json={'mac_id': 'linux-01'}, headers={
        'X-NOC-Sensor-ID': 'another-sensor', 'X-NOC-Sensor-Key': token,
    }).status_code == 400

    conn = database.get_db()
    own_id = conn.execute(
        "INSERT INTO ips_custom (sensor_mac, ip, descricao) VALUES ('linux-01', '10.0.0.1', 'own') RETURNING id"
    ).fetchone()['id']
    foreign_id = conn.execute(
        "INSERT INTO ips_custom (sensor_mac, ip, descricao) VALUES ('windows-02', '10.0.0.2', 'foreign') RETURNING id"
    ).fetchone()['id']
    conn.commit(); conn.close()
    auth_headers = {'X-NOC-Sensor-ID': 'linux-01', 'X-NOC-Sensor-Key': token}
    assert client.post('/api/v2/reportar_latencia_custom', json={
        'id': own_id, 'latencia': 2, 'mac_id': 'linux-01',
    }, headers=auth_headers).status_code == 200
    assert client.post('/api/v2/reportar_latencia_custom', json={
        'id': foreign_id, 'latencia': 2, 'mac_id': 'linux-01',
    }, headers=auth_headers).status_code == 403

    login()
    assert client.delete('/api/v2/sensor_credential/linux-01').status_code == 200
    with client.session_transaction() as browser_session:
        browser_session.clear()
    assert client.post('/api/v2/report_data', json={'mac_id': 'linux-01'}, headers={
        'X-NOC-Sensor-ID': 'linux-01', 'X-NOC-Sensor-Key': token,
    }).status_code == 401


def test_sensor_credential_is_master_only(client, login):
    client.post('/api/v2/report_data', json={'mac_id': 'windows-01'})
    login(2, 'Cliente')
    assert client.post('/api/v2/sensor_credential/windows-01').status_code == 403


def test_untrusted_sensor_text_is_rejected(client):
    response = client.post('/api/v2/registrar_sensor', json={
        'mac_id': 'malicious',
        'nome_local': '<img src=x onerror=alert(1)>',
    })
    assert response.status_code == 400
    response = client.post('/api/v2/alertas_ia', json={
        'mac_id': 'malicious',
        'alertas': [{'tipo': '<svg/onload=alert(1)>', 'gravidade': 'Crítica', 'detalhes': 'x'}],
    })
    assert response.status_code == 400


def test_security_headers_and_tv_escaping(client, login):
    response = client.get('/login')
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    login()
    tv = client.get('/tv').get_data(as_text=True)
    assert 'function escapeHtml(value)' in tv
    assert '${escapeHtml(s.nome_local' in tv
    assert '${escapeHtml(l.detalhes)}' in tv


def test_csrf_required_for_browser_mutations(monkeypatch):
    previous = central.app.config['CSRF_PROTECT']
    central.app.config['CSRF_PROTECT'] = True
    try:
        browser = central.app.test_client()
        assert browser.get('/login').status_code == 200
        with browser.session_transaction() as browser_session:
            token = browser_session['_csrf_token']
        assert browser.post('/login', data={'usuario': 'x', 'senha': 'x'}).status_code == 403
        assert browser.post('/login', data={
            'usuario': 'x', 'senha': 'x', '_csrf_token': token,
        }).status_code == 200
    finally:
        central.app.config['CSRF_PROTECT'] = previous


def test_duplicate_username_is_rejected(client, login):
    login()
    response = client.post('/api/v2/usuarios', json={
        'nome': 'Duplicado', 'usuario': 'user2', 'senha': 'test-only', 'role': 'Cliente',
    })
    assert response.status_code == 409


class Response:
    status=200
    def __init__(self,body):self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps(self.body).encode()

@pytest.mark.parametrize('code,description,expected,retry',[(401,'Unauthorized','invalid_token',False),(400,'Bad Request: chat not found','invalid_chat',False),(403,'bot was blocked by user','permissions',False),(429,'Too Many Requests','rate_limit',True),(503,'Unavailable','unavailable',True)])
def test_telegram_errors(code,description,expected,retry):
    error=urllib.error.HTTPError('https://api.telegram.org/botSECRET/sendMessage',code,'error',{},io.BytesIO(json.dumps({'ok':False,'description':description,'parameters':{'retry_after':30}}).encode()))
    with patch('urllib.request.urlopen',side_effect=error):result=tg.deliver('SECRET','1','test')
    assert result['code']==expected and result['transient']==retry
    assert result['retry_after']==30
    assert 'SECRET' not in json.dumps(result)


def test_telegram_network_redacted():
    with patch('urllib.request.urlopen',side_effect=urllib.error.URLError('botSECRET')):
        result=tg.deliver('SECRET','1','test')
    assert result['code']=='network' and 'SECRET' not in str(result)


def test_queue_retry_dedup_and_restart(client):
    assert tg.enqueue('incident')['queued']
    assert tg.enqueue('incident')['duplicate']
    with patch('notifications.deliver',return_value={'ok':False,'code':'network','transient':True,'retry_after':30}):
        for attempt in range(3):
            conn=database.get_db();conn.execute('UPDATE telegram_outbox SET next_at=0');conn.commit();conn.close()
            assert tg.process_one('token','chat')
            database.init_db()
    conn=database.get_db();row=conn.execute('SELECT * FROM telegram_outbox').fetchone();conn.close()
    assert row['attempts']==3 and row['state']=='failed'
    assert not tg.process_one('token','chat')
    status=tg.status(None,'token','chat')
    assert status['last_failure'] and status['status']=='Falha'


def test_success_status_and_permanent_failure(client):
    with patch('urllib.request.urlopen',return_value=Response({'ok':True,'result':{'message_id':12}})):
        tg.enqueue('ok');tg.process_one('token','chat')
    assert tg.status(None,'token','chat')['last_success']
    tg.enqueue('missing')
    tg.process_one('','')
    conn=database.get_db();assert conn.execute("SELECT attempts FROM telegram_outbox WHERE message='missing'").fetchone()['attempts']==1;conn.close()


def test_alert_outbox_atomic_no_sqlite_lock(client):
    client.post('/api/v2/report_data',json={'mac_id':'A'})
    response=client.post('/api/v2/alertas_ia',json={'mac_id':'A','alertas':[{'tipo':'Overheat','gravidade':'Crítica','detalhes':'High temperature'}]})
    assert response.status_code==200
    conn=database.get_db();assert conn.execute('SELECT COUNT(*) n FROM telegram_outbox').fetchone()['n']==2;conn.close()


def test_partial_tenant_configuration(client):
    conn=database.get_db();conn.execute("UPDATE clientes SET telegram_token='tenant-secret' WHERE id=2");conn.commit();conn.close()
    assert tg.credentials(2,'master','master-chat')==('tenant-secret','','cliente:2')


def test_cross_origin(client,login):
    login()
    assert client.post('/api/v2/ack_alerta',headers={'Origin':'https://evil.test'}).status_code==403


def test_user_insert_after_fixture_sequence(client,login):
    login()
    response=client.post('/api/v2/usuarios',json={'nome':'New client','usuario':'new-client','senha':'test-only','role':'Cliente'})
    assert response.status_code==200 and response.json['status']=='OK'


def test_migrate_legacy_coordinates_and_reapply(tmp_path,monkeypatch):
    import sqlite3
    path=str(tmp_path/'legacy.db')
    conn=sqlite3.connect(path)
    conn.execute('CREATE TABLE sensores (mac_id TEXT PRIMARY KEY, nome_local TEXT, ip_sensor TEXT, cpu_usage REAL, ram_usage REAL, temp REAL, status TEXT, lat REAL, lon REAL, ping_gateway REAL, ping_global TEXT)')
    conn.execute("INSERT INTO sensores(mac_id,lat,lon) VALUES ('placeholder',-14.235,-51.925),('equator',0,0)")
    conn.commit();conn.close()
    monkeypatch.setattr(database,'DATABASE_URL','')
    monkeypatch.setenv('NOC_DB_PATH',path)
    database.init_db();database.init_db()
    conn=database.get_db()
    placeholder=conn.execute("SELECT * FROM sensores WHERE mac_id='placeholder'").fetchone()
    equator=conn.execute("SELECT * FROM sensores WHERE mac_id='equator'").fetchone()
    assert placeholder['lat'] is None and placeholder['latitude'] is None
    assert equator['latitude']==0 and equator['location_source']=='legacy'
    assert equator['location_updated_at'] is None
    assert conn.execute('SELECT COUNT(*) n FROM location_migration_backup').fetchone()['n']==2
    conn.close()


@pytest.mark.parametrize('parameters', [[], 'invalid', {'retry_after': 'invalid'}, {'retry_after': None}, {'retry_after': float('inf')}])
def test_telegram_malformed_retry_does_not_hide_permanent_error(parameters):
    with patch('urllib.request.urlopen', return_value=Response({'ok': False, 'error_code': 403, 'description': 'Forbidden', 'parameters': parameters})):
        result = tg.deliver('SECRET', '1', 'test')
    assert result['code'] == 'permissions'
    assert result['transient'] is False
    assert result['retry_after'] == 2


@pytest.mark.parametrize('kind,expected', [('dns', 'dns'), ('tls', 'tls'), ('timeout', 'timeout'), ('connection', 'connection')])
def test_telegram_network_diagnostics_redact_secrets(kind, expected):
    import socket
    import ssl
    errors = {'dns': socket.gaierror('SECRET'), 'tls': ssl.SSLError('SECRET'),
              'timeout': TimeoutError('SECRET'), 'connection': ConnectionResetError('SECRET')}
    with patch('urllib.request.urlopen', side_effect=urllib.error.URLError(errors[kind])):
        result = tg.deliver('SECRET', '1', 'test')
    assert result['code'] == expected
    assert result['transient'] is True
    assert 'SECRET' not in str(result)


def test_queue_malformed_retry_is_terminal(client):
    tg.enqueue('invalid-response-permanent')
    with patch('urllib.request.urlopen', return_value=Response({'ok': False, 'error_code': 403, 'parameters': ['unexpected']})):
        assert tg.process_one('SECRET', '1')
    conn = database.get_db()
    row = conn.execute("SELECT state, attempts FROM telegram_outbox WHERE message='invalid-response-permanent'").fetchone()
    conn.close()
    assert row['state'] == 'failed' and row['attempts'] == 1


def _css_block(css, selector):
    start = css.index(selector + ' {')
    body = css[start:css.index('}', start)]
    return dict(re.findall(r'--([\w-]+):\s*(#[0-9A-Fa-f]{6})', body))


def _contrast(fg, bg):
    def luminance(hex_color):
        channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_status_palette_is_separate_from_brand_and_accessible():
    css = (Path(__file__).resolve().parents[1] / 'static/product.css').read_text(encoding='utf-8')
    dark = _css_block(css, ':root')
    light = {**dark, **_css_block(css, 'html[data-theme="light"]')}

    status = ('status-ok', 'status-warn', 'status-crit', 'status-offline', 'status-unknown')
    for theme in (dark, light):
        assert all(token in theme for token in status)
        # Verde Pulse é cor de marca e não pode representar "OK".
        assert theme['status-ok'].upper() not in {dark['brand-green'].upper(), theme['green'].upper()}
        assert theme['status-unknown'].upper() != theme['status-ok'].upper()
    assert '.status-unknown { color:var(--status-unknown); border-style:dashed;' in css

    text_tokens = ('text-main', 'text-muted', 'link', 'blue', 'green', 'red', 'yellow', 'purple', *status)
    for token in text_tokens:
        for background in ('bg-base', 'bg-card', 'bg-input', 'surface-raised'):
            assert _contrast(light[token], light[background]) >= 4.5, (token, background)
    assert light['link'].upper() == '#082E56'


def test_login_and_base_layout_accessibility(client):
    page = client.get('/login').get_data(as_text=True)
    assert '<label for="login-usuario">' in page and 'id="login-usuario"' in page
    assert '<label for="login-senha">' in page and 'id="login-senha"' in page
    assert 'autocomplete="username"' in page
    assert 'autocomplete="current-password"' in page
    assert 'var(--brand-gradient)' in page

    root = Path(__file__).resolve().parents[1]
    brand_js = (root / 'static/brand.js').read_text(encoding='utf-8')
    assert 'try { return localStorage.getItem(STORAGE_KEY); }' in brand_js
    assert 'meta[name="theme-color"]' in brand_js

    for template in (root / 'templates').glob('*.html'):
        source = template.read_text(encoding='utf-8')
        assert 'nexopulse-1' not in source, template.name
        if 'fonts.googleapis.com' in source:
            assert 'family=Inter:wght@400;600;700' in source, template.name
            assert 'family=JetBrains+Mono:wght@400;700' in source, template.name


def test_unverified_sensor_state_is_never_shown_as_healthy(client, login):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local) VALUES ('AA:NOVO', 'Sem relatorio')")
    conn.execute("INSERT INTO sensores (mac_id, nome_local, status) VALUES ('AA:ON', 'Ativo', 'online')")
    conn.commit(); conn.close()
    login()
    page = client.get('/sensores').get_data(as_text=True)
    assert 'b-unknown' in page and 'SEM DADOS' in page
    assert page.count('status-badge b-on">ONLINE') == 1

    root = Path(__file__).resolve().parents[1]
    sensor = (root / 'templates/sensor.html').read_text(encoding='utf-8')
    # Antes da primeira leitura, nada aparece como estável/online.
    assert '<span id="status-local" class="status-pill pill-unknown">AGUARDANDO</span>' in sensor
    assert '<span id="status-wan" class="status-pill pill-unknown">AGUARDANDO</span>' in sensor
    assert "data.ping_gateway || 0" not in sensor
    assert "let cor = item.latencia === 0 ? 'var(--red)' : 'var(--green)'" not in sensor

    for name in ('index.html', 'sensor.html', 'sensores.html', 'sensor_virtual.html'):
        source = (root / 'templates' / name).read_text(encoding='utf-8').lower()
        # Paleta antiga (Catppuccin) fora das telas operacionais.
        for legacy in ('#a6e3a1', '#f38ba8', '#f9e2af', '#89b4fa', '#cdd6f4', '#0b0b13', 'rgba(166,227,161', 'rgba(243,139,168'):
            assert legacy not in source, (name, legacy)


def test_report_and_tv_make_no_unverified_claims(client, login):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, status) VALUES ('AA:REL', 'Relatorio', 'online')")
    conn.execute("INSERT INTO dispositivos (sensor_mac, ip, mac, fabricante, status) VALUES ('AA:REL', '10.0.0.7', 'BB:01', 'Teste', 'desconhecido')")
    conn.commit(); conn.close()
    login()
    report = client.get('/relatorio/AA:REL').get_data(as_text=True)
    assert report.count('Relatorio') >= 1
    assert '100% estável' not in report
    assert 'auto-cura' not in report and 'atesta os níveis de serviço' not in report
    assert 'Dispositivos Descobertos' in report
    assert '<span class="badge desconhecido">Desconhecido</span>' in report
    assert 'color-scheme: light' in report

    root = Path(__file__).resolve().parents[1]
    tv = (root / 'templates/tv.html').read_text(encoding='utf-8')
    assert "} else if (!s.status) {" in tv and "classe = 'unknown'; text = 'SEM DADOS';" in tv
    for name in ('tv.html', 'usuarios.html', 'relatorio.html'):
        source = (root / 'templates' / name).read_text(encoding='utf-8').lower()
        for legacy in ('#a6e3a1', '#f38ba8', '#f9e2af', '#89b4fa', '#cdd6f4', '#0b0b13', '#10b981', '#3b82f6'):
            assert legacy not in source, (name, legacy)
