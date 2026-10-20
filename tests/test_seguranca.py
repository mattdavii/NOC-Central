"""NexoHub PR5: bloqueio de login, verificação em duas etapas, sessões encerráveis e senha forte."""
import time

from werkzeug.security import generate_password_hash

import database
import seguranca

SENHA = 'senha-de-teste-42'


def _preparar(ident=2, totp=False):
    """Dá senha ao usuário e, se pedido, verificação em duas etapas ativa. Devolve o segredo TOTP."""
    segredo = seguranca.gerar_segredo() if totp else None
    conn = database.get_db()
    conn.execute('UPDATE clientes SET senha=?, totp_ativo=?, totp_segredo=?, totp_ultimo_passo=NULL WHERE id=?',
                 (generate_password_hash(SENHA), 1 if totp else 0, seguranca.cifrar(segredo) if totp else None, ident))
    conn.commit(); conn.close()
    return segredo


def _codigo_agora(segredo):
    return seguranca.codigo(segredo, seguranca.passo_atual())


def _auditoria(acao):
    conn = database.get_db()
    rows = [dict(r) for r in conn.execute('SELECT * FROM auditoria_acessos WHERE acao=? ORDER BY id', (acao,)).fetchall()]
    conn.close()
    return rows


def _col(ident, coluna):
    conn = database.get_db()
    valor = conn.execute(f'SELECT {coluna} FROM clientes WHERE id=?', (ident,)).fetchone()[coluna]
    conn.close()
    return valor


# ===== Unidade =====

def test_totp_janela_e_reuso():
    segredo = seguranca.gerar_segredo()
    agora = 1_800_000_000
    passo = seguranca.passo_atual(agora)
    assert seguranca.verificar_totp(segredo, seguranca.codigo(segredo, passo - 1), agora=agora) == passo - 1  # relógio atrasado
    assert seguranca.verificar_totp(segredo, seguranca.codigo(segredo, passo - 3), agora=agora) is None
    assert seguranca.verificar_totp(segredo, seguranca.codigo(segredo, passo), ultimo_passo=passo, agora=agora) is None  # já usado
    assert seguranca.verificar_totp(segredo, '12a456', agora=agora) is None


def test_senha_forte():
    assert seguranca.validar_senha_forte('curta1') and seguranca.validar_senha_forte('semnumeroaqui')
    assert seguranca.validar_senha_forte('1234567890') and seguranca.validar_senha_forte('maria-2026-x', usuario='maria')
    assert seguranca.validar_senha_forte('Ponte-azul-77') is None


# ===== Login =====

def test_erro_de_senha_aparece_na_tela(client):
    _preparar()
    page = client.post('/login', data={'usuario': 'user2', 'senha': 'errada'}).get_data(as_text=True)
    assert 'Usuário ou senha incorretos.' in page


def test_bloqueio_apos_cinco_erros(client):
    _preparar()
    for _ in range(4):
        assert 'incorretos' in client.post('/login', data={'usuario': 'user2', 'senha': 'errada'}).get_data(as_text=True)
    page = client.post('/login', data={'usuario': 'user2', 'senha': 'errada'}).get_data(as_text=True)
    assert 'Muitas tentativas' in page
    # Nem a senha certa entra durante o bloqueio.
    r = client.post('/login', data={'usuario': 'user2', 'senha': SENHA})
    assert r.status_code == 200 and 'Muitas tentativas' in r.get_data(as_text=True)
    assert len(_auditoria('login_bloqueado')) == 1
    # Passado o bloqueio, entra e as falhas zeram.
    conn = database.get_db()
    conn.execute('UPDATE tentativas_login SET bloqueado_ate=? WHERE chave=?', (time.time() - 1, 'u:user2'))
    conn.commit(); conn.close()
    assert client.post('/login', data={'usuario': 'user2', 'senha': SENHA}).status_code == 302
    conn = database.get_db()
    assert conn.execute("SELECT COUNT(*) AS n FROM tentativas_login WHERE chave='u:user2'").fetchone()['n'] == 0
    conn.close()


def test_conta_inexistente_tambem_bloqueia(client):
    for _ in range(5):
        client.post('/login', data={'usuario': 'nao-existe', 'senha': 'x'})
    assert 'Muitas tentativas' in client.post('/login', data={'usuario': 'nao-existe', 'senha': 'x'}).get_data(as_text=True)


