import app as central


def test_socketio_connect_uses_flask_session(client, login):
    # Regression: Flask >= 3.1.3 made RequestContext.session read-only and
    # Flask-SocketIO < 5.6.1 crashed assigning it ("message handler error").
    login()
    sio = central.socketio.test_client(central.app, flask_test_client=client)
    assert sio.is_connected()
    central.socketio.emit('atualizacao_global', {})
    assert any(m['name'] == 'atualizacao_global' for m in sio.get_received())
    sio.disconnect()


def test_socketio_rejects_anonymous_connection(client):
    sio = central.socketio.test_client(central.app, flask_test_client=client)
    assert not sio.is_connected()
