import time, json, platform, subprocess, uuid, os, threading, sqlite3, socket
import urllib.request
from datetime import datetime
from flask import Flask, request, Response, render_template_string, jsonify
import pystray
from PIL import Image, ImageDraw
import concurrent.futures
try: import speedtest
except: pass

# ==========================================
# ⚙️ CONFIGURAÇÃO DO AGENTE
# ==========================================
# Link atualizado da sua Central na Nuvem
URL_CENTRAL = "https://noc-central.onrender.com/api/v2/report_data" 
PORTA_LOCAL = 10000

app = Flask(__name__)

dados_sensores = {
    "cpu": 0, "ram": 0, "meu_ip": "Detectando...", "gateway_ip": "Detectando...", 
    "ping_gateway": 0, "pings": {"Google":0, "Cloudflare":0, "AWS":0, "Quad9":0}, 
    "custom_ips": [], "topologia": [], "logs": []
}

def get_mac():
    mac = uuid.getnode()
    return ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))

def get_network_info():
    meu_ip = "127.0.0.1"
    gateway = "Desconhecido"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        meu_ip = s.getsockname()[0]
        s.close()
    except: pass

    try:
        if platform.system().lower() == 'windows':
            saida = subprocess.check_output("route print 0.0.0.0", shell=True, universal_newlines=True, creationflags=subprocess.CREATE_NO_WINDOW)
            for linha in saida.split('\n'):
                partes = linha.split()
                if len(partes) >= 3 and partes[0] == '0.0.0.0':
                    gateway = partes[2]
                    break
        else:
            saida = subprocess.check_output("ip route | grep default", shell=True, universal_newlines=True)
            gateway = saida.split()[2]
    except: pass
    return meu_ip, gateway

def get_topologia_arp(meu_ip):
    dispositivos = []
    prefixo_rede = '.'.join(meu_ip.split('.')[:-1]) + '.'
    try:
        saida = subprocess.check_output("arp -a", shell=True, universal_newlines=True, creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0)
        for linha in saida.split('\n'):
            partes = linha.split()
            if len(partes) >= 2 and '.' in partes[0] and ('-' in partes[1] or ':' in partes[1]):
                ip = partes[0]
                mac = partes[1].replace('-', ':').upper()
                if ip.startswith(prefixo_rede) and not ip.endswith(".255"):
                    dispositivos.append({"ip": ip, "mac": mac, "nome": "Dispositivo Genérico"})
    except: pass
    return dispositivos

def ping(host):
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    comando = ['ping', param, '1', host]
    try:
        saida = subprocess.check_output(comando, stderr=subprocess.STDOUT, universal_newlines=True, creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0)
        if '<1ms' in saida: return 1
        if 'time=' in saida or 'tempo=' in saida:
            for palavra in saida.split():
                if palavra.startswith('time=') or palavra.startswith('tempo='):
                    return int(float(palavra.split('=')[1].replace('ms', '')))
        return 0
    except: return 0

# ==========================================
# 🧠 BANCO DE DADOS LOCAL (EDGE COMPUTING)
# ==========================================
def init_local_db():
    conn = sqlite3.connect('sensor_local.db')
    conn.execute('''CREATE TABLE IF NOT EXISTS alvos_locais (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT, descricao TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS logs_locais (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, detalhes TEXT, gravidade TEXT, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS nomes_topologia (mac TEXT PRIMARY KEY, nome TEXT)''')
    conn.commit()
    conn.close()

def log_local_event(tipo, detalhes, gravidade="Alerta"):
    try:
        conn = sqlite3.connect('sensor_local.db')
        conn.execute("INSERT INTO logs_locais (tipo, detalhes, gravidade) VALUES (?, ?, ?)", (tipo, detalhes, gravidade))
        conn.execute("DELETE FROM logs_locais WHERE id NOT IN (SELECT id FROM logs_locais ORDER BY id DESC LIMIT 30)")
        conn.commit()
        conn.close()
    except: pass

