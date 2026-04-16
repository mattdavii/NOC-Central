from flask import Flask, jsonify, request, render_template, session, redirect, url_for, flash, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
from flask_socketio import SocketIO 
import database
import urllib.request, json # Necessários para o Telegram

app = Flask(__name__)
app.secret_key = 'chave_super_secreta_noc_md' 
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent') 

# =========================================================
# 🤖 CHAVES DO TELEGRAM (PREENCHA AQUI)
# =========================================================
TELEGRAM_BOT_TOKEN = "8611160616:AAEYnOAXG-EInv4yDYSje5J_K0XbO6jIee0"
TELEGRAM_CHAT_ID = "-5147163793"

def enviar_telegram(mensagem):
    """ Função silenciosa que envia alertas para o seu celular """
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "SEU_TOKEN_AQUI": return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": mensagem, "parse_mode": "HTML"}).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=3)
    except Exception as e:
        print(f"⚠️ Erro ao enviar Telegram: {e}")

# =========================================================
# INICIALIZAÇÃO E AUTO-CURA DO BANCO DE DADOS
# =========================================================
try:
    database.init_db() 
    conn = database.get_db()
    
    admin = conn.execute("SELECT * FROM clientes WHERE usuario = 'admin'").fetchone()
    if not admin:
        conn.execute("INSERT INTO clientes (usuario, senha, role) VALUES ('admin', 'admin123', 'Administrador Master')")
        conn.commit()

    colunas_sensores = [
        "last_seen TIMESTAMP", "ip_gateway TEXT", "ultima_rota TEXT", 
        "download REAL", "upload REAL", "alerta_reconhecido INTEGER DEFAULT 1", 
        "disco REAL", "net_up REAL", "net_down REAL", "portas TEXT", 
        "em_manutencao INTEGER DEFAULT 0", "cliente_id INTEGER"
    ]
    for col in colunas_sensores:
        try: 
            conn.execute(f"ALTER TABLE sensores ADD COLUMN {col}")
            conn.commit()
        except: 
            try: conn.execute("ROLLBACK") 
            except: pass

    colunas_clientes = ["nome TEXT", "cliente_pai_id INTEGER", "ativo INTEGER DEFAULT 1", "logo_url TEXT"]
    for col in colunas_clientes:
        try: 
            conn.execute(f"ALTER TABLE clientes ADD COLUMN {col}")
            conn.commit()
        except: 
            try: conn.execute("ROLLBACK")
            except: pass

    try:
        conn.execute("UPDATE clientes SET role = 'Administrador Master' WHERE usuario = 'admin'")
        conn.commit()
    except:
        try: conn.execute("ROLLBACK")
        except: pass

    conn.close()
    print("✅ Banco de Dados sincronizado, atualizado e blindado!")
except Exception as e:
    print(f"⚠️ Aviso na inicialização do banco: {e}")

# =========================================================
# VARIÁVEIS GLOBAIS DE MEMÓRIA
# =========================================================
SPEEDTEST_REQUESTS = set()
TRACEROUTE_REQUESTS = set()
UPDATE_REQUESTS = set()
PENDING_COMMANDS = {}
AUTO_SPEEDTEST_DONE = set()

# ==========================================
# 🔐 SISTEMA DE LOGIN E SESSÃO (BLINDADO)
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    erro = None
    if request.method == 'POST':
        usuario_digitado = request.form.get('usuario', '').strip()
        senha_digitada = request.form.get('senha', '').strip()
        
        try:
            conn = database.get_db()
            
            if usuario_digitado == 'admin' and senha_digitada == 'admin123':
                user = conn.execute("SELECT * FROM clientes WHERE usuario = 'admin'").fetchone()
                if not user:
                    conn.execute("INSERT INTO clientes (usuario, senha, role) VALUES ('admin', 'admin123', 'Administrador Master')")
                    user = conn.execute("SELECT * FROM clientes WHERE usuario = 'admin'").fetchone()
                else:
                    conn.execute("UPDATE clientes SET senha = 'admin123' WHERE usuario = 'admin'")
                conn.commit()
                
                session['logged_in'] = True; session['usuario'] = 'admin'; session['role'] = 'Administrador Master'
                session['user_id'] = user['id'] if user else 1; session['logo_cliente'] = dict(user).get('logo_url', '') if user else ''
                conn.close()
                return redirect(url_for('index'))

            user = None
            try: user = conn.execute("SELECT * FROM clientes WHERE usuario = ?", (usuario_digitado,)).fetchone()
            except: user = conn.execute("SELECT * FROM clientes WHERE usuario = %s", (usuario_digitado,)).fetchone()
            
            if user:
                senha_banco = str(user['senha']).strip()
                senha_valida = False
                
                if senha_banco == senha_digitada: senha_valida = True
                else:
                    try:
                        if check_password_hash(senha_banco, senha_digitada): senha_valida = True
                    except: pass
                
                if senha_valida:
                    session['logged_in'] = True; session['usuario'] = user['usuario']; session['role'] = user['role']
                    session['user_id'] = user['id']; session['logo_cliente'] = dict(user).get('logo_url', '')
                    conn.close()
                    return redirect(url_for('index'))
                else: erro = "Usuário ou senha incorretos!"
            else: erro = "Usuário ou senha incorretos!" 
            conn.close()
                
        except Exception as e:
            erro = "Erro interno ao validar as credenciais."

    return render_template('login.html', erro=erro)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/api/v2/ack_alerta', methods=['POST'])
