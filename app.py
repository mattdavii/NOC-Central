from flask import Flask, jsonify, request, render_template, session, redirect, url_for, flash, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
import database

app = Flask(__name__)
app.secret_key = 'chave_super_secreta_noc_md' 

# =========================================================
# SETUP INICIAL: Cria as tabelas e o usuário Admin na Nuvem
# =========================================================
try:
    import database
    database.init_db() # Garante que as tabelas sejam criadas
    
    conn = database.get_db()
    # Verifica se o admin já existe no banco
    admin = conn.execute("SELECT * FROM clientes WHERE usuario = 'admin'").fetchone()
    
    if not admin:
        # Se o banco for novo, ele injeta o usuário padrão
        conn.execute("INSERT INTO clientes (usuario, senha, role) VALUES ('admin', 'admin123', 'Administrador Master')")
        conn.commit()
        print("Usuário admin criado com sucesso na nuvem!")
        
    conn.close()
except Exception as e:
    print(f"Aviso na inicialização do banco: {e}")
# =========================================================

# Inicializa o banco de dados
database.init_db()

# Patch: Promove o usuário 'admin' antigo para 'Administrador Master'
try:
    conn = database.get_db()
    cursor = conn.cursor() # O PostgreSQL exige o Cursor!
    cursor.execute("UPDATE clientes SET role = 'Administrador Master' WHERE usuario = 'admin'")
    conn.commit()
    cursor.close()
except Exception as e:
    print(f"Aviso ao iniciar banco: {e}")

# Variável em memória para controlar os pedidos de Speedtest manuais
SPEEDTEST_REQUESTS = set()
# Fila de comandos de energia para os sensores
PENDING_COMMANDS = {}

# ==========================================
# 🔐 SISTEMA DE LOGIN E SESSÃO
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    from flask import request, session, redirect, url_for, render_template
    import database
    
    erro = None
    if request.method == 'POST':
        # .strip() arranca qualquer espaço invisível que venha do navegador
        usuario_digitado = request.form.get('usuario', '').strip()
        senha_digitada = request.form.get('senha', '').strip()
        
        print(f"TENTATIVA: User='{usuario_digitado}' | Pass='{senha_digitada}'")
        
        try:
            # 🚨 CHAVE MESTRA E AUTO-CORREÇÃO DO BANCO 🚨
            if usuario_digitado == 'admin' and senha_digitada == 'admin123':
                conn = database.get_db()
                user = conn.execute("SELECT * FROM clientes WHERE usuario = 'admin'").fetchone()
                
                if not user:
                    # Se não existia, cria do jeito certo
                    conn.execute("INSERT INTO clientes (usuario, senha, role) VALUES ('admin', 'admin123', 'Administrador Master')")
                else:
                    # Se existia e estava bugado, força a senha a voltar a ser admin123
                    conn.execute("UPDATE clientes SET senha = 'admin123' WHERE usuario = 'admin'")
                
                conn.commit()
                conn.close()
                
                # Libera a entrada!
                session['logged_in'] = True
                session['usuario'] = 'admin'
                session['role'] = 'Administrador Master'
                print("ACESSO MASTER LIBERADO COM SUCESSO!")
                return redirect(url_for('index'))

            # Fluxo normal para outros usuários que você criar no futuro
            conn = database.get_db()
            user = conn.execute("SELECT * FROM clientes WHERE usuario = ?", (usuario_digitado,)).fetchone()
            
            # str().strip() garante que a senha do banco também não tenha espaços acidentais
            if user and str(user['senha']).strip() == senha_digitada:
                session['logged_in'] = True
                session['usuario'] = user['usuario']
                session['role'] = user['role']
                print("ACESSO NORMAL LIBERADO!")
                return redirect(url_for('index'))
            else:
                erro = "Usuário ou senha incorretos!"
                
        except Exception as e:
            erro = f"Erro no banco de dados: {e}"
            print(erro)

    return render_template('login.html', erro=erro)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ==========================================
