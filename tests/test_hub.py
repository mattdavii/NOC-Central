"""NexoHub PR3: página inicial (cliente e MD), "ver como cliente", vitrine e menu por aplicativo."""
import time

import acessos
import database


def _cenario():
    conn = database.get_db()
    conn.execute("UPDATE clientes SET nome='Alfa Engenharia' WHERE id=2")
    conn.execute("UPDATE clientes SET nome='Beta Logistica' WHERE id=3")
    for mac, nome, status, cliente, manut in (('AA:01', 'Alfa Matriz', 'online', 2, 0), ('AA:02', 'Alfa Filial', 'offline', 2, 0),
                                               ('AA:03', 'Alfa Deposito', 'offline', 2, 1), ('BB:01', 'Beta Galpao', 'online', 3, 0),
                                               ('CC:01', 'Sensor Solto', 'online', None, 0)):
        conn.execute('INSERT INTO sensores (mac_id, nome_local, status, cliente_id, em_manutencao) VALUES (?, ?, ?, ?, ?)',
                     (mac, nome, status, cliente, manut))
    agora = time.time()
    conn.execute("INSERT INTO diagnosticos (sensor_mac, chave, causa, gravidade, inicio, estado) VALUES ('AA:02', 'sem_comunicacao', 'Site sem comunicação', 'Crítica', ?, 'ativo')", (agora - 600,))
    conn.execute("INSERT INTO diagnosticos (sensor_mac, chave, causa, gravidade, inicio, estado) VALUES ('BB:01', 'perda_pacotes', 'Perda de pacotes no provedor', 'Alerta', ?, 'ativo')", (agora - 60,))
    conn.commit(); conn.close()


