import io
import json
import time
import urllib.error
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
    assert client.get('/api/v2/agent_versao').json['versao']=='2.1.0'
    assert client.get('/healthz').json['central_version']=='2.2.0-rc1'


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
