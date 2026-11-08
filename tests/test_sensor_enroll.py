import database

CHAVE = 'k' * 43


def _post(client, mac, chave=None):
    headers = {'X-NOC-Sensor-ID': mac}
    if chave:
        headers['X-NOC-Sensor-Key'] = chave
    return client.post('/api/v2/report_data', json={'mac_id': mac}, headers=headers)


def test_first_contact_binds_key_and_then_requires_it(client):
    assert _post(client, 'AA:00:00:00:10:01', CHAVE).status_code == 200
    assert _post(client, 'AA:00:00:00:10:01', CHAVE).status_code == 200
    assert _post(client, 'AA:00:00:00:10:01', 'x' * 43).status_code == 401
    conn = database.get_db()
    mensagens = [r['message'] for r in conn.execute('SELECT message FROM telegram_outbox').fetchall()]
    conn.close()
    assert any('SENSOR VINCULADO' in m for m in mensagens)


def test_revoked_key_is_never_rebound(client):
    _post(client, 'AA:00:00:00:10:02', CHAVE)
    conn = database.get_db()
    conn.execute('UPDATE sensor_credentials SET revoked_at=CURRENT_TIMESTAMP WHERE sensor_mac=?', ('AA:00:00:00:10:02',))
    conn.commit(); conn.close()
    assert _post(client, 'AA:00:00:00:10:02', 'y' * 43).status_code == 401
    assert _post(client, 'AA:00:00:00:10:02', CHAVE).status_code == 401
