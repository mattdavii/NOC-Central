import json
import database

MAC = 'AA:00:00:00:30:01'


def _info(**extra):
    base = {'sistema': {'boot_time': 1_000_000, 'uptime_s': 60}, 'discos': [{'ponto': '/', 'percentual': 40, 'usado_gb': 40, 'total_gb': 100}],
            'swap': {'total_mb': 1000, 'percentual': 5}, 'servicos_falha': [], 'dns': {'ok': True}, 'hora_sincronizada': True}
    base.update(extra)
    return base


def _report(client, info):
    return client.post('/api/v2/report_data', json={'mac_id': MAC, 'host_info': info})


def _eventos():
    conn = database.get_db()
    logs = [r['detalhes'] for r in conn.execute("SELECT detalhes FROM logs_ia WHERE sensor_mac=? AND tipo_evento IN ('Saúde do host','Máquina reiniciada') ORDER BY id", (MAC,)).fetchall()]
    tg = [r['message'] for r in conn.execute('SELECT message FROM telegram_outbox').fetchall()]
    sensor = dict(conn.execute('SELECT host_info, alertas_host FROM sensores WHERE mac_id=?', (MAC,)).fetchone())
    conn.close()
    return logs, tg, sensor


def test_host_info_is_stored(client):
    assert _report(client, _info()).status_code == 200
    _, _, sensor = _eventos()
    assert json.loads(sensor['host_info'])['discos'][0]['ponto'] == '/'
    assert json.loads(sensor['alertas_host']) == []


def test_alert_fires_once_and_normalizes(client):
    cheio = _info(discos=[{'ponto': '/', 'percentual': 95, 'usado_gb': 95, 'total_gb': 100}], servicos_falha=['nginx.service'])
    _report(client, cheio)
    _report(client, cheio)  # mesmo problema: não repete
    logs, tg, sensor = _eventos()
    assert sum('Disco / em 95' in l for l in logs) == 1
    assert sum('nginx.service' in l for l in logs) == 1
    assert sorted(json.loads(sensor['alertas_host'])) == ['disco:/', 'servico:nginx.service']
    _report(client, _info())
    logs, tg, _ = _eventos()
    assert any('Normalizado: /' in l for l in logs)
    assert any('HOST NORMALIZADO' in m for m in tg)


def test_reboot_is_detected(client):
    _report(client, _info())
    _report(client, _info(sistema={'boot_time': 1_000_000 + 3600, 'uptime_s': 30}))
    logs, tg, _ = _eventos()
    assert any('foi reiniciada' in l for l in logs)
    assert any('MÁQUINA REINICIOU' in m for m in tg)


def test_oversized_host_info_is_ignored(client):
    assert _report(client, _info(lixo='x' * 40000)).status_code == 200
    _, _, sensor = _eventos()
    assert sensor['host_info'] is None
