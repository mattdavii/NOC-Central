import time
from unittest.mock import patch

import app as central
import notifications as tg


def test_healthz_reports_unavailable_database_without_500(client):
    with patch.object(central.database, 'get_db', side_effect=RuntimeError('quota exceeded')):
        response = client.get('/healthz')
    assert response.status_code == 503
    assert response.json['status'] == 'db_unavailable'
    assert 'quota' not in response.get_data(as_text=True)
    assert client.get('/healthz').json['status'] == 'ok'


def test_telegram_queue_wakes_worker_instead_of_polling(client):
    tg._wakeup.clear()
    started = time.monotonic()
    tg.wait_for_work(0.05)
    assert time.monotonic() - started >= 0.04
    tg.enqueue('Evento de teste para acordar o worker')
    started = time.monotonic()
    tg.wait_for_work(5)
    assert time.monotonic() - started < 1
    assert not tg._wakeup.is_set()
