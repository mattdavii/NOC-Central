import io

import pytest

import acessos
import database
import nexodesk

EMPRESA, OUTRA, ADMIN_EMPRESA = 2, 3, 4
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 200


@pytest.fixture(autouse=True)
def _limpa():
    conn = database.get_db()
    for t in ('chamado_anexos', 'chamado_mensagens', 'chamados'):
        conn.execute(f'DELETE FROM {t}')
    conn.commit(); conn.close()
    yield


def _abrir(cli, **campos):
    dados = {'categoria': 'rede', 'impacto': 'atrapalha', 'titulo': 'Internet caindo no fim da tarde',
             'descricao': 'Cai depois das 16h.', **campos}
    return cli.post('/api/v2/desk/chamados', data=dados, content_type='multipart/form-data')


def _usuario_leitura(conn, ident=6):
    conn.execute("INSERT INTO clientes (id, usuario, senha, role, ativo, cliente_pai_id) VALUES (?, ?, 'x', 'Operador Cliente', 1, ?)",
                 (ident, f'user{ident}', EMPRESA))
    acessos.definir_nivel(conn, ident, 'desk', 'leitura')
    conn.commit()


# ------------------------------------------------------------------ abrir e ver
def test_client_opens_a_ticket_with_attachment_and_md_is_told(client, login):
    cli = login(EMPRESA, 'Cliente')
    r = _abrir(cli, arquivos=(io.BytesIO(PNG), 'antena.png'))
    assert r.status_code == 201
    numero = r.json['numero']
    pagina = cli.get(f'/desk/{numero}').get_data(as_text=True)
    assert 'Internet caindo no fim da tarde' in pagina and 'antena.png' in pagina and 'Novo' in pagina
    assert 'Nota interna' not in pagina
    conn = database.get_db()
    c = nexodesk.chamado(conn, numero)
    assert c['prioridade'] == 'alta' and c['canal'] == 'nexohub' and c['solicitante_id'] == EMPRESA
    assert conn.execute("SELECT 1 FROM telegram_outbox WHERE message LIKE ?", (f'%Chamado #{numero}%',)).fetchone()
    assert conn.execute("SELECT 1 FROM auditoria_acessos WHERE acao = 'chamado_aberto'").fetchone()
    conn.close()
    assert f'/desk/{numero}' in cli.get('/desk').get_data(as_text=True)


def test_open_is_validated(client, login):
    cli = login(EMPRESA, 'Cliente')
    assert _abrir(cli, categoria='x').status_code == 400
    assert _abrir(cli, titulo='  ').status_code == 400
    r = _abrir(cli, arquivos=(io.BytesIO(b'MZ...'), 'programa.exe'))
    assert r.status_code == 400 and 'não é de um tipo aceito' in r.json['error']
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id) VALUES ('AA:00:00:00:70:01', 'Outro', ?)", (OUTRA,))
    conn.commit(); conn.close()
    assert _abrir(cli, sensor_mac='AA:00:00:00:70:01').status_code == 400   # local de outra empresa
    conn = database.get_db()
    assert conn.execute('SELECT COUNT(*) AS n FROM chamados').fetchone()['n'] == 0
    conn.close()


def test_attachment_limits(client, login, monkeypatch):
    monkeypatch.setattr(nexodesk, 'MAX_ARQUIVO', 100)
    cli = login(EMPRESA, 'Cliente')
    r = _abrir(cli, arquivos=(io.BytesIO(PNG), 'grande.png'))
    assert r.status_code == 400 and 'passa de' in r.json['error']
    r = _abrir(cli, arquivos=[(io.BytesIO(b'ok'), f'{i}.txt') for i in range(6)])
    assert r.status_code == 400 and 'no máximo 5' in r.json['error']


def test_request_body_limit_is_raised_only_for_desk(client, login):
    import app as central
    with central.app.test_request_context('/api/v2/desk/chamados', method='POST'):
        from flask import request
        assert request.max_content_length == nexodesk.LIMITE_REQUISICAO
    with central.app.test_request_context('/api/v2/report_data', method='POST'):
        from flask import request
        assert request.max_content_length == 1024 * 1024


