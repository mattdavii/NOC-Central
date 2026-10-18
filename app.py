from flask import Flask, jsonify, request, render_template, session, redirect, url_for, flash, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
from flask_socketio import SocketIO
import database
import notifications
from location import parse_location, save_location
from version import CENTRAL_VERSION
from urllib.parse import urlsplit
import urllib.request, json
import os
import jwt
import html
import hmac
import math
import secrets
import threading
import time
import urllib.error
import re
import hashlib
from datetime import datetime, timedelta, timezone

# Chave privada RSA usada para assinar os tokens de acesso local do agente (agente_v2.py guarda só a pública)
JWT_PRIVATE_KEY = os.environ.get('JWT_PRIVATE_KEY', '')

app = Flask(__name__)

# Em produção, configure FLASK_SECRET_KEY no Railway. Sem ela, usamos uma
# chave efêmera segura (as sessões serão invalidadas a cada restart/deploy).
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
if not os.environ.get("FLASK_SECRET_KEY"):
    print("⚠️ FLASK_SECRET_KEY ausente: usando chave efêmera. Configure-a no Railway.")

SOCKETIO_ALLOWED_ORIGINS = os.environ.get("SOCKETIO_ALLOWED_ORIGINS") or None
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '1') != '0',
    MAX_CONTENT_LENGTH=1024 * 1024,
    CSRF_PROTECT=os.environ.get('CSRF_PROTECT', '1') != '0',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

socketio = SocketIO(app, cors_allowed_origins=SOCKETIO_ALLOWED_ORIGINS, async_mode='gevent')

# ==========================================
# ⚡ TRADUTOR DE SQL (SQLITE <-> POSTGRES)
# ==========================================
def db_execute(conn, sql, params=()):
    """Garante que comandos com '?' funcionem no Neon(Postgres) mudando para '%s' automaticamente"""
    if bool(os.environ.get('DATABASE_URL')) and params:
        sql = sql.replace('?', '%s')
    if params:
        return conn.execute(sql, params)
    return conn.execute(sql)

# =========================================================
# 🤖 CHAVES DO TELEGRAM MASTER (O SEU BOT DE ADMIN)
# =========================================================
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# Autenticação progressiva dos sensores. Chaves individuais podem ser
# provisionadas sem interromper agentes antigos; a exigência é ativada em etapa separada.
# NOC_SENSOR_API_KEY na Central é apenas a opção compartilhada legada de transição.
NOC_SENSOR_API_KEY = os.environ.get("NOC_SENSOR_API_KEY", "").strip()
NOC_SENSOR_AUTH_REQUIRED = os.environ.get("NOC_SENSOR_AUTH_REQUIRED", "0").strip().lower() in ('1', 'true', 'yes', 'on')
if not NOC_SENSOR_API_KEY:
    print("⚠️ NOC_SENSOR_API_KEY ausente: API de sensores em modo compatibilidade.")
if not NOC_SENSOR_AUTH_REQUIRED:
    print("ℹ️ Credenciais individuais de sensores em adoção gradual (não obrigatórias).")

CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()

MUTATING_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
ALLOWED_ROLES = {
    'Administrador Master', 'Operador Master', 'Cliente',
    'Administrador Cliente', 'Operador Cliente',
}


def bounded_text(value, field, *, maximum=160, minimum=1, allow_empty=False):
    """Normaliza texto externo e rejeita HTML/controles como defesa adicional."""
    if value is None and allow_empty:
        return ''
    if not isinstance(value, str):
        raise ValueError(f'{field} inválido.')
    value = value.strip()
    if allow_empty and not value:
        return ''
    if not minimum <= len(value) <= maximum:
        raise ValueError(f'{field} deve ter entre {minimum} e {maximum} caracteres.')
    if any(ord(char) < 32 for char in value) or '<' in value or '>' in value:
        raise ValueError(f'{field} contém caracteres não permitidos.')
    return value


def bounded_secret(value, field, *, maximum=256, minimum=8):
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ValueError(f'{field} deve ter entre {minimum} e {maximum} caracteres.')
    if any(ord(char) < 32 for char in value):
        raise ValueError(f'{field} contém caracteres não permitidos.')
    return value


def get_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@app.before_request
def enforce_csrf():
    if not app.config.get('CSRF_PROTECT') or request.method not in MUTATING_METHODS:
        return None
    # Sensores não usam sessão de navegador. A transição Linux/Windows continua
    # compatível e é protegida separadamente pela credencial do agente.
    browser_request = bool(session.get('user_id')) or request.endpoint == 'login'
    if not browser_request:
        return None
    expected = session.get('_csrf_token')
    supplied = request.headers.get('X-CSRF-Token') or request.form.get('_csrf_token', '')
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return jsonify(error='Token de segurança ausente ou inválido.'), 403
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(self), camera=(), microphone=()')
    response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
        "https://unpkg.com https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' "
        "https://fonts.googleapis.com https://unpkg.com https://cdnjs.cloudflare.com; "
        "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; "
        "media-src 'self' https://assets.mixkit.co; connect-src 'self' https://api.ipify.org wss:"
    )
    return response


def _sensor_request_identity():
    """Resolve o sensor sem confiar em uma única origem manipulável."""
    args = request.view_args or {}
    data = request.get_json(silent=True) if request.is_json else {}
    data = data if isinstance(data, dict) else {}
    candidates = [
        request.headers.get('X-NOC-Sensor-ID'),
        args.get('mac_id'), args.get('mac_sensor'), args.get('mac'),
        data.get('mac_id'), data.get('sensor_mac'),
    ]
    identities = {value.strip() for value in candidates if isinstance(value, str) and value.strip()}
    if len(identities) > 1:
        return None, (jsonify(error='Identificadores de sensor divergentes.'), 400)
    if not identities:
        return None, None
    identity = identities.pop()
    if len(identity) > 80 or any(ord(char) < 32 for char in identity) or '<' in identity or '>' in identity:
        return None, (jsonify(error='Identificador do sensor inválido.'), 400)
    return identity, None


def _sensor_child_scope_error(conn, sensor_id):
    """Impede uma credencial individual de alterar registros de outro sensor."""
    child_tables = {
        'reportar_latencia_custom': 'ips_custom',
        'reportar_latencia_energia': 'ips_energia',
        'reportar_status_servico': 'servicos_os',
    }
    table = child_tables.get(request.endpoint)
    if not table:
        return None
    data = request.get_json(silent=True) or {}
    record_id = data.get('id') if isinstance(data, dict) else None
    if not isinstance(record_id, int):
        return jsonify(error='Identificador do registro inválido.'), 400
    row = conn.execute(f'SELECT sensor_mac FROM {table} WHERE id=?', (record_id,)).fetchone()
    if not row:
        return jsonify(error='Registro não encontrado.'), 404
    if row['sensor_mac'] != sensor_id:
        return jsonify(error='Registro pertence a outro sensor.'), 403
    return None


def validar_sensor_ou_sessao():
    if session.get("user_id"):
        return None
    fornecida = request.headers.get("X-NOC-Sensor-Key", "")
    sensor_id, identity_error = _sensor_request_identity()
    if identity_error:
        return identity_error
    if fornecida:
        # A chave compartilhada continua aceita durante a migração dos agentes.
        if NOC_SENSOR_API_KEY and hmac.compare_digest(fornecida, NOC_SENSOR_API_KEY):
            return None
        if sensor_id:
            conn = database.get_db()
            try:
                credential = conn.execute(
                    'SELECT key_hash FROM sensor_credentials WHERE sensor_mac=? AND revoked_at IS NULL',
                    (sensor_id,),
                ).fetchone()
                supplied_hash = hashlib.sha256(fornecida.encode('utf-8')).hexdigest()
                if credential and hmac.compare_digest(str(credential['key_hash']), supplied_hash):
                    scope_error = _sensor_child_scope_error(conn, sensor_id)
                    return scope_error
            finally:
                conn.close()
        # Uma chave apresentada, mas inválida, nunca cai silenciosamente no modo legado.
        return jsonify({"error": "Sensor não autenticado"}), 401
    if NOC_SENSOR_API_KEY or NOC_SENSOR_AUTH_REQUIRED:
        return jsonify({"error": "Sensor não autenticado"}), 401
    # Modo legado explicitamente preservado até a conclusão do rollout Windows/Linux.
    return None


def exigir_sensor_ou_sessao():
    erro = validar_sensor_ou_sessao()
    if erro:
        return erro
    return None

def enviar_telegram(mensagem, cliente_id=None, conn=None):
    """O grupo NexoPulse recebe todos os eventos; o grupo do cliente, só os dele."""
    owns_connection = conn is None
    conn = conn or database.get_db()
    try:
        mensagem_master = mensagem
        if cliente_id:
            row = conn.execute('SELECT nome, usuario FROM clientes WHERE id = ?', (cliente_id,)).fetchone()
            nome_cliente = (row['nome'] or row['usuario']) if row else f'#{cliente_id}'
            mensagem_master = f"{mensagem}\n🏢 <b>Cliente:</b> {html.escape(str(nome_cliente))}"
        resultado = notifications.enqueue(mensagem_master, None, conn=conn)
        if cliente_id and notifications.client_has_chat(cliente_id, conn):
            notifications.enqueue(mensagem, cliente_id, conn=conn)
        if owns_connection:
            conn.commit()
        return resultado
    finally:
        if owns_connection:
            conn.close()