def executar_speedtest(mac, url_central):
    try:
        print("⏳ Central solicitou Speedtest! Testando... (Leva uns 20s)")
        st = speedtest.Speedtest()
        st.get_best_server()
        d = st.download() / 1_000_000
        u = st.upload() / 1_000_000
        payload = {"mac_id": mac, "down": round(d, 2), "up": round(u, 2)}

        url_speed = url_central.replace('report_data', 'reportar_velocidade')
        req = urllib.request.Request(url_speed, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=10)
        print(f"✅ Speedtest Enviado: Down {round(d, 2)} Mbps | Up {round(u, 2)} Mbps")
    except Exception as e: print(f"❌ Erro no Speedtest: {e}")

# ==========================================
# 📡 MOTOR 1: ENVIO E COLETA (BACKGROUND)
# ==========================================
def loop_telemetria():
    global dados_sensores
    mac = get_mac()
    os_name = platform.system()
    estado_anterior = {"wan": True, "gw": True}
    
    while True:
        try: import psutil; cpu = psutil.cpu_percent(interval=0.1); ram = psutil.virtual_memory().percent
        except: cpu = 10.0; ram = 10.0

        # Dispara os 4 pings ao MESMO TEMPO (Muito mais rápido)
        hosts_ping = {"Google": "8.8.8.8", "Cloudflare": "1.1.1.1", "AWS": "aws.amazon.com", "Quad9": "9.9.9.9"}
        pings = {}
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(ping, ip): name for name, ip in hosts_ping.items()}
            for future in concurrent.futures.as_completed(futures):
                pings[futures[future]] = future.result()
        meu_ip, gateway_ip = get_network_info()
        ping_gw = ping(gateway_ip) if gateway_ip != "Desconhecido" else 0
        
        wan_online = any(v > 0 for k, v in pings.items())
        gw_online = ping_gw > 0
        if wan_online != estado_anterior["wan"]:
            log_local_event("Link WAN", f"Conexão Global {'Restabelecida' if wan_online else 'Perdida'}", "OK" if wan_online else "Crítica")
            estado_anterior["wan"] = wan_online
        if gw_online != estado_anterior["gw"]:
            log_local_event("Gateway Local", f"Comunicação com Roteador ({gateway_ip}) {'Voltou' if gw_online else 'Falhou'}", "OK" if gw_online else "Crítica")
            estado_anterior["gw"] = gw_online

        conn = sqlite3.connect('sensor_local.db')
        alvos = conn.execute("SELECT id, ip, descricao FROM alvos_locais").fetchall()
        logs_db = conn.execute("SELECT tipo, detalhes, gravidade, strftime('%H:%M:%S', data_hora) FROM logs_locais ORDER BY id DESC LIMIT 15").fetchall()
        nomes_db = {row[0]: row[1] for row in conn.execute("SELECT mac, nome FROM nomes_topologia").fetchall()}
        conn.close()
        
        resultados_alvos = [{"id": a[0], "ip": a[1], "descricao": a[2], "latencia": ping(a[1])} for a in alvos]
        logs_formatados = [{"tipo": l[0], "detalhes": l[1], "gravidade": l[2], "hora": l[3]} for l in logs_db]
        
        topologia_bruta = get_topologia_arp(meu_ip)
        for t in topologia_bruta:
            if t["mac"] in nomes_db: t["nome"] = nomes_db[t["mac"]]

        dados_sensores = {
            "cpu": cpu, "ram": ram, "meu_ip": meu_ip, "gateway_ip": gateway_ip, "ping_gateway": ping_gw, 
            "pings": pings, "custom_ips": resultados_alvos, "topologia": topologia_bruta, "logs": logs_formatados
        }

        # ========================================================
        # 🚀 ENVIO PARA A NUVEM (PACOTE COMPLETO)
        # ========================================================
        
        # 1. Envio da Telemetria Básica (CPU, RAM, Pings)
        payload = {"mac_id": mac, "nome_local": f"NOC Sensor ({os_name})", "ip_local": meu_ip, "cpu_usage": cpu, "ram_usage": ram, "temp": 40, "ping_gateway": ping_gw, "ping_global": json.dumps(pings)}
        try:
            req = urllib.request.Request(URL_CENTRAL, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=5) as response:
                print("✅ [TELEMETRIA] Dados básicos sincronizados!")
                
                # A variável comando nasce e já é checada aqui dentro com segurança
                comando = json.loads(response.read().decode('utf-8')).get("command")
                if comando == "reboot": 
                    os.system("shutdown /r /t 0" if os_name == "Windows" else "sudo reboot")
                elif comando == "run_speedtest": 
                    threading.Thread(target=executar_speedtest, args=(mac, URL_CENTRAL), daemon=True).start()
                    
        except Exception as e: 
            print(f"❌ Erro Telemetria: {e}")

        # 2. Envio da Topologia (Radar de Rede)
        try:
            url_topo = URL_CENTRAL.replace("report_data", "atualizar_dispositivos")
            lista_topo = [{"mac": t["mac"], "ip": t["ip"], "fabricante": "Desconhecido"} for t in topologia_bruta]
            req_topo = urllib.request.Request(url_topo, data=json.dumps({"mac_id": mac, "lista": lista_topo}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
            urllib.request.urlopen(req_topo, timeout=5)
            print("✅ [TOPOLOGIA] Radar de rede sincronizado!")
        except Exception as e: 
            pass # Ignora silenciosamente se falhar

        # 3. Envio dos Logs Locais (Avisos de Internet caindo, etc)
        try:
            if logs_formatados:
                url_logs = URL_CENTRAL.replace("report_data", "alertas_ia")
                alertas = [{"tipo": l["tipo"], "gravidade": l["gravidade"], "detalhes": l["detalhes"]} for l in logs_formatados]
                req_logs = urllib.request.Request(url_logs, data=json.dumps({"mac_id": mac, "alertas": alertas}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
                urllib.request.urlopen(req_logs, timeout=5)
        except Exception as e:
            pass 

        time.sleep(2)

# ==========================================
# 🖥️ MOTOR 2: PAINEL WEB LOCAL (FOREGROUND)
# ==========================================
def check_auth(username, password): return username == 'Admin' and password == 'Admin'

@app.route('/api/local_data')
def api_local_data():
    return jsonify({**dados_sensores, "mac": get_mac(), "hora": datetime.now().strftime('%H:%M:%S')})

@app.route('/api/alvos', methods=['POST'])
def add_alvo():
    data = request.json
    conn = sqlite3.connect('sensor_local.db')
    conn.execute("INSERT INTO alvos_locais (ip, descricao) VALUES (?, ?)", (data['ip'], data['descricao']))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/alvos/<int:id_alvo>', methods=['DELETE'])
def del_alvo(id_alvo):
    conn = sqlite3.connect('sensor_local.db')
    conn.execute("DELETE FROM alvos_locais WHERE id = ?", (id_alvo,))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/api/topologia/nome', methods=['POST'])
def rename_topo():
    data = request.json
    conn = sqlite3.connect('sensor_local.db')
    conn.execute("INSERT OR REPLACE INTO nomes_topologia (mac, nome) VALUES (?, ?)", (data['mac'], data['nome']))
    conn.commit()
    conn.close()
    return jsonify({"status": "OK"})

@app.route('/')
def index():
    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password):
        return Response('Acesso Negado.', 401, {'WWW-Authenticate': 'Basic realm="NOC Sensor Local"'})

    HTML_CYBERPUNK = """
    <!DOCTYPE html>
    <html lang="pt-BR"><head><meta charset="UTF-8"><title>Acesso Local - NOC Sensor</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root { --bg-base: #0b0b13; --bg-card: #151521; --bg-input: #232334; --border: #36364a; --text-main: #cdd6f4; --text-muted: #9399b2; --blue: #89b4fa; --green: #a6e3a1; --red: #f38ba8; --yellow: #f9e2af; --purple: #cba6f7; }
        body { margin: 0; font-family: 'Inter', sans-serif; background: var(--bg-base); color: var(--text-main); }
        .navbar { background: rgba(21,21,33,0.9); padding: 15px 30px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
        .container { padding: 30px; max-width: 1600px; margin: 0 auto; display: grid; grid-template-columns: repeat(3, 1fr); gap: 25px; }
        .card { background: linear-gradient(145deg, var(--bg-card) 0%, #11111b 100%); border: 1px solid var(--border); border-radius: 12px; padding: 22px; display: flex; flex-direction: column; box-shadow: 0 10px 30px rgba(0,0,0,0.5);}
        .card h3 { margin-top: 0; border-bottom: 1px solid var(--border); padding-bottom: 15px; display: flex; justify-content: space-between; color: var(--text-main);}
        .card-hw { border-top: 4px solid var(--blue); } .card-net { border-top: 4px solid var(--green); } .card-speed { border-top: 4px solid var(--purple); }
        .card-radar { border-top: 4px solid var(--blue); grid-column: 1 / -1; } .card-global { border-top: 4px solid var(--yellow); grid-column: span 2;}
        .card-custom { border-top: 4px solid var(--red); } .card-hist { border-top: 4px solid var(--text-muted); grid-column: span 2;}
        .data-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 1px dashed var(--bg-input); padding-bottom: 8px;}
        .highlight { font-family: 'JetBrains Mono', monospace; font-size: 2.2em; font-weight: bold; }
        .progress-bg { background: rgba(0,0,0,0.4); border: 1px solid var(--bg-input); border-radius: 10px; height: 12px; width: 100%; margin-top: 6px; overflow: hidden;}
        .progress-fill { background: linear-gradient(90deg, var(--blue), #b4befe); height: 100%; width: 0%; transition: 0.5s; box-shadow: 0 0 10px var(--blue); }
        .global-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-top: 10px;}
        .g-card { background: rgba(0,0,0,0.3); border: 1px solid var(--bg-input); border-radius: 8px; padding: 15px; text-align: center; }
        .ms { font-family: 'JetBrains Mono', monospace; font-size: 1.4em; font-weight: bold; margin-top: 5px; color: var(--green);}
        .pill-ok { background: rgba(166,227,161,0.1); color: var(--green); border: 1px solid var(--green); padding: 5px 12px; border-radius: 6px; font-size: 0.75em; font-weight: bold;}
        .pill-fail { background: rgba(243,139,168,0.1); color: var(--red); border: 1px solid var(--red); padding: 5px 12px; border-radius: 6px; font-size: 0.75em; font-weight: bold;}
        input { width: 100%; padding: 10px; background: rgba(0,0,0,0.3); border: 1px solid var(--border); color: var(--text-main); border-radius: 6px; box-sizing: border-box; outline: none;}
        button.action-btn { cursor: pointer; padding: 10px 15px; border: none; border-radius: 6px; font-weight: bold; color: var(--bg-base); background: var(--red);}
        .topology-box { display: flex; flex-direction: column; align-items: center; background: rgba(0,0,0,0.2); padding: 30px; border-radius: 8px; border: 1px solid var(--bg-input); overflow-x: auto;}
        .t-card { background: var(--bg-card); padding: 12px; border-radius: 8px; border: 1px solid var(--border); width: 170px; text-align: center; position: relative; border-top: 4px solid var(--blue);}
        .t-gateway { border-top: 4px solid var(--red); } .t-sensor { border-top: 4px solid var(--purple); }
        .t-ip { font-family: 'JetBrains Mono', monospace; color: var(--green); font-weight: bold; }
        .t-mac { font-size: 0.7em; color: var(--text-muted); margin: 5px 0;}
        .t-name { font-size: 0.85em; color: var(--blue); font-weight: bold; }
        .t-line-v { width: 3px; height: 25px; background: var(--border); } .t-line-h { height: 3px; background: var(--border); }
    </style></head>
    <body>
        <nav class="navbar">
            <div style="font-size: 1.3em; font-weight: bold; color: var(--blue);"><i class="fa-solid fa-tower-broadcast"></i> NOC SENSOR LOCAL</div>
            <div style="color: var(--text-muted);"><i class="fa-solid fa-microchip"></i> MAC: <strong style="color:var(--text-main)" id="mac-id">--</strong></div>
        </nav>
        <div class="container">
            <div class="card card-hw">
                <h3><span><i class="fa-solid fa-microchip"></i> Telemetria Local</span> <span class="pill-ok">ONLINE</span></h3>
                <div style="margin-bottom: 15px; margin-top: 10px;">
                    <div style="display: flex; justify-content: space-between;"><span><i class="fa-solid fa-microchip"></i> CPU</span> <strong id="cpu-text">0%</strong></div>
                    <div class="progress-bg"><div id="cpu-fill" class="progress-fill"></div></div>
                </div>
                <div>
                    <div style="display: flex; justify-content: space-between;"><span><i class="fa-solid fa-memory"></i> RAM</span> <strong id="ram-text">0%</strong></div>
                    <div class="progress-bg"><div id="ram-fill" class="progress-fill"></div></div>
                </div>
            </div>

            <div class="card card-net">
                <h3><span><i class="fa-solid fa-shield-heart"></i> Integridade da Rede</span></h3>
                <div class="data-row"><span><i class="fa-solid fa-network-wired" style="color:var(--green)"></i> Gateway (<span id="gw-ip">--</span>):</span> <span id="status-local" class="pill-ok">ESTÁVEL</span></div>
                <div class="data-row"><span><i class="fa-solid fa-globe" style="color:var(--blue)"></i> Internet (WAN):</span> <span id="status-wan" class="pill-ok">ONLINE</span></div>
                <div style="margin-top: auto; background: rgba(0,0,0,0.3); padding: 18px; border-radius: 8px; text-align: center; border: 1px solid var(--bg-input);">
                    <div style="font-size: 0.75em; color: var(--text-muted);">Latência Sensor ➔ Gateway</div>
                    <div id="ping-local" class="highlight" style="color: var(--green);">0 ms</div>
                </div>
            </div>

            <div class="card card-speed" style="display: flex; align-items: center; justify-content: center; border-top: 4px solid var(--border); opacity: 0.7;">
                <i class="fa-solid fa-cloud-arrow-up" style="font-size: 3em; color: var(--yellow); margin-bottom: 15px;"></i>
                <div style="font-weight: bold; color: var(--yellow); font-size: 1.1em; text-align: center;">Orquestrado<br>pela Central NOC</div>
            </div>

            <div class="card card-radar">
                <h3><span><i class="fa-solid fa-project-diagram"></i> Topologia de Rede (Scanner ARP Local)</span></h3>
                <div class="topology-box">
                    <div style="text-align: center;"><i class="fa-solid fa-cloud" style="font-size: 3.2em; color: var(--blue);"></i><div style="font-size: 0.8em; font-weight: bold; margin-top: 8px; color: var(--blue);">INTERNET / WAN</div></div>
                    <div class="t-line-v"></div>
                    <div style="display: flex; align-items: center; justify-content: center;">
                        <div id="diag-gateway"></div>
                        <div class="t-line-h" style="width: 60px;"></div>
                        <div class="t-card t-sensor"><div class="t-ip" id="meu-ip">ESTE PC</div><div class="t-mac" id="topo-mac"></div><div class="t-name">SENSOR NOC</div></div>
                    </div>
                    <div class="t-line-v"></div>
                    <div class="t-line-h" style="width: 70%; max-width: 800px;"></div>
                    <div id="diag-outros" style="display: flex; gap: 15px; flex-wrap: wrap; justify-content: center; margin-top: 12px;"></div>
                </div>
            </div>

            <div class="card card-global">
                <h3><span><i class="fa-solid fa-earth-americas"></i> Disponibilidade (Live Local)</span></h3>
                <div class="global-grid">
                    <div class="g-card"><i class="fa-brands fa-google"></i> Google <div id="pg-google" class="ms">--</div></div>
                    <div class="g-card"><i class="fa-solid fa-cloud"></i> Cloudflare <div id="pg-cf" class="ms">--</div></div>
                    <div class="g-card"><i class="fa-brands fa-aws"></i> AWS <div id="pg-aws" class="ms">--</div></div>
                    <div class="g-card"><i class="fa-solid fa-shield-halved"></i> Quad9 <div id="pg-quad9" class="ms">--</div></div>
                </div>
                <div style="height: 220px; width: 100%; margin-top: 15px; background: rgba(0,0,0,0.2); border-radius: 8px; border: 1px solid var(--bg-input); padding: 10px; box-sizing: border-box;"><canvas id="chartPing"></canvas></div>
            </div>

            <div class="card card-custom">
                <h3><span><i class="fa-solid fa-crosshairs"></i> Radar de Alvos Locais</span></h3>
                <div style="display: flex; gap: 5px; margin-bottom: 12px;"><input type="text" id="new-ip" placeholder="IP do Servidor/Câmera"><input type="text" id="new-desc" placeholder="Nome do Alvo"><button class="action-btn" onclick="addIP()"><i class="fa-solid fa-plus"></i></button></div>
                <div id="lista-custom-ips" style="max-height: 180px; overflow-y: auto;"></div>
            </div>
            
            <div class="card card-hist">
                <h3><span><i class="fa-solid fa-clock-rotate-left"></i> Histórico de Eventos (Memória do Sensor)</span></h3>
                <div id="lista-historico" style="background: rgba(0,0,0,0.2); padding: 12px; border-radius: 8px; max-height: 250px; overflow-y: auto; border: 1px solid var(--bg-input);"></div>
            </div>
        </div>

        <script>
            let chartPingInstance = null; let historicoHoras = [], dGoogle = [], dCf = [], dAws = [], dQuad = [];
            async function addIP() { const ip = document.getElementById('new-ip').value; const desc = document.getElementById('new-desc').value; if(!ip) return; await fetch('/api/alvos', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ip, descricao:desc}) }); document.getElementById('new-ip').value=''; document.getElementById('new-desc').value=''; }
            async function excluirIP(id) { if(confirm("Remover alvo local?")) { await fetch('/api/alvos/' + id, { method: 'DELETE' }); } }
            async function renomearTopo(mac, atual) { let n = prompt("Novo nome para este dispositivo na topologia:", atual); if(n) { await fetch('/api/topologia/nome', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mac, nome:n}) }); } }
            async function atualizarLocal() {
                try {
                    const res = await fetch('/api/local_data'); const data = await res.json();
                    document.getElementById('mac-id').innerText = data.mac; document.getElementById('topo-mac').innerText = data.mac;
                    document.getElementById('meu-ip').innerText = data.meu_ip;
                    document.getElementById('cpu-text').innerText = data.cpu + '%'; document.getElementById('cpu-fill').style.width = data.cpu + '%';
                    document.getElementById('ram-text').innerText = data.ram + '%'; document.getElementById('ram-fill').style.width = data.ram + '%';
                    document.getElementById('gw-ip').innerText = data.gateway_ip;
                    const pl = data.ping_gateway;
                    document.getElementById('ping-local').innerText = pl + ' ms';
                    if(pl === 0 || pl > 100) { document.getElementById('status-local').className = "pill-fail"; document.getElementById('status-local').innerText = "FALHA"; }
                    else { document.getElementById('status-local').className = "pill-ok"; document.getElementById('status-local').innerText = "ESTÁVEL"; }
                    if(data.pings.Google === 0 && data.pings.Cloudflare === 0) { document.getElementById('status-wan').className = "pill-fail"; document.getElementById('status-wan').innerText = "OFFLINE"; }
                    else { document.getElementById('status-wan').className = "pill-ok"; document.getElementById('status-wan').innerText = "ONLINE"; }
                    document.getElementById('pg-google').innerText = data.pings.Google + ' ms'; document.getElementById('pg-cf').innerText = data.pings.Cloudflare + ' ms';
                    document.getElementById('pg-aws').innerText = data.pings.AWS + ' ms'; document.getElementById('pg-quad9').innerText = data.pings.Quad9 + ' ms';

                    let htmlAlvos = '';
                    data.custom_ips.forEach(item => {
                        let cor = item.latencia === 0 ? 'var(--red)' : 'var(--green)'; let latText = item.latencia === 0 ? 'FALHA' : item.latencia + ' ms';
                        htmlAlvos += `<div style="background:var(--bg-input); padding:8px; border-radius:8px; margin-bottom:5px; display:flex; justify-content:space-between; border-left:4px solid ${cor}; font-size:0.9em;">
                            <div><b style="color:var(--blue);">${item.descricao}</b><br><small style="color:var(--text-muted);">${item.ip}</small></div>
                            <div style="display:flex; align-items:center; gap:10px;"><b style="color:${cor};">${latText}</b><button onclick="excluirIP(${item.id})" style="background:none; border:none; color:var(--red); cursor:pointer;"><i class="fa-solid fa-trash"></i></button></div></div>`;
                    });
                    document.getElementById('lista-custom-ips').innerHTML = htmlAlvos || '<div style="text-align:center; padding:15px; color:var(--text-muted);">Nenhum alvo.</div>';

                    let gHtml = `<div class="t-card t-gateway"><div class="t-ip">${data.gateway_ip}</div><div class="t-mac">ROTEADOR</div><div class="t-name">GATEWAY PADRÃO</div></div>`;
                    let oHtml = '';
                    data.topologia.forEach(t => {
                        if(t.ip === data.gateway_ip || t.ip === data.meu_ip) return;
                        oHtml += `<div class="t-card"><div class="t-ip">${t.ip}</div><div class="t-mac">${t.mac}</div><div class="t-name">${t.nome} <button onclick="renomearTopo('${t.mac}','${t.nome}')" style="background:none; border:none; color:var(--yellow); cursor:pointer;"><i class="fa-solid fa-pen-to-square"></i></button></div></div>`;
                    });
                    document.getElementById('diag-gateway').innerHTML = gHtml; document.getElementById('diag-outros').innerHTML = oHtml;

                    let htmlLogs = '';
                    data.logs.forEach(l => {
                        let cor = l.gravidade === 'Crítica' ? 'var(--red)' : (l.gravidade === 'OK' ? 'var(--green)' : 'var(--yellow)');
                        htmlLogs += `<div style="padding: 8px; border-bottom: 1px dashed var(--border); font-size:0.85em;"><span style="color:var(--text-muted)">[${l.hora}]</span> <b style="color:${cor}">${l.tipo}</b>: ${l.detalhes}</div>`;
                    });
                    document.getElementById('lista-historico').innerHTML = htmlLogs || '<div style="text-align:center; padding:10px; color:var(--text-muted);">Sem logs detectados.</div>';

                    if(historicoHoras.length > 20) { historicoHoras.shift(); dGoogle.shift(); dCf.shift(); dAws.shift(); dQuad.shift(); }
                    historicoHoras.push(data.hora); dGoogle.push(data.pings.Google); dCf.push(data.pings.Cloudflare); dAws.push(data.pings.AWS); dQuad.push(data.pings.Quad9);
                    if (chartPingInstance) { chartPingInstance.update('none'); } 
                    else {
                        chartPingInstance = new Chart(document.getElementById('chartPing').getContext('2d'), { type: 'line', data: { labels: historicoHoras, datasets: [ { label: 'Google', data: dGoogle, borderColor: '#a6e3a1', tension: 0.3 }, { label: 'Cloudflare', data: dCf, borderColor: '#89b4fa', tension: 0.3 }, { label: 'AWS', data: dAws, borderColor: '#f9e2af', tension: 0.3 }, { label: 'Quad9', data: dQuad, borderColor: '#cba6f7', tension: 0.3 }]}, options: { responsive: true, maintainAspectRatio: false, animation: false, scales: { x: { display: false } } } });
                    }
                } catch(e) {}
            }
            setInterval(atualizarLocal, 2000); atualizarLocal();
        </script>
    </body></html>
    """
    return render_template_string(HTML_CYBERPUNK)

# ==========================================
# 🛠️ MOTOR 3: SYSTEM TRAY E INICIALIZAÇÃO
# ==========================================

# Cria a imagem do ícone (Um escudo/círculo azul simples)
def create_image():
    image = Image.new('RGB', (64, 64), color=(11, 11, 19))
    dc = ImageDraw.Draw(image)
    dc.ellipse((8, 8, 56, 56), fill=(137, 180, 250))
    return image

# Função que o usuário chama ao clicar em "Sair"
def on_quit(icon, item):
    icon.stop()
    os._exit(0) # Mata todas as threads e fecha o programa

def run_tray():
    image = create_image()
    menu = pystray.Menu(pystray.MenuItem('Encerrar Sensor NOC', on_quit))
    icon = pystray.Icon("NOC Sensor", image, "NOC Sensor (Ativo)", menu)
    icon.run()

if __name__ == "__main__":
    init_local_db() 
    
    # 1. Inicia a telemetria em segundo plano
    threading.Thread(target=loop_telemetria, daemon=True).start()
    
    # 2. Inicia o painel local em segundo plano (use_reloader=False é vital para .exe)
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=PORTA_LOCAL, debug=False, use_reloader=False), daemon=True).start()
    
    # 3. Inicia o Ícone na Bandeja do Windows (Trava a thread principal mantendo o programa vivo)
    run_tray()