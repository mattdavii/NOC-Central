import app as central
import database
import notifications as tg


def _outbox():
    conn = database.get_db()
    rows = [dict(r) for r in conn.execute('SELECT client_id, message FROM telegram_outbox').fetchall()]
    conn.close()
    # Postgres e SQLite ordenam NULL em lados opostos: o master (None) vem primeiro aqui.
    return sorted(rows, key=lambda r: (r['client_id'] is not None, r['client_id'] or 0))


def _set_chat(client_id, chat, token=''):
    conn = database.get_db()
    conn.execute('UPDATE clientes SET nome=?, telegram_chat_id=?, telegram_token=? WHERE id=?', (f'Loja {client_id}', chat, token, client_id))
    conn.commit()
    conn.close()


def test_sensor_without_client_goes_only_to_master(client):
    central.enviar_telegram('queda X')
    assert [(r['client_id'], r['message']) for r in _outbox()] == [(None, 'queda X')]


def test_client_sensor_goes_to_master_with_client_name_and_to_client_group(client):
    _set_chat(2, '-100222')
    central.enviar_telegram('queda Y', cliente_id=2)
    rows = _outbox()
    assert [r['client_id'] for r in rows] == [None, 2]
    assert 'Loja 2' in rows[0]['message'] and rows[1]['message'] == 'queda Y'


def test_client_without_chat_is_not_notified(client):
    central.enviar_telegram('queda Z', cliente_id=3)
    assert [r['client_id'] for r in _outbox()] == [None]


def test_client_group_uses_nexopulse_bot_when_it_has_no_own_token(client):
    _set_chat(2, '-100222')
    assert tg.credentials(2, 'master-token', 'master-chat') == ('master-token', '-100222', 'cliente:2')
    _set_chat(3, '-100333', token='bot-proprio')
    assert tg.credentials(3, 'master-token', 'master-chat') == ('bot-proprio', '-100333', 'cliente:3')
    assert tg.credentials(None, 'master-token', 'master-chat') == ('master-token', 'master-chat', 'master')