@app.route("/api/v2/testar_telegram/<int:cliente_id>", methods=["POST"])
@app.route("/api/v2/status_telegram/<int:cliente_id>", methods=["GET"])
def testar_telegram(cliente_id):
    if "user_id" not in session or session.get("role") not in [
        "Administrador Master", "Cliente", "Administrador Cliente"
    ]:
        return jsonify({"error": "Acesso Negado"}), 403

    role = session.get("role")
    if role == "Cliente" and session.get("user_id") != cliente_id:
        return jsonify({"error": "Acesso Negado"}), 403
    if role == "Administrador Cliente":
        conn = database.get_db()
        try:
            info = db_execute(
                conn,
                "SELECT cliente_pai_id FROM clientes WHERE id = ?",
                (session.get("user_id"),),
            ).fetchone()
            if not info or info["cliente_pai_id"] != cliente_id:
                return jsonify({"error": "Acesso Negado"}), 403
        finally:
            conn.close()

    destino = None if cliente_id == 0 else cliente_id
    if request.method == 'GET':
        return jsonify(notifications.status(destino, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID))
    token, chat, source = notifications.credentials(destino, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    resultado = notifications.deliver(token, chat, '✅ <b>TESTE NEXOPULSE CENTRAL</b>\nIntegração de notificações.')
    resultado['origem'] = source
    notifications.record_status(source, resultado)
    return jsonify(resultado), (200 if resultado['ok'] else 502)

# Migrations are explicit and fail fast instead of serving against a partial schema.
database.init_db()
conn = database.get_db()
try:
    if not conn.execute("SELECT id FROM clientes WHERE usuario='admin'").fetchone():
        initial_password = os.environ.get('ADMIN_SENHA_INICIAL')
        if not initial_password:
            raise RuntimeError('Configure ADMIN_SENHA_INICIAL para criar o primeiro administrador.')
        conn.execute("INSERT INTO clientes (usuario, senha, role, ativo) VALUES ('admin', ?, 'Administrador Master', 1)",
                     (generate_password_hash(initial_password),))
        conn.commit()
finally:
    conn.close()


def _telegram_worker():
    while True:
        try:
            notifications.process_one(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        except Exception:
            app.logger.exception('Falha no processamento da fila Telegram')
        time.sleep(1)

if os.environ.get('DISABLE_BACKGROUND_GUARDIAN', '0') != '1':
    threading.Thread(target=_telegram_worker, name='noc-telegram', daemon=True).start()


@app.context_processor
def product_context():
    return {'central_version': CENTRAL_VERSION, 'csrf_token': get_csrf_token}


@socketio.on('connect')
def socket_connect(auth=None):
    return bool(session.get('user_id'))


def tenant_id(conn):
    if session.get('role') == 'Cliente':
        return session['user_id']
    row = conn.execute('SELECT cliente_pai_id FROM clientes WHERE id=?', (session['user_id'],)).fetchone()
    return row['cliente_pai_id'] if row else None


@app.before_request
def validate_json_body():
    # Agentes mandam Content-Type: application/json também em GET sem corpo.
    if request.method in ('GET', 'HEAD', 'DELETE') and not request.get_data(cache=True):
        return None
    if request.is_json and not isinstance(request.get_json(silent=True), dict):
        return jsonify(error='Objeto JSON válido obrigatório.'), 400


@app.before_request
def enforce_tenant_scope():
    if not session.get('user_id'):
        return None
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            return jsonify(error='Origem não autorizada.'), 403
    conn = database.get_db()
    try:
        current = conn.execute('SELECT ativo FROM clientes WHERE id=?', (session['user_id'],)).fetchone()
        if not current or current['ativo'] == 0:
            session.clear()
            return jsonify(error='Sessão inválida.'), 401
        master = session.get('role') in ('Administrador Master', 'Operador Master')
        tenant = None if master else tenant_id(conn)
        args = request.view_args or {}
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error='Objeto JSON obrigatório.'), 400
        if any(k in data and (not isinstance(data[k], str) or not 1 <= len(data[k]) <= 80) for k in ('mac_id', 'sensor_mac')):
            return jsonify(error='Identificador do sensor inválido.'), 400
        mac = args.get('mac_id') or args.get('mac_sensor') or args.get('mac') or data.get('mac_id') or data.get('sensor_mac')
        if mac and not master:
            row = conn.execute('SELECT cliente_id FROM sensores WHERE mac_id=?', (mac,)).fetchone()
            if not row or tenant is None or row['cliente_id'] != tenant:
                return jsonify(error='Acesso negado ao sensor.'), 403
        # Endpoints that refer to a child record must match both sensor and owner.
        id_tables = {'reportar_latencia_custom': 'ips_custom', 'reportar_latencia_energia': 'ips_energia',
                     'reportar_status_servico': 'servicos_os', 'del_ips_energia': 'ips_energia',
                     'del_servico_os': 'servicos_os', 'crud_ips': 'ips_custom'}
        if request.endpoint in id_tables:
            record_id = args.get('id_ip') or args.get('id_srv') or data.get('id')
            row = conn.execute(f"SELECT sensor_mac FROM {id_tables[request.endpoint]} WHERE id=?", (record_id,)).fetchone()
            if not row or (mac and row['sensor_mac'] != mac):
                return jsonify(error='Registro não encontrado.'), 404
            if not master:
                owner = conn.execute('SELECT cliente_id FROM sensores WHERE mac_id=?', (row['sensor_mac'],)).fetchone()
                if not owner or tenant is None or owner['cliente_id'] != tenant:
                    return jsonify(error='Acesso negado.'), 403
        if 'id_user' in args and not master:
            row = conn.execute('SELECT id, cliente_pai_id FROM clientes WHERE id=?', (args['id_user'],)).fetchone()
            if not row or tenant is None or not (row['id'] == tenant or row['cliente_pai_id'] == tenant):
                return jsonify(error='Acesso negado ao usuário.'), 403
        if request.endpoint == 'criar_usuario' and not master:
            if data.get('role') not in ('Administrador Cliente', 'Operador Cliente'):
                return jsonify(error='Perfil não autorizado.'), 403
    finally:
        conn.close()


@app.route('/privacidade')
def privacidade():
    # Página pública: exigida pelo Google para publicar o app OAuth do backup.
    return render_template('privacidade.html')


@app.route('/healthz')
def healthz():
    conn = database.get_db()
    try:
        conn.execute('SELECT 1').fetchone()
        return jsonify(
            status='ok', central_version=CENTRAL_VERSION,
            sensor_auth_required=NOC_SENSOR_AUTH_REQUIRED,
        )
    finally:
        conn.close()

SPEEDTEST_REQUESTS = set()
TRACEROUTE_REQUESTS = set()
UPDATE_REQUESTS = set()
PENDING_COMMANDS = {}
AUTO_SPEEDTEST_DONE = set()

GUARDIAO_INTERVALO = max(10, int(os.environ.get("GUARDIAO_INTERVALO", "15")))
_guardiao_lock = threading.Lock()
_guardiao_ultima_execucao = 0.0

PING_HISTORY_INTERVAL = max(30, int(os.environ.get("PING_HISTORY_INTERVAL", "60")))
_ultimo_historico_ping = {}


def enfileirar_comando(sensor_mac, comando, conn=None):
    """Fila persistente: sobrevive a restart e permite múltiplos workers."""
    if not sensor_mac or not comando:
        return False
    propria = conn is None
    if propria:
        conn = database.get_db()
    try:
        db_execute(
            conn,
            "INSERT INTO comandos_pendentes (sensor_mac, comando) VALUES (?, ?)",
            (sensor_mac, comando),
        )
        conn.commit()
        return True
    finally:
        if propria:
            conn.close()


def consumir_comando(conn, sensor_mac):
    """Remove e devolve, de forma atômica no Postgres, o comando mais antigo."""
    if os.environ.get("DATABASE_URL"):
        row = db_execute(
            conn,
            """
            DELETE FROM comandos_pendentes
            WHERE id = (
                SELECT id FROM comandos_pendentes
                WHERE sensor_mac = ?
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING comando
            """,
            (sensor_mac,),
        ).fetchone()
        conn.commit()
        return row["comando"] if row else "none"

    row = db_execute(
        conn,
        "SELECT id, comando FROM comandos_pendentes WHERE sensor_mac = ? ORDER BY id LIMIT 1",
        (sensor_mac,),
    ).fetchone()
    if not row:
        return "none"
    db_execute(conn, "DELETE FROM comandos_pendentes WHERE id = ?", (row["id"],))
    conn.commit()
    return row["comando"]


# ==========================================
# ⚡ FUNÇÃO GUARDIÃ GLOBAL (CRON DA NUVEM)
# ==========================================
def verificar_quedas_global(conn, force=False):
    global _guardiao_ultima_execucao

    agora_monotonic = time.monotonic()
    with _guardiao_lock:
        if not force and (agora_monotonic - _guardiao_ultima_execucao) < GUARDIAO_INTERVALO:
            return
        _guardiao_ultima_execucao = agora_monotonic

    caidos = []
    is_postgres = bool(os.environ.get('DATABASE_URL'))
    condicao = "last_seen < NOW() - INTERVAL '60 seconds'" if is_postgres else "last_seen < datetime('now', '-60 seconds')"

    try:
        # Puxa os sensores que acabaram de cair
        caidos = conn.execute(f"SELECT mac_id, nome_local, cliente_id FROM sensores WHERE status = 'online' AND em_manutencao = 0 AND (memoria_alerta = 'ONLINE' OR memoria_alerta IS NULL) AND {condicao}").fetchall()

        # Altera para OFFLINE na memória IMEDIATAMENTE (evita spam no Telegram)
        conn.execute(f"UPDATE sensores SET status = 'offline', memoria_alerta = 'OFFLINE', alerta_reconhecido = 0 WHERE status = 'online' AND em_manutencao = 0 AND {condicao}")
        conn.commit()
    except Exception as e:
        print(f"Erro na varredura do Guardião: {e}")
        return

    # Processa os disparos reais de alertas para o Telegram
    for c in caidos:
        try:
            mac = c['mac_id'] if hasattr(c, 'keys') else c[0]
            nome = c['nome_local'] if hasattr(c, 'keys') else c[1]
            cid = c['cliente_id'] if hasattr(c, 'keys') else c[2]

            db_execute(conn, "INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, 'Queda de Conexão', 'Crítica', 'Sensor parou de responder.')", (mac,))
            conn.commit()

            enviar_telegram(f"🚨 <b>QUEDA CRÍTICA DETECTADA</b>\n\n🏢 <b>Host:</b> {html.escape(str(nome))}\n🆔 <b>MAC:</b> {html.escape(str(mac))}\n❌ <b>Status:</b> OFFLINE TOTAL", cliente_id=cid, conn=conn)
            conn.commit()
        except Exception as err:
            print(f"Erro ao emitir alerta de queda: {err}")


def _loop_guardiao_background():
    """Detecta quedas mesmo quando nenhum sensor novo está enviando telemetria."""
    while True:
        time.sleep(GUARDIAO_INTERVALO)
        conn = None
        try:
            conn = database.get_db()
            verificar_quedas_global(conn, force=True)
        except Exception as e:
            print(f"⚠️ Guardião em background: {e}")
        finally:
            if conn:
                conn.close()


if os.environ.get("DISABLE_BACKGROUND_GUARDIAN", "0") != "1":
    threading.Thread(
        target=_loop_guardiao_background,
        name="noc-guardiao",
        daemon=True,
    ).start()

# ==========================================
# 🔐 SISTEMA DE LOGIN E SESSÃO
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    erro = None
    if request.method == 'POST':
        usuario_digitado = request.form.get('usuario', '').strip()
        senha_digitada = request.form.get('senha', '').strip()
        conn = database.get_db()
        try:
            user = db_execute(conn, "SELECT * FROM clientes WHERE usuario = ?", (usuario_digitado,)).fetchone()

            stored_password = str(user['senha']) if user else ''
            is_hashed = stored_password.startswith(('scrypt:', 'pbkdf2:'))
            authenticated = check_password_hash(stored_password, senha_digitada) if is_hashed else hmac.compare_digest(stored_password, senha_digitada)
            if user and dict(user).get('ativo', 1) and authenticated:
                if not is_hashed:
                    conn.execute('UPDATE clientes SET senha=? WHERE id=?', (generate_password_hash(senha_digitada), user['id']))
                    conn.commit()
                session.clear()
                session['logged_in'] = True; session['usuario'] = user['usuario']; session['role'] = user['role']
                session['user_id'] = user['id']; session['logo_cliente'] = dict(user).get('logo_url', '')
                session['_csrf_token'] = secrets.token_urlsafe(32)
                session.permanent = True
                return redirect(url_for('index'))
            else:
                erro = "Usuário ou senha incorretos!"
        except Exception:
            app.logger.exception('Falha no processo de autenticação')
            erro = 'Não foi possível autenticar agora. Tente novamente.'
        finally: conn.close()

    return render_template('login.html', erro=erro)

@app.route('/logout', methods=['POST'])
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/api/v2/ack_alerta', methods=['POST'])
def ack_alerta():
    if 'usuario' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    if session.get('role') in ('Administrador Master', 'Operador Master'):
        conn.execute("UPDATE sensores SET alerta_reconhecido=1 WHERE status='offline'")
    else:
        conn.execute("UPDATE sensores SET alerta_reconhecido=1 WHERE status='offline' AND cliente_id=?", (tenant_id(conn),))
    db_execute(conn, "INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES ('SISTEMA', 'Acknowledge (Ciente)', 'Aviso', ?)", (f"Operador {session['usuario']} silenciou o alarme.",))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/')
def index():
    if 'usuario' not in session: return redirect(url_for('login'))
    return render_template('index.html', nome=session['usuario'])

@app.route('/tv')
def noc_tv():
    if 'usuario' not in session: return redirect(url_for('login'))
    return render_template('tv.html')

@app.route('/sensor/<mac_id>')
def painel_sensor(mac_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = database.get_db()
    sensor = db_execute(conn, "SELECT * FROM sensores WHERE mac_id = ?", (mac_id,)).fetchone()
    conn.close()
    if not sensor: return "Sensor não encontrado", 404
    return render_template('sensor.html', sensor=sensor, nome=session['usuario'])

# ==========================================
# 📥 RECEPÇÃO DE DADOS (AGENTE PARA NUVEM)
# ==========================================
@app.route('/api/v2/report_data', methods=['POST'])
def report_data():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    global AUTO_SPEEDTEST_DONE
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('mac_id'), str) or not 1 <= len(data['mac_id']) <= 80:
        return jsonify(error='mac_id obrigatório (até 80 caracteres).'), 400
    conn = None
    try:
        mac = data.get('mac_id')
        ip_display = data.get('ip_local')

        conn = database.get_db()

        # ⚡ CRON EMBUTIDO: Toda vez que chegar dado de qualquer sensor, a nuvem caça os caídos
        verificar_quedas_global(conn)

        try:
            from datetime import datetime
            agora_hora = datetime.now().hour
            hoje_id = datetime.now().strftime('%Y-%m-%d')
            if 'AUTO_SPEEDTEST_DONE' not in globals(): AUTO_SPEEDTEST_DONE = set()
            if agora_hora == 3 and f"{mac}_{hoje_id}" not in AUTO_SPEEDTEST_DONE:
                enfileirar_comando(mac, "run_speedtest", conn=conn)
                AUTO_SPEEDTEST_DONE.add(f"{mac}_{hoje_id}")
                if len(AUTO_SPEEDTEST_DONE) > 500: AUTO_SPEEDTEST_DONE.clear()
        except Exception: app.logger.exception('Falha em operação auxiliar')

        sensor = db_execute(conn, "SELECT * FROM sensores WHERE mac_id = ?", (mac,)).fetchone()
        # O agente sugere o hostname; nomes dados na Central nunca são sobrescritos.
        nome_sugerido = data.get('nome_local') if isinstance(data.get('nome_local'), str) else ''
        nome_sugerido = ''.join(ch for ch in nome_sugerido if ch.isprintable()).strip()[:160] or 'Novo Sensor'

        if sensor:
            sensor_dict = dict(sensor)

            estado_anterior = sensor_dict.get('memoria_alerta', 'ONLINE')

            if estado_anterior == 'OFFLINE' or sensor_dict.get('status') == 'offline':
                try:
                    db_execute(conn, "INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, 'Conexão Restaurada', 'Aviso', 'O sensor restabeleceu a comunicação com a rede')", (mac,))
                    enviar_telegram(f"✅ <b>CONEXÃO RESTAURADA</b>\n\n🖥️ <b>Sensor:</b> {html.escape(str(sensor_dict.get('nome_local', mac)))}\n🌐 <b>Status:</b> ONLINE", cliente_id=sensor_dict.get('cliente_id'), conn=conn)
                except Exception: app.logger.exception('Falha em operação auxiliar')

            db_execute(conn, '''UPDATE sensores SET
                ip_sensor = ?, cpu_usage = ?, ram_usage = ?, temp = ?, gpu_temp = ?,
                status = 'online', memoria_alerta = 'ONLINE', ping_gateway = ?, ping_global = ?,
                ip_gateway = ?, last_seen = CURRENT_TIMESTAMP,
                disco = ?, net_up = ?, net_down = ?, portas = ?,
                nome_local = CASE WHEN nome_local IS NULL OR nome_local IN ('Novo Sensor', 'Sensor Novo') THEN ? ELSE nome_local END
                WHERE mac_id = ?''',
                (ip_display, data.get('cpu_usage'), data.get('ram_usage'),
                 data.get('temp'), data.get('gpu_temp'), data.get('ping_gateway'),
                 data.get('ping_global'), data.get('ip_gateway'),
                 data.get('disco'), data.get('net_up'), data.get('net_down'), data.get('portas'),
                 nome_sugerido, mac))
            conn.commit()
        else:
            db_execute(conn, '''INSERT INTO sensores
                (mac_id, nome_local, ip_sensor, cpu_usage, ram_usage, temp, gpu_temp, status, memoria_alerta, lat, lon, ping_gateway, ping_global, ip_gateway, last_seen, alerta_reconhecido, em_manutencao)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'online', 'ONLINE', NULL, NULL, ?, ?, ?, CURRENT_TIMESTAMP, 1, 0)''',
                (mac, nome_sugerido, ip_display, data.get('cpu_usage'), data.get('ram_usage'),
                 data.get('temp'), data.get('gpu_temp'), data.get('ping_gateway'),
                 data.get('ping_global'), data.get('ip_gateway')))
            conn.commit()
            enviar_telegram(f"🎉 <b>NOVO SENSOR REGISTRADO</b>\n\n🖥️ <b>Nome:</b> {html.escape(nome_sugerido)}\n🔖 <b>MAC:</b> {html.escape(str(mac))}\n🌐 <b>IP:</b> {html.escape(str(ip_display))}", conn=conn)

        if data.get('location_source') == 'ip' and data.get('latitude') is not None:
            try:
                loc = parse_location(data)
                save_location(conn, mac, loc, protect_precise=True)
            except ValueError:
                app.logger.warning('Sensor reportou localização aproximada inválida')

        # Metadados de plataforma/rede são opcionais para manter compatibilidade
        # com agentes antigos. COALESCE preserva o último valor conhecido.
        db_execute(conn, '''UPDATE sensores SET
            disco = COALESCE(?, disco), net_up = COALESCE(?, net_up), net_down = COALESCE(?, net_down), portas = COALESCE(?, portas),
            so_nome = COALESCE(?, so_nome),
            so_versao = COALESCE(?, so_versao),
            so_arquitetura = COALESCE(?, so_arquitetura),
            interface_nome = COALESCE(?, interface_nome),
            interface_mac = COALESCE(?, interface_mac),
            rede_mascara = COALESCE(?, rede_mascara),
            rede_cidr = COALESCE(?, rede_cidr),
            link_speed_mbps = COALESCE(?, link_speed_mbps),
            interface_up = COALESCE(?, interface_up),
            scan_rede = COALESCE(?, scan_rede),
            scan_limitado = COALESCE(?, scan_limitado),
            agent_version = COALESCE(?, agent_version)
            WHERE mac_id = ?''',
            (data.get('disco'), data.get('net_up'), data.get('net_down'), data.get('portas'),
             data.get('so_nome'), data.get('so_versao'), data.get('so_arquitetura'),
             data.get('interface_nome'), data.get('interface_mac'), data.get('rede_mascara'),
             data.get('rede_cidr'), data.get('link_speed_mbps'), data.get('interface_up'),
             data.get('scan_rede'), data.get('scan_limitado'), data.get('agent_version'), mac))
        conn.commit()

        try:
            # Estado atual continua sendo atualizado a cada batimento, mas o histórico
            # é amostrado para evitar dezenas de milhares de linhas/dia por sensor.
            agora_hist = time.monotonic()
            ultimo_hist = _ultimo_historico_ping.get(mac, 0)
            if data.get("ping_global") and (agora_hist - ultimo_hist) >= PING_HISTORY_INTERVAL:
                pings = json.loads(data["ping_global"])
                db_execute(
                    conn,
                    "INSERT INTO historico_pings (sensor_mac, google, cloudflare, aws, quad9) VALUES (?, ?, ?, ?, ?)",
                    (mac, pings.get("Google"), pings.get("Cloudflare"), pings.get("AWS"), pings.get("Quad9")),
                )
                conn.commit()
                _ultimo_historico_ping[mac] = agora_hist
        except Exception as e:
            print(f"⚠️ Falha ao gravar histórico de ping: {e}")

        comando = consumir_comando(conn, mac)
        conn.close()

        socketio.emit('atualizacao_global', {})
        return jsonify({"status": "OK", "command": comando})

    except Exception:
        app.logger.exception('Falha ao receber telemetria')
        return jsonify(status='error', command='none', error='Falha temporária ao persistir telemetria.'), 503
    finally:
        if conn:
            conn.close()

# ==========================================
# ⏰ ROTA DO CRON (PARA O UPTIMEROBOT)
# ==========================================
@app.route('/api/v2/cron')
def public_cron_trigger():
    if CRON_SECRET:
        fornecido = request.headers.get("X-Cron-Secret") or request.args.get("token", "")
        if not hmac.compare_digest(fornecido, CRON_SECRET):
            return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    verificar_quedas_global(conn)
    conn.close()
    return jsonify({"status": "Watchdog executado com sucesso"})

# ==========================================
# 🔑 TOKEN DE ACESSO AO PAINEL LOCAL DO AGENTE
# ==========================================
@app.route('/api/v2/gerar_token_local/<mac>')
def gerar_token_local(mac):
    # Só quem já está autenticado na central pode gerar acesso ao painel local de um site
    if 'usuario' not in session:
        return jsonify({"erro": "Não autenticado"}), 401
    if not JWT_PRIVATE_KEY:
        return jsonify({"erro": "JWT_PRIVATE_KEY não configurada no servidor (variável de ambiente ausente)"}), 500
    payload = {
        "mac": mac,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1)
    }
    token = jwt.encode(payload, JWT_PRIVATE_KEY, algorithm="RS256")
    return jsonify({"token": token, "mac": mac, "expira_em_horas": 1})

