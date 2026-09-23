"""Persistent Telegram outbox. No tokens or request URLs are stored in diagnostics."""
import hashlib
import json
import logging
import time
import urllib.error
import urllib.request

import database

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def credentials(client_id, master_token, master_chat):
    token, chat, source = master_token.strip(), master_chat.strip(), 'master'
    if client_id:
        conn = database.get_db()
        try:
            row = conn.execute('SELECT telegram_token, telegram_chat_id FROM clientes WHERE id = ?', (client_id,)).fetchone()
            if row and (row['telegram_token'] or row['telegram_chat_id']):
                # A partial tenant configuration must not silently route to another bot.
                token, chat = (row['telegram_token'] or '').strip(), (row['telegram_chat_id'] or '').strip()
                source = f'cliente:{client_id}'
        finally:
            conn.close()
    return token, chat, source


def deliver(token, chat, message):
    if not token or token == 'SEU_TOKEN_AQUI':
        return dict(ok=False, code='missing_token', erro='Token não configurado.', transient=False)
    if not chat:
        return dict(ok=False, code='missing_chat', erro='Chat ID não configurado.', transient=False)
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',
        data=json.dumps(dict(chat_id=chat, text=message, parse_mode='HTML')).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError('Invalid Telegram response')
        if body.get('ok') is True and 200 <= status < 300:
            return dict(ok=True, code='sent', http_status=status, transient=False)
        status = int(body.get('error_code', status))
        description = str(body.get('description', 'Resposta rejeitada.')).replace(token, '[redigido]')[:300]
        if status in (401, 404):
            code, hint = 'invalid_token', 'Token inválido ou revogado. Verifique no BotFather.'
        elif status == 403:
            code, hint = 'permissions', 'Bot bloqueado ou sem permissão para enviar neste chat.'
        elif status == 400 and 'chat' in description.lower():
            code, hint = 'invalid_chat', 'Chat ID inválido/inacessível. Inicie o bot ou adicione-o ao grupo.'
        elif status == 429:
            code, hint = 'rate_limit', 'Limite do Telegram; nova tentativa respeita retry_after.'
        elif status >= 500:
            code, hint = 'unavailable', 'Telegram temporariamente indisponível.'
        else:
            code, hint = 'rejected', 'Telegram rejeitou a mensagem.'
        retry = max(1, min(86400, int((body.get('parameters') or {}).get('retry_after', 2))))
        return dict(ok=False, code=code, erro=f'{hint} {description}', http_status=status,
                    transient=status == 429 or status >= 500, retry_after=retry)
    except (urllib.error.URLError, TimeoutError, OSError):
        # Exception strings may contain the URL and bot token.
        return dict(ok=False, code='network', erro='Falha de DNS, TLS, conexão ou timeout ao Telegram. Entrega não confirmada.', transient=True, retry_after=2)
    except (ValueError, TypeError):
        return dict(ok=False, code='invalid_response', erro='Resposta inválida da API Telegram.', transient=True, retry_after=2)


def record_status(source, result):
    now = time.time()
    conn = database.get_db()
    try:
        conn.execute('''INSERT INTO telegram_status (destination, result, last_attempt, last_success, last_failure)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(destination) DO UPDATE SET
            result=excluded.result, last_attempt=excluded.last_attempt,
            last_success=COALESCE(excluded.last_success, telegram_status.last_success),
            last_failure=COALESCE(excluded.last_failure, telegram_status.last_failure)''',
            (source, json.dumps(result), now, now if result['ok'] else None, None if result['ok'] else now))
        conn.commit()
    finally:
        conn.close()


def enqueue(message, client_id=None, conn=None):
    now = time.time()
    key = hashlib.sha256(f'{client_id}:{message}'.encode()).hexdigest()
    owns_connection = conn is None
    conn = conn or database.get_db()
    try:
        cur = conn.execute('''INSERT INTO telegram_outbox (event_key, message, client_id, attempts, next_at, expires_at, state)
            VALUES (?, ?, ?, 0, ?, ?, 'pending') ON CONFLICT(event_key) DO UPDATE SET
            message=excluded.message, attempts=0, next_at=excluded.next_at,
            expires_at=excluded.expires_at, state='pending'
            WHERE telegram_outbox.expires_at < ? AND telegram_outbox.state IN ('sent', 'failed')''',
            (key, message, client_id, now, now + 300, now))
        queued = cur.rowcount > 0
        if owns_connection:
            conn.commit()
        return dict(ok=True, queued=queued, duplicate=not queued)
    finally:
        if owns_connection:
            conn.close()


def process_one(master_token, master_chat):
    now = time.time()
    conn = database.get_db()
    try:
        row = conn.execute("SELECT * FROM telegram_outbox WHERE state IN ('pending', 'sending') AND next_at <= ? ORDER BY next_at LIMIT 1", (now,)).fetchone()
        if row is None:
            return False
        row = dict(row)
        # Atomic lease prevents two workers delivering the same queued event.
        claim = conn.execute("UPDATE telegram_outbox SET state='sending', next_at=? WHERE event_key=? AND next_at=?", (now + 60, row['event_key'], row['next_at']))
        conn.commit()
        if claim.rowcount != 1:
            return False
    finally:
        conn.close()
    token, chat, source = credentials(row['client_id'], master_token, master_chat)
    result = deliver(token, chat, row['message'])
    attempts = row['attempts'] + 1
    result.update(origem=source, attempts=attempts)
    record_status(source, result)
    retry = not result['ok'] and result.get('transient') and attempts < MAX_ATTEMPTS
    state = 'pending' if retry else ('sent' if result['ok'] else 'failed')
    conn = database.get_db()
    try:
        conn.execute('UPDATE telegram_outbox SET attempts=?, state=?, next_at=?, result=? WHERE event_key=?',
            (attempts, state, time.time() + max(2 ** attempts, result.get('retry_after', 0)), json.dumps(result), row['event_key']))
        conn.execute("DELETE FROM telegram_outbox WHERE state IN ('sent', 'failed') AND expires_at < ?", (now - 7 * 86400,))
        conn.commit()
    finally:
        conn.close()
    log.info('Telegram destination=%s code=%s attempt=%s', source, result['code'], attempts)
    return True


def status(client_id, master_token, master_chat):
    token, chat, source = credentials(client_id, master_token, master_chat)
    conn = database.get_db()
    try:
        row = conn.execute('SELECT * FROM telegram_status WHERE destination=?', (source,)).fetchone()
        result = dict(row) if row else {}
        if result:
            result['result'] = json.loads(result['result'])
        result.update(configured=bool(token and chat and token != 'SEU_TOKEN_AQUI'), origem=source)
        result['status'] = ('Não configurado' if not result['configured'] else
            ('Configurado · ainda não testado' if not row else ('Operacional' if result['result']['ok'] else 'Falha')))
        return result
    finally:
        conn.close()