# ------------------------------------------------------------------ quem vê o quê
def test_other_company_never_sees_the_ticket_or_attachment(client, login):
    numero = _abrir(login(EMPRESA, 'Cliente'), arquivos=(io.BytesIO(PNG), 'antena.png')).json['numero']
    conn = database.get_db()
    anexo_id = conn.execute('SELECT id FROM chamado_anexos').fetchone()['id']
    conn.close()
    outra = login(OUTRA, 'Cliente')
    assert outra.get(f'/desk/{numero}').status_code == 404
    assert outra.get(f'/desk/anexo/{anexo_id}').status_code == 404
    assert outra.post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'oi'}).status_code == 404
    assert f'/desk/{numero}' not in outra.get('/desk').get_data(as_text=True)


def test_reader_sees_only_own_tickets_operator_sees_company(client, login):
    conn = database.get_db()
    _usuario_leitura(conn)
    conn.close()
    do_dono = _abrir(login(EMPRESA, 'Cliente'), titulo='Do dono da conta').json['numero']
    leitor = login(6, 'Operador Cliente')
    proprio = _abrir(leitor, titulo='Do leitor').json['numero']           # leitura também abre chamado
    lista = leitor.get('/desk').get_data(as_text=True)
    assert f'/desk/{proprio}' in lista and f'/desk/{do_dono}' not in lista
    assert leitor.get(f'/desk/{do_dono}').status_code == 404
    assert leitor.post(f'/api/v2/desk/{proprio}/mensagens', data={'texto': 'mais um detalhe'}).status_code == 201
    admin = login(ADMIN_EMPRESA, 'Administrador Cliente')                   # admin padrão vê a empresa toda
    lista = admin.get('/desk').get_data(as_text=True)
    assert f'/desk/{proprio}' in lista and f'/desk/{do_dono}' in lista


def test_no_desk_level_means_no_access(client, login):
    conn = database.get_db()
    acessos.definir_nivel(conn, ADMIN_EMPRESA, 'desk', 'nenhum')
    conn.commit(); conn.close()
    cli = login(ADMIN_EMPRESA, 'Administrador Cliente')
    assert cli.get('/desk').status_code == 403
    assert _abrir(cli).status_code == 403


# ------------------------------------------------------------------ conversa
def test_md_reply_takes_the_ticket_and_internal_note_stays_internal(client, login):
    numero = _abrir(login(EMPRESA, 'Cliente')).json['numero']
    md = login()
    assert f'/desk/{numero}' in md.get('/desk').get_data(as_text=True)
    r = md.post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'Vamos olhar a antena.'})
    assert r.status_code == 201 and r.json['situacao'] == 'atendimento'
    r = md.post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'Levar a escada grande.', 'nota': '1',
                                                          'arquivos': (io.BytesIO(PNG), 'interno.png')},
                content_type='multipart/form-data')
    assert r.status_code == 201
    conn = database.get_db()
    c = nexodesk.chamado(conn, numero)
    assert c['status'] == 'atendimento' and c['responsavel_id'] == 1
    anexo_interno = conn.execute('SELECT id FROM chamado_anexos').fetchone()['id']
    conn.close()
    cliente = login(EMPRESA, 'Cliente')
    pagina = cliente.get(f'/desk/{numero}').get_data(as_text=True)
    assert 'Vamos olhar a antena.' in pagina and 'assumiu o chamado' in pagina
    assert 'Levar a escada grande.' not in pagina and 'interno.png' not in pagina
    assert cliente.get(f'/desk/anexo/{anexo_interno}').status_code == 404
    assert cliente.post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'x', 'nota': '1'}).status_code == 403
    assert 'Levar a escada grande.' in login().get(f'/desk/{numero}').get_data(as_text=True)


