import time
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


# ------------------------------------------------------------------ entrega 2: fila e atendimento da MD
def _md_extra(conn, ident=7, role='Operador Master'):
    conn.execute('INSERT INTO clientes (id, usuario, nome, senha, role, ativo) VALUES (?, ?, ?, ?, ?, 1)',
                 (ident, f'user{ident}', 'Vicente Vinagre', 'x', role))
    conn.commit()


def test_queue_orders_by_priority_and_counts(client, login):
    cli = login(EMPRESA, 'Cliente')
    baixa = _abrir(cli, impacto='duvida', titulo='Dúvida').json['numero']
    critica = _abrir(cli, impacto='parou', titulo='Parou tudo').json['numero']
    _abrir(login(OUTRA, 'Cliente'), titulo='Lentidão na outra').json['numero']
    md = login()
    pagina = md.get('/desk').get_data(as_text=True)
    assert 'Fila de atendimento' in pagina and '3 chamados abertos em 2 empresas' in pagina
    assert pagina.index(f'/desk/{critica}"') < pagina.index(f'/desk/{baixa}"')
    assert f'/desk/{baixa}"' not in md.get('/desk?busca=parou').get_data(as_text=True)
    assert f'/desk/{critica}"' in md.get(f'/desk?busca=%23{critica}').get_data(as_text=True)
    so_outra = md.get(f'/desk?empresa={OUTRA}').get_data(as_text=True)
    assert f'/desk/{critica}"' not in so_outra and 'Lentidão na outra' in so_outra
    conn = database.get_db()
    _, cont = nexodesk.fila(conn, {'eu': 1})
    conn.close()
    assert cont['novos'] == 3 and cont['abertos'] == 3 and cont['empresas'] == 2


def test_md_changes_owner_priority_and_category_with_history(client, login):
    numero = _abrir(login(EMPRESA, 'Cliente')).json['numero']
    conn = database.get_db()
    _md_extra(conn)
    conn.close()
    md = login()
    assert md.patch(f'/api/v2/desk/{numero}', json={'responsavel_id': 7}).json['chamado']['status'] == 'atendimento'
    assert md.patch(f'/api/v2/desk/{numero}', json={'prioridade': 'critica', 'categoria': 'seguranca'}).status_code == 200
    assert md.patch(f'/api/v2/desk/{numero}', json={'responsavel_id': EMPRESA}).status_code == 400   # cliente não é da equipe
    assert md.patch(f'/api/v2/desk/{numero}', json={'prioridade': 'urgentissima'}).status_code == 400
    assert md.patch(f'/api/v2/desk/{numero}', json={'status': 'resolvido'}).status_code == 400     # solução vai com mensagem
    pagina = login(EMPRESA, 'Cliente').get(f'/desk/{numero}').get_data(as_text=True)
    assert 'Responsável: Vicente Vinagre' in pagina and 'Prioridade: Alta → Crítica' in pagina
    assert 'Categoria: Internet e rede → Segurança e câmeras' in pagina
    assert login(EMPRESA, 'Cliente').patch(f'/api/v2/desk/{numero}', json={'prioridade': 'critica'}).status_code == 403


def test_ask_for_information_then_solve_then_client_reopens(client, login):
    cliente = login(EMPRESA, 'Cliente')
    numero = _abrir(cliente).json['numero']
    md = login()
    assert md.post(f'/api/v2/desk/{numero}/mensagens', data={'texto': '', 'depois': 'aguardando'}).status_code == 400
    assert md.post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'x', 'nota': '1', 'depois': 'resolvido'}).status_code == 400
    r = md.post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'Manda uma foto da antena?', 'depois': 'aguardando'})
    assert r.json['situacao'] == 'aguardando'
    pagina = login(EMPRESA, 'Cliente').get(f'/desk/{numero}').get_data(as_text=True)
    assert 'A MD está esperando a sua resposta' in pagina
    assert login(EMPRESA, 'Cliente').post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'Foto enviada'}).json['situacao'] == 'atendimento'
    r = login().post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'Subimos o mastro.', 'depois': 'resolvido'})
    assert r.json['situacao'] == 'resolvido'
    conn = database.get_db()
    assert nexodesk.chamado(conn, numero)['resolvido_em']
    conn.close()
    assert 'marcou este chamado como resolvido' in login(EMPRESA, 'Cliente').get(f'/desk/{numero}').get_data(as_text=True)
    assert login(EMPRESA, 'Cliente').post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'Caiu de novo'}).json['situacao'] == 'atendimento'
    conn = database.get_db()
    assert nexodesk.chamado(conn, numero)['resolvido_em'] is None
    conn.close()