# ==========================================
# 🔄 VERSÃO DO AGENTE
# ==========================================
# O agente é só Linux (no Windows, via WSL) e roda do repositório: a Central
# anuncia a versão e o sensor se atualiza com linux/update.sh <tag>.
@app.route('/api/v2/agent_versao')
def agent_versao():
    return jsonify({
        "versao": CENTRAL_VERSION,
        "platform": "linux",
        "update_command": f"bash linux/update.sh v{CENTRAL_VERSION}",
        "central_version": CENTRAL_VERSION,
    })

# ==========================================
# 🔌 ROTAS: ENERGIA E SERVIÇOS DO SO
# ==========================================
@app.route('/api/v2/ips_energia/<mac_id>', methods=['GET', 'POST'])
def gerenciar_ips_energia(mac_id):
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    conn = database.get_db()
    if request.method == 'POST':
        data = request.json
        try:
            ip = bounded_text(data.get('ip'), 'IP', maximum=64)
            descricao = bounded_text(data.get('descricao'), 'Descrição', maximum=160)
        except ValueError as error:
            conn.close()
            return jsonify(error=str(error)), 400
        db_execute(conn, "INSERT INTO ips_energia (sensor_mac, ip, descricao) VALUES (?, ?, ?)", (mac_id, ip, descricao))
        conn.commit()
    ips = db_execute(conn, "SELECT * FROM ips_energia WHERE sensor_mac = ? ORDER BY id DESC", (mac_id,)).fetchall()
    conn.close()
    return jsonify([dict(i) for i in ips])