# 🗺️ ROTAS DO PAINEL (Frontend UI)
# ==========================================
@app.route('/')
def index():
    # Agora ele procura a chave correta que o login gerou
    if 'usuario' not in session: 
        return redirect(url_for('login'))
    
    # Passa o nome correto para o HTML mostrar na tela
    return render_template('index.html', nome=session['usuario'])

@app.route('/sensor/<mac_id>')
def painel_sensor(mac_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = database.get_db()
    sensor = conn.execute("SELECT * FROM sensores WHERE mac_id = ?", (mac_id,)).fetchone()
    conn.close()
    
    if not sensor: return "Sensor não encontrado", 404
    return render_template('sensor.html', sensor=sensor, nome=session['user_nome'])

# ==========================================
# 📥 RECEPÇÃO DE DADOS (SENSORES FÍSICOS E VIRTUAIS)
# ==========================================

@app.route('/api/v2/report_data', methods=['POST'])
def report_data():
    try:
        data = request.json
        mac = data.get('mac_id')
        
        if not mac: return jsonify({"error": "MAC ID não fornecido"}), 400

        conn = database.get_db()
        
        # Mágica de Banco de Dados: Garante colunas
        colunas = ['lat', 'lon', 'ping_global', 'ping_gateway', 'ip_gateway']
        for col in colunas:
            try: conn.execute(f"ALTER TABLE sensores ADD COLUMN {col} TEXT")
            except: pass

        sensor = conn.execute("SELECT mac_id FROM sensores WHERE mac_id = ?", (mac,)).fetchone()
        ip_display = data.get('ip_publico') if data.get('ip_publico') else data.get('ip_local', '0.0.0.0')

        if sensor:
            conn.execute('''UPDATE sensores SET 
                nome_local = ?, ip_sensor = ?, cpu_usage = ?, ram_usage = ?, temp = ?, 
                status = 'online', lat = ?, lon = ?, ping_gateway = ?, ping_global = ? 
                WHERE mac_id = ?''', 
                (data.get('nome_local'), ip_display, data.get('cpu_usage'), data.get('ram_usage'), 
                 data.get('temp'), data.get('lat'), data.get('lon'), data.get('ping_gateway'), 
                 data.get('ping_global'), mac))
        else:
            conn.execute('''INSERT INTO sensores 
                (mac_id, nome_local, ip_sensor, cpu_usage, ram_usage, temp, status, lat, lon, ping_gateway, ping_global) 
                VALUES (?, ?, ?, ?, ?, ?, 'online', ?, ?, ?, ?)''',
                (mac, data.get('nome_local'), ip_display, data.get('cpu_usage'), data.get('ram_usage'), 
                 data.get('temp'), data.get('lat'), data.get('lon'), data.get('ping_gateway'), data.get('ping_global')))
        
        # --- 📊 HISTÓRICO DE PINGS A CADA 2 SEGUNDOS ---
        try: 
            conn.execute('''CREATE TABLE IF NOT EXISTS historico_pings (
                id INTEGER PRIMARY KEY AUTOINCREMENT, sensor_mac TEXT, 
                google INTEGER, cloudflare INTEGER, aws INTEGER, quad9 INTEGER, 
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            import json
            ping_str = data.get('ping_global')
            if ping_str:
                p = json.loads(ping_str) if isinstance(ping_str, str) else ping_str
                conn.execute("INSERT INTO historico_pings (sensor_mac, google, cloudflare, aws, quad9) VALUES (?, ?, ?, ?, ?)",
                             (mac, p.get('Google',0), p.get('Cloudflare',0), p.get('AWS',0), p.get('Quad9',0)))
                # Limita o histórico aos últimos 30 registros para não lotar o banco
                conn.execute("DELETE FROM historico_pings WHERE id NOT IN (SELECT id FROM historico_pings WHERE sensor_mac = ? ORDER BY id DESC LIMIT 30)", (mac,))
        except Exception as ping_err: 
            print("🔴 Erro ao salvar histórico de ping:", ping_err)

        conn.commit()
        conn.close()
        
        # --- RESPOSTA INTELIGENTE (VERIFICA COMANDOS) ---
        comando_pendente = PENDING_COMMANDS.get(mac)
        if comando_pendente:
            del PENDING_COMMANDS[mac] # Limpa da fila após entregar
            return jsonify({"status": "OK", "command": comando_pendente})
            
        return jsonify({"status": "OK", "command": "none"})
        
    except Exception as e:
        print(f"\n🔴 ERRO GRAVE NA RECEPÇÃO: {e}\n")
        return jsonify({"error": str(e)}), 500

@app.route('/api/v2/comando_energia/<mac_id>', methods=['POST'])
def enviar_comando_energia(mac_id):
    # Apenas Admin Master pode desligar equipamentos
    if 'user_id' not in session or session.get('role') != 'Administrador Master':
        return jsonify({"error": "Acesso Negado"}), 403
        
    data = request.json
    PENDING_COMMANDS[mac_id] = data.get('comando')
    return jsonify({"status": "Comando enfileirado"})

# ==========================================
# 📊 ROTA DO GRÁFICO DE PINGS
# ==========================================
@app.route('/api/v2/graficos_ping/<mac_id>')
def obter_graficos_ping(mac_id):
    """ Busca os últimos 30 pings para o gráfico de disponibilidade """
    conn = database.get_db()
    try:
        registros = conn.execute("SELECT google, cloudflare, aws, quad9, strftime('%H:%M:%S', data_hora) as hora FROM historico_pings WHERE sensor_mac = ? ORDER BY id DESC LIMIT 30", (mac_id,)).fetchall()
    except:
        registros = []
    conn.close()
    
    registros.reverse() # Inverte para mostrar da esquerda (mais antigo) para a direita (mais novo)
    return jsonify([dict(r) for r in registros])

@app.route('/api/v2/registrar_sensor', methods=['POST'])
def registrar_sensor():
    data = request.json
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT mac_id FROM sensores WHERE mac_id = ?", (data['mac_id'],))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO sensores (mac_id, cliente_id, nome_local, lat, lon) VALUES (?, 1, ?, ?, ?)", 
                       (data['mac_id'], data.get('nome_local', 'Sensor Novo'), data.get('lat', -14.235), data.get('lon', -51.925)))
        conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/telemetria_instantanea', methods=['POST'])
def telemetria_instantanea():
    data = request.json
    mac_id = data['mac_id']
    
    # Verifica se a central pediu um speedtest para este sensor
    run_st = mac_id in SPEEDTEST_REQUESTS
    if run_st: 
        SPEEDTEST_REQUESTS.remove(mac_id) 
    
    conn = database.get_db()
    conn.execute("""
        UPDATE sensores 
        SET status = 'online', cpu_usage = ?, ram_usage = ?, temp = ?, ping_gateway = ?, ip_sensor = ?, ip_gateway = ?, last_ping = CURRENT_TIMESTAMP 
        WHERE mac_id = ?
    """, (data.get('cpu'), data.get('ram'), data.get('temp', 0), data.get('ping_gw'), data.get('ip_sensor'), data.get('ip_gateway'), mac_id))
    conn.commit()
    conn.close()
    
    # Responde ao sensor mandando a ordem de rodar o speedtest, se houver
    return jsonify({"status": "OK", "run_speedtest": run_st})

@app.route('/api/v2/telemetria_global', methods=['POST'])
def telemetria_global():
    data = request.json
    conn = database.get_db()
    conn.execute("UPDATE sensores SET ping_global = ?, traceroute = ? WHERE mac_id = ?", 
                 (data.get('pings'), data.get('tracert'), data['mac_id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/atualizar_dispositivos', methods=['POST'])
def atualizar_dispositivos():
    data = request.json
    sensor_mac = data.get('mac_id')
    conn = database.get_db()
    
    # Cria uma tabela auxiliar invisível de "Memória Fotográfica" de MACs (Sem precisar resetar o banco!)
    try: conn.execute("CREATE TABLE IF NOT EXISTS nomes_conhecidos (mac TEXT PRIMARY KEY, nome TEXT)")
    except: pass
    try: conn.execute("ALTER TABLE dispositivos ADD COLUMN nome_custom TEXT")
    except: pass
    
    # Lê a memória fotográfica para lembrar o nome de todo mundo pelo MAC
    nomes_salvos = {row['mac']: row['nome'] for row in conn.execute("SELECT mac, nome FROM nomes_conhecidos").fetchall()}

    conn.execute("DELETE FROM dispositivos WHERE sensor_mac = ?", (sensor_mac,))
    for disp in data.get('lista', []):
        nome = nomes_salvos.get(disp['mac']) # Puxa da memória pelo MAC, mesmo se o IP mudou
        conn.execute("INSERT INTO dispositivos (sensor_mac, ip, mac, fabricante, nome_custom) VALUES (?, ?, ?, ?, ?)",
                       (sensor_mac, disp['ip'], disp['mac'], disp['fabricante'], nome))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/renomear_dispositivo', methods=['POST'])
def renomear_dispositivo():
    data = request.json
    conn = database.get_db()
    try: conn.execute("CREATE TABLE IF NOT EXISTS nomes_conhecidos (mac TEXT PRIMARY KEY, nome TEXT)")
    except: pass
    
    # Salva na Memória Permanente (para nunca mais esquecer)
    conn.execute("INSERT OR REPLACE INTO nomes_conhecidos (mac, nome) VALUES (?, ?)", (data['mac'], data['nome']))
    
    # Atualiza a tela atual
    conn.execute("UPDATE dispositivos SET nome_custom = ? WHERE mac = ? AND sensor_mac = ?",
                 (data['nome'], data['mac'], data['sensor_mac']))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/alertas_ia', methods=['POST'])
def alertas_ia():
    data = request.json
    conn = database.get_db()
    for alerta in data.get('alertas', []):
        conn.execute("INSERT INTO logs_ia (sensor_mac, tipo_evento, gravidade, detalhes) VALUES (?, ?, ?, ?)",
                     (data['mac_id'], alerta['tipo'], alerta['gravidade'], alerta['detalhes']))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

# ==========================================
# ⚙️ APIS DA INTERFACE (Botões, Mapas, Edição)
# ==========================================

@app.route('/api/v2/mapa_sensores')
def api_mapa_sensores():
    """ Retorna a lista de sensores filtrada pelo nível de acesso do usuário """
    if 'user_id' not in session:
        return jsonify({"error": "Acesso Negado"}), 401
        
    role = session.get('role')
    user_id = session.get('user_id')
    
    conn = database.get_db()
    
    # Prevenção passiva: garante que a coluna cliente_id existe
    try: conn.execute("ALTER TABLE sensores ADD COLUMN cliente_id INTEGER")
    except: pass

    # LÓGICA DE ISOLAMENTO DE TENANT (MULTI-EMPRESA)
    if role in ['Administrador Master', 'Operador Master']:
        # Nível Supremo: Vê o mapa global com todos os sensores de todas as empresas
        sensores = conn.execute("SELECT mac_id, nome_local, status, lat, lon, cpu_usage FROM sensores").fetchall()
        
    elif role == 'Cliente':
        # Dono da Empresa: Vê apenas os sensores onde ele é o dono (cliente_id = ID dele)
        sensores = conn.execute("SELECT mac_id, nome_local, status, lat, lon, cpu_usage FROM sensores WHERE cliente_id = ?", (user_id,)).fetchall()
        
    else:
        # Administrador Cliente, Operador Cliente ou Local:
        # 1º Passo: Descobre quem é a "Empresa" (cliente_pai_id) desse funcionário
        user_info = conn.execute("SELECT cliente_pai_id FROM clientes WHERE id = ?", (user_id,)).fetchone()
        
        # 2º Passo: Se ele tem uma empresa vinculada, mostra os sensores daquela empresa
        if user_info and user_info['cliente_pai_id']:
            tenant_id = user_info['cliente_pai_id']
            sensores = conn.execute("SELECT mac_id, nome_local, status, lat, lon, cpu_usage FROM sensores WHERE cliente_id = ?", (tenant_id,)).fetchall()
        else:
            # Se for um operador "solto" (sem vínculo), ele vê o mapa vazio por segurança
            sensores = []

    conn.close()
    return jsonify({"sensores": [dict(s) for s in sensores]})

@app.route('/api/v2/sensor_data/<mac_id>', methods=['GET'])
def get_sensor_data(mac_id):
    conn = database.get_db()
    sensor = conn.execute("SELECT * FROM sensores WHERE mac_id = ?", (mac_id,)).fetchone()
    conn.close()
    if sensor: return jsonify(dict(sensor))
    return jsonify({"error": "Sensor não encontrado"}), 404

@app.route('/api/v2/configurar_sensor', methods=['POST'])
def configurar_sensor():
    data = request.json
    conn = database.get_db()
    conn.execute("UPDATE sensores SET nome_local = ?, lat = ?, lon = ? WHERE mac_id = ?", 
                 (data['nome'], data['lat'], data['lon'], data['mac_id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "Configurado!"})

@app.route('/api/v2/solicitar_speedtest/<mac_id>', methods=['POST'])
def solicitar_speedtest(mac_id):
    SPEEDTEST_REQUESTS.add(mac_id)
    return jsonify({"status": "Teste na fila"})

# ==========================================
# 📊 SISTEMA DE GRÁFICOS E HISTÓRICO
# ==========================================

@app.route('/api/v2/reportar_velocidade', methods=['POST'])
def reportar_velocidade():
    data = request.json
    mac = data.get('mac_id')
    conn = database.get_db()
    
    # Mágica: Cria a tabela de histórico silenciosamente se não existir
    try: 
        conn.execute('''CREATE TABLE IF NOT EXISTS historico_telemetria (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sensor_mac TEXT, 
            download REAL, upload REAL, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
    except: pass

    # 1. Atualiza a velocidade atual na tela
    conn.execute("UPDATE sensores SET download = ?, upload = ? WHERE mac_id = ?", (data['down'], data['up'], mac))
    
    # 2. Guarda na "Caixa Preta" para desenhar o gráfico
    conn.execute("INSERT INTO historico_telemetria (sensor_mac, download, upload) VALUES (?, ?, ?)", (mac, data['down'], data['up']))
    
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/v2/graficos/<mac_id>')
def obter_graficos(mac_id):
    """ Busca os últimos 15 testes de velocidade do sensor para montar o gráfico """
    conn = database.get_db()
    try:
        # Pega as últimas 15 medições e extrai apenas a Hora e o Minuto
        registros = conn.execute("SELECT download, upload, strftime('%H:%M', data_hora) as hora FROM historico_telemetria WHERE sensor_mac = ? ORDER BY id DESC LIMIT 15", (mac_id,)).fetchall()
    except:
        registros = []
    conn.close()

    # Inverte a lista para o gráfico ficar na ordem cronológica certa (da esquerda pra direita)
    registros.reverse()
    return jsonify([dict(r) for r in registros])

# --- GERENCIAMENTO DE IPs CUSTOMIZADOS ---
@app.route('/api/v2/ips_customizados/<mac_id>', methods=['GET', 'POST'])
def gerenciar_ips(mac_id):
    conn = database.get_db()
    if request.method == 'POST':
        data = request.json
        conn.execute("INSERT INTO ips_custom (sensor_mac, ip, descricao) VALUES (?, ?, ?)", 
                     (mac_id, data['ip'], data['descricao']))
        conn.commit()
    
    ips = conn.execute("SELECT * FROM ips_custom WHERE sensor_mac = ? ORDER BY id DESC", (mac_id,)).fetchall()
    conn.close()
    return jsonify([dict(i) for i in ips])

@app.route('/api/v2/ips_customizados/<mac_id>/<int:id_ip>', methods=['DELETE', 'PUT'])
def crud_ips(mac_id, id_ip):
    conn = database.get_db()
    if request.method == 'DELETE':
        conn.execute("DELETE FROM ips_custom WHERE id = ?", (id_ip,))
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

# --- HISTÓRICO DE ALERTAS ---
@app.route('/api/v2/historico/<mac_id>')
def historico_alertas(mac_id):
    data_filtro = request.args.get('data')
    conn = database.get_db()
    query = "SELECT * FROM logs_ia WHERE sensor_mac = ?"
    params = [mac_id]
    
    if data_filtro:
        query += " AND date(data_hora) = ?"
        params.append(data_filtro)
    
    logs = conn.execute(query + " ORDER BY data_hora DESC", params).fetchall()
    conn.close()
    return jsonify([dict(l) for l in logs])

@app.route('/api/v2/dispositivos/<mac_id>', methods=['GET'])
def get_dispositivos(mac_id):
    """ Busca todos os dispositivos encontrados na rede local do sensor """
    conn = database.get_db()
    dispositivos = conn.execute("SELECT * FROM dispositivos WHERE sensor_mac = ?", (mac_id,)).fetchall()
    conn.close()
    return jsonify([dict(d) for d in dispositivos])

# ==========================================
# 📱 ROTAS DO APLICATIVO (PWA)
# ==========================================
@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js')

# ==========================================
# 👥 SISTEMA DE GERENCIAMENTO DE USUÁRIOS
# ==========================================

@app.route('/usuarios')
def gerenciar_usuarios():
    # Agora o Administrador Cliente também entra na festa
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: 
        return "Acesso Negado.", 403
    
    conn = database.get_db()
    try: conn.execute("ALTER TABLE clientes ADD COLUMN cliente_pai_id INTEGER")
    except: pass
    try: conn.execute("ALTER TABLE clientes ADD COLUMN ativo INTEGER DEFAULT 1")
    except: pass

    if session['role'] == 'Administrador Master':
        usuarios = conn.execute("SELECT id, nome, usuario, role, ativo, cliente_pai_id FROM clientes ORDER BY id DESC").fetchall()
        clientes_pais = conn.execute("SELECT id, nome FROM clientes WHERE role = 'Cliente'").fetchall()
    else:
        # Lógica Multi-Tenant: Descobre a qual "Empresa" esse cara pertence
        if session['role'] == 'Cliente':
            tenant_id = session['user_id']
        else: # Se for Administrador Cliente, puxa o ID do Cliente Dono
            user_info = conn.execute("SELECT cliente_pai_id FROM clientes WHERE id = ?", (session['user_id'],)).fetchone()
            tenant_id = user_info['cliente_pai_id']

        usuarios = conn.execute("SELECT id, nome, usuario, role, ativo, cliente_pai_id FROM clientes WHERE cliente_pai_id = ? ORDER BY id DESC", (tenant_id,)).fetchall()
        clientes_pais = [] 
        
    conn.close()
    return render_template('usuarios.html', usuarios=[dict(u) for u in usuarios], role_atual=session['role'], clientes_pais=[dict(c) for c in clientes_pais])

@app.route('/api/v2/usuarios', methods=['POST'])
def criar_usuario():
    if 'user_id' not in session or session.get('role') not in ['Administrador Master', 'Cliente', 'Administrador Cliente']: return jsonify({"error": "Acesso Negado"}), 403
    data = request.json
    senha_hash = generate_password_hash(data['senha'])
    
    conn = database.get_db()
    # Mágica do Vínculo
    if session['role'] == 'Cliente':
        cliente_pai = session['user_id'] 
    elif session['role'] == 'Administrador Cliente':
        user_info = conn.execute("SELECT cliente_pai_id FROM clientes WHERE id = ?", (session['user_id'],)).fetchone()
        cliente_pai = user_info['cliente_pai_id']
    else:
        cliente_pai = data.get('cliente_pai') 
        if not cliente_pai or cliente_pai == "null": cliente_pai = None

    try:
        conn.execute("INSERT INTO clientes (nome, usuario, senha, role, cliente_pai_id, ativo) VALUES (?, ?, ?, ?, ?, 1)",
                     (data['nome'], data['usuario'], senha_hash, data['role'], cliente_pai))
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
    conn = database.get_db()
    try:
        conn.execute("UPDATE clientes SET nome = ?, usuario = ? WHERE id = ?", (data.get('nome'), data.get('usuario'), id_user))
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

# ==========================================
# 📡 SISTEMA DE GERENCIAMENTO DE SENSORES (ZTP)
# ==========================================

@app.route('/sensores')
def gerenciar_sensores():
    # Apenas o Chefão pode acessar essa tela
    if 'user_id' not in session or session.get('role') != 'Administrador Master':
        return "Acesso Negado. Área restrita ao Administrador Master.", 403
    
    conn = database.get_db()
    
    # Prevenção: Garante que a coluna cliente_id existe no banco
    try: conn.execute("ALTER TABLE sensores ADD COLUMN cliente_id INTEGER")
    except: pass

    # Busca todos os sensores e cruza com a tabela de clientes para saber o nome do dono
    sensores = conn.execute('''
        SELECT s.mac_id, s.nome_local, s.status, s.ip_sensor, s.cliente_id, c.nome as cliente_nome 
        FROM sensores s 
        LEFT JOIN clientes c ON s.cliente_id = c.id
        ORDER BY s.cliente_id ASC
    ''').fetchall()
    
    # Busca apenas os usuários que são "Clientes" ou "Admins" para popular a lista de seleção
    clientes = conn.execute("SELECT id, nome FROM clientes WHERE role IN ('Cliente', 'Administrador Master')").fetchall()
    conn.close()
    
    return render_template('sensores.html', sensores=[dict(s) for s in sensores], clientes=[dict(c) for c in clientes])

@app.route('/api/v2/alocar_sensor', methods=['POST'])
def alocar_sensor():
    if 'user_id' not in session or session.get('role') != 'Administrador Master':
        return jsonify({"error": "Acesso Negado"}), 403
        
    data = request.json
    cliente_id = data.get('cliente_id')
    mac_id = data.get('mac_id')
    
    # Se o valor for "null" ou vazio, transformamos em None (para desvincular)
    if not cliente_id or cliente_id == "null":
        cliente_id = None
        
    conn = database.get_db()
    conn.execute("UPDATE sensores SET cliente_id = ? WHERE mac_id = ?", (cliente_id, mac_id))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "OK"})

# ==========================================
# 🧪 SENSOR VIRTUAL (MODO DEMONSTRAÇÃO)
# ==========================================

@app.route('/sensor_virtual')
def sensor_virtual():
    # Ferramenta estritamente restrita ao Chefão
    if 'user_id' not in session or session.get('role') != 'Administrador Master':
        return "Acesso Negado. Ferramenta Exclusiva.", 403
    
    return render_template('sensor_virtual.html', nome_operador=session.get('nome', 'Admin'))

if __name__ == '__main__':
    # O host='0.0.0.0' permite que o site seja acessado pelo IP da rede local
    app.run(host='0.0.0.0', port=10000, debug=True)