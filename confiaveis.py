"""Dispositivos lembrados na verificação em duas etapas.

Depois do código certo, quem marca "lembrar este dispositivo" recebe um cookie com um segredo aleatório; no banco
fica só o hash. Nos próximos 30 dias, senha certa nesse navegador dispensa o código. A senha continua sempre exigida.

Deixa de valer quando: passam os 30 dias; o usuário esquece o aparelho em Minha conta; troca a senha ou sai dos outros
aparelhos (os outros são esquecidos, este continua); desativa a verificação; um administrador redefine a senha ou a
verificação. Cada usuário guarda no máximo MAX_POR_USUARIO aparelhos; o mais antigo sai.
"""
import hashlib
import hmac
import re
import secrets
import time

VALIDADE_DIAS = 30
MAX_POR_USUARIO = 10


def nome_do_cookie(usuario_id):
    return f'nh_disp_{int(usuario_id)}'


def _hash(segredo):
    return hashlib.sha256(segredo.encode()).hexdigest()


def rotulo(user_agent):
    """'Chrome no Windows' a partir do User-Agent; só para a pessoa reconhecer o aparelho na lista."""
    ua = user_agent or ''
    navegador = next((nome for padrao, nome in ((r'Edg/', 'Edge'), (r'OPR/|Opera', 'Opera'), (r'SamsungBrowser', 'Samsung Internet'),
                                                 (r'Firefox/|FxiOS', 'Firefox'), (r'Chrome/|CriOS', 'Chrome'), (r'Safari/', 'Safari'))
                      if re.search(padrao, ua)), 'Navegador')
    sistema = next((nome for padrao, nome in ((r'iPhone|iPad', 'iPhone/iPad'), (r'Android', 'Android'), (r'Windows', 'Windows'),
                                               (r'Mac OS X|Macintosh', 'Mac'), (r'CrOS', 'Chromebook'), (r'Linux', 'Linux'))
                    if re.search(padrao, ua)), None)
    return f'{navegador} no {sistema}' if sistema else navegador


def lembrar(conn, usuario_id, user_agent, ip, agora=None):
    """Grava o aparelho e devolve (valor do cookie, segundos de validade)."""
    agora = agora or time.time()
    segredo = secrets.token_urlsafe(32)
    row = conn.execute('INSERT INTO dispositivos_confiaveis (usuario_id, segredo_hash, rotulo, ip, criado_em, expira_em, usado_em) '
                       'VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id',
                       (usuario_id, _hash(segredo), rotulo(user_agent)[:60], (ip or '')[:64], agora,
                        agora + VALIDADE_DIAS * 86400, agora)).fetchone()
    antigos = conn.execute('SELECT id FROM dispositivos_confiaveis WHERE usuario_id = ? ORDER BY criado_em DESC',
                           (usuario_id,)).fetchall()[MAX_POR_USUARIO:]
    for r in antigos:
        conn.execute('DELETE FROM dispositivos_confiaveis WHERE id = ?', (r['id'],))
    return f"{row['id']}.{segredo}", VALIDADE_DIAS * 86400


def _ler(valor):
    try:
        ident, segredo = (valor or '').split('.', 1)
        return int(ident), segredo
    except ValueError:
        return None, None


def reconhecer(conn, usuario_id, valor_cookie, agora=None):
    """Id do aparelho lembrado se o cookie vale para este usuário; senão None. Atualiza o último uso."""
    agora = agora or time.time()
    ident, segredo = _ler(valor_cookie)
    if not ident or not segredo:
        return None
    row = conn.execute('SELECT id, usuario_id, segredo_hash, expira_em FROM dispositivos_confiaveis WHERE id = ?', (ident,)).fetchone()
    if not row or row['usuario_id'] != usuario_id or not hmac.compare_digest(row['segredo_hash'], _hash(segredo)):
        return None
    if row['expira_em'] < agora:
        conn.execute('DELETE FROM dispositivos_confiaveis WHERE id = ?', (ident,))
        return None
    conn.execute('UPDATE dispositivos_confiaveis SET usado_em = ? WHERE id = ?', (agora, ident))
    return ident


def id_do_cookie(valor_cookie):
    return _ler(valor_cookie)[0]


def listar(conn, usuario_id, agora=None):
    agora = agora or time.time()
    conn.execute('DELETE FROM dispositivos_confiaveis WHERE usuario_id = ? AND expira_em < ?', (usuario_id, agora))
    return [dict(r) for r in conn.execute('SELECT id, rotulo, criado_em, usado_em, expira_em FROM dispositivos_confiaveis '
                                          'WHERE usuario_id = ? ORDER BY usado_em DESC', (usuario_id,)).fetchall()]


def esquecer(conn, usuario_id, dispositivo_id=None, exceto=None):
    """Um aparelho (dispositivo_id) ou todos, menos `exceto`. Devolve quantos saíram."""
    sql, params = 'DELETE FROM dispositivos_confiaveis WHERE usuario_id = ?', [usuario_id]
    if dispositivo_id is not None:
        sql += ' AND id = ?'
        params.append(dispositivo_id)
    if exceto is not None:
        sql += ' AND id <> ?'
        params.append(exceto)
    return conn.execute(sql, params).rowcount
