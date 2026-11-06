"""NexoHub: verificação em duas etapas (TOTP, RFC 6238), códigos de recuperação, bloqueio de login e senha forte.

O segredo TOTP de cada usuário fica cifrado no banco com uma chave derivada de FLASK_SECRET_KEY:
sem essa variável a ativação é recusada (uma chave efêmera perderia os segredos no próximo restart).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

EMISSOR = 'NexoHub (MD)'
PASSO_S = 30
DIGITOS = 6
JANELA = 1  # aceita o código do passo anterior e do seguinte (relógio do celular adiantado/atrasado)
CODIGOS_RECUPERACAO = 10

# Bloqueio de login por conta (inclusive contas que não existem, para não revelar quais existem).
MAX_FALHAS = 5
JANELA_FALHAS_S = 15 * 60
BLOQUEIO_S = 15 * 60

SENHA_MINIMO = 10
SENHAS_COMUNS = {'1234567890', '0123456789', 'senha12345', 'senha123456', 'password123', 'qwerty1234',
                 'abcdefghij', 'abc1234567', 'nexopulse1', 'nexohub123', 'mudar12345', 'admin12345'}


# ===== TOTP =====

def gerar_segredo():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def _chave(segredo):
    return base64.b32decode(segredo + '=' * (-len(segredo) % 8), casefold=True)


def codigo(segredo, passo):
    digest = hmac.new(_chave(segredo), struct.pack('>Q', passo), hashlib.sha1).digest()
    deslocamento = digest[-1] & 0x0F
    valor = struct.unpack('>I', digest[deslocamento:deslocamento + 4])[0] & 0x7FFFFFFF
    return str(valor % 10 ** DIGITOS).zfill(DIGITOS)


def passo_atual(agora=None):
    return int((time.time() if agora is None else agora) // PASSO_S)


def verificar_totp(segredo, digitado, ultimo_passo=None, agora=None):
    """Passo aceito (para gravar e impedir reuso) ou None. Recusa código já usado ou de passo anterior a ele."""
    digitado = re.sub(r'\s', '', str(digitado or ''))
    if not re.fullmatch(rf'\d{{{DIGITOS}}}', digitado):
        return None
    atual = passo_atual(agora)
    for passo in range(atual - JANELA, atual + JANELA + 1):
        if ultimo_passo is not None and passo <= ultimo_passo:
            continue
        if hmac.compare_digest(codigo(segredo, passo), digitado):
            return passo
    return None


def uri_otpauth(segredo, conta):
    rotulo = quote(f'{EMISSOR}:{conta}')
    return f'otpauth://totp/{rotulo}?secret={segredo}&issuer={quote(EMISSOR)}&algorithm=SHA1&digits={DIGITOS}&period={PASSO_S}'


# ===== Segredo cifrado no banco =====

def cifra_disponivel():
    return bool(os.environ.get('FLASK_SECRET_KEY'))


def _fernet():
    chave = os.environ.get('FLASK_SECRET_KEY')
    if not chave:
        raise RuntimeError('Configure FLASK_SECRET_KEY para usar a verificação em duas etapas.')
    derivada = hashlib.sha256(b'nexohub-2fa:' + chave.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derivada))


def cifrar(segredo):
    return _fernet().encrypt(segredo.encode()).decode()


def decifrar(cifrado):
    try:
        return _fernet().decrypt(str(cifrado).encode()).decode()
    except (InvalidToken, ValueError):
        return None


# ===== Códigos de recuperação (uso único, guardados só como hash) =====

def _normalizar(codigo_rec):
    return re.sub(r'[^a-z2-7]', '', str(codigo_rec or '').lower())


def _hash_codigo(codigo_rec):
    return hashlib.sha256(_normalizar(codigo_rec).encode()).hexdigest()


def gerar_codigos_recuperacao():
    """(códigos para mostrar uma única vez, JSON com os hashes para guardar)."""
    codigos = []
    for _ in range(CODIGOS_RECUPERACAO):
        bruto = base64.b32encode(secrets.token_bytes(5)).decode().lower()
        codigos.append(f'{bruto[:4]}-{bruto[4:8]}')
    return codigos, json.dumps([_hash_codigo(c) for c in codigos])


def usar_codigo_recuperacao(guardados_json, digitado):
    """JSON atualizado (sem o código usado) se o código vale; None se não vale."""
    if len(_normalizar(digitado)) != 8:
        return None
    try:
        guardados = json.loads(guardados_json or '[]')
    except ValueError:
        return None
    alvo = _hash_codigo(digitado)
    restantes = [h for h in guardados if not hmac.compare_digest(h, alvo)]
    return json.dumps(restantes) if len(restantes) < len(guardados) else None


def codigos_restantes(guardados_json):
    try:
        return len(json.loads(guardados_json or '[]'))
    except ValueError:
        return 0


# ===== Bloqueio de login =====

def _chave_conta(usuario):
    return 'u:' + str(usuario or '').strip().lower()[:80]


def bloqueado_ate(conn, usuario, agora=None):
    """Timestamp até quando a conta está bloqueada, ou None."""
    agora = time.time() if agora is None else agora
    row = conn.execute('SELECT bloqueado_ate FROM tentativas_login WHERE chave=?', (_chave_conta(usuario),)).fetchone()
    if row and row['bloqueado_ate'] and row['bloqueado_ate'] > agora:
        return row['bloqueado_ate']
    return None


def registrar_falha(conn, usuario, agora=None):
    """Conta a falha; devolve o timestamp do bloqueio quando ele começa agora (senão None)."""
    agora = time.time() if agora is None else agora
    chave = _chave_conta(usuario)
    row = conn.execute('SELECT falhas, janela_inicio FROM tentativas_login WHERE chave=?', (chave,)).fetchone()
    if not row or agora - (row['janela_inicio'] or 0) > JANELA_FALHAS_S:
        falhas, inicio = 1, agora
    else:
        falhas, inicio = row['falhas'] + 1, row['janela_inicio']
    ate = agora + BLOQUEIO_S if falhas >= MAX_FALHAS else None
    conn.execute('INSERT INTO tentativas_login (chave, falhas, janela_inicio, bloqueado_ate) VALUES (?, ?, ?, ?) '
                 'ON CONFLICT (chave) DO UPDATE SET falhas=excluded.falhas, janela_inicio=excluded.janela_inicio, '
                 'bloqueado_ate=excluded.bloqueado_ate',
                 (chave, 0 if ate else falhas, agora if ate else inicio, ate))
    return ate


def limpar_falhas(conn, usuario):
    conn.execute('DELETE FROM tentativas_login WHERE chave=?', (_chave_conta(usuario),))


def minutos_restantes(ate, agora=None):
    agora = time.time() if agora is None else agora
    return max(1, int((ate - agora + 59) // 60))


# ===== Senha forte =====

def validar_senha_forte(senha, usuario=''):
    """Mensagem de erro ou None. Vale para senhas novas (as atuais continuam entrando)."""
    if not isinstance(senha, str) or len(senha) < SENHA_MINIMO:
        return f'A senha precisa ter pelo menos {SENHA_MINIMO} caracteres.'
    if len(senha) > 256 or any(ord(c) < 32 for c in senha):
        return 'Senha com tamanho ou caracteres não permitidos.'
    if not re.search(r'[A-Za-zÀ-ÿ]', senha) or not re.search(r'\d', senha):
        return 'A senha precisa ter letras e números.'
    if senha.lower() in SENHAS_COMUNS or (usuario and usuario.lower() in senha.lower()):
        return 'Essa senha é fácil de adivinhar. Escolha outra.'
    return None