def test_cancel_and_reopen(client, login):
    numero = _abrir(login(EMPRESA, 'Cliente')).json['numero']
    md = login()
    assert md.patch(f'/api/v2/desk/{numero}', json={'status': 'cancelado'}).json['chamado']['status'] == 'cancelado'
    assert md.patch(f'/api/v2/desk/{numero}', json={'prioridade': 'baixa'}).status_code == 400   # encerrado
    assert login(EMPRESA, 'Cliente').post(f'/api/v2/desk/{numero}/mensagens', data={'texto': 'oi'}).status_code == 400
    assert login().patch(f'/api/v2/desk/{numero}', json={'status': 'atendimento'}).json['chamado']['status'] == 'atendimento'
    conn = database.get_db()
    c = nexodesk.chamado(conn, numero)
    conn.close()
    assert c['fechado_em'] is None and 'Reaberto pela MD' in login(EMPRESA, 'Cliente').get(f'/desk/{numero}').get_data(as_text=True)


# ------------------------------------------------------------------ entrega 3: prazo do contrato (SLA)
from datetime import datetime as _dt

from hub import FUSO_BR as _FUSO


def _ts(m, d, h, mi=0):
    return _dt(2026, m, d, h, mi, tzinfo=_FUSO).timestamp()


def _abrir_em(conn, quando, impacto='atrapalha', empresa=EMPRESA):
    autor = acessos._usuario(conn, empresa)
    return nexodesk.abrir(conn, autor, {'categoria': 'rede', 'impacto': impacto, 'titulo': 'Lento', 'descricao': 'x'}, agora=quando)


def test_ticket_clocks_follow_the_contract_snapshot(client):
    conn = database.get_db()
    numero = _abrir_em(conn, _ts(11, 6, 17))                     # sexta 17h, prioridade alta (2 h / 16 h)
    p = nexodesk.prazos(nexodesk.chamado(conn, numero), agora=_ts(11, 6, 17, 30))
    assert p['resposta']['vence_em'] == _ts(11, 9, 9) and p['resposta']['estado'] == 'ok'
    assert p['solucao']['vence_em'] == _ts(11, 10, 13) and p['proximo']['qual'] == 'resposta'
    assert nexodesk.prazos(nexodesk.chamado(conn, numero), agora=_ts(11, 9, 8, 40))['resposta']['estado'] == 'perto'
    assert nexodesk.prazos(nexodesk.chamado(conn, numero), agora=_ts(11, 9, 9, 30))['resposta']['estado'] == 'estourado'
    # Mudar o contrato depois não muda o chamado já aberto.
    nexodesk.definir_sla(conn, EMPRESA, {'horario': '24x7', 'prazos': {'critica': [1, 2], 'alta': [1, 2], 'media': [1, 2], 'baixa': [1, 2]}})
    assert nexodesk.prazos(nexodesk.chamado(conn, numero), agora=_ts(11, 6, 17, 30))['resposta']['vence_em'] == _ts(11, 9, 9)
    assert nexodesk.prazos(nexodesk.chamado(conn, _abrir_em(conn, _ts(11, 6, 17))), agora=_ts(11, 6, 17, 1))['resposta']['vence_em'] == _ts(11, 6, 18)
    conn.close()


def test_first_reply_priority_change_and_pause(client):
    conn = database.get_db()
    md = acessos._usuario(conn, 1)
    numero = _abrir_em(conn, _ts(11, 9, 9))                      # segunda 9h, alta
    nexodesk.responder(conn, md, nexodesk.chamado(conn, numero), 'Olhando.', agora=_ts(11, 9, 9, 30))
    c = nexodesk.chamado(conn, numero)
    p = nexodesk.prazos(c, agora=_ts(11, 9, 10))
    assert p['resposta']['estado'] == 'cumprido' and p['proximo']['qual'] == 'solucao'
    nexodesk.atualizar(conn, md, c, {'prioridade': 'critica'}, agora=_ts(11, 9, 10))   # solução 8 h a partir da abertura
    assert nexodesk.prazos(nexodesk.chamado(conn, numero), agora=_ts(11, 9, 10))['solucao']['vence_em'] == _ts(11, 9, 17)
    nexodesk.responder(conn, md, nexodesk.chamado(conn, numero), 'Manda a foto?', depois='aguardando', agora=_ts(11, 9, 11))
    pausado = nexodesk.prazos(nexodesk.chamado(conn, numero), agora=_ts(11, 9, 15))
    assert pausado['solucao']['estado'] == 'pausado' and pausado['solucao']['vence_em'] is None
    cliente = acessos._usuario(conn, EMPRESA)
    nexodesk.responder(conn, cliente, nexodesk.chamado(conn, numero), 'Foto', agora=_ts(11, 9, 14))
    p = nexodesk.prazos(nexodesk.chamado(conn, numero), agora=_ts(11, 9, 14, 30))
    assert p['solucao']['vence_em'] == pytest.approx(_ts(11, 10, 10), abs=1)   # as 3 h pausadas empurram o prazo
    nexodesk.responder(conn, md, nexodesk.chamado(conn, numero), 'Resolvido.', depois='resolvido', agora=_ts(11, 9, 16))
    assert nexodesk.prazos(nexodesk.chamado(conn, numero), agora=_ts(11, 12, 9))['solucao']['estado'] == 'cumprido'
    conn.close()


