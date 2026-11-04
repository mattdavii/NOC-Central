"""Tela de Sensores: agrupada por empresa, versão de cada agente e botão de atualizar."""
import app as central
import database


def _sensor(conn, mac, nome, status='online', cliente=None, build=None, recusado=None, acoes=0):
    conn.execute('INSERT INTO sensores (mac_id, nome_local, status, cliente_id, agent_build, build_recusado, acoes_remotas) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?)', (mac, nome, status, cliente, build, recusado, acoes))


def test_versao_e_botao_de_atualizar(client, login):
    conn = database.get_db()
    conn.execute("UPDATE clientes SET nome='Fazenda' WHERE id=2")
    _sensor(conn, 'S:1', 'Em dia', cliente=2, build=central.AGENT_BUILD)
    _sensor(conn, 'S:2', 'Antigo', cliente=2, build='velho')
    _sensor(conn, 'S:3', 'Falhou', cliente=2, build='velho', recusado=central.AGENT_BUILD)
    _sensor(conn, 'S:4', 'Antigo offline', status='offline', cliente=2, build='velho')
    _sensor(conn, 'S:5', 'Solto', build=central.AGENT_BUILD)
    conn.commit(); conn.close()
    page = login().get('/sensores?filtro=versao').get_data(as_text=True)
    assert 'Em dia</span>' in page and 'Versão antiga</span>' in page and 'Versão nova falhou' in page
    # Botão "Atualizar" para os desatualizados online (S:2 e S:3); o offline não tem.
    import re
    linhas = {m.group(1): m.group(0) for m in re.finditer(r'<tr data-mac="([^"]+)".*?</tr>', page, re.S)}
    assert 'class="action-btn btn-primary" data-atualizar' in linhas['S:2']
    assert 'data-atualizar' in linhas['S:3']
    assert 'class="action-btn btn-primary" data-atualizar' not in linhas['S:4']
    # Resumo e atalho para atualizar todos: S:2, S:3 e S:4 estão com versão antiga.
    assert 'Atualizar 3 sensores desatualizados' in page
    # Sem empresa vem primeiro; o filtro do endereço chega ao JavaScript.
    assert page.index('Sem empresa</h2>') < page.index('Fazenda</h2>')
    assert 'let filtro = "versao"' in page


def test_atualizacao_em_andamento_aparece(client, login):
    conn = database.get_db()
    _sensor(conn, 'S:9', 'Indo', cliente=2, build='velho', acoes=1)
    conn.execute("INSERT INTO acoes_remotas (sensor_mac, acao, solicitante, status) VALUES ('S:9', 'atualizar_agente', 'admin', 'pendente')")
    conn.commit(); conn.close()
    page = login().get('/sensores').get_data(as_text=True)
    assert 'Atualizando…' in page and 'Atualizar 1 sensor desatualizado' not in page
