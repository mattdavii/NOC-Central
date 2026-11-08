import time

import app as central
import database
import saude


def _conn():
    return database.get_db()


def _limpar():
    conn = _conn()
    conn.execute('DELETE FROM saude_estado')
    conn.execute('DELETE FROM config_sistema')
    conn.commit()
    return conn


def test_config_defaults_and_telegram_destination_go_to_db(client):
    conn = _limpar()
    saude.garantir_config(conn, 'TOKEN-DE-TESTE', '-100123')
    cfg = saude.ler_config(conn)
    assert cfg['vigia_intervalo_min'] == '3' and cfg['falhas_para_alertar'] == '2'
    assert 'telegram_bot_token' not in cfg  # o token não vai para a página
    linhas = {r['chave']: r['valor'] for r in conn.execute("SELECT chave, valor FROM config_sistema WHERE chave LIKE 'telegram_%'").fetchall()}
    assert linhas == {'telegram_bot_token': 'TOKEN-DE-TESTE', 'telegram_chat_id': '-100123'}
    # valor editado não é sobrescrito no próximo boot
    assert saude.salvar_config(conn, {'vigia_intervalo_min': 5}) == (True, None)
    saude.garantir_config(conn)
    assert saude.ler_config(conn)['vigia_intervalo_min'] == '5'
    conn.close()


def test_config_validation(client):
    conn = _limpar()
    assert saude.salvar_config(conn, {'vigia_intervalo_min': 1})[0] is False  # mínimo 2
    assert saude.salvar_config(conn, {'vigia_intervalo_min': 6})[0] is False  # máximo 5
    assert saude.salvar_config(conn, {'telegram_bot_token': 'x'})[0] is False
    assert saude.salvar_config(conn, {'falhas_para_alertar': 'abc'})[0] is False
    conn.close()


def test_alert_only_after_consecutive_failures_and_once(client):
    conn = _limpar()
    cfg = saude.ler_config(conn)
    enviados = []
    ruim = [{'chave': 'banco_latencia', 'origem': 'central', 'rotulo': 'Latência do banco', 'estado': 'critico', 'valor': '4000 ms', 'detalhe': ''}]
    bom = [{**ruim[0], 'estado': 'ok', 'valor': '200 ms'}]
    assert saude.aplicar(conn, ruim, cfg, enviados.append) == []  # 1ª falha: ainda não
    assert len(saude.aplicar(conn, ruim, cfg, enviados.append)) == 1  # 2ª seguida: alerta
    assert saude.aplicar(conn, ruim, cfg, enviados.append) == []  # já avisado
    assert 'SAÚDE DO SERVIDOR' in enviados[0] and '4000 ms' in enviados[0]
    normal = saude.aplicar(conn, bom, cfg, enviados.append)
    assert len(normal) == 1 and 'NORMALIZADO' in normal[0]
    estado = conn.execute("SELECT falhas, notificado, desde FROM saude_estado WHERE chave='banco_latencia'").fetchone()
    assert (estado['falhas'], estado['notificado'], estado['desde']) == (0, 0, None)
    conn.close()


def test_one_failure_then_ok_never_alerts(client):
    conn = _limpar()
    cfg = saude.ler_config(conn)
    enviados = []
    c = {'chave': 'memoria', 'origem': 'central', 'rotulo': 'Memória', 'estado': 'alerta', 'valor': '90%', 'detalhe': ''}
    saude.aplicar(conn, [c], cfg, enviados.append)
    saude.aplicar(conn, [{**c, 'estado': 'ok'}], cfg, enviados.append)
    saude.aplicar(conn, [c], cfg, enviados.append)
    assert enviados == []
    conn.close()


def test_collect_reports_core_checks_and_stale_vigia(client):
    conn = _limpar()
    saude.garantir_config(conn)
    chaves = {c['chave']: c for c in saude.coletar(conn, {'guardiao_ligado': True, 'guardiao_idade_s': 999, 'guardiao_intervalo_s': 15})}
    assert {'banco_latencia', 'telegram_fila', 'guardiao', 'vigia', 'sensores'} <= set(chaves)
    assert chaves['guardiao']['estado'] == 'critico' and chaves['vigia']['estado'] == 'na'
    conn.execute("INSERT INTO config_sistema (chave, valor, atualizado_em) VALUES ('vigia_ultimo_batimento', ?, 0)", (str(time.time() - 3600),))
    conn.commit()
    vigia = next(c for c in saude.coletar(conn) if c['chave'] == 'vigia')
    assert vigia['estado'] == 'alerta' and 'parou' in vigia['detalhe']
    conn.close()


def test_health_page_and_api_are_master_only(client, login):
    _limpar().close()
    cli = login(2, 'Cliente')
    assert cli.get('/saude').status_code == 403
    assert cli.get('/api/v2/saude').status_code == 403
    assert cli.post('/api/v2/saude/config', json={'vigia_intervalo_min': 4}).status_code == 403
    master = login()
    assert master.get('/saude').status_code == 200
    dados = master.get('/api/v2/saude').get_json()
    assert dados['central'] and 'telegram_bot_token' not in dados['config']
    assert master.post('/api/v2/saude/config', json={'vigia_intervalo_min': 4}).get_json()['config']['vigia_intervalo_min'] == '4'
    assert master.post('/api/v2/saude/config', json={'vigia_intervalo_min': 9}).status_code == 400


def test_health_alerts_go_only_to_master_group(client):
    conn = _limpar()
    conn.execute('DELETE FROM telegram_outbox')
    conn.commit()
    conn.execute("INSERT INTO saude_estado (chave, origem, rotulo, estado, falhas, notificado) VALUES ('guardiao', 'central', 'Guardião de quedas', 'critico', 5, 0)")
    conn.commit()
    central.GUARDIAO_LIGADO = True
    central._guardiao_ultima_execucao_ts = time.time() - 9999
    try:
        central.verificar_saude(conn)
    finally:
        central.GUARDIAO_LIGADO = False
    msgs = conn.execute('SELECT client_id, message FROM telegram_outbox').fetchall()
    conn.close()
    assert [m['client_id'] for m in msgs] == [None] and '<b>SAÚDE DO SERVIDOR</b>' in msgs[0]['message']