def test_login_leva_ao_hub_e_operacao_em_pulse(client):
    from werkzeug.security import generate_password_hash
    conn = database.get_db()
    conn.execute('UPDATE clientes SET senha=? WHERE id=2', (generate_password_hash('senha-de-teste-1'),))
    conn.commit(); conn.close()
    r = client.post('/login', data={'usuario': 'user2', 'senha': 'senha-de-teste-1'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/')
    assert client.get('/pulse').status_code == 200


def test_cliente_ve_so_os_proprios_sites(login):
    _cenario()
    page = login(2, 'Cliente').get('/').get_data(as_text=True)
    assert 'Alfa Engenharia' in page and 'Abrir a Operação' in page
    assert 'Alfa Filial' in page and 'Site sem comunicação' in page
    # Nada de outro cliente (resumos inclusive) nem da visão da MD.
    assert 'Beta' not in page and 'Perda de pacotes' not in page and 'Sensor Solto' not in page
    assert 'Ver como cliente' not in page


def test_vitrine_e_em_breve(login):
    page = login(2, 'Cliente').get('/').get_data(as_text=True)
    assert 'Conheça o NexoGuard' in page and 'Conheça o NexoReport' in page
    conn = database.get_db()
    acessos.definir_contrato(conn, 2, 'guard', True)
    conn.commit(); conn.close()
    page = login(2, 'Cliente').get('/').get_data(as_text=True)
    assert 'Conheça o NexoGuard' not in page and 'Em breve' in page


def test_sem_acesso_esconde_card_e_menu(login):
    conn = database.get_db()
    conn.execute("INSERT INTO clientes (id, usuario, senha, role, ativo, cliente_pai_id) VALUES (5, 'user5', 'x', 'Operador Cliente', 1, 2)")
    acessos.definir_nivel(conn, 5, 'pulse', 'nenhum')
    conn.commit(); conn.close()
    page = login(5, 'Operador Cliente').get('/').get_data(as_text=True)
    assert 'Abrir a Operação' not in page and 'id="menu-pulse"' not in page
    assert 'Nenhum aplicativo liberado' in page or 'Conheça' in page


def test_visao_da_md(login):
    _cenario()
    page = login(1).get('/').get_data(as_text=True)
    assert 'Precisa de atenção' in page and 'Últimos eventos' in page and 'até agora' in page
    assert 'Alfa Engenharia' in page and 'Beta Logistica' in page
    assert '/?ver_como=2' in page and '/?ver_como=3' in page
    assert '1 sensor sem empresa' in page  # o Sensor Solto, como item de atenção
    conn = database.get_db()
    import hub
    linhas = {l['nome']: l for l in hub.visao_md(conn)}
    total = hub.resumo_pulse(conn)
    conn.close()
    assert (linhas['Alfa Engenharia']['online'], linhas['Alfa Engenharia']['offline'], linhas['Alfa Engenharia']['manutencao']) == (1, 1, 1)
    assert linhas['Alfa Engenharia']['alertas'] == 1 and linhas['Beta Logistica']['alertas'] == 1
    assert list(linhas)[0] == 'Alfa Engenharia'  # quem tem site offline vem primeiro
    assert total['total_diagnosticos'] == 2


def test_ver_como_cliente(login):
    _cenario()
    page = login(1).get('/?ver_como=2').get_data(as_text=True)
    assert 'Você está vendo o NexoHub como' in page and 'Alfa Engenharia' in page
    assert 'Alfa Filial' in page and 'Beta' not in page
    assert 'Abrir a Operação' not in page  # só consulta
    assert login(1).get('/?ver_como=999').status_code == 302
    # Para quem não é da MD o parâmetro é ignorado.
    page = login(3, 'Cliente').get('/?ver_como=2').get_data(as_text=True)
    assert 'Alfa' not in page and 'Você está vendo' not in page


def test_menu_por_aplicativo(login):
    page = login(1).get('/pulse').get_data(as_text=True)
    assert 'id="menu-pulse"' in page and 'href="/sensores"' in page and 'href="/saude"' in page
    page = login(4, 'Administrador Cliente').get('/').get_data(as_text=True)
    assert 'id="menu-pulse"' in page and 'href="/sensores"' not in page
    assert 'href="/usuarios"' in page and 'href="/saude"' not in page
    login(1).post('/api/v2/acessos/empresas/2/delegacao', json={'ativo': False})
    page = login(4, 'Administrador Cliente').get('/').get_data(as_text=True)
    assert 'href="/usuarios"' not in page and 'id="menu-admin"' not in page


def test_identidade_nexohub(client, login):
    manifest = client.get('/manifest.json').get_json()
    # O id não muda: quem já instalou o app recebe a atualização no mesmo app.
    assert (manifest['id'], manifest['name'], manifest['start_url']) == ('/', 'NexoHub', '/')
    assert {(a['short_name'], a['url']) for a in manifest['shortcuts']} == {('Operação', '/pulse'), ('Sala', '/tv')}
    login(1)
    hub_page = client.get('/').get_data(as_text=True)
    assert '/static/brand/nexohub/logo-escuro.svg' in hub_page and 'por MD Soluções' in hub_page
    assert '/static/brand/nexohub/favicon.svg' in hub_page
    # Páginas do módulo NexoPulse: marca do portal na barra, favicon da NexoPulse na aba.
    pulse_page = client.get('/pulse').get_data(as_text=True)
    assert '<title>NexoPulse - Operação</title>' in pulse_page
    assert '/static/brand/nexohub/logo-escuro.svg' in pulse_page and '/static/brand/nexopulse/favicon.svg' in pulse_page
    acessos_page = client.get('/usuarios').get_data(as_text=True)
    assert '<title>NexoHub - Central de Acessos</title>' in acessos_page


def test_disponibilidade_do_mes_conta_quedas_cortadas_no_inicio_do_mes(client):
    import hub
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, status, cliente_id, em_manutencao) VALUES ('DD:01', 'Delta A', 'online', 2, 0)")
    conn.execute("INSERT INTO sensores (mac_id, nome_local, status, cliente_id, em_manutencao) VALUES ('DD:02', 'Delta B', 'online', 2, 0)")
    conn.execute("INSERT INTO sensores (mac_id, nome_local, status, cliente_id, em_manutencao) VALUES ('DD:03', 'Delta C', 'offline', 2, 1)")
    inicio, mes = hub.inicio_do_mes(time.time())
    agora = inicio + 10 * 86400  # dia 11, 00:00 de Brasília
    sql = "INSERT INTO diagnosticos (sensor_mac, chave, causa, gravidade, inicio, fim, estado) VALUES (?, 'sem_comunicacao', 'x', 'Crítica', ?, ?, ?)"
    # Começou no mês anterior: só conta a partir do dia 1 (2 h).
    conn.execute(sql, ('DD:01', inicio - 3600, inicio + 7200, 'resolvido'))
    # Registro duplicado sobreposto ao primeiro não conta duas vezes.
    conn.execute(sql, ('DD:01', inicio + 3600, inicio + 5400, 'resolvido'))
    # Em andamento: conta até agora (4 h).
    conn.execute(sql, ('DD:02', agora - 4 * 3600, None, 'ativo'))
    # Site em manutenção fica fora da conta.
    conn.execute(sql, ('DD:03', inicio, None, 'ativo'))
    conn.commit()
    d = hub.disponibilidade_mes(conn, 2, agora=agora)
    conn.close()
    fora = 6 * 3600
    assert d['fora_s'] == fora and d['fora_txt'] == '6 h'
    esperado = 100 * (1 - fora / (2 * 10 * 86400))  # 98,75%
    assert d['pct'] == 98.7 and abs(d['pct'] - esperado) < 0.1
    assert d['faixa'] == 'atencao' and d['mes'] == mes


def test_disponibilidade_nunca_mostra_100_com_queda_e_some_sem_sites(client):
    import hub
    conn = database.get_db()
    assert hub.disponibilidade_mes(conn, 2) is None
    conn.execute("INSERT INTO sensores (mac_id, nome_local, status, cliente_id, em_manutencao) VALUES ('EE:01', 'Eco', 'online', 2, 0)")
    inicio, _ = hub.inicio_do_mes(time.time())
    agora = inicio + 20 * 86400
    conn.execute("INSERT INTO diagnosticos (sensor_mac, chave, causa, gravidade, inicio, fim, estado) VALUES ('EE:01', 'sem_comunicacao', 'x', 'Crítica', ?, ?, 'resolvido')",
                 (agora - 300, agora - 200))
    conn.commit()
    d = hub.disponibilidade_mes(conn, 2, agora=agora)
    conn.close()
    assert d['pct'] == 99.9 and d['faixa'] == 'ok' and d['fora_txt'] == '1 min'


def test_hub_mostra_disponibilidade_no_card_e_na_visao_md(login):
    _cenario()
    page = login(2, 'Cliente').get('/').get_data(as_text=True)
    assert 'de disponibilidade em' in page and 'sem comunicação' in page
    md = login(1, 'Administrador Master').get('/').get_data(as_text=True)
    assert 'Disponibilidade no mês' in md and 'data-ordem="disp"' in md and '/usuarios#empresas' in md
    assert 'window.location.reload' not in md
    # Versão (rc) só para a MD.
    assert 'product-version' in md and 'product-version' not in page


def test_disponibilidade_de_site_instalado_no_meio_do_mes(client):
    import hub
    agora = time.time()
    inicio, _ = hub.inicio_do_mes(agora)
    if agora - inicio < 3 * 3600:
        import pytest
        pytest.skip('primeiras horas do mês: o site "instalado há 2 h" cairia no mês anterior')
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, status, cliente_id) VALUES ('N1', 'Novo', 'online', 2)")
    instalado = agora - 2 * 3600
    conn.execute("INSERT INTO medicoes_rede (sensor_mac, coletado_em, tipo) VALUES ('N1', ?, 'ping')", (instalado,))
    conn.execute("INSERT INTO diagnosticos (sensor_mac, chave, causa, gravidade, inicio, fim, estado) "
                 "VALUES ('N1', 'sem_comunicacao', 'Site sem comunicação', 'Crítica', ?, ?, 'resolvido')", (agora - 3600, agora - 2400))
    conn.commit()
    d = hub.disponibilidade_mes(conn, 2, agora=agora)
    conn.close()
    # 20 min fora em 2 h monitoradas (não no mês inteiro): 83,3%.
    assert 83.0 <= d['pct'] <= 83.4


