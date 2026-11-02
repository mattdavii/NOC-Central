"""Família Nexo: contratos, níveis por aplicativo, delegação e auditoria (NexoHub PR1)."""
import json

import acessos
import database


def _add_operador(ident=5, parent=2):
    conn = database.get_db()
    conn.execute("INSERT INTO clientes (id, usuario, senha, role, ativo, cliente_pai_id) VALUES (?, ?, 'x', 'Operador Cliente', 1, ?)",
                 (ident, f'user{ident}', parent))
    conn.commit(); conn.close()


def _auditoria(acao):
    conn = database.get_db()
    rows = [dict(r) for r in conn.execute('SELECT * FROM auditoria_acessos WHERE acao=? ORDER BY id', (acao,)).fetchall()]
    conn.close()
    return rows


def test_padrao_mantem_acesso_de_hoje(login):
    _add_operador()
    conn = database.get_db()
    assert acessos.acesso_efetivo(conn, 1) == {'pulse': 'admin', 'guard': 'admin', 'report': 'admin'}
    assert acessos.acesso_efetivo(conn, 2) == {'pulse': 'admin'}
    assert acessos.acesso_efetivo(conn, 4) == {'pulse': 'admin'}
    assert acessos.acesso_efetivo(conn, 5) == {'pulse': 'operador'}
    conn.close()
    client = login(5, 'Operador Cliente')
    assert client.get('/pulse').status_code == 200
    assert client.post('/api/v2/ack_alerta').status_code == 200


def test_contrato_desligado_bloqueia_empresa_inteira(login):
    client = login(1)
    r = client.post('/api/v2/acessos/empresas/2/contrato', json={'aplicativo': 'pulse', 'ativo': False})
    assert r.status_code == 200 and r.get_json()['contrato']['pulse'] is False
    client = login(4, 'Administrador Cliente')
    assert client.get('/pulse').status_code == 403
    assert client.get('/api/v2/diagnosticos').status_code == 403
    # Outra empresa e telas do Hub seguem funcionando.
    assert login(3, 'Cliente').get('/api/v2/diagnosticos').status_code == 200
    assert login(4, 'Administrador Cliente').get('/api/v2/acessos/me').get_json()['acesso'] == {}
    assert _auditoria('contrato_alterado')[0]['empresa_id'] == 2


def test_nivel_nenhum_e_leitura(login):
    _add_operador()
    client = login(1)
    assert client.post('/api/v2/acessos/usuarios/5/nivel', json={'aplicativo': 'pulse', 'nivel': 'leitura'}).status_code == 200
    operador = login(5, 'Operador Cliente')
    assert operador.get('/pulse').status_code == 200
    r = operador.post('/api/v2/ack_alerta')
    assert r.status_code == 403 and 'leitura' in r.get_json()['error']
    assert login(1).post('/api/v2/acessos/usuarios/5/nivel', json={'aplicativo': 'pulse', 'nivel': 'nenhum'}).status_code == 200
    assert login(5, 'Operador Cliente').get('/pulse').status_code == 403
    detalhes = json.loads(_auditoria('nivel_alterado')[-1]['detalhes'])
    assert detalhes == {'aplicativo': 'pulse', 'antes': 'leitura', 'depois': 'nenhum'}


def test_nivel_em_app_nao_contratado_e_recusado(login):
    _add_operador()
    r = login(1).post('/api/v2/acessos/usuarios/5/nivel', json={'aplicativo': 'guard', 'nivel': 'operador'})
    assert r.status_code == 403 and 'contratou' in r.get_json()['error']


def test_admin_cliente_nao_concede_acima_do_proprio_nivel(login):
    _add_operador()
    conn = database.get_db()
    acessos.definir_nivel(conn, 4, 'pulse', 'operador')
    conn.commit(); conn.close()
    admin = login(4, 'Administrador Cliente')
    assert admin.post('/api/v2/acessos/usuarios/5/nivel', json={'aplicativo': 'pulse', 'nivel': 'admin'}).status_code == 403
    assert admin.post('/api/v2/acessos/usuarios/5/nivel', json={'aplicativo': 'pulse', 'nivel': 'leitura'}).status_code == 200
    assert admin.post('/api/v2/acessos/usuarios/4/nivel', json={'aplicativo': 'pulse', 'nivel': 'admin'}).status_code == 403


def test_isolamento_entre_empresas(login):
    _add_operador(5, parent=3)
    admin = login(4, 'Administrador Cliente')
    assert admin.post('/api/v2/acessos/usuarios/5/nivel', json={'aplicativo': 'pulse', 'nivel': 'nenhum'}).status_code == 403
    ids = {u['id'] for u in admin.get('/api/v2/acessos/usuarios').get_json()['usuarios']}
    assert ids == {2, 4}
    assert admin.get('/api/v2/acessos/empresas').status_code == 403
    assert admin.post('/api/v2/acessos/empresas/2/contrato', json={'aplicativo': 'guard', 'ativo': True}).status_code == 403


