"""Lembrar o dispositivo na verificação em duas etapas: a senha basta por 30 dias naquele navegador."""
import time

import app as central
import confiaveis
import database
from tests.test_seguranca import SENHA, _auditoria, _codigo_agora, _preparar

COOKIE = confiaveis.nome_do_cookie(2)


def _limpar():
    conn = database.get_db()
    conn.execute('DELETE FROM dispositivos_confiaveis')
    conn.commit(); conn.close()


def _entrar_com_codigo(client, segredo, lembrar):
    conn = database.get_db()   # o mesmo código não vale duas vezes; aqui os logins caem no mesmo intervalo de 30 s
    conn.execute('UPDATE clientes SET totp_ultimo_passo = NULL WHERE id = 2')
    conn.commit(); conn.close()
    client.post('/login', data={'usuario': 'user2', 'senha': SENHA})
    dados = {'codigo': _codigo_agora(segredo)}
    if lembrar:
        dados['lembrar'] = '1'
    return client.post('/login/2fa', data=dados, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537'})


def _so_senha(client):
    return client.post('/login', data={'usuario': 'user2', 'senha': SENHA})


def _cookie(client):
    c = client.get_cookie(COOKIE)
    return c.value if c else None


def test_lembrar_dispensa_o_codigo_so_neste_navegador(client):
    _limpar()
    segredo = _preparar(totp=True)
    r = _entrar_com_codigo(client, segredo, lembrar=False)
    assert r.headers['Location'].endswith('/') and _cookie(client) is None
    client.post('/logout')

    r = _entrar_com_codigo(client, segredo, lembrar=True)
    assert r.headers['Location'].endswith('/')
    set_cookie = r.headers['Set-Cookie']
    assert 'HttpOnly' in set_cookie and 'SameSite=Lax' in set_cookie and 'Max-Age=2592000' in set_cookie
    valor = _cookie(client)
    conn = database.get_db()
    row = conn.execute('SELECT * FROM dispositivos_confiaveis').fetchone()
    conn.close()
    assert row['rotulo'] == 'Chrome no Windows' and valor.split('.', 1)[1] not in row['segredo_hash']

    # mesmo navegador: senha certa entra direto; senha errada continua errada
    client.post('/logout')
    assert 'incorretos' in client.post('/login', data={'usuario': 'user2', 'senha': 'errada'}).get_data(as_text=True)
    r = _so_senha(client)
    assert r.headers['Location'].endswith('/') and client.get('/pulse').status_code == 200
    assert _auditoria('login')[-1]['detalhes'] == '{"metodo": "dispositivo_lembrado"}'

    # outro navegador (sem o cookie) ou cookie adulterado: pede o código
    outro = central.app.test_client()
    assert _so_senha(outro).headers['Location'].endswith('/login/2fa')
    outro.set_cookie(COOKIE, valor[:-2] + 'xx')
    assert _so_senha(outro).headers['Location'].endswith('/login/2fa')
    # o cookie de um usuário não serve para outro
    outro.set_cookie(confiaveis.nome_do_cookie(3), valor)
    conn = database.get_db()
    assert confiaveis.reconhecer(conn, 3, valor) is None
    conn.close()


def test_vence_e_e_esquecido_quando_a_seguranca_muda(client):
    _limpar()
    segredo = _preparar(totp=True)
    _entrar_com_codigo(client, segredo, lembrar=True)
    este = confiaveis.id_do_cookie(_cookie(client))
    conn = database.get_db()
    outro_valor, _ = confiaveis.lembrar(conn, 2, 'Mozilla/5.0 (iPhone) Safari/604', '10.0.0.9')
    conn.commit(); conn.close()

    # Minha conta lista os dois e marca este aparelho
    html = client.get('/conta').get_data(as_text=True)
    assert 'Chrome no Windows' in html and 'Safari no iPhone/iPad' in html and 'Este aparelho' in html

    # sair dos outros aparelhos esquece só os outros
    assert client.post('/api/v2/conta/sessoes/encerrar').status_code == 200
    conn = database.get_db()
    assert [r['id'] for r in conn.execute('SELECT id FROM dispositivos_confiaveis').fetchall()] == [este]

    # vencido: não vale e sai da tabela
    conn.execute('UPDATE dispositivos_confiaveis SET expira_em = ?', (time.time() - 1,))
    conn.commit(); conn.close()
    client.post('/logout')
    assert _so_senha(client).headers['Location'].endswith('/login/2fa')
    conn = database.get_db()
    assert conn.execute('SELECT COUNT(*) AS n FROM dispositivos_confiaveis').fetchone()['n'] == 0
    conn.close()

    # esquecer pela conta: o próximo login pede o código
    _entrar_com_codigo(client, segredo, lembrar=True)
    este = confiaveis.id_do_cookie(_cookie(client))
    assert client.post('/api/v2/conta/dispositivos/esquecer', json={'id': este}).status_code == 200
    assert client.post('/api/v2/conta/dispositivos/esquecer', json={'id': este}).status_code == 404
    client.post('/logout')
    assert _so_senha(client).headers['Location'].endswith('/login/2fa')

    # administrador redefine a senha: todos os aparelhos são esquecidos
    _entrar_com_codigo(client, segredo, lembrar=True)
    conn = database.get_db()
    confiaveis.esquecer(conn, 2)
    assert conn.execute('SELECT COUNT(*) AS n FROM dispositivos_confiaveis').fetchone()['n'] == 0
    conn.close()
    assert _auditoria('dispositivo_lembrado') and _auditoria('dispositivo_esquecido')


def test_limite_de_aparelhos_e_rotulos():
    _limpar()
    conn = database.get_db()
    for _ in range(confiaveis.MAX_POR_USUARIO + 3):
        confiaveis.lembrar(conn, 2, '', '')
    assert conn.execute('SELECT COUNT(*) AS n FROM dispositivos_confiaveis WHERE usuario_id = 2').fetchone()['n'] == confiaveis.MAX_POR_USUARIO
    conn.commit(); conn.close()
    assert confiaveis.rotulo('Mozilla/5.0 (Linux; Android 14) Chrome/130 Mobile Safari/537') == 'Chrome no Android'
    assert confiaveis.rotulo('Mozilla/5.0 (Windows NT 10.0) Chrome/130 Safari/537 Edg/130') == 'Edge no Windows'
    assert confiaveis.rotulo('Mozilla/5.0 (Macintosh; Mac OS X 14) Version/17 Safari/605') == 'Safari no Mac'
    assert confiaveis.rotulo(None) == 'Navegador'
    _limpar()