def test_inicio_da_md_lista_o_que_precisa_de_atencao(client):
    import hub
    _cenario()
    conn = database.get_db()
    conn.execute("UPDATE sensores SET agent_build='antigo' WHERE mac_id='BB:01'")
    conn.commit()
    itens = hub.precisa_de_atencao(conn, build_atual='novo')
    conn.close()
    titulos = [i['titulo'] for i in itens]
    # Diagnósticos ativos (a queda do AA:02 não se repete como "Sensor offline"), sensor solto e versão antiga.
    assert 'Site sem comunicação' in titulos and 'Perda de pacotes no provedor' in titulos
    assert titulos.count('Sensor offline') == 0
    assert '1 sensor sem empresa' in titulos and '1 sensor com versão antiga' in titulos
    assert itens[0]['nivel'] == 'critico' and itens[-1]['nivel'] == 'info'
    assert all(i['link'].startswith('/') for i in itens)


def test_grafico_do_mes_tem_um_item_por_dia(client):
    import hub
    agora = time.time()
    inicio, _ = hub.inicio_do_mes(agora)
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, status, cliente_id) VALUES ('G1', 'Site', 'online', 2)")
    conn.execute("INSERT INTO diagnosticos (sensor_mac, chave, causa, gravidade, inicio, fim, estado) "
                 "VALUES ('G1', 'sem_comunicacao', 'Site sem comunicação', 'Crítica', ?, ?, 'resolvido')", (inicio + 60, inicio + 660))
    conn.commit()
    g = hub.disponibilidade_por_dia(conn, agora=agora)
    conn.close()
    assert 28 <= len(g['dias']) <= 31 and g['dias'][0]['dia'] == 1
    assert g['dias'][0]['fora_s'] == min(600, int(agora - inicio - 60)) and g['dias'][0]['quedas'] == 1
    assert sum(1 for d in g['dias'] if d.get('hoje')) == 1
    assert all(d['pct'] is None for d in g['dias'] if d.get('futuro'))


def test_eventos_recentes_juntam_log_e_diagnosticos(client):
    import hub
    _cenario()
    conn = database.get_db()
    conn.execute("INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES ('AA:02', 'Queda de Conexão', 'Crítica', 'Sensor parou de responder.')")
    conn.commit()
    eventos = hub.eventos_recentes(conn)
    conn.close()
    tipos = {(e['tipo'], e['titulo']) for e in eventos}
    assert ('queda', 'Queda de Conexão') in tipos and ('alerta', 'Perda de pacotes no provedor') in tipos
    assert [e['quando'] for e in eventos] == sorted((e['quando'] for e in eventos), reverse=True)