def test_delegacao_desligada_bloqueia_gestao_pela_empresa(login):
    _add_operador()
    assert login(1).post('/api/v2/acessos/empresas/2/delegacao', json={'ativo': False}).status_code == 200
    admin = login(4, 'Administrador Cliente')
    assert admin.get('/usuarios').status_code == 403
    assert admin.post('/api/v2/usuarios/5/toggle_status').status_code == 403
    assert admin.post('/api/v2/acessos/usuarios/5/nivel', json={'aplicativo': 'pulse', 'nivel': 'leitura'}).status_code == 403
    # O monitoramento continua; e a MD segue podendo gerenciar.
    assert admin.get('/pulse').status_code == 200
    assert login(1).post('/api/v2/usuarios/5/toggle_status').status_code == 200
    assert _auditoria('usuario_desativado')[0]['empresa_id'] == 2


def test_master_ve_empresas_e_auditoria(login):
    client = login(1)
    empresas = client.get('/api/v2/acessos/empresas').get_json()['empresas']
    assert {e['id'] for e in empresas} == {2, 3}
    assert all(e['contrato'] == acessos.CONTRATO_PADRAO and e['delegacao'] for e in empresas)
    client.post('/api/v2/acessos/empresas/3/delegacao', json={'ativo': False})
    assert login(4, 'Administrador Cliente').get('/api/v2/acessos/auditoria').get_json()['registros'] == []
    assert login(1).get('/api/v2/acessos/auditoria').get_json()['registros'][0]['acao'] == 'delegacao_alterada'


def test_login_registra_sucesso_e_falha(client):
    conn = database.get_db()
    conn.execute("UPDATE clientes SET senha=? WHERE id=2", (central_hash('senha-de-teste-1'),))
    conn.commit(); conn.close()
    client.post('/login', data={'usuario': 'user2', 'senha': 'errada'})
    client.post('/login', data={'usuario': 'user2', 'senha': 'senha-de-teste-1'})
    assert _auditoria('login_falhou')[0]['alvo'] == 'user2'
    sucesso = _auditoria('login')[0]
    assert sucesso['autor_id'] == 2 and sucesso['empresa_id'] == 2


def test_excluir_usuario_apaga_registros_de_acesso(login):
    _add_operador()
    client = login(1)
    client.post('/api/v2/acessos/usuarios/5/nivel', json={'aplicativo': 'pulse', 'nivel': 'leitura'})
    assert client.delete('/api/v2/usuarios/5').status_code == 200
    conn = database.get_db()
    assert conn.execute('SELECT COUNT(*) AS n FROM usuario_aplicativos WHERE usuario_id=5').fetchone()['n'] == 0
    conn.close()
    assert _auditoria('usuario_excluido')[0]['alvo'] == 'user5'


def central_hash(senha):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(senha)


# ===== PR2: Central de Acessos (tela e perfis prontos) =====

def test_perfil_pronto_aplica_so_no_contratado(login):
    _add_operador()
    r = login(1).post('/api/v2/acessos/usuarios/5/perfil', json={'perfil': 'diretoria'})
    assert r.status_code == 200
    # Só a NexoPulse está contratada: Report (leitura) não é gravado.
    assert r.get_json()['usuario']['acesso_efetivo'] == {'pulse': 'leitura'}
    conn = database.get_db()
    assert acessos.niveis_registrados(conn, 5) == {'pulse': 'leitura'}
    conn.close()
    detalhes = json.loads(_auditoria('perfil_aplicado')[0]['detalhes'])
    assert detalhes == {'perfil': 'Diretoria', 'niveis': {'pulse': 'leitura'}}


def test_perfil_pronto_e_tudo_ou_nada(login):
    _add_operador()
    conn = database.get_db()
    acessos.definir_contrato(conn, 2, 'guard', True)
    acessos.definir_nivel(conn, 4, 'guard', 'leitura')  # o admin só lê o Guard
    conn.commit(); conn.close()
    r = login(4, 'Administrador Cliente').post('/api/v2/acessos/usuarios/5/perfil', json={'perfil': 'tecnico'})
    assert r.status_code == 403
    conn = database.get_db()
    assert acessos.niveis_registrados(conn, 5) == {}  # nem a NexoPulse foi gravada
    conn.close()
    assert login(4, 'Administrador Cliente').post('/api/v2/acessos/usuarios/5/perfil', json={'perfil': 'xyz'}).status_code == 400


def test_tela_central_de_acessos_master(login):
    _add_operador()
    page = login(1).get('/usuarios').get_data(as_text=True)
    assert 'Central de Acessos' in page
    assert 'id="painel-empresas"' in page and 'Aplicativos contratados' in page
    assert 'Acesso total (MD)' in page
    assert 'NexoPulse · <strong>Operador</strong>' in page
    assert 'value="Operador Cliente"' in page and 'value="Operador"' not in page
    assert "abrirAcessos(5)" in page


def test_tela_central_de_acessos_admin_da_empresa(login):
    _add_operador()
    page = login(4, 'Administrador Cliente').get('/usuarios').get_data(as_text=True)
    assert 'Central de Acessos' in page
    assert 'id="painel-empresas"' not in page  # contratos são só da MD
    assert 'value="Administrador Master"' not in page and 'value="Cliente"' not in page
    assert "abrirAcessos(5)" in page
    assert "abrirAcessos(4)" not in page  # não edita o próprio acesso
    dados = page.split('id="dados-acessos" type="application/json">')[1].split('</script>')[0]
    dados = json.loads(dados)
    assert dados['master'] is False and set(dados['usuarios']) == {'5'}
    assert dados['meu_acesso'] == {'pulse': 'admin'}