def test_login_em_duas_etapas(client):
    segredo = _preparar(totp=True)
    r = client.post('/login', data={'usuario': 'user2', 'senha': SENHA})
    assert r.status_code == 302 and r.headers['Location'].endswith('/login/2fa')
    # Só a senha não abre nada.
    assert client.get('/pulse').status_code == 302
    assert 'Código inválido' in client.post('/login/2fa', data={'codigo': '000000'}).get_data(as_text=True)
    codigo = _codigo_agora(segredo)
    r = client.post('/login/2fa', data={'codigo': codigo})
    assert r.status_code == 302 and r.headers['Location'].endswith('/')
    assert client.get('/pulse').status_code == 200
    assert _auditoria('login')[-1]['detalhes'] == '{"metodo": "2fa"}'
    # O mesmo código não serve duas vezes.
    client.post('/logout')
    client.post('/login', data={'usuario': 'user2', 'senha': SENHA})
    assert 'Código inválido' in client.post('/login/2fa', data={'codigo': codigo}).get_data(as_text=True)


def test_codigo_de_recuperacao_vale_uma_vez(client):
    _preparar(totp=True)
    codigos, guardados = seguranca.gerar_codigos_recuperacao()
    conn = database.get_db()
    conn.execute('UPDATE clientes SET codigos_recuperacao=? WHERE id=2', (guardados,))
    conn.commit(); conn.close()
    client.post('/login', data={'usuario': 'user2', 'senha': SENHA})
    assert client.post('/login/2fa', data={'codigo': codigos[0].upper()}).status_code == 302
    assert seguranca.codigos_restantes(_col(2, 'codigos_recuperacao')) == 9
    client.post('/logout')
    client.post('/login', data={'usuario': 'user2', 'senha': SENHA})
    assert 'Código inválido' in client.post('/login/2fa', data={'codigo': codigos[0]}).get_data(as_text=True)


def test_codigos_errados_bloqueiam_a_segunda_etapa(client):
    _preparar(totp=True)
    client.post('/login', data={'usuario': 'user2', 'senha': SENHA})
    for _ in range(4):
        client.post('/login/2fa', data={'codigo': '000000'})
    assert 'Muitas tentativas' in client.post('/login/2fa', data={'codigo': '000000'}).get_data(as_text=True)
    assert client.get('/login/2fa').status_code == 302  # etapa pendente descartada


def test_etapa_pendente_expira(client):
    _preparar(totp=True)
    client.post('/login', data={'usuario': 'user2', 'senha': SENHA})
    with client.session_transaction() as s:
        s['2fa_pendente']['ate'] = time.time() - 1
        s.modified = True
    assert client.get('/login/2fa').headers['Location'].endswith('/login')


# ===== Minha conta =====

def test_ativar_e_desativar_2fa(login):
    client = login(2, 'Cliente')
    dados = client.post('/api/v2/conta/2fa/iniciar').get_json()
    assert dados['uri'].startswith('otpauth://totp/') and len(dados['segredo']) >= 32
    assert not _col(2, 'totp_ativo') and dados['segredo'] not in (_col(2, 'totp_segredo') or '')  # guardado cifrado
    assert client.post('/api/v2/conta/2fa/confirmar', json={'codigo': '000000'}).status_code == 400
    r = client.post('/api/v2/conta/2fa/confirmar', json={'codigo': _codigo_agora(dados['segredo'])})
    assert r.status_code == 200 and len(r.get_json()['codigos_recuperacao']) == 10 and _col(2, 'totp_ativo')
    assert client.post('/api/v2/conta/2fa/iniciar').status_code == 409
    assert client.post('/api/v2/conta/2fa/desativar', json={'codigo': '000000'}).status_code == 400
    # Um código de recuperação também desativa.
    cod = r.get_json()['codigos_recuperacao'][0]
    assert client.post('/api/v2/conta/2fa/desativar', json={'codigo': cod}).status_code == 200
    assert not _col(2, 'totp_ativo') and _col(2, 'totp_segredo') is None
    assert [a['acao'] for a in _auditoria('2fa_ativado')] and _auditoria('2fa_desativado')


def test_empresa_exige_2fa(client, login):
    # A empresa 2 passa a exigir; o administrador dela (4) entra e só a Minha conta abre.
    assert login(1).post('/api/v2/acessos/empresas/2/exigir_2fa', json={'ativo': True}).status_code == 200
    _preparar(4)
    client.post('/logout')
    assert client.post('/login', data={'usuario': 'user4', 'senha': SENHA}).status_code == 302
    assert client.get('/pulse').headers['Location'].endswith('/conta')
    assert client.get('/api/v2/diagnosticos').status_code == 403
    assert 'Ative a verificação em duas etapas para continuar' in client.get('/conta').get_data(as_text=True)
    segredo = client.post('/api/v2/conta/2fa/iniciar').get_json()['segredo']
    client.post('/api/v2/conta/2fa/confirmar', json={'codigo': _codigo_agora(segredo)})
    assert client.get('/pulse').status_code == 200
    # Obrigatória: não dá para desativar.
    assert client.post('/api/v2/conta/2fa/desativar', json={'codigo': '123456'}).status_code == 403


