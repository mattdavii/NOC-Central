"""Banco do outro lado da tailnet: requisições que não precisam do banco não abrem conexão."""
import database
import app as central


def test_arquivos_do_visual_nao_consultam_o_banco(login, monkeypatch):
    cliente = login()
    chamadas = []
    original = database.get_db
    monkeypatch.setattr(database, 'get_db', lambda: chamadas.append(1) or original())
    for caminho in ('/static/product.css', '/manifest.json', '/sw.js'):
        assert cliente.get(caminho).status_code in (200, 304)
    assert chamadas == []


def test_situacao_da_conta_fica_guardada_e_escrita_limpa(login, monkeypatch):
    cliente = login(2, 'Cliente')
    cliente.get('/api/v2/mapa_sensores')
    assert 2 in central._cache_sessao
    # Desativar direto no banco só vale depois do cache; por uma escrita da Central vale na hora.
    conn = database.get_db(); conn.execute('UPDATE clientes SET ativo=0 WHERE id=2'); conn.commit(); conn.close()
    cliente.post('/api/v2/qualquer_coisa', json={})   # qualquer escrita limpa a situação guardada
    assert 2 not in central._cache_sessao
    assert cliente.get('/api/v2/mapa_sensores').status_code == 401
