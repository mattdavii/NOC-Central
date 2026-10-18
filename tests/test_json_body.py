def test_get_with_json_content_type_and_no_body_is_accepted(client):
    # O agente envia Content-Type: application/json em todas as chamadas.
    response = client.get('/api/v2/ips_customizados/AA:BB:CC:DD:EE:FF',
                          headers={'Content-Type': 'application/json', 'X-NOC-Sensor-ID': 'AA:BB:CC:DD:EE:FF'})
    assert response.status_code == 200


def test_post_with_invalid_json_is_still_rejected(client):
    response = client.post('/api/v2/report_data', data='[]', content_type='application/json')
    assert response.status_code == 400


def _nome(mac):
    import database
    conn = database.get_db()
    row = conn.execute('SELECT nome_local FROM sensores WHERE mac_id=?', (mac,)).fetchone()
    conn.close()
    return row['nome_local']


def test_new_sensor_is_named_after_reported_hostname(client):
    assert client.post('/api/v2/report_data', json={'mac_id': 'AA:00:00:00:00:01', 'nome_local': 'pdv-loja-03'}).status_code == 200
    assert _nome('AA:00:00:00:00:01') == 'pdv-loja-03'


def test_placeholder_name_is_replaced_but_custom_name_is_kept(client, login):
    client.post('/api/v2/report_data', json={'mac_id': 'AA:00:00:00:00:02'})
    assert _nome('AA:00:00:00:00:02') == 'Novo Sensor'
    client.post('/api/v2/report_data', json={'mac_id': 'AA:00:00:00:00:02', 'nome_local': 'servidor-backup'})
    assert _nome('AA:00:00:00:00:02') == 'servidor-backup'
    login().post('/api/v2/configurar_sensor', json={'mac_id': 'AA:00:00:00:00:02', 'nome': 'Rack principal'})
    client.post('/api/v2/report_data', json={'mac_id': 'AA:00:00:00:00:02', 'nome_local': 'servidor-backup'})
    assert _nome('AA:00:00:00:00:02') == 'Rack principal'