@app.route('/api/v2/ips_energia/<mac_id>/<int:id_ip>', methods=['DELETE'])
def del_ips_energia(mac_id, id_ip):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    db_execute(conn, "DELETE FROM ips_energia WHERE id = ?", (id_ip,))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/reportar_latencia_energia', methods=['POST'])
def reportar_latencia_energia():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    data = request.json
    conn = database.get_db()
    db_execute(conn, "UPDATE ips_energia SET latencia = ? WHERE id = ?", (data['latencia'], data['id']))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/servicos_os/<mac_id>', methods=['GET', 'POST'])
def gerenciar_servicos_os(mac_id):
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    conn = database.get_db()
    if request.method == 'POST':
        data = request.json
        try:
            nome_servico = bounded_text(data.get('nome_servico'), 'Serviço', maximum=160)
            descricao = bounded_text(data.get('descricao'), 'Descrição', maximum=160)
        except ValueError as error:
            conn.close()
            return jsonify(error=str(error)), 400
        db_execute(conn, "INSERT INTO servicos_os (sensor_mac, nome_servico, descricao) VALUES (?, ?, ?)", (mac_id, nome_servico, descricao))
        conn.commit()
    srvs = db_execute(conn, "SELECT * FROM servicos_os WHERE sensor_mac = ? ORDER BY id DESC", (mac_id,)).fetchall()
    conn.close()
    return jsonify([dict(i) for i in srvs])

@app.route('/api/v2/servicos_os/<mac_id>/<int:id_srv>', methods=['DELETE'])
def del_servico_os(mac_id, id_srv):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    db_execute(conn, "DELETE FROM servicos_os WHERE id = ?", (id_srv,))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/reportar_status_servico', methods=['POST'])
def reportar_status_servico():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    data = request.json
    conn = database.get_db()
    db_execute(conn, "UPDATE servicos_os SET status = ? WHERE id = ?", (data['status'], data['id']))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

# ==========================================
# 🕹️ COMANDOS REMOTOS
# ==========================================
@app.route('/api/v2/comando_energia/<mac_id>', methods=['POST'])
def enviar_comando_energia(mac_id):
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return jsonify({"error": "Acesso Negado"}), 403
    comando = request.json.get('comando')
    if comando not in ('reboot', 'shutdown', 'flush_dns', 'scan_loop', 'top_processos', 'run_speedtest', 'run_traceroute', 'update_agent'):
        return jsonify(error='Comando inválido.'), 400
    enfileirar_comando(mac_id, comando)
    return jsonify({"status": "Comando enfileirado"})

