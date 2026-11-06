"""Empresa Master (MD Soluções): dona da plataforma, com a equipe Master vinculada a ela."""
import acessos
import database
import hub


def _db():
    return database.get_db()


def _garantir():
    conn = _db()
    try:
        r = acessos.garantir_empresa_master(conn, 'hash-inutil')
        conn.commit()
        return r
    finally:
        conn.close()


def _sensores(*linhas):
    conn = _db()
    conn.execute("DELETE FROM sensores")
    for mac, cliente in linhas:
        conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id, status) VALUES (?, ?, ?, 'online')", (mac, mac, cliente))
    conn.commit(); conn.close()


def _cliente(id_):
    conn = _db()
    try:
        return dict(conn.execute('SELECT * FROM clientes WHERE id=?', (id_,)).fetchone())
    finally:
        conn.close()


def test_cria_uma_vez_vincula_masters_e_transfere_sensores_da_md(client):
    conn = _db()
    # Sem id fixo: no PostgreSQL o id vem da sequência, como em produção.
    conn.execute("INSERT INTO clientes (usuario, senha, role, ativo) VALUES ('op-md', 'x', 'Operador Master', 1)")
    op = conn.execute("SELECT id FROM clientes WHERE usuario='op-md'").fetchone()['id']
    conn.commit(); conn.close()
    _sensores(('S-MASTER', 1), ('S-CLIENTE', 2), ('S-SEM', None))
    r = _garantir()
    assert r['criada'] and r['masters'] == 2 and r['sensores'] == 1
    md = _cliente(r['id'])
    assert md['nome'] == 'MD Soluções' and md['role'] == 'Cliente' and md['empresa_master'] == 1 and md['ativo'] == 1
    assert _cliente(1)['cliente_pai_id'] == md['id'] and _cliente(op)['cliente_pai_id'] == md['id']
    assert _cliente(2)['cliente_pai_id'] is None                     # empresa cliente não muda
    conn = _db()
    dono = {s['mac_id']: s['cliente_id'] for s in conn.execute('SELECT mac_id, cliente_id FROM sensores').fetchall()}
    conn.close()
    assert dono == {'S-MASTER': md['id'], 'S-CLIENTE': 2, 'S-SEM': None}   # sem empresa continua sem empresa
    assert all(conn_ok for conn_ok in acessos.contrato(_db(), md['id']).values())
    # idempotente: rodar de novo não cria outra nem mexe em nada
    r2 = _garantir()
    assert not r2['criada'] and r2['id'] == md['id'] and r2['masters'] == 0 and r2['sensores'] == 0
    conn = _db()
    assert conn.execute('SELECT COUNT(*) AS n FROM clientes WHERE empresa_master=1').fetchone()['n'] == 1
    conn.close()


def test_reaproveita_empresa_md_que_ja_existe(client):
    conn = _db()
    conn.execute("INSERT INTO clientes (nome, usuario, senha, role, ativo) VALUES ('MD Soluções', 'mdsol', 'x', 'Cliente', 1)")
    existente = conn.execute("SELECT id FROM clientes WHERE usuario='mdsol'").fetchone()['id']
    conn.commit(); conn.close()
    r = _garantir()
    assert not r['criada'] and r['id'] == existente and _cliente(existente)['empresa_master'] == 1


def test_master_continua_vendo_tudo_depois_de_vinculado(client, login):
    _sensores(('S-A', 2), ('S-B', 3), ('S-C', None))
    _garantir()
    macs = {s['mac_id'] for s in login(1).get('/api/v2/mapa_sensores').json['sensores']}
    assert {'S-A', 'S-B', 'S-C'} <= macs


def test_usuario_master_novo_nasce_na_empresa_master(client, login):
    md = _garantir()['id']
    r = login(1).post('/api/v2/usuarios', json={'nome': 'Novo Master', 'usuario': 'novo-master', 'senha': 'teste-forte-42',
                                                'role': 'Operador Master', 'cliente_pai': '2'})
    assert r.status_code == 200
    conn = _db()
    pai = conn.execute("SELECT cliente_pai_id FROM clientes WHERE usuario='novo-master'").fetchone()['cliente_pai_id']
    conn.close()
    assert pai == md                                                 # ignora a empresa escolhida


def test_empresa_master_nao_pode_ser_excluida_nem_desativada(client, login):
    md = _garantir()['id']
    master = login(1)
    assert master.post(f'/api/v2/usuarios/{md}/toggle_status').status_code == 403
    assert master.delete(f'/api/v2/usuarios/{md}').status_code == 403
    assert _cliente(md)['ativo'] == 1


def test_alocacao_so_para_empresas(client, login):
    md = _garantir()['id']
    _sensores(('S-X', None))
    master = login(1)
    assert master.post('/api/v2/alocar_sensor', json={'mac_id': 'S-X', 'cliente_id': 1}).status_code == 400   # conta Master
    assert master.post('/api/v2/alocar_sensor', json={'mac_id': 'S-X', 'cliente_id': 999}).status_code == 400
    assert master.post('/api/v2/alocar_sensor', json={'mac_id': 'S-X', 'cliente_id': md}).status_code == 200
    assert master.post('/api/v2/alocar_sensor', json={'mac_id': 'S-X', 'cliente_id': None}).status_code == 200
    pagina = master.get('/sensores').get_data(as_text=True)
    assert 'MD Soluções (Empresa Master)' in pagina
    assert pagina.index('MD Soluções (Empresa Master)') < pagina.index('-- Desvinculado') + 400


def test_hub_mostra_a_empresa_master_primeiro(client):
    md = _garantir()['id']
    conn = _db()
    linhas = hub.visao_md(conn)
    conn.close()
    assert linhas[0]['id'] == md and linhas[0]['master'] and not any(l['master'] for l in linhas[1:])


def test_tela_de_acessos_mostra_a_regra_da_senha_e_o_motivo_do_erro(login):
    page = login().get('/usuarios').get_data(as_text=True)
    assert 'id="senha-regra"' in page and 'alert(r.error ||' in page
    # O motivo vem no campo "error" da resposta, que a tela agora mostra.
    r = login().post('/api/v2/usuarios', json={'nome': 'Fazenda Sol', 'usuario': 'fazenda.sol', 'senha': 'curta123', 'role': 'Cliente'})
    assert r.status_code == 400 and 'pelo menos' in r.get_json()['error']
    ok = login().post('/api/v2/usuarios', json={'nome': 'Fazenda Sol', 'usuario': 'fazenda.sol', 'senha': 'Girassol2026x', 'role': 'Cliente'})
    assert ok.get_json()['status'] == 'OK'