def test_deadline_alerts_go_once_per_milestone(client):
    import app as central
    conn = database.get_db()
    numero = _abrir_em(conn, _ts(11, 9, 9), impacto='parou')     # crítica: resposta 1 h
    conn.commit(); conn.close()
    assert central.verificar_prazos_desk(agora=_ts(11, 9, 9, 10)) == []
    avisos = central.verificar_prazos_desk(agora=_ts(11, 9, 9, 50))
    assert len(avisos) == 1 and 'perto de vencer' in avisos[0] and f'#{numero}' in avisos[0]
    assert central.verificar_prazos_desk(agora=_ts(11, 9, 9, 55)) == []
    avisos = central.verificar_prazos_desk(agora=_ts(11, 9, 10, 5))
    assert len(avisos) == 1 and 'estourado' in avisos[0]
    assert central.verificar_prazos_desk(agora=_ts(11, 9, 10, 30)) == []
    conn = database.get_db()
    assert conn.execute("SELECT COUNT(*) AS n FROM telegram_outbox WHERE message LIKE '%prazo estourado%'").fetchone()['n'] == 1
    conn.close()


def test_sla_api_permissions_validation_and_audit(client, login):
    md = login()
    d = md.get(f'/api/v2/desk/sla/{EMPRESA}').json
    assert d['proprio'] is False and d['horario'] == 'comercial' and d['prazos']['alta'] == [2, 16]
    novo = {'horario': '24x7', 'prazos': {'critica': [1, 4], 'alta': [2, 8], 'media': [4, 24], 'baixa': [8, 40]}}
    r = md.put(f'/api/v2/desk/sla/{EMPRESA}', json=novo)
    assert r.status_code == 200 and r.json['proprio'] is True and r.json['prazos']['alta'] == [2.0, 8.0]
    assert md.put(f'/api/v2/desk/sla/{EMPRESA}', json={'horario': '24x7', 'prazos': {}}).status_code == 400
    assert login(EMPRESA, 'Cliente').get(f'/api/v2/desk/sla/{EMPRESA}').status_code == 403
    conn = database.get_db()
    conn.execute("INSERT INTO clientes (id, usuario, senha, role, ativo) VALUES (8, 'op', 'x', 'Operador Master', 1)")
    conn.commit(); conn.close()
    assert login(8, 'Operador Master').put(f'/api/v2/desk/sla/{EMPRESA}', json={'padrao': True}).status_code == 403
    assert login().put(f'/api/v2/desk/sla/{EMPRESA}', json={'padrao': True}).json['proprio'] is False
    conn = database.get_db()
    assert conn.execute("SELECT COUNT(*) AS n FROM auditoria_acessos WHERE acao = 'sla_alterado'").fetchone()['n'] == 2
    conn.close()
    assert 'Pelo seu contrato' in login(EMPRESA, 'Cliente').get('/desk/novo').get_data(as_text=True)


def test_queue_puts_the_nearest_deadline_first_and_internal_manual_deadline(client, login):
    conn = database.get_db()
    tarde = _abrir_em(conn, time.time() - 60, impacto='duvida')          # baixa: resposta 8 h
    cedo = _abrir_em(conn, time.time() - 120, impacto='parou')           # crítica: resposta 1 h
    conn.execute("INSERT INTO clientes (id, usuario, senha, role, ativo, empresa_master) VALUES (5, 'md', 'x', 'Cliente', 1, 1)")
    conn.commit()
    interno = nexodesk.abrir(conn, acessos._usuario(conn, 1), {'empresa_id': 5, 'categoria': 'sistemas', 'impacto': 'duvida', 'titulo': 'Interno', 'descricao': 'x'})
    conn.commit(); conn.close()
    pagina = login().get('/desk').get_data(as_text=True)
    assert pagina.index(f'/desk/{cedo}"') < pagina.index(f'/desk/{tarde}"') < pagina.index(f'/desk/{interno}"')
    assert login().patch(f'/api/v2/desk/{cedo}', json={'prazo_manual': '2026-12-01T10:00'}).status_code == 400
    assert login().patch(f'/api/v2/desk/{interno}', json={'prazo_manual': '2099-12-01T10:00'}).status_code == 200
    conn = database.get_db()
    p = nexodesk.chamado(conn, interno)['prazo']
    conn.close()
    assert p['manual'] and p['solucao']['estado'] == 'ok'
    assert 'Prazo do contrato' in login(EMPRESA, 'Cliente').get(f'/desk/{cedo}').get_data(as_text=True)