@app.route('/api/v2/enviar_comando/<mac_id>', methods=['POST'])
def enviar_comando_remoto(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    comando = request.json.get('comando')
    if comando not in ('reboot', 'shutdown', 'flush_dns', 'scan_loop', 'top_processos', 'run_speedtest', 'run_traceroute', 'update_agent'):
        return jsonify(error='Comando inválido.'), 400
    enfileirar_comando(mac_id, comando)
    conn = database.get_db()
    db_execute(conn, "INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, 'Comando Remoto', 'Aviso', ?)", (mac_id, f"Operador {session['usuario']} enviou o comando: {comando}"))
    conn.commit(); conn.close()
    return jsonify({"status": "Comando enfileirado."})

@app.route('/api/v2/enviar_wol/<mac_sensor>', methods=['POST'])
def enviar_wol_remoto(mac_sensor):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    try:
        mac_alvo = bounded_text(request.json.get('mac_alvo'), 'MAC de destino', maximum=80)
        nome_alvo = bounded_text(request.json.get('nome_alvo', 'Dispositivo'), 'Nome do dispositivo', maximum=160)
    except ValueError as error:
        return jsonify(error=str(error)), 400

    enfileirar_comando(mac_sensor, f"wol:{mac_alvo}")

    conn = database.get_db()
    db_execute(conn, "INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, 'Comando Remoto', 'Aviso', ?)",
                 (mac_sensor, f"Operador disparou Magic Packet (Wake-on-LAN) para ligar o dispositivo: {nome_alvo} ({mac_alvo})"))
    conn.commit()
    conn.close()
    return jsonify({"status": f"Sinal de energia enviado para {nome_alvo}!"})

# ==========================================
# 📊 OUTRAS APIs DE TELA E ALERTAS
# ==========================================
@app.route('/api/v2/graficos_ping/<mac_id>')
def obter_graficos_ping(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    try: registros = db_execute(conn, "SELECT google, cloudflare, aws, quad9, data_hora as hora FROM historico_pings WHERE sensor_mac = ? ORDER BY id DESC LIMIT 30", (mac_id,)).fetchall()
    except: registros = []
    conn.close()
    registros.reverse()
    return jsonify([dict(r) for r in registros])

@app.route('/api/v2/registrar_sensor', methods=['POST'])
def registrar_sensor():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('mac_id'), str) or not 1 <= len(data['mac_id']) <= 80:
        return jsonify(error='mac_id obrigatório.'), 400
    try:
        nome_local = bounded_text(data.get('nome_local', 'Sensor Novo'), 'Nome do sensor', maximum=160)
        loc = parse_location(data, default_source='legacy')
        if loc['latitude'] == -14.235 and loc['longitude'] == -51.925 and loc['location_source'] == 'legacy':
            loc = parse_location({})
    except ValueError as error:
        return jsonify(error=str(error)), 400
    conn = database.get_db()
    try:
        conn.execute("INSERT INTO sensores (mac_id, nome_local) VALUES (?, ?) ON CONFLICT(mac_id) DO NOTHING", (data['mac_id'], nome_local))
        if loc['latitude'] is not None:
            save_location(conn, data['mac_id'], loc, protect_precise=True)
        conn.commit()
    finally:
        conn.close()
    return jsonify(status='OK')

@app.route('/api/v2/telemetria_instantanea', methods=['POST'])
def telemetria_instantanea():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    data = request.json; mac_id = data['mac_id']; run_st = mac_id in SPEEDTEST_REQUESTS
    if run_st: SPEEDTEST_REQUESTS.remove(mac_id)
    conn = database.get_db()
    db_execute(conn, "UPDATE sensores SET status = 'online', cpu_usage = ?, ram_usage = ?, temp = ?, ping_gateway = ?, ip_sensor = ?, ip_gateway = ?, last_seen = CURRENT_TIMESTAMP WHERE mac_id = ?", (data.get('cpu'), data.get('ram'), data.get('temp', 0), data.get('ping_gw'), data.get('ip_sensor'), data.get('ip_gateway'), mac_id))
    conn.commit(); conn.close()
    return jsonify({"status": "OK", "run_speedtest": run_st})

@app.route('/api/v2/telemetria_global', methods=['POST'])
def telemetria_global():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    data = request.json; conn = database.get_db()
    db_execute(conn, "UPDATE sensores SET ping_global = ?, traceroute = ? WHERE mac_id = ?", (data.get('pings'), data.get('tracert'), data['mac_id']))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/atualizar_dispositivos', methods=['POST'])
def atualizar_dispositivos():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    data = request.json; sensor_mac = data.get('mac_id')
    lista = data.get('lista', [])
    if not isinstance(lista, list) or len(lista) > 2048:
        return jsonify(error='Lista de dispositivos inválida ou acima do limite.'), 400
    normalized_devices = []
    try:
        for item in lista:
            if not isinstance(item, dict):
                raise ValueError('Dispositivo inválido.')
            normalized_devices.append({
                'ip': bounded_text(item.get('ip'), 'IP', maximum=64),
                'mac': bounded_text(item.get('mac'), 'MAC', maximum=80),
                'fabricante': bounded_text(item.get('fabricante', 'Desconhecido'), 'Fabricante', maximum=160),
                'status': bounded_text(item.get('status', 'desconhecido'), 'Status', maximum=32),
                'neighbor_state': bounded_text(item.get('neighbor_state', ''), 'Estado L2', maximum=64, allow_empty=True),
                'latencia': item.get('latencia'),
            })
    except ValueError as error:
        return jsonify(error=str(error)), 400
    conn = database.get_db()
    sensor_data = db_execute(conn, "SELECT ip_gateway FROM sensores WHERE mac_id = ?", (sensor_mac,)).fetchone()
    ip_gw = sensor_data['ip_gateway'] if sensor_data else None




    nomes_salvos = {row['mac']: row['nome'] for row in conn.execute("SELECT mac, nome FROM nomes_conhecidos").fetchall()}

    db_execute(conn, "DELETE FROM dispositivos WHERE sensor_mac = ?", (sensor_mac,))
    for disp in normalized_devices:
        nome = nomes_salvos.get(disp['mac'])
        if not nome: nome = "Gateway / Roteador" if disp['ip'] == ip_gw else "Desconhecido"
        status_disp = disp.get('status', 'desconhecido')
        db_execute(
            conn,
            "INSERT INTO dispositivos (sensor_mac, ip, mac, fabricante, nome_custom, status, latencia, neighbor_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sensor_mac, disp['ip'], disp['mac'], disp.get('fabricante', 'Desconhecido'), nome,
             status_disp, disp.get('latencia'), disp.get('neighbor_state'))
        )
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/renomear_dispositivo', methods=['POST'])
def renomear_dispositivo():
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    try:
        device_mac = bounded_text(data.get('mac'), 'MAC', maximum=80)
        device_name = bounded_text(data.get('nome'), 'Nome', maximum=160)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    conn = database.get_db()
    try:
        existe = db_execute(conn, "SELECT mac FROM nomes_conhecidos WHERE mac = ?", (device_mac,)).fetchone()
        if existe: db_execute(conn, "UPDATE nomes_conhecidos SET nome = ? WHERE mac = ?", (device_name, device_mac))
        else: db_execute(conn, "INSERT INTO nomes_conhecidos (mac, nome) VALUES (?, ?)", (device_mac, device_name))
        db_execute(conn, "UPDATE dispositivos SET nome_custom = ? WHERE mac = ? AND sensor_mac = ?", (device_name, device_mac, data['sensor_mac']))
        conn.commit()
    except Exception:
        app.logger.exception('Falha ao renomear dispositivo')
        conn.close()
        return jsonify(error='Não foi possível renomear o dispositivo.'), 500
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/alertas_ia', methods=['POST'])
def alertas_ia():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    data = request.json
    mac = data.get('mac_id', 'Desconhecido')
    alertas = data.get('alertas', [])
    if not isinstance(alertas, list) or len(alertas) > 50:
        return jsonify(error='Lista de alertas inválida ou acima do limite.'), 400
    normalized_alerts = []
    try:
        for alerta in alertas:
            if not isinstance(alerta, dict):
                raise ValueError('Alerta inválido.')
            normalized_alerts.append({
                'tipo': bounded_text(alerta.get('tipo'), 'Tipo do alerta', maximum=160),
                'gravidade': bounded_text(alerta.get('gravidade'), 'Gravidade', maximum=32),
                'detalhes': bounded_text(alerta.get('detalhes'), 'Detalhes', maximum=1000),
            })
    except ValueError as error:
        return jsonify(error=str(error)), 400
    conn = database.get_db()

    sensor = db_execute(conn, "SELECT nome_local, cliente_id FROM sensores WHERE mac_id = ?", (mac,)).fetchone()
    nome_sensor = sensor['nome_local'] if sensor else mac
    cid = sensor['cliente_id'] if sensor else None

    for alerta in normalized_alerts:
        db_execute(conn, "INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, ?, ?, ?)", (mac, alerta['tipo'], alerta['gravidade'], alerta['detalhes']))
        if alerta['gravidade'] == 'Crítica':
            icone = "🔥" if "Superaquecimento" in alerta['tipo'] else ("🌪️" if "Tempestade" in alerta['tipo'] else "🖥️")
            enviar_telegram(f"🚨 <b>ALERTA CRÍTICO</b>\n\n{icone} <b>Sensor:</b> {html.escape(str(nome_sensor))}\n⚠️ <b>Evento:</b> {html.escape(str(alerta['tipo']))}\n❌ <b>Detalhe:</b> {html.escape(str(alerta['detalhes']))}", cliente_id=cid, conn=conn)
        elif alerta['gravidade'] == 'OK' and ('Restaurad' in alerta['tipo']):
            enviar_telegram(f"✅ <b>SISTEMA NORMALIZADO</b>\n\n🖥️ <b>Sensor:</b> {html.escape(str(nome_sensor))}\n🟢 <b>Evento:</b> {html.escape(str(alerta['tipo']))}\nℹ️ <b>Detalhe:</b> {html.escape(str(alerta['detalhes']))}", cliente_id=cid, conn=conn)

    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