def test_permissao_para_exigir_2fa(login):
    assert login(4, 'Administrador Cliente').post('/api/v2/acessos/empresas/2/exigir_2fa', json={'ativo': True}).status_code == 200
    assert login(4, 'Administrador Cliente').post('/api/v2/acessos/empresas/3/exigir_2fa', json={'ativo': True}).status_code == 403
    assert login(4, 'Administrador Cliente').post('/api/v2/acessos/md/exigir_2fa', json={'ativo': True}).status_code == 403
    assert login(1).post('/api/v2/acessos/md/exigir_2fa', json={'ativo': True}).status_code == 200


def test_trocar_a_propria_senha(login):
    _preparar(2)
    client = login(2, 'Cliente')
    assert client.post('/api/v2/conta/senha', json={'atual': 'errada', 'nova': 'Outra-senha-99'}).status_code == 400
    assert client.post('/api/v2/conta/senha', json={'atual': SENHA, 'nova': 'fraca'}).status_code == 400
    assert client.post('/api/v2/conta/senha', json={'atual': SENHA, 'nova': 'Outra-senha-99'}).status_code == 200
    assert _col(2, 'sessao_versao') == 1
    assert client.get('/pulse').status_code == 200  # esta sessão continua


# ===== Sessões =====

def test_encerrar_sessoes_de_um_usuario(login):
    assert login(4, 'Administrador Cliente').get('/pulse').status_code == 200
    assert login(1).post('/api/v2/acessos/usuarios/4/encerrar_sessoes').status_code == 200
    # A sessão antiga do usuário 4 (versão 0) deixa de valer.
    client = login(4, 'Administrador Cliente')
    r = client.get('/pulse')
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')
    assert login(4, 'Administrador Cliente').get('/api/v2/diagnosticos').status_code == 401
    assert _auditoria('sessoes_encerradas')[0]['alvo'] == 'user4'


def test_sair_dos_outros_aparelhos_mantem_este(login):
    client = login(2, 'Cliente')
    assert client.post('/api/v2/conta/sessoes/encerrar').status_code == 200
    assert client.get('/pulse').status_code == 200 and _col(2, 'sessao_versao') == 1


def test_redefinir_senha_pelo_admin_encerra_sessoes(login):
    assert login(1).post('/api/v2/usuarios/4/senha', json={'senha': 'fraca'}).status_code == 400
    assert login(1).post('/api/v2/usuarios/4/senha', json={'senha': 'Nova-senha-forte-1'}).status_code == 200
    assert _col(4, 'sessao_versao') == 1


def test_redefinir_2fa_permissoes(login):
    conn = database.get_db()
    conn.execute("INSERT INTO clientes (id, usuario, senha, role, ativo, cliente_pai_id) VALUES (5, 'user5', 'x', 'Operador Cliente', 1, 2)")
    conn.execute("INSERT INTO clientes (id, usuario, senha, role, ativo, cliente_pai_id) VALUES (6, 'user6', 'x', 'Operador Cliente', 1, 3)")
    conn.commit(); conn.close()
    _preparar(5, totp=True)
    admin = login(4, 'Administrador Cliente')
    assert admin.post('/api/v2/acessos/usuarios/6/redefinir_2fa').status_code == 403  # outra empresa
    assert admin.post('/api/v2/acessos/usuarios/4/redefinir_2fa').status_code == 403  # a própria conta vai pela Minha conta
    assert admin.post('/api/v2/acessos/usuarios/5/redefinir_2fa').status_code == 200
    assert not _col(5, 'totp_ativo') and _col(5, 'sessao_versao') == 1
    login(1).post('/api/v2/acessos/empresas/2/delegacao', json={'ativo': False})
    assert login(4, 'Administrador Cliente').post('/api/v2/acessos/usuarios/5/encerrar_sessoes').status_code == 403


def test_central_de_acessos_mostra_2fa(login):
    _preparar(4, totp=True)
    page = login(1).get('/usuarios').get_data(as_text=True)
    assert 'Exigir verificação em duas etapas da equipe MD' in page and '2FA obrigatória' in page
    assert "redefinir2fa(4" in page and "encerrarSessoes(4" in page
    page = login(4, 'Administrador Cliente').get('/usuarios').get_data(as_text=True)
    assert 'Exigir verificação em duas etapas de todos os usuários da empresa' in page
    assert "encerrarSessoes(4" not in page  # a própria conta não aparece com ações de segurança