def ack_alerta():
    if 'usuario' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    conn.execute("UPDATE sensores SET alerta_reconhecido = 1 WHERE status = 'offline'")
    detalhe = f"O operador {session['usuario']} silenciou o alarme e assumiu a ocorrência."
    conn.execute("INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES ('SISTEMA', 'Acknowledge (Ciente)', 'Aviso', ?)", (detalhe,))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

# ==========================================
# 🗺️ ROTAS DO PAINEL (Frontend UI)
# ==========================================
@app.route('/')
def index():
    if 'usuario' not in session: return redirect(url_for('login'))
    return render_template('index.html', nome=session['usuario'])

@app.route('/sensor/<mac_id>')
def painel_sensor(mac_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = database.get_db()
    sensor = conn.execute("SELECT * FROM sensores WHERE mac_id = ?", (mac_id,)).fetchone()
    conn.close()
    if not sensor: return "Sensor não encontrado", 404
    return render_template('sensor.html', sensor=sensor, nome=session['usuario'])

# ==========================================
# 📥 RECEPÇÃO DE DADOS (TELEMETRIA DA NUVEM)
# ==========================================
@app.route('/api/v2/report_data', methods=['POST'])
def report_data():
    global AUTO_SPEEDTEST_DONE 
    try:
        data = request.json
        mac = data.get('mac_id')
        ip_display = data.get('ip_local')
        nome_local_agente = data.get('nome_local', mac)
        
        conn = database.get_db()
        
        try:
            from datetime import datetime
            agora_hora = datetime.now().hour
            hoje_id = datetime.now().strftime('%Y-%m-%d')
            if 'AUTO_SPEEDTEST_DONE' not in globals(): AUTO_SPEEDTEST_DONE = set()
            if agora_hora == 3 and f"{mac}_{hoje_id}" not in AUTO_SPEEDTEST_DONE:
                SPEEDTEST_REQUESTS.add(mac)
                AUTO_SPEEDTEST_DONE.add(f"{mac}_{hoje_id}")
                if len(AUTO_SPEEDTEST_DONE) > 500: AUTO_SPEEDTEST_DONE.clear()
        except: pass

        sensor = conn.execute("SELECT * FROM sensores WHERE mac_id = ?", (mac,)).fetchone()
        
        if sensor:
            sensor_dict = dict(sensor) 
            if sensor_dict.get('status') == 'offline':
                try:
                    conn.execute("INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, 'Conexão Restaurada', 'Aviso', 'O sensor restabeleceu a comunicação com a rede')", (mac,))
                    # 🤖 TELEGRAM: SENSOR VOLTOU!
                    enviar_telegram(f"✅ <b>CONEXÃO RESTAURADA</b>\n\n🖥️ <b>Sensor:</b> {sensor_dict.get('nome_local', mac)}\n🌐 <b>Status:</b> ONLINE\nℹ️ <b>Detalhe:</b> O equipamento restabeleceu a comunicação com a Central NOC.")
                except: pass

            conn.execute('''UPDATE sensores SET 
                ip_sensor = ?, cpu_usage = ?, ram_usage = ?, temp = ?, 
                status = 'online', ping_gateway = ?, ping_global = ?,
                ip_gateway = ?, last_seen = CURRENT_TIMESTAMP,
                disco = ?, net_up = ?, net_down = ?, portas = ?
                WHERE mac_id = ?''', 
                (ip_display, data.get('cpu_usage'), data.get('ram_usage'), 
                 data.get('temp'), data.get('ping_gateway'), 
                 data.get('ping_global'), data.get('ip_gateway'),
                 data.get('disco'), data.get('net_up'), data.get('net_down'), data.get('portas'), 
                 mac))
            conn.commit()
        else:
            conn.execute('''INSERT INTO sensores 
                (mac_id, nome_local, ip_sensor, cpu_usage, ram_usage, temp, status, lat, lon, ping_gateway, ping_global, ip_gateway, last_seen, alerta_reconhecido, em_manutencao) 
                VALUES (?, 'Novo Sensor', ?, ?, ?, ?, 'online', -14.235, -51.925, ?, ?, ?, CURRENT_TIMESTAMP, 1, 0)''', 
                (mac, ip_display, data.get('cpu_usage'), data.get('ram_usage'), 
                 data.get('temp'), data.get('ping_gateway'), 
                 data.get('ping_global'), data.get('ip_gateway')))
            conn.commit()
            enviar_telegram(f"🎉 <b>NOVO SENSOR REGISTRADO</b>\n\n🖥️ <b>MAC:</b> {mac}\n🌐 <b>IP Local:</b> {ip_display}\nO NOC está monitorando um novo ambiente.")

        try:
            conn.execute('''CREATE TABLE IF NOT EXISTS historico_pings (
                id SERIAL PRIMARY KEY, sensor_mac TEXT, google INTEGER, cloudflare INTEGER, aws INTEGER, quad9 INTEGER, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            if data.get('ping_global'):
                import json
                pings = json.loads(data['ping_global'])
                conn.execute("INSERT INTO historico_pings (sensor_mac, google, cloudflare, aws, quad9) VALUES (?, ?, ?, ?, ?)",
                             (mac, pings.get('Google'), pings.get('Cloudflare'), pings.get('AWS'), pings.get('Quad9')))
            conn.commit()
        except: pass

        conn.close()

        comando = "none"
        if mac in SPEEDTEST_REQUESTS: SPEEDTEST_REQUESTS.remove(mac); comando = "run_speedtest"
        elif mac in TRACEROUTE_REQUESTS: TRACEROUTE_REQUESTS.remove(mac); comando = "run_traceroute"
        elif mac in UPDATE_REQUESTS: UPDATE_REQUESTS.remove(mac); comando = "update_agent"
        elif mac in PENDING_COMMANDS: comando = PENDING_COMMANDS.pop(mac)

        socketio.emit('atualizacao_global', {'mac_id': mac})
        return jsonify({"status": "OK", "command": comando})

    except Exception as e:
        return jsonify({"status": "error", "command": "none", "erro_backend": str(e)}), 200

# ==========================================
# 🔌 ROTAS DE MONITORAMENTO DE ENERGIA
# ==========================================
@app.route('/api/v2/ips_energia/<mac_id>', methods=['GET', 'POST'])
def gerenciar_ips_energia(mac_id):
    conn = database.get_db()
    try: conn.execute('''CREATE TABLE IF NOT EXISTS ips_energia (id SERIAL PRIMARY KEY, sensor_mac TEXT, ip TEXT, descricao TEXT, latencia INTEGER DEFAULT 0)''')
    except: pass
    if request.method == 'POST':
        data = request.json
        conn.execute("INSERT INTO ips_energia (sensor_mac, ip, descricao) VALUES (?, ?, ?)", (mac_id, data['ip'], data['descricao']))
        conn.commit()
    ips = conn.execute("SELECT * FROM ips_energia WHERE sensor_mac = ? ORDER BY id DESC", (mac_id,)).fetchall()
    conn.close()
    return jsonify([dict(i) for i in ips])

@app.route('/api/v2/ips_energia/<mac_id>/<int:id_ip>', methods=['DELETE'])
def del_ips_energia(mac_id, id_ip):
    conn = database.get_db()
    conn.execute("DELETE FROM ips_energia WHERE id = ?", (id_ip,))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/reportar_latencia_energia', methods=['POST'])
def reportar_latencia_energia():
    data = request.json
    conn = database.get_db()
    conn.execute("UPDATE ips_energia SET latencia = ? WHERE id = ?", (data['latencia'], data['id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/comando_energia/<mac_id>', methods=['POST'])
def enviar_comando_energia(mac_id):
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return jsonify({"error": "Acesso Negado"}), 403
    PENDING_COMMANDS[mac_id] = request.json.get('comando')
    return jsonify({"status": "Comando enfileirado"})

@app.route('/api/v2/enviar_comando/<mac_id>', methods=['POST'])
def enviar_comando_remoto(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    comando = request.json.get('comando')
    PENDING_COMMANDS[mac_id] = comando
    
    conn = database.get_db()
    try: conn.execute('''CREATE TABLE IF NOT EXISTS logs_ia (id SERIAL PRIMARY KEY, sensor_mac TEXT, tipo_evento TEXT, gravidade TEXT, detalhes TEXT, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    except: pass
    
    conn.execute("INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, 'Comando Remoto', 'Aviso', ?)", (mac_id, f"Operador {session['usuario']} enviou o comando: {comando}"))
    conn.commit()
    conn.close()
    return jsonify({"status": "Comando enfileirado e aguardando o Agente buscar."})

@app.route('/api/v2/graficos_ping/<mac_id>')
def obter_graficos_ping(mac_id):
    conn = database.get_db()
    try: registros = conn.execute("SELECT google, cloudflare, aws, quad9, to_char(data_hora - INTERVAL '3 hours', 'HH24:MI:SS') as hora FROM historico_pings WHERE sensor_mac = ? ORDER BY id DESC LIMIT 30", (mac_id,)).fetchall()
    except: registros = []
    conn.close()
    registros.reverse()
    return jsonify([dict(r) for r in registros])

@app.route('/api/v2/registrar_sensor', methods=['POST'])
def registrar_sensor():
    data = request.json
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT mac_id FROM sensores WHERE mac_id = ?", (data['mac_id'],))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO sensores (mac_id, cliente_id, nome_local, lat, lon) VALUES (?, 1, ?, ?, ?)", (data['mac_id'], data.get('nome_local', 'Sensor Novo'), data.get('lat', -14.235), data.get('lon', -51.925)))
        conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/telemetria_instantanea', methods=['POST'])
def telemetria_instantanea():
    data = request.json; mac_id = data['mac_id']
    run_st = mac_id in SPEEDTEST_REQUESTS
    if run_st: SPEEDTEST_REQUESTS.remove(mac_id) 
    
    conn = database.get_db()
    conn.execute("UPDATE sensores SET status = 'online', cpu_usage = ?, ram_usage = ?, temp = ?, ping_gateway = ?, ip_sensor = ?, ip_gateway = ?, last_ping = CURRENT_TIMESTAMP WHERE mac_id = ?", (data.get('cpu'), data.get('ram'), data.get('temp', 0), data.get('ping_gw'), data.get('ip_sensor'), data.get('ip_gateway'), mac_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK", "run_speedtest": run_st})

@app.route('/api/v2/telemetria_global', methods=['POST'])
def telemetria_global():
    data = request.json
    conn = database.get_db()
    conn.execute("UPDATE sensores SET ping_global = ?, traceroute = ? WHERE mac_id = ?", (data.get('pings'), data.get('tracert'), data['mac_id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/atualizar_dispositivos', methods=['POST'])
def atualizar_dispositivos():
    data = request.json; sensor_mac = data.get('mac_id')
    conn = database.get_db()
    sensor_data = conn.execute("SELECT ip_gateway FROM sensores WHERE mac_id = ?", (sensor_mac,)).fetchone()
    ip_gw = sensor_data['ip_gateway'] if sensor_data else None

    try: conn.execute('''CREATE TABLE IF NOT EXISTS dispositivos (id SERIAL PRIMARY KEY, sensor_mac TEXT, ip TEXT, mac TEXT, fabricante TEXT, nome_custom TEXT)'''); conn.commit()
    except: pass
    try: conn.execute("CREATE TABLE IF NOT EXISTS nomes_conhecidos (mac TEXT PRIMARY KEY, nome TEXT)"); conn.commit()
    except: pass
    
    nomes_salvos = {row['mac']: row['nome'] for row in conn.execute("SELECT mac, nome FROM nomes_conhecidos").fetchall()}

    conn.execute("DELETE FROM dispositivos WHERE sensor_mac = ?", (sensor_mac,))
    for disp in data.get('lista', []):
        nome = nomes_salvos.get(disp['mac'])
        if not nome: nome = "Gateway / Roteador" if disp['ip'] == ip_gw else "Desconhecido"
        conn.execute("INSERT INTO dispositivos (sensor_mac, ip, mac, fabricante, nome_custom) VALUES (?, ?, ?, ?, ?)", (sensor_mac, disp['ip'], disp['mac'], disp['fabricante'], nome))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/renomear_dispositivo', methods=['POST'])
def renomear_dispositivo():
    data = request.json
    conn = database.get_db()
    try: conn.execute("CREATE TABLE IF NOT EXISTS nomes_conhecidos (mac TEXT PRIMARY KEY, nome TEXT)"); conn.commit()
    except: pass
    try:
        existe = conn.execute("SELECT mac FROM nomes_conhecidos WHERE mac = ?", (data['mac'],)).fetchone()
        if existe: conn.execute("UPDATE nomes_conhecidos SET nome = ? WHERE mac = ?", (data['nome'], data['mac']))
        else: conn.execute("INSERT INTO nomes_conhecidos (mac, nome) VALUES (?, ?)", (data['mac'], data['nome']))
        conn.execute("UPDATE dispositivos SET nome_custom = ? WHERE mac = ? AND sensor_mac = ?", (data['nome'], data['mac'], data['sensor_mac']))
        conn.commit()
    except:
        try:
            existe = conn.execute("SELECT mac FROM nomes_conhecidos WHERE mac = %s", (data['mac'],)).fetchone()
            if existe: conn.execute("UPDATE nomes_conhecidos SET nome = %s WHERE mac = %s", (data['nome'], data['mac']))
            else: conn.execute("INSERT INTO nomes_conhecidos (mac, nome) VALUES (%s, %s)", (data['mac'], data['nome']))
            conn.execute("UPDATE dispositivos SET nome_custom = %s WHERE mac = %s AND sensor_mac = %s", (data['nome'], data['mac'], data['sensor_mac']))
            conn.commit()
        except: pass
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/alertas_ia', methods=['POST'])
def alertas_ia():
    data = request.json
    conn = database.get_db()
    try: conn.execute('''CREATE TABLE IF NOT EXISTS logs_ia (id SERIAL PRIMARY KEY, sensor_mac TEXT, tipo_evento TEXT, gravidade TEXT, detalhes TEXT, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    except: pass
    
    # 🤖 TELEGRAM: INTERCEPTA OS AVISOS DO WATCHDOG AQUI!
    mac = data.get('mac_id', 'Desconhecido')
    sensor = conn.execute("SELECT nome_local FROM sensores WHERE mac_id = ?", (mac,)).fetchone()
    nome_sensor = sensor['nome_local'] if sensor else mac

    for alerta in data.get('alertas', []):
        conn.execute("INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, ?, ?, ?)", (mac, alerta['tipo'], alerta['gravidade'], alerta['detalhes']))
        
        # Lógica de Disparo
        if alerta['gravidade'] == 'Crítica':
            icone = "🔌" if "Energia" in alerta['tipo'] else "🖥️"
            enviar_telegram(f"🚨 <b>ALERTA DE SISTEMA (CRÍTICO)</b>\n\n{icone} <b>Sensor:</b> {nome_sensor}\n⚠️ <b>Evento:</b> {alerta['tipo']}\n❌ <b>Detalhe:</b> {alerta['detalhes']}")
        elif alerta['gravidade'] == 'OK' and ('Restaurad' in alerta['tipo']):
            icone = "🔌" if "Energia" in alerta['tipo'] else "🖥️"
            enviar_telegram(f"✅ <b>SISTEMA NORMALIZADO</b>\n\n{icone} <b>Sensor:</b> {nome_sensor}\n🟢 <b>Evento:</b> {alerta['tipo']}\nℹ️ <b>Detalhe:</b> {alerta['detalhes']}")

    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/mapa_sensores')
def api_mapa_sensores():
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 401
    role = session.get('role'); user_id = session.get('user_id')
    conn = database.get_db()

    try:
        import os
        is_postgres = bool(os.environ.get('DATABASE_URL'))
        condicao_tempo = "last_seen < NOW() - INTERVAL '15 seconds'" if is_postgres else "last_seen < datetime('now', '-15 seconds', 'localtime')"
        
        # 🤖 TELEGRAM: O CEIFEIRO DETECTA QUEDAS TOTAIS DE INTERNET AQUI
        caidos = conn.execute(f"SELECT mac_id, nome_local FROM sensores WHERE status = 'online' AND em_manutencao = 0 AND {condicao_tempo}").fetchall()
        for c in caidos:
            conn.execute("INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, 'Queda de Conexão', 'Crítica', 'Sensor parou de responder.')", (c['mac_id'],))
            enviar_telegram(f"🚨 <b>QUEDA CRÍTICA (NOC Central)</b>\n\n🏢 <b>Local:</b> {c.get('nome_local', c['mac_id'])}\n❌ <b>Status:</b> OFFLINE TOTAL\nℹ️ <b>Detalhe:</b> O Agente parou de se comunicar com a nuvem. Verifique a energia e o link principal do site.")
        
        conn.execute(f"UPDATE sensores SET status = 'offline', alerta_reconhecido = 0 WHERE status = 'online' AND em_manutencao = 0 AND {condicao_tempo}")
        conn.commit()
    except Exception as e: print(f"Aviso no Ceifeiro: {e}")

    try:
        query_base = "SELECT s.mac_id, s.nome_local, s.status, s.lat, s.lon, s.cpu_usage, s.ram_usage, s.net_down, s.net_up, s.alerta_reconhecido, s.em_manutencao, s.ping_global, c.nome as cliente_nome FROM sensores s LEFT JOIN clientes c ON s.cliente_id = c.id"
        if role in ['Administrador Master', 'Operador Master']: sensores = conn.execute(query_base).fetchall()
        elif role == 'Cliente': sensores = conn.execute(query_base + " WHERE s.cliente_id = ?", (user_id,)).fetchall()
        else:
            user_info = conn.execute("SELECT cliente_pai_id FROM clientes WHERE id = ?", (user_id,)).fetchone()
            if user_info and user_info['cliente_pai_id']: sensores = conn.execute(query_base + " WHERE s.cliente_id = ?", (user_info['cliente_pai_id'],)).fetchall()
            else: sensores = []
        conn.close()
        return jsonify({"sensores": [dict(s) for s in sensores]})
    except Exception as e:
        conn.close()
        return jsonify({"error_sql": str(e), "sensores": []})

@app.route('/debug')
def debug_db():
    conn = database.get_db()
    try:
        sensores = conn.execute("SELECT * FROM sensores").fetchall()
        return jsonify({"SISTEMA_VIVO": True, "sensores_no_banco": [dict(s) for s in sensores]})
    except Exception as e: return jsonify({"SISTEMA_VIVO": False, "erro_fatal": str(e)})

@app.route('/api/v2/sensor_data/<mac_id>', methods=['GET'])
def get_sensor_data(mac_id):
    conn = database.get_db()
    try:
        import os
        is_postgres = bool(os.environ.get('DATABASE_URL'))
        condicao_tempo = "last_seen < NOW() - INTERVAL '15 seconds'" if is_postgres else "last_seen < datetime('now', '-15 seconds', 'localtime')"
        conn.execute(f"UPDATE sensores SET status = 'offline' WHERE em_manutencao = 0 AND {condicao_tempo}")
        conn.commit()
    except: pass

    sensor = conn.execute("SELECT * FROM sensores WHERE mac_id = ?", (mac_id,)).fetchone()
    conn.close()
    if sensor: return jsonify(dict(sensor))
    return jsonify({"error": "Sensor não encontrado"}), 404

@app.route('/api/v2/configurar_sensor', methods=['POST'])
def configurar_sensor():
    data = request.json
    conn = database.get_db()
    conn.execute("UPDATE sensores SET nome_local = ?, lat = ?, lon = ? WHERE mac_id = ?", (data['nome'], data['lat'], data['lon'], data['mac_id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/solicitar_speedtest/<mac_id>', methods=['POST'])
def solicitar_speedtest(mac_id):
    SPEEDTEST_REQUESTS.add(mac_id)
    return jsonify({"status": "Teste na fila"})

@app.route('/api/v2/reportar_velocidade', methods=['POST'])
def reportar_velocidade():
    try:
        data = request.json; mac = data.get('mac_id')
        conn = database.get_db()
        conn.execute("UPDATE sensores SET download = ?, upload = ? WHERE mac_id = ?", (data['down'], data['up'], mac))
        conn.execute("INSERT INTO historico_telemetria (sensor_mac, download, upload) VALUES (?, ?, ?)", (mac, data['down'], data['up']))
        conn.commit()
        conn.close()
        return jsonify({"status": "OK"})
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/api/v2/graficos/<mac_id>')
def obter_graficos(mac_id):
    conn = database.get_db()
    try: registros = conn.execute("SELECT download, upload, to_char(data_hora - INTERVAL '3 hours', 'HH24:MI') as hora FROM historico_telemetria WHERE sensor_mac = ? ORDER BY id DESC LIMIT 15", (mac_id,)).fetchall()
    except: registros = []
    conn.close()
    return jsonify([dict(r) for r in registros][::-1])

@app.route('/api/v2/ips_customizados/<mac_id>', methods=['GET', 'POST'])
def gerenciar_ips(mac_id):
    conn = database.get_db()
    try: conn.execute('''CREATE TABLE IF NOT EXISTS ips_custom (id SERIAL PRIMARY KEY, sensor_mac TEXT, ip TEXT, descricao TEXT, latencia INTEGER DEFAULT 0)''')
    except: pass
    if request.method == 'POST':
        data = request.json
        conn.execute("INSERT INTO ips_custom (sensor_mac, ip, descricao) VALUES (?, ?, ?)", (mac_id, data['ip'], data['descricao']))
        conn.commit()
    ips = conn.execute("SELECT * FROM ips_custom WHERE sensor_mac = ? ORDER BY id DESC", (mac_id,)).fetchall()
    conn.close()
    return jsonify([dict(i) for i in ips])

@app.route('/api/v2/ips_customizados/<mac_id>/<int:id_ip>', methods=['DELETE', 'PUT'])
def crud_ips(mac_id, id_ip):
    conn = database.get_db()
    if request.method == 'DELETE': conn.execute("DELETE FROM ips_custom WHERE id = ?", (id_ip,))
    elif request.method == 'PUT':
        data = request.json
        conn.execute("UPDATE ips_custom SET ip = ?, descricao = ? WHERE id = ?", (data['ip'], data['descricao'], id_ip))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/reportar_latencia_custom', methods=['POST'])
def reportar_latencia_custom():
    data = request.json
    conn = database.get_db()
    conn.execute("UPDATE ips_custom SET latencia = ? WHERE id = ?", (data['latencia'], data['id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/historico/<mac_id>')
def historico_alertas(mac_id):
    data_filtro = request.args.get('data')
    conn = database.get_db()
    try: conn.execute('''CREATE TABLE IF NOT EXISTS logs_ia (id SERIAL PRIMARY KEY, sensor_mac TEXT, tipo_evento TEXT, gravidade TEXT, detalhes TEXT, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    except: pass
    query = "SELECT id, sensor_mac, tipo_evento, gravidade, detalhes, to_char(data_hora - INTERVAL '3 hours', 'DD/MM/YYYY HH24:MI:SS') as data_hora FROM logs_ia WHERE sensor_mac = ?"
    params = [mac_id]
    if data_filtro: query += " AND DATE(data_hora) = %s"; params.append(data_filtro)
    logs = conn.execute(query + " ORDER BY data_hora DESC", params).fetchall()
    conn.close()
    return jsonify([dict(l) for l in logs])

@app.route('/api/v2/dispositivos/<mac_id>', methods=['GET'])
def get_dispositivos(mac_id):
    conn = database.get_db()
    dispositivos = conn.execute("SELECT * FROM dispositivos WHERE sensor_mac = ?", (mac_id,)).fetchall()
    conn.close()
    return jsonify([dict(d) for d in dispositivos])

@app.route('/manifest.json')
def manifest(): return send_from_directory('static', 'manifest.json')
@app.route('/sw.js')
def service_worker(): return send_from_directory('static', 'sw.js')

@app.route('/usuarios')
def gerenciar_usuarios():
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return "Acesso Negado.", 403
    conn = database.get_db()
    if session['role'] == 'Administrador Master':
        usuarios = conn.execute("SELECT id, nome, usuario, role, ativo, cliente_pai_id, logo_url FROM clientes ORDER BY id DESC").fetchall()
        clientes_pais = conn.execute("SELECT id, nome FROM clientes WHERE role = 'Cliente'").fetchall()
    else:
        if session['role'] == 'Cliente': tenant_id = session['user_id']
        else: 
            user_info = conn.execute("SELECT cliente_pai_id FROM clientes WHERE id = ?", (session['user_id'],)).fetchone()
            tenant_id = user_info['cliente_pai_id']
        usuarios = conn.execute("SELECT id, nome, usuario, role, ativo, cliente_pai_id, logo_url FROM clientes WHERE cliente_pai_id = ? ORDER BY id DESC", (tenant_id,)).fetchall()
        clientes_pais = [] 
    conn.close()
    return render_template('usuarios.html', usuarios=[dict(u) for u in usuarios], role_atual=session['role'], clientes_pais=[dict(c) for c in clientes_pais])

@app.route('/api/v2/usuarios', methods=['POST'])
def criar_usuario():
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    senha_hash = generate_password_hash(data['senha'])
    logo_url = data.get('logo_url', '')
    conn = database.get_db()
    if session['role'] == 'Cliente': cliente_pai = session['user_id'] 
    elif session['role'] == 'Administrador Cliente':
        user_info = conn.execute("SELECT cliente_pai_id FROM clientes WHERE id = ?", (session['user_id'],)).fetchone()
        cliente_pai = user_info['cliente_pai_id']
    else:
        cliente_pai = data.get('cliente_pai') 
        if not cliente_pai or cliente_pai == "null": cliente_pai = None
    try:
        conn.execute("INSERT INTO clientes (nome, usuario, senha, role, cliente_pai_id, ativo, logo_url) VALUES (?, ?, ?, ?, ?, 1, ?)", (data['nome'], data['usuario'], senha_hash, data['role'], cliente_pai, logo_url))
        conn.commit()
        status = "OK"
    except: status = "Erro: Usuário já existe"
    finally: conn.close()
    return jsonify({"status": status})

@app.route('/api/v2/usuarios/<int:id_user>/toggle_status', methods=['POST'])
def toggle_user_status(id_user):
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    if id_user == session['user_id'] or id_user == 1: return jsonify({"error": "Ação não permitida"}), 403
    conn = database.get_db()
    user = conn.execute("SELECT ativo FROM clientes WHERE id = ?", (id_user,)).fetchone()
    novo_status = 0 if user.get('ativo', 1) == 1 else 1
    conn.execute("UPDATE clientes SET ativo = ? WHERE id = ?", (novo_status, id_user))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/usuarios/<int:id_user>/senha', methods=['POST'])
def alterar_senha_user(id_user):
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    nova_senha = generate_password_hash(data['senha'])
    conn = database.get_db()
    conn.execute("UPDATE clientes SET senha = ? WHERE id = ?", (nova_senha, id_user))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/usuarios/<int:id_user>/info', methods=['PUT'])
def editar_usuario_info(id_user):
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    logo_url = data.get('logo_url', '')
    conn = database.get_db()
    try:
        conn.execute("UPDATE clientes SET nome = ?, usuario = ?, logo_url = ? WHERE id = ?", (data.get('nome'), data.get('usuario'), logo_url, id_user))
        conn.commit()
        status = "OK"
    except: status = "Erro: Login já está em uso."
    finally: conn.close()
    return jsonify({"status": status})

@app.route('/api/v2/usuarios/<int:id_user>', methods=['DELETE'])
def deletar_usuario(id_user):
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    if id_user == session['user_id'] or id_user == 1: return jsonify({"error": "Ação não permitida"}), 403
    conn = database.get_db()
    conn.execute("DELETE FROM clientes WHERE id = ?", (id_user,))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/sensores')
def gerenciar_sensores():
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return "Acesso Negado. Área restrita ao Administrador Master.", 403
    conn = database.get_db()
    sensores = conn.execute('''SELECT s.mac_id, s.nome_local, s.status, s.ip_sensor, s.cliente_id, c.nome as cliente_nome FROM sensores s LEFT JOIN clientes c ON s.cliente_id = c.id ORDER BY s.cliente_id ASC''').fetchall()
    clientes = conn.execute("SELECT id, nome FROM clientes WHERE role IN ('Cliente', 'Administrador Master')").fetchall()
    conn.close()
    return render_template('sensores.html', sensores=[dict(s) for s in sensores], clientes=[dict(c) for c in clientes])

@app.route('/api/v2/alocar_sensor', methods=['POST'])
def alocar_sensor():
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    cliente_id = data.get('cliente_id')
    mac_id = data.get('mac_id')
    if not cliente_id or cliente_id == "null": cliente_id = None
    conn = database.get_db()
    conn.execute("UPDATE sensores SET cliente_id = ? WHERE mac_id = ?", (cliente_id, mac_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/sensor_virtual')
def sensor_virtual():
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return "Acesso Negado. Ferramenta Exclusiva.", 403
    return render_template('sensor_virtual.html', nome_operador=session.get('nome', 'Admin'))

@app.route('/api/v2/renomear_sensor', methods=['POST'])
def renomear_sensor():
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    novo_nome = data.get('novo_nome')
    mac_id = data.get('mac_id')
    if novo_nome and mac_id:
        conn = database.get_db()
        conn.execute("UPDATE sensores SET nome_local = ? WHERE mac_id = ?", (novo_nome, mac_id))
        conn.commit()
        conn.close()
        return jsonify({"status": "OK"})
    return jsonify({"error": "Dados inválidos"}), 400

@app.route('/api/v2/deletar_sensor/<mac_id>', methods=['DELETE'])
def deletar_sensor(mac_id):
    if 'user_id' not in session or session.get('role') != 'Administrador Master': return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    conn.execute("DELETE FROM sensores WHERE mac_id = ?", (mac_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/solicitar_traceroute/<mac_id>', methods=['POST'])
def solicitar_traceroute(mac_id):
    TRACEROUTE_REQUESTS.add(mac_id)
    return jsonify({"status": "OK"})

@app.route('/api/v2/reportar_rota', methods=['POST'])
def reportar_rota():
    data = request.json
    conn = database.get_db()
    conn.execute("UPDATE sensores SET ultima_rota = ? WHERE mac_id = ?", (data['rota'], data['mac_id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/logs_globais')
def logs_globais():
    conn = database.get_db()
    try:
        logs = conn.execute('''SELECT l.tipo_evento, l.gravidade, l.detalhes, to_char(l.data_hora - INTERVAL '3 hours', 'DD/MM HH24:MI:SS') as hora, s.nome_local FROM logs_ia l LEFT JOIN sensores s ON l.sensor_mac = s.mac_id ORDER BY l.id DESC LIMIT 50''').fetchall()
    except:
        try: logs = conn.execute("SELECT tipo_evento, gravidade, detalhes, to_char(data_hora - INTERVAL '3 hours', 'DD/MM HH24:MI:SS') as hora, sensor_mac as nome_local FROM logs_ia ORDER BY id DESC LIMIT 50").fetchall()
        except: logs = []
    conn.close()
    return jsonify([dict(l) for l in logs])

@app.route('/api/v2/solicitar_update/<mac_id>', methods=['POST'])
def solicitar_update(mac_id):
    UPDATE_REQUESTS.add(mac_id)
    return jsonify({"status": "OK"})

@app.route('/api/v2/toggle_manutencao/<mac_id>', methods=['POST'])
def toggle_manutencao(mac_id):
    if 'user_id' not in session: return jsonify({"error": "Acesso Negado"}), 403
    conn = database.get_db()
    sensor = conn.execute("SELECT em_manutencao FROM sensores WHERE mac_id = ?", (mac_id,)).fetchone()
    novo_estado = 1 if sensor['em_manutencao'] == 0 else 0
    conn.execute("UPDATE sensores SET em_manutencao = ?, status = 'online' WHERE mac_id = ?", (novo_estado, mac_id))
    
    msg = "SENSOR EM MANUTENÇÃO" if novo_estado == 1 else "MANUTENÇÃO ENCERRADA"
    conn.execute("INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, 'Setup', 'Aviso', ?)", (mac_id, f"Operador {session['usuario']} alterou para: {msg}"))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK", "novo_estado": novo_estado})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000, debug=True)