CONNECTIVITY_TARGETS = ('Google', 'Cloudflare', 'AWS', 'Quad9')


def resumir_conectividade(sensores):
    """Consolida pings reais dos sensores online sem transformar falha em 0 ms."""
    amostras = {alvo: [] for alvo in CONNECTIVITY_TARGETS}
    sem_icmp = {alvo: 0 for alvo in CONNECTIVITY_TARGETS}

    for sensor in sensores:
        if str(sensor.get('status') or '').lower() != 'online':
            continue
        pings = sensor.get('ping_global')
        if isinstance(pings, str):
            try:
                pings = json.loads(pings)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        if not isinstance(pings, dict):
            continue

        for alvo in CONNECTIVITY_TARGETS:
            valor = pings.get(alvo)
            if isinstance(valor, bool):
                continue
            try:
                latencia = float(valor)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(latencia) or latencia < 0:
                continue
            if latencia == 0:
                sem_icmp[alvo] += 1
            else:
                amostras[alvo].append(latencia)

    resumo = {}
    for alvo in CONNECTIVITY_TARGETS:
        valores = amostras[alvo]
        if valores:
            resumo[alvo] = {
                'status': 'measured',
                'latency_ms': round(sum(valores) / len(valores), 1),
                'sample_count': len(valores),
                'no_icmp_count': sem_icmp[alvo],
            }
        elif sem_icmp[alvo]:
            resumo[alvo] = {
                'status': 'no_icmp',
                'latency_ms': None,
                'sample_count': 0,
                'no_icmp_count': sem_icmp[alvo],
            }
        else:
            resumo[alvo] = {
                'status': 'unavailable',
                'latency_ms': None,
                'sample_count': 0,
                'no_icmp_count': 0,
            }
    return resumo


@app.route('/api/v2/mapa_sensores')
def api_mapa_sensores():
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 401
    role = session.get('role'); user_id = session.get('user_id')
    conn = database.get_db()

    verificar_quedas_global(conn)

    try:
        query_base = "SELECT s.mac_id, s.nome_local, s.status, s.lat, s.lon, s.cpu_usage, s.ram_usage, s.net_down, s.net_up, s.alerta_reconhecido, s.em_manutencao, s.ping_global, s.so_nome, s.so_versao, s.so_arquitetura, s.interface_nome, s.interface_mac, s.rede_cidr, s.link_speed_mbps, s.scan_rede, s.scan_limitado, s.location_source, s.accuracy_m, s.agent_version, c.nome as cliente_nome FROM sensores s LEFT JOIN clientes c ON s.cliente_id = c.id"
        if role in ['Administrador Master', 'Operador Master']: sensores = conn.execute(query_base).fetchall()
        elif role == 'Cliente': sensores = db_execute(conn, query_base + " WHERE s.cliente_id = ?", (user_id,)).fetchall()
        else:
            user_info = db_execute(conn, "SELECT cliente_pai_id FROM clientes WHERE id = ?", (user_id,)).fetchone()
            if user_info and user_info['cliente_pai_id']: sensores = db_execute(conn, query_base + " WHERE s.cliente_id = ?", (user_info['cliente_pai_id'],)).fetchall()
            else: sensores = []
        sensores_serializados = [dict(s) for s in sensores]
        conn.close()
        return jsonify({
            "sensores": sensores_serializados,
            "connectivity": resumir_conectividade(sensores_serializados),
        })
    except Exception as e:
        conn.close(); return jsonify({
            "error_sql": str(e),
            "sensores": [],
            "connectivity": resumir_conectividade([]),
        })