def test_client_reply_brings_waiting_ticket_back(client, login):
    numero = _abrir(login(EMPRESA, 'Cliente')).json['numero']
    conn = database.get_db()
    conn.execute("UPDATE chamados SET status = 'aguardando' WHERE id = ?", (numero,))
    conn.commit(); conn.close()
    r = login(EMPRESA, 'Cliente').post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'Mandei as fotos.'})
    assert r.json['situacao'] == 'atendimento'
    assert login(EMPRESA, 'Cliente').post(f'/api/v2/desk/{numero}/mensagens', data={'texto': ' '}).status_code == 400
    conn = database.get_db()
    conn.execute("UPDATE chamados SET status = 'fechado' WHERE id = ?", (numero,))
    conn.commit(); conn.close()
    r = login(EMPRESA, 'Cliente').post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'Mais uma coisa'})
    assert r.status_code == 400 and 'encerrado' in r.json['error']


def test_attachment_download_is_audited(client, login):
    cli = login(EMPRESA, 'Cliente')
    _abrir(cli, arquivos=(io.BytesIO(PNG), 'antena.png'))
    conn = database.get_db()
    anexo_id = conn.execute('SELECT id FROM chamado_anexos').fetchone()['id']
    conn.close()
    r = cli.get(f'/desk/anexo/{anexo_id}?mini=1')
    assert r.status_code == 200 and r.data == PNG and r.mimetype == 'image/png'
    r = cli.get(f'/desk/anexo/{anexo_id}?baixar=1')
    assert 'attachment' in r.headers['Content-Disposition'] and r.headers['X-Content-Type-Options'] == 'nosniff'
    conn = database.get_db()
    acoes = [x['acao'] for x in conn.execute("SELECT acao FROM auditoria_acessos WHERE acao LIKE 'anexo_%'").fetchall()]
    conn.close()
    assert acoes == ['anexo_baixado']


# ------------------------------------------------------------------ a MD abrindo
def test_md_opens_on_behalf_of_a_client_and_internally(client, login):
    md = login()
    assert _abrir(md).status_code == 400                       # sem empresa
    assert _abrir(md, empresa_id=EMPRESA, canal='whatsapp').status_code == 400   # sem solicitante
    r = _abrir(md, empresa_id=EMPRESA, canal='whatsapp', solicitante='Carla', prioridade='critica')
    assert r.status_code == 201
    conn = database.get_db()
    c = nexodesk.chamado(conn, r.json['numero'])
    assert (c['canal'], c['solicitante'], c['prioridade'], c['interno']) == ('whatsapp', 'Carla', 'critica', 0)
    conn.execute("INSERT INTO clientes (id, usuario, senha, role, ativo, empresa_master) VALUES (5, 'md', 'x', 'Cliente', 1, 1)")
    conn.commit(); conn.close()
    interno = _abrir(md, empresa_id=5, titulo='Atualizar agente').json['numero']
    conn = database.get_db()
    c = nexodesk.chamado(conn, interno)
    assert c['interno'] == 1 and c['canal'] == 'interno'
    conn.close()
    cliente = login(EMPRESA, 'Cliente')
    lista = cliente.get('/desk').get_data(as_text=True)
    assert f"/desk/{r.json['numero']}" in lista and f'/desk/{interno}' not in lista
    assert 'via WhatsApp' in cliente.get(f"/desk/{r.json['numero']}").get_data(as_text=True)


# ------------------------------------------------------------------ NexoHub
def test_desk_is_on_by_default_and_shows_in_the_hub(client, login):
    conn = database.get_db()
    assert acessos.contrato(conn, EMPRESA)['desk'] is True
    conn.close()
    cli = login(EMPRESA, 'Cliente')
    _abrir(cli)
    pagina = cli.get('/').get_data(as_text=True)
    assert 'href="/desk"' in pagina and 'Ver os chamados' in pagina
    assert 'NexoDesk' in login().get('/').get_data(as_text=True)