@app.route('/api/v2/sensor_data/<mac_id>', methods=['GET'])
def get_sensor_data(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()

    verificar_quedas_global(conn)

    sensor = db_execute(conn, "SELECT * FROM sensores WHERE mac_id = ?", (mac_id,)).fetchone()
    conn.close()
    if sensor: return jsonify(dict(sensor))
    return jsonify({"error": "Sensor não encontrado"}), 404

@app.route('/api/v2/configurar_sensor', methods=['POST'])
def configurar_sensor():
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    data = request.get_json(silent=True) or {}
    try:
        location = parse_location(data) if any(k in data for k in ('lat', 'lon', 'latitude', 'longitude')) else None
        name = bounded_text(data.get('nome'), 'Nome', maximum=160)
        if not isinstance(data.get('mac_id'), str) or not data['mac_id']:
            raise ValueError('Identificador do sensor obrigatório.')
    except ValueError as error:
        return jsonify(error=str(error)), 400
    conn = database.get_db()
    try:
        conn.execute('UPDATE sensores SET nome_local=? WHERE mac_id=?', (name, data['mac_id']))
        if location is not None:
            save_location(conn, data['mac_id'], location)
        conn.commit()
    finally:
        conn.close()
    return jsonify(status='OK')

@app.route('/api/v2/solicitar_speedtest/<mac_id>', methods=['POST'])
def solicitar_speedtest(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    enfileirar_comando(mac_id, "run_speedtest")
    return jsonify({"status": "Teste na fila"})

@app.route('/api/v2/reportar_velocidade', methods=['POST'])
def reportar_velocidade():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    try:
        data = request.json
        mac = data.get('mac_id')
        conn = database.get_db()


        db_execute(conn, "UPDATE sensores SET download = ?, upload = ? WHERE mac_id = ?", (data['down'], data['up'], mac))
        db_execute(conn, "INSERT INTO historico_telemetria (sensor_mac, download, upload) VALUES (?, ?, ?)", (mac, data['down'], data['up']))

        conn.commit()
        conn.close()

        socketio.emit('atualizacao_global', {})
        return jsonify({"status": "OK"})
    except Exception as e:
        print(f"Erro ao salvar Speedtest: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/api/v2/graficos/<mac_id>')
def obter_graficos(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    try: registros = db_execute(conn, "SELECT download, upload, to_char(data_hora - INTERVAL '3 hours', 'HH24:MI') as hora FROM historico_telemetria WHERE sensor_mac = ? ORDER BY id DESC LIMIT 15", (mac_id,)).fetchall()
    except: registros = []
    conn.close()
    return jsonify([dict(r) for r in registros][::-1])

@app.route('/api/v2/ips_customizados/<mac_id>', methods=['GET', 'POST'])
def gerenciar_ips(mac_id):
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    conn = database.get_db()
    if request.method == 'POST':
        data = request.json
        try:
            ip = bounded_text(data.get('ip'), 'IP', maximum=64)
            descricao = bounded_text(data.get('descricao'), 'Descrição', maximum=160)
        except ValueError as error:
            conn.close()
            return jsonify(error=str(error)), 400
        db_execute(conn, "INSERT INTO ips_custom (sensor_mac, ip, descricao) VALUES (?, ?, ?)", (mac_id, ip, descricao))
        conn.commit()
    ips = db_execute(conn, "SELECT * FROM ips_custom WHERE sensor_mac = ? ORDER BY id DESC", (mac_id,)).fetchall()
    conn.close()
    return jsonify([dict(i) for i in ips])

@app.route('/api/v2/ips_customizados/<mac_id>/<int:id_ip>', methods=['DELETE', 'PUT'])
def crud_ips(mac_id, id_ip):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    if request.method == 'DELETE': db_execute(conn, "DELETE FROM ips_custom WHERE id = ?", (id_ip,))
    elif request.method == 'PUT':
        data = request.json
        try:
            ip = bounded_text(data.get('ip'), 'IP', maximum=64)
            descricao = bounded_text(data.get('descricao'), 'Descrição', maximum=160)
        except ValueError as error:
            conn.close()
            return jsonify(error=str(error)), 400
        db_execute(conn, "UPDATE ips_custom SET ip = ?, descricao = ? WHERE id = ?", (ip, descricao, id_ip))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/reportar_latencia_custom', methods=['POST'])
def reportar_latencia_custom():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    data = request.json; conn = database.get_db()
    db_execute(conn, "UPDATE ips_custom SET latencia = ? WHERE id = ?", (data['latencia'], data['id']))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/historico/<mac_id>')
def historico_alertas(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    data_filtro = request.args.get('data')
    conn = database.get_db()
    query = "SELECT id, sensor_mac, tipo_evento, gravidade, detalhes, data_hora FROM logs_ia WHERE sensor_mac = ?"
    params = [mac_id]
    if data_filtro: query += " AND DATE(data_hora) = ?"; params.append(data_filtro)
    logs = db_execute(conn, query + " ORDER BY id DESC LIMIT 1000", params).fetchall()
    conn.close()
    return jsonify([dict(l) for l in logs])

@app.route('/api/v2/dispositivos/<mac_id>', methods=['GET'])
def get_dispositivos(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    dispositivos = db_execute(conn, "SELECT * FROM dispositivos WHERE sensor_mac = ?", (mac_id,)).fetchall()
    conn.close()
    return jsonify([dict(d) for d in dispositivos])

@app.route('/manifest.json')
def manifest():
    response = send_from_directory('static', 'manifest.json')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response
@app.route('/sw.js')
def service_worker():
    response = send_from_directory('static', 'sw.js')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Service-Worker-Allowed'] = '/'
    return response

@app.route('/usuarios')
def gerenciar_usuarios():
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return "Acesso Negado.", 403
    conn = database.get_db()
    if session['role'] == 'Administrador Master':
        usuarios = conn.execute("SELECT id, nome, usuario, role, ativo, cliente_pai_id, logo_url, telegram_token, telegram_chat_id FROM clientes ORDER BY id DESC").fetchall()
        clientes_pais = conn.execute("SELECT id, nome FROM clientes WHERE role = 'Cliente'").fetchall()
    else:
        if session['role'] == 'Cliente': tenant_id = session['user_id']
        else:
            user_info = db_execute(conn, "SELECT cliente_pai_id FROM clientes WHERE id = ?", (session['user_id'],)).fetchone()
            tenant_id = user_info['cliente_pai_id']
        usuarios = db_execute(conn, "SELECT id, nome, usuario, role, ativo, cliente_pai_id, logo_url, telegram_token, telegram_chat_id FROM clientes WHERE cliente_pai_id = ? ORDER BY id DESC", (tenant_id,)).fetchall()
        clientes_pais = []
    conn.close()
    return render_template('usuarios.html', usuarios=[dict(u) for u in usuarios], role_atual=session['role'], telegram_tenant=locals().get('tenant_id', 0), clientes_pais=[dict(c) for c in clientes_pais])

@app.route('/api/v2/usuarios', methods=['POST'])
def criar_usuario():
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    try:
        nome = bounded_text(data.get('nome'), 'Nome', maximum=160)
        usuario = bounded_text(data.get('usuario'), 'Usuário', maximum=80)
        if not re.fullmatch(r'[A-Za-z0-9._@-]+', usuario):
            raise ValueError('Usuário deve usar apenas letras, números, ponto, hífen, @ ou underscore.')
        senha = bounded_secret(data.get('senha'), 'Senha')
        role = bounded_text(data.get('role'), 'Perfil', maximum=40)
        if role not in ALLOWED_ROLES:
            raise ValueError('Perfil inválido.')
        logo_url = bounded_text(data.get('logo_url', ''), 'URL do logo', maximum=500, allow_empty=True)
        if logo_url and urlsplit(logo_url).scheme not in ('http', 'https'):
            raise ValueError('URL do logo deve usar HTTP ou HTTPS.')
        tg_token = bounded_text(data.get('telegram_token', ''), 'Token Telegram', maximum=256, allow_empty=True)
        tg_chat = bounded_text(data.get('telegram_chat_id', ''), 'Chat Telegram', maximum=128, allow_empty=True)
    except ValueError as error:
        return jsonify(error=str(error), status='Erro de validação'), 400
    senha_hash = generate_password_hash(senha)
    conn = database.get_db()
    if session['role'] == 'Cliente': cliente_pai = session['user_id']
    elif session['role'] == 'Administrador Cliente':
        user_info = db_execute(conn, "SELECT cliente_pai_id FROM clientes WHERE id = ?", (session['user_id'],)).fetchone()
        cliente_pai = user_info['cliente_pai_id']
    else:
        cliente_pai = data.get('cliente_pai')
        if not cliente_pai or cliente_pai == "null": cliente_pai = None
    try:
        if db_execute(conn, 'SELECT id FROM clientes WHERE usuario = ?', (usuario,)).fetchone():
            return jsonify(error='Usuário já existe.', status='Erro: Usuário já existe'), 409
        db_execute(conn, "INSERT INTO clientes (nome, usuario, senha, role, cliente_pai_id, ativo, logo_url, telegram_token, telegram_chat_id) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)", (nome, usuario, senha_hash, role, cliente_pai, logo_url, tg_token, tg_chat))
        conn.commit(); status = "OK"
    except Exception:
        app.logger.exception('Falha ao criar usuário')
        return jsonify(error='Não foi possível criar o usuário.', status='Erro'), 500
    finally: conn.close()
    return jsonify({"status": status})

@app.route('/api/v2/usuarios/<int:id_user>/toggle_status', methods=['POST'])
def toggle_user_status(id_user):
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    if id_user == session['user_id'] or id_user == 1: return jsonify({"error": "Ação não permitida"}), 403
    conn = database.get_db()
    user = db_execute(conn, "SELECT ativo FROM clientes WHERE id = ?", (id_user,)).fetchone()
    novo_status = 0 if user.get('ativo', 1) == 1 else 1
    db_execute(conn, "UPDATE clientes SET ativo = ? WHERE id = ?", (novo_status, id_user))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/usuarios/<int:id_user>/senha', methods=['POST'])
def alterar_senha_user(id_user):
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    try:
        senha = bounded_secret(data.get('senha'), 'Senha')
    except ValueError as error:
        return jsonify(error=str(error)), 400
    nova_senha = generate_password_hash(senha)
    conn = database.get_db()
    db_execute(conn, "UPDATE clientes SET senha = ? WHERE id = ?", (nova_senha, id_user))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/usuarios/<int:id_user>/info', methods=['PUT'])
def editar_usuario_info(id_user):
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    try:
        nome = bounded_text(data.get('nome'), 'Nome', maximum=160)
        usuario = bounded_text(data.get('usuario'), 'Usuário', maximum=80)
        if not re.fullmatch(r'[A-Za-z0-9._@-]+', usuario):
            raise ValueError('Usuário inválido.')
        logo_url = bounded_text(data.get('logo_url', ''), 'URL do logo', maximum=500, allow_empty=True)
        if logo_url and urlsplit(logo_url).scheme not in ('http', 'https'):
            raise ValueError('URL do logo deve usar HTTP ou HTTPS.')
        tg_token = bounded_text(data.get('telegram_token', ''), 'Token Telegram', maximum=256, allow_empty=True)
        tg_chat = bounded_text(data.get('telegram_chat_id', ''), 'Chat Telegram', maximum=128, allow_empty=True)
    except ValueError as error:
        return jsonify(error=str(error), status='Erro de validação'), 400
    conn = database.get_db()
    try:
        existing = db_execute(conn, 'SELECT id FROM clientes WHERE usuario = ? AND id <> ?', (usuario, id_user)).fetchone()
        if existing:
            return jsonify(error='Login já está em uso.', status='Erro: Login já está em uso.'), 409
        db_execute(conn, "UPDATE clientes SET nome = ?, usuario = ?, logo_url = ?, telegram_token = ?, telegram_chat_id = ? WHERE id = ?", (nome, usuario, logo_url, tg_token, tg_chat, id_user))
        conn.commit(); status = "OK"
    except Exception:
        app.logger.exception('Falha ao editar usuário')
        return jsonify(error='Não foi possível editar o usuário.', status='Erro'), 500
    finally: conn.close()
    return jsonify({"status": status})

@app.route('/api/v2/usuarios/<int:id_user>', methods=['DELETE'])
def deletar_usuario(id_user):
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    if id_user == session['user_id'] or id_user == 1: return jsonify({"error": "Ação não permitida"}), 403
    conn = database.get_db()
    db_execute(conn, "DELETE FROM clientes WHERE id = ?", (id_user,))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/sensores')
def gerenciar_sensores():
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return "Acesso Negado.", 403
    conn = database.get_db()
    sensores = conn.execute('''SELECT s.mac_id, s.nome_local, s.status, s.ip_sensor, s.cliente_id,
        c.nome as cliente_nome, sc.key_prefix,
        CASE WHEN sc.key_hash IS NOT NULL AND sc.revoked_at IS NULL THEN 1 ELSE 0 END AS credential_active
        FROM sensores s
        LEFT JOIN clientes c ON s.cliente_id = c.id
        LEFT JOIN sensor_credentials sc ON sc.sensor_mac = s.mac_id
        ORDER BY s.cliente_id ASC''').fetchall()
    clientes = conn.execute("SELECT id, nome FROM clientes WHERE role IN ('Cliente', 'Administrador Master')").fetchall()
    conn.close()
    return render_template('sensores.html', sensores=[dict(s) for s in sensores], clientes=[dict(c) for c in clientes])


@app.route('/api/v2/sensor_credential/<mac_id>', methods=['POST', 'DELETE'])
def sensor_credential(mac_id):
    if 'user_id' not in session or session.get('role') != 'Administrador Master':
        return jsonify(error='Acesso Negado'), 403
    conn = database.get_db()
    try:
        sensor = conn.execute('SELECT mac_id FROM sensores WHERE mac_id=?', (mac_id,)).fetchone()
        if not sensor:
            return jsonify(error='Sensor não encontrado.'), 404
        if request.method == 'DELETE':
            conn.execute(
                'UPDATE sensor_credentials SET revoked_at=CURRENT_TIMESTAMP WHERE sensor_mac=? AND revoked_at IS NULL',
                (mac_id,),
            )
            conn.commit()
            return jsonify(status='OK', active=False)

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        prefix = token[:8]
        conn.execute('''INSERT INTO sensor_credentials
            (sensor_mac, key_hash, key_prefix, created_at, revoked_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, NULL)
            ON CONFLICT(sensor_mac) DO UPDATE SET
                key_hash=excluded.key_hash,
                key_prefix=excluded.key_prefix,
                created_at=CURRENT_TIMESTAMP,
                revoked_at=NULL''', (mac_id, token_hash, prefix))
        conn.commit()
        return jsonify(
            status='OK', active=True, sensor_mac=mac_id, key_prefix=prefix,
            sensor_api_key=token,
            warning='Esta chave será exibida somente uma vez.',
        ), 201
    finally:
        conn.close()

@app.route('/api/v2/alocar_sensor', methods=['POST'])
def alocar_sensor():
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return jsonify({"error": "Acesso Negado"}), 403
    data = request.json; cliente_id = data.get('cliente_id')
    if not cliente_id or cliente_id == "null": cliente_id = None
    conn = database.get_db()
    db_execute(conn, "UPDATE sensores SET cliente_id = ? WHERE mac_id = ?", (cliente_id, data.get('mac_id')))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/sensor_virtual')
def sensor_virtual():
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return "Acesso Negado.", 403
    return render_template('sensor_virtual.html', nome_operador=session.get('nome', 'Admin'))

@app.route('/api/v2/renomear_sensor', methods=['POST'])
def renomear_sensor():
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    if data.get('novo_nome') and data.get('mac_id'):
        conn = database.get_db()
        db_execute(conn, "UPDATE sensores SET nome_local = ? WHERE mac_id = ?", (data.get('novo_nome'), data.get('mac_id')))
        conn.commit(); conn.close()
        return jsonify({"status": "OK"})
    return jsonify({"error": "Dados inválidos"}), 400

@app.route('/api/v2/deletar_sensor/<mac_id>', methods=['DELETE'])
def deletar_sensor(mac_id):
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    db_execute(conn, "DELETE FROM sensor_credentials WHERE sensor_mac = ?", (mac_id,))
    db_execute(conn, "DELETE FROM sensores WHERE mac_id = ?", (mac_id,))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/solicitar_traceroute/<mac_id>', methods=['POST'])
def solicitar_traceroute(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    enfileirar_comando(mac_id, "run_traceroute")
    return jsonify({"status": "OK"})

@app.route('/api/v2/reportar_rota', methods=['POST'])
def reportar_rota():
    auth_error = exigir_sensor_ou_sessao()
    if auth_error: return auth_error
    data = request.json; conn = database.get_db()
    db_execute(conn, "UPDATE sensores SET ultima_rota = ? WHERE mac_id = ?", (data['rota'], data['mac_id']))
    conn.commit(); conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/logs_globais')
def logs_globais():
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    try:
        sql = "SELECT l.tipo_evento, l.gravidade, l.detalhes, l.data_hora as hora, s.nome_local FROM logs_ia l LEFT JOIN sensores s ON l.sensor_mac=s.mac_id"
        params = ()
        if session.get('role') not in ('Administrador Master', 'Operador Master'):
            sql += ' WHERE s.cliente_id=?'
            params = (tenant_id(conn),)
        logs = conn.execute(sql + ' ORDER BY l.id DESC LIMIT 50', params).fetchall()
        return jsonify([dict(row) for row in logs])
    finally:
        conn.close()

@app.route('/api/v2/solicitar_update/<mac_id>', methods=['POST'])
def solicitar_update(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    enfileirar_comando(mac_id, "update_agent")
    return jsonify({"status": "OK"})

@app.route('/api/v2/toggle_manutencao/<mac_id>', methods=['POST'])
def toggle_manutencao(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    sensor = db_execute(conn, "SELECT em_manutencao FROM sensores WHERE mac_id = ?", (mac_id,)).fetchone()
    novo_estado = 1 if sensor['em_manutencao'] == 0 else 0
    db_execute(conn, "UPDATE sensores SET em_manutencao = ?, status = 'online' WHERE mac_id = ?", (novo_estado, mac_id))
    db_execute(conn, "INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, 'Setup', 'Aviso', ?)", (mac_id, f"Operador {session['usuario']} alterou para: {'SENSOR EM MANUTENÇÃO' if novo_estado == 1 else 'MANUTENÇÃO ENCERRADA'}"))
    conn.commit(); conn.close()
    return jsonify({"status": "OK", "novo_estado": novo_estado})

@app.route('/debug')
def debug_db():
    if 'user_id' not in session or session.get('role') != 'Administrador Master':
        return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    try: sensores = conn.execute("SELECT * FROM sensores").fetchall(); return jsonify({"SISTEMA_VIVO": True, "sensores_no_banco": [dict(s) for s in sensores]})
    except Exception as e: return jsonify({"SISTEMA_VIVO": False, "erro_fatal": str(e)})

# ==========================================
# 📄 ROTA DA FASE 4: RELATÓRIO EXECUTIVO PDF (COM FILTROS)
# ==========================================
@app.route('/relatorio/<mac_id>')
def gerar_relatorio(mac_id):
    if 'user_id' not in session: return redirect(url_for('login'))

    tipo_filtro = request.args.get('tipo', 'ultimos')
    data_inicio = request.args.get('inicio', '')
    data_fim = request.args.get('fim', '')

    conn = database.get_db()
    sensor = db_execute(conn, "SELECT s.*, c.nome as cliente_nome, c.logo_url FROM sensores s LEFT JOIN clientes c ON s.cliente_id = c.id WHERE s.mac_id = ?", (mac_id,)).fetchone()

    if not sensor:
        conn.close(); return "Sensor não encontrado", 404

    # Monta a Query Baseada no Filtro Escolhido
    periodo_str = "Últimos 30 eventos detectados"

    try:
        import os
        is_postgres = bool(os.environ.get('DATABASE_URL'))

        # Sintaxe adaptável para SQLite ou Postgres
        if is_postgres: query_logs = "SELECT tipo_evento, gravidade, detalhes, to_char(data_hora - INTERVAL '3 hours', 'DD/MM/YYYY HH24:MI') as data_hora FROM logs_ia WHERE sensor_mac = %s"
        else: query_logs = "SELECT tipo_evento, gravidade, detalhes, strftime('%d/%m/%Y %H:%M', data_hora, '-3 hours') as data_hora FROM logs_ia WHERE sensor_mac = ?"

        params = [mac_id]

        if tipo_filtro == 'dia' and data_inicio:
            if is_postgres: query_logs += " AND DATE(data_hora - INTERVAL '3 hours') = %s"
            else: query_logs += " AND date(data_hora, '-3 hours') = ?"
            params.append(data_inicio)
            data_formatada = datetime.strptime(data_inicio, '%Y-%m-%d').strftime('%d/%m/%Y')
            periodo_str = f"Eventos do dia {data_formatada}"

        elif tipo_filtro == 'periodo' and data_inicio and data_fim:
            if is_postgres: query_logs += " AND DATE(data_hora - INTERVAL '3 hours') BETWEEN %s AND %s"
            else: query_logs += " AND date(data_hora, '-3 hours') BETWEEN ? AND ?"
            params.extend([data_inicio, data_fim])
            d1 = datetime.strptime(data_inicio, '%Y-%m-%d').strftime('%d/%m/%Y')
            d2 = datetime.strptime(data_fim, '%Y-%m-%d').strftime('%d/%m/%Y')
            periodo_str = f"Período: {d1} a {d2}"

        query_logs += " ORDER BY id DESC"
        if tipo_filtro == 'ultimos': query_logs += " LIMIT 30"
        else: query_logs += " LIMIT 1000" # Limite de segurança para PDFs mensais

        logs = conn.execute(query_logs, params).fetchall()
    except Exception as e:
        print("Erro relatorio:", e)
        logs = []

    try: dispositivos = db_execute(conn, "SELECT ip, mac, fabricante, nome_custom, status FROM dispositivos WHERE sensor_mac = ?", (mac_id,)).fetchall()
    except: dispositivos = []

    conn.close()

    return render_template('relatorio.html', sensor=dict(sensor), logs=[dict(l) for l in logs], dispositivos=[dict(d) for d in dispositivos], data_emissao=datetime.now().strftime('%d/%m/%Y às %H:%M'), periodo_str=periodo_str)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000, debug=False)
