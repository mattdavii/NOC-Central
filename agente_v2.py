import sys
import os
import threading
import subprocess
import re 
import struct

# 🛡️ TRUQUE ANTI-CRASH DO PYINSTALLER (--noconsole)
if sys.stdout is None: sys.stdout = open(os.devnull, "w")
if sys.stderr is None: sys.stderr = open(os.devnull, "w")
if sys.stdin is None:  sys.stdin = open(os.devnull, "r")

# 📦 IMPORTS LIMPOS E ORGANIZADOS
import time, json, platform, uuid, sqlite3, socket, urllib.request, concurrent.futures
from datetime import datetime
from flask import Flask, request, Response, render_template_string, jsonify
import pystray
from PIL import Image, ImageDraw
import speedtest 

# 🛡️ BALA DE PRATA: Corrige o bug de SSL do PyInstaller com HTTPS
import ssl
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError: pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

try: import psutil
except ImportError: psutil = None

# ==========================================
# ⚙️ CONFIGURAÇÃO DO AGENTE
# ==========================================
URL_CENTRAL = "https://noc-central.onrender.com/api/v2/report_data" 
PORTA_LOCAL = 10000
TOKEN_SCADA = "admin123"

IS_WIN = platform.system().lower() == 'windows'
C_FLAGS = subprocess.CREATE_NO_WINDOW if IS_WIN else 0

app = Flask(__name__)

dados_sensores = { "cpu": 0, "ram": 0, "disco": 0, "temp": 0.0, "gpu_temp": 0.0, "net_down": 0, "net_up": 0, "portas": "", "meu_ip": "Detectando...", "gateway_ip": "Detectando...", "ping_gateway": 0, "pings": {"Google":0, "Cloudflare":0, "AWS":0, "Quad9":0}, "topologia": [], "logs": [], "custom_ips": [], "energia_ips": [] }

cache_alvos = {}
MAC_GATEWAY_CONHECIDO = None 
ALARMES_DISPARADOS = {"temp_cpu": False, "temp_gpu": False}

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
        if IS_WIN:
            saida = subprocess.check_output("route print 0.0.0.0", shell=True, universal_newlines=True, creationflags=C_FLAGS, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for linha in saida.split('\n'):
                partes = linha.split()
                if len(partes) >= 3 and partes[0] == '0.0.0.0':
                    gateway = partes[2]
                    break
        else:
            saida = subprocess.check_output("ip route | grep default", shell=True, universal_newlines=True, stdin=subprocess.DEVNULL)
            gateway = saida.split()[2]
    except: pass
    return meu_ip, gateway

def ping_silencioso(ip):
    param = '-n' if IS_WIN else '-c'
    try: subprocess.call(['ping', param, '1', '-w', '500', ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=C_FLAGS)
    except: pass

def varredura_profunda_arp(ip_gateway):
    try:
        base_ip = ".".join(ip_gateway.split('.')[:-1])
        threads = []
        for i in range(1, 255):
            ip_alvo = f"{base_ip}.{i}"
            t = threading.Thread(target=ping_silencioso, args=(ip_alvo,))
            threads.append(t)
            t.start()
        for t in threads: t.join()
    except: pass

def get_topologia_arp(meu_ip, gateway_ip, forcar_varredura=False):
    if forcar_varredura and gateway_ip != "Desconhecido":
        varredura_profunda_arp(gateway_ip) 

    dispositivos = []
    prefixo_rede = '.'.join(meu_ip.split('.')[:-1]) + '.'
    try:
        saida = subprocess.check_output("arp -a", shell=True, universal_newlines=True, creationflags=C_FLAGS, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for linha in saida.split('\n'):
            partes = linha.split()
            if len(partes) >= 2 and '.' in partes[0] and ('-' in partes[1] or ':' in partes[1]):
                ip = partes[0]
                mac = partes[1].replace('-', ':').upper()
                if ip.startswith(prefixo_rede) and not ip.endswith(".255"):
                    dispositivos.append({"ip": ip, "mac": mac, "nome": "Desconhecido", "fabricante": "Desconhecido"})
    except: pass
    return dispositivos

def ping(host):
    param = '-n' if IS_WIN else '-c'
    comando = ['ping', param, '1', host]
    try:
        saida = subprocess.check_output(comando, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=C_FLAGS).decode('cp850' if IS_WIN else 'utf-8', errors='ignore')
        if 'unreachable' in saida.lower() or 'inacessível' in saida.lower(): return 0
        if '<1ms' in saida: return 1
        match = re.search(r'(?:time|tempo)[=<](\d+)', saida.lower())
        if match: return int(match.group(1))
        if 'ttl=' in saida.lower(): return 1
        return 0
    except: return 0

# ==========================================
# 🌡️ MOTOR TÉRMICO HÍBRIDO (OHM + Nativo)
# ==========================================
def ler_temperaturas():
    cpu_t = 0.0
    gpu_t = 0.0
    
    # 1. TENTATIVA HARDCORE: OpenHardwareMonitor (Se o cliente instalou)
    if IS_WIN:
        try:
            cmd_ohm = 'powershell -Command "(Get-WmiObject -Namespace root\\OpenHardwareMonitor -Class Sensor -ErrorAction Stop | Where-Object { $_.SensorType -eq \'Temperature\' -and ($_.Name -match \'CPU Package\' -or $_.Name -match \'CPU Core\') } | Measure-Object -Property Value -Average).Average"'
            out = subprocess.check_output(cmd_ohm, shell=True, universal_newlines=True, creationflags=C_FLAGS, stderr=subprocess.DEVNULL).strip()
            if out and out != "0":
                cpu_t = round(float(out.replace(',', '.')), 1)
        except: pass

    # 2. TENTATIVA NATIVA PYTHON (Linux/Mac/Alguns Windows)
    if cpu_t == 0.0 and hasattr(psutil, "sensors_temperatures"):
        try:
            st = psutil.sensors_temperatures()
            for name, entries in st.items():
                if "coretemp" in name.lower() or "cpu" in name.lower(): cpu_t = round(entries[0].current, 1)
        except: pass

    # 3. TENTATIVA WMI DO WINDOWS (Placas-mãe amigáveis)
    if IS_WIN and cpu_t == 0.0:
        try:
            cmd = 'powershell -Command "Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi -ErrorAction Stop | Select -ExpandProperty CurrentTemperature"'
            out = subprocess.check_output(cmd, shell=True, universal_newlines=True, creationflags=C_FLAGS, stderr=subprocess.DEVNULL).strip()
            if out:
                kelvin_raw = float(out.split('\n')[0])
                celsius = (kelvin_raw / 10.0) - 273.15
                if 20 < celsius < 120: cpu_t = round(celsius, 1)
        except: pass

    # LEITURA DA GPU (Nvidia)
    try:
        out = subprocess.check_output('nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader', shell=True, universal_newlines=True, creationflags=C_FLAGS, stderr=subprocess.DEVNULL).strip()
        if out: gpu_t = float(out.split('\n')[0])
    except: pass

    return cpu_t, gpu_t

# ==========================================
# 🧠 BANCO DE DADOS LOCAL
# ==========================================
def init_local_db():
    conn = sqlite3.connect('sensor_local.db')
    conn.execute('''CREATE TABLE IF NOT EXISTS alvos_locais (id INTEGER PRIMARY KEY, ip TEXT, descricao TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS logs_locais (id INTEGER PRIMARY KEY, tipo TEXT, detalhes TEXT, gravidade TEXT, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS nomes_topologia (mac TEXT PRIMARY KEY, nome TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS alvos_energia (id INTEGER PRIMARY KEY, ip TEXT, descricao TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS servicos_os (id INTEGER PRIMARY KEY, nome_servico TEXT, descricao TEXT)''')
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
    d, u = 0.0, 0.0
    erro_principal = ""
    try:
        st = speedtest.Speedtest(secure=False)
        st.get_best_server()
        d = st.download(threads=8) / 1_000_000
        u = st.upload(threads=8) / 1_000_000
    except Exception as e:
        erro_principal = str(e)
        try:
            url_dl = "http://speedtest.tele2.net/10MB.zip"
            inicio = time.time()
            urllib.request.urlopen(url_dl, timeout=20).read()
            tempo_dl = time.time() - inicio
            d = 80.0 / tempo_dl 
            u = d * 0.5 
        except Exception as e2:
            try:
                url_log = url_central.replace('report_data', 'alertas_ia')
                alerta = [{"tipo": "Falha de Speedtest", "gravidade": "Aviso", "detalhes": f"Ookla: {erro_principal} | Tele2: {str(e2)}"}]
                req_log = urllib.request.Request(url_log, data=json.dumps({"mac_id": mac, "alertas": alerta}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
                urllib.request.urlopen(req_log, timeout=5)
            except: pass
            return

    try:
        payload = {"mac_id": mac, "down": round(d, 2), "up": round(u, 2)}
        url_speed = url_central.replace('report_data', 'reportar_velocidade')
        req = urllib.request.Request(url_speed, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=10)
    except: pass

def executar_traceroute(mac, url_central):
    try:
        cmd = ['tracert', '-d', '-h', '15', '8.8.8.8'] if IS_WIN else ['traceroute', '-m', '15', '-n', '8.8.8.8']
        resultado = subprocess.check_output(cmd, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=40, creationflags=C_FLAGS).decode('cp850' if IS_WIN else 'utf-8', errors='ignore')
        payload = {"mac_id": mac, "rota": resultado}
        url_trace = url_central.replace('report_data', 'reportar_rota')
        req = urllib.request.Request(url_trace, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=10)
    except: pass

def executar_scan_loop(mac, url_central, gateway_ip):
    try:
        req = urllib.request.Request(url_central.replace('report_data', 'alertas_ia'), data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "🔍 Scan de Loop Iniciado", "gravidade": "Aviso", "detalhes": "Injetando pacotes de estresse na rede local para medir a taxa de reflexão do Switch..."}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=3)
        
        if not psutil or gateway_ip == "Desconhecido": return

        # 1. Mede o "Silêncio" (Tráfego Base)
        net_start = psutil.net_io_counters()
        time.sleep(2)
        net_mid = psutil.net_io_counters()
        bytes_base = net_mid.bytes_recv - net_start.bytes_recv
        
        # 2. Injeta o Estresse (Stress Test Local)
        for _ in range(30):
            threading.Thread(target=ping_silencioso, args=(gateway_ip,), daemon=True).start()
        
        time.sleep(3) # Aguarda o switch surtar (se houver loop)
        
        # 3. Mede a Reflexão
        net_end = psutil.net_io_counters()
        bytes_stress = net_end.bytes_recv - net_mid.bytes_recv
        
        # 4. Avaliação (Se o tráfego estresse for 5x maior que a base E muito alto)
        if bytes_stress > (bytes_base * 5) and bytes_stress > 2_000_000: # 2MB de reflexão de pacotes minúsculos = Tempestade
            msg = f"⚠️ ATENÇÃO: LOOP L2 CONFIRMADO! O Switch refletiu um volume absurdo de lixo durante o teste ({round(bytes_stress/1_000_000, 2)} MB de puro broadcast recebidos em 3s). Isole os cabos do Switch imediatamente!"
            grav = "Crítica"
        else:
            msg = f"✅ Rede Limpa. Nenhum Loop de Reflexão ou Tempestade de Broadcast foi detectada durante o teste de estresse de carga."
            grav = "OK"
            
        req2 = urllib.request.Request(url_central.replace('report_data', 'alertas_ia'), data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "Resultado: Scan de Loop L2", "gravidade": grav, "detalhes": msg}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req2, timeout=5)
    except: pass

def acordar_pc(macaddress):
    """Envia o Magic Packet para acordar PCs na rede (Wake-on-LAN)"""
    try:
        if len(macaddress) == 17: sep = macaddress[2]
        else: sep = ''
        macaddress = macaddress.replace(sep, '')
        data = b'FFFFFFFFFFFF' + (macaddress * 16).encode()
        send_data = b''
        for i in range(0, len(data), 2):
            send_data = send_data + struct.pack('B', int(data[i: i + 2], 16))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(send_data, ('255.255.255.255', 9))
        sock.close()
    except: pass

def executar_scan_loop(mac, url_central, gateway_ip):
    """ Busca Ativa por Loops L2 (Tempestade de Broadcast) """
    try:
        req = urllib.request.Request(url_central.replace('report_data', 'alertas_ia'), data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "🔍 Scan de Loop Iniciado", "gravidade": "Aviso", "detalhes": "Injetando pacotes de estresse na rede local para medir a taxa de reflexão do Switch..."}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=3)
        if not psutil or gateway_ip == "Desconhecido": return
        net_start = psutil.net_io_counters(); time.sleep(2); net_mid = psutil.net_io_counters()
        bytes_base = net_mid.bytes_recv - net_start.bytes_recv
        for _ in range(30): threading.Thread(target=ping_silencioso, args=(gateway_ip,), daemon=True).start()
        time.sleep(3)
        net_end = psutil.net_io_counters()
        bytes_stress = net_end.bytes_recv - net_mid.bytes_recv
        if bytes_stress > (bytes_base * 5) and bytes_stress > 2_000_000:
            msg = f"⚠️ ATENÇÃO: LOOP L2 CONFIRMADO! O Switch refletiu um volume absurdo de tráfego ({round(bytes_stress/1_000_000, 2)} MB). Isole os cabos!"
            grav = "Crítica"
        else:
            msg = f"✅ Rede Limpa. Nenhum Loop de Reflexão ou Tempestade de Broadcast detectada."
            grav = "OK"
        urllib.request.urlopen(urllib.request.Request(url_central.replace('report_data', 'alertas_ia'), data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "Resultado: Scan de Loop", "gravidade": grav, "detalhes": msg}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5)
    except: pass

# ==========================================
# 📡 MOTOR 1: TELEMETRIA E AUTO-CURA WAN
# ==========================================
def loop_telemetria():
    global dados_sensores, MAC_GATEWAY_CONHECIDO, ALARMES_DISPARADOS
    mac = get_mac()
    os_name = platform.system()
    ultima_varredura = 0 
    ultima_medicao_speedtest = 0
    contador_falhas_wan = 0
    ultimo_reparo_wan = 0
    
    if psutil:
        last_net = psutil.net_io_counters()
        last_net_time = time.time()
    
    while True:
        try:
            agora = time.time()
            if agora - ultima_medicao_speedtest > 900:
                threading.Thread(target=executar_speedtest, args=(mac, URL_CENTRAL), daemon=True).start()
                ultima_medicao_speedtest = agora

            cpu = psutil.cpu_percent(interval=None) if psutil else 0.0
            ram = psutil.virtual_memory().percent if psutil else 0.0
            disco = psutil.disk_usage('/').percent if psutil else 0.0 
            cpu_temp, gpu_temp = ler_temperaturas()

            # 🚨 GATILHO DE PROTEÇÃO TÉRMICA
            alertas_rede = []
            if cpu_temp >= 85.0 and not ALARMES_DISPARADOS.get("temp_cpu", False):
                alertas_rede.append({"tipo": "🔥 Superaquecimento CPU", "gravidade": "Crítica", "detalhes": f"A temperatura atingiu {cpu_temp}°C."})
                ALARMES_DISPARADOS["temp_cpu"] = True
            elif cpu_temp < 75.0 and ALARMES_DISPARADOS.get("temp_cpu", False): ALARMES_DISPARADOS["temp_cpu"] = False

            if gpu_temp >= 85.0 and not ALARMES_DISPARADOS.get("temp_gpu", False):
                alertas_rede.append({"tipo": "🔥 Superaquecimento GPU", "gravidade": "Crítica", "detalhes": f"A placa de vídeo atingiu {gpu_temp}°C."})
                ALARMES_DISPARADOS["temp_gpu"] = True
            elif gpu_temp < 75.0 and ALARMES_DISPARADOS.get("temp_gpu", False): ALARMES_DISPARADOS["temp_gpu"] = False

            net_up = 0.0; net_down = 0.0
            if psutil:
                current_net = psutil.net_io_counters()
                time_diff = agora - last_net_time if (agora - last_net_time) > 0 else 1
                net_up = round(((current_net.bytes_sent - last_net.bytes_sent) * 8 / 1_000_000) / time_diff, 2)
                net_down = round(((current_net.bytes_recv - last_net.bytes_recv) * 8 / 1_000_000) / time_diff, 2)
                last_net = current_net; last_net_time = agora

            portas_alvo = {80: "HTTP", 443: "HTTPS", 3306: "MySQL", 5432: "Postgres", 3389: "RDP"}
            portas_abertas = []
            for porta, servico in portas_alvo.items():
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.1)
                    if sock.connect_ex(('127.0.0.1', porta)) == 0: portas_abertas.append(f"{porta} ({servico})")
                    sock.close()
                except: pass
            str_portas = ", ".join(portas_abertas) if portas_abertas else "Nenhuma (Seguro)"

            pings = {}
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = {executor.submit(ping, ip): name for name, ip in {"Google":"8.8.8.8", "Cloudflare":"1.1.1.1", "AWS":"aws.amazon.com", "Quad9":"9.9.9.9"}.items()}
                for future in concurrent.futures.as_completed(futures): pings[futures[future]] = future.result()

            # ⚙️ MÓDULO DE AUTO-CURA (SELF-HEALING WAN)
            if pings["Google"] == 0 and pings["Cloudflare"] == 0: contador_falhas_wan += 1
            else: contador_falhas_wan = 0
                
            if contador_falhas_wan == 3 and (agora - ultimo_reparo_wan) > 300:
                try:
                    if IS_WIN:
                        subprocess.call("ipconfig /flushdns", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=C_FLAGS)
                        subprocess.call("ipconfig /renew", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=C_FLAGS)
                    else:
                        subprocess.call("sudo systemd-resolve --flush-caches", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    log_local_event("Auto-Cura", "Queda de DNS/WAN detectada. O Agente executou Flush DNS e Renew IP.", "Aviso")
                    try:
                        url_log = URL_CENTRAL.replace('report_data', 'alertas_ia')
                        urllib.request.urlopen(urllib.request.Request(url_log, data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "⚙️ Sistema de Auto-Cura", "gravidade": "Aviso", "detalhes": "Agente executou script de Flush DNS localmente."}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=3)
                    except: pass
                except: pass
                ultimo_reparo_wan = agora

            meu_ip, gateway_ip = get_network_info()
            ping_gw = ping(gateway_ip) if gateway_ip != "Desconhecido" else 0
            
            forcar_varredura = (agora - ultima_varredura > 300)
            dispositivos = get_topologia_arp(meu_ip, gateway_ip, forcar_varredura=forcar_varredura)
            if forcar_varredura: ultima_varredura = agora

            # 🌪️ MÓDULO STORM WATCH
            gw_mac_atual = None
            for d in dispositivos:
                if d['ip'] == gateway_ip: gw_mac_atual = d['mac']; break

            if MAC_GATEWAY_CONHECIDO and gw_mac_atual and MAC_GATEWAY_CONHECIDO != gw_mac_atual:
                alertas_rede.append({"tipo": "🌪️ MAC Flapping", "gravidade": "Crítica", "detalhes": f"O MAC do Gateway mudou de {MAC_GATEWAY_CONHECIDO} para {gw_mac_atual}."})
            if gw_mac_atual: MAC_GATEWAY_CONHECIDO = gw_mac_atual

            if (ping_gw == 0 or ping_gw > 500) and net_down > 15.0 and net_up < 2.0:
                alertas_rede.append({"tipo": "🌪️ Tempestade de Broadcast", "gravidade": "Crítica", "detalhes": f"Inundação L2 detectada ({net_down} Mbps de lixo)."})

            if alertas_rede:
                try: urllib.request.urlopen(urllib.request.Request(URL_CENTRAL.replace('report_data', 'alertas_ia'), data=json.dumps({"mac_id": mac, "alertas": alertas_rede}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=3)
                except: pass

            dados_sensores["cpu"] = cpu; dados_sensores["ram"] = ram; dados_sensores["disco"] = disco; dados_sensores["temp"] = cpu_temp; dados_sensores["gpu_temp"] = gpu_temp; dados_sensores["net_down"] = net_down; dados_sensores["net_up"] = net_up; dados_sensores["portas"] = str_portas; dados_sensores["meu_ip"] = meu_ip; dados_sensores["gateway_ip"] = gateway_ip; dados_sensores["ping_gateway"] = ping_gw; dados_sensores["pings"] = pings; dados_sensores["topologia"] = dispositivos

            payload = { "mac_id": mac, "nome_local": f"NOC Sensor ({os_name})", "ip_local": meu_ip, "ip_gateway": gateway_ip, "cpu_usage": cpu, "ram_usage": ram, "disco": disco, "temp": cpu_temp, "gpu_temp": gpu_temp, "ping_gateway": ping_gw, "ping_global": json.dumps(pings), "net_up": net_up, "net_down": net_down, "portas": str_portas }
            
            espera_remota = 3
            try:
                req = urllib.request.Request(URL_CENTRAL, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
                with urllib.request.urlopen(req, timeout=5) as response:
                    res_data = json.loads(response.read().decode('utf-8'))
                    comando = res_data.get("command")
                    espera_remota = res_data.get("intervalo", 3) 

                    if comando == "reboot": subprocess.call("shutdown /r /t 0" if IS_WIN else "sudo reboot", shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=C_FLAGS)
                    elif comando == "run_speedtest": threading.Thread(target=executar_speedtest, args=(mac, URL_CENTRAL), daemon=True).start()
                    elif comando == "run_traceroute": threading.Thread(target=executar_traceroute, args=(mac, URL_CENTRAL), daemon=True).start()
                    elif comando == "flush_dns": subprocess.call("ipconfig /flushdns" if IS_WIN else "sudo systemd-resolve --flush-caches", shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=C_FLAGS)
                    elif comando == "scan_loop": 
                        threading.Thread(target=executar_scan_loop, args=(mac, URL_CENTRAL, gateway_ip), daemon=True).start()
                    elif comando and comando.startswith("wol:"): 
                        mac_pc_desligado = comando.split(":")[1]
                        acordar_pc(mac_pc_desligado)
                    elif comando == "top_processos":
                    
                        if psutil: 
                            try:
                                for p in psutil.process_iter(['cpu_percent']): pass
                                num_cores = psutil.cpu_count() or 1
                                procs = sorted(psutil.process_iter(['name', 'cpu_percent']), key=lambda p: p.info.get('cpu_percent') or 0, reverse=True)[:5]
                                lista_procs = " | ".join([f"{p.info['name']} ({round((p.info.get('cpu_percent') or 0) / num_cores, 1)}%)" for p in procs])
                                urllib.request.urlopen(urllib.request.Request(URL_CENTRAL.replace('report_data', 'alertas_ia'), data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "Diagnóstico", "gravidade": "Aviso", "detalhes": lista_procs}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5)
                            except: pass
                
                if forcar_varredura:
                    try: urllib.request.urlopen(urllib.request.Request(URL_CENTRAL.replace('report_data', 'atualizar_dispositivos'), data=json.dumps({"mac_id": mac, "lista": dispositivos}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5)
                    except: pass
            except: espera_remota = 5
            time.sleep(espera_remota)
        except: time.sleep(5)

# ==========================================
# ⚡ MOTOR 4: WATCHDOG COMPLETO (Rede + Energia + SO)
# ==========================================
def loop_watchdog_local():
    global cache_alvos
    mac = get_mac()
    url_get = URL_CENTRAL.replace('report_data', f'ips_customizados/{mac}')
    url_report = URL_CENTRAL.replace('report_data', 'reportar_latencia_custom')
    url_get_energia = URL_CENTRAL.replace('report_data', f'ips_energia/{mac}')
    url_report_energia = URL_CENTRAL.replace('report_data', 'reportar_latencia_energia')
    url_get_srv = URL_CENTRAL.replace('report_data', f'servicos_os/{mac}')
    url_report_srv = URL_CENTRAL.replace('report_data', 'reportar_status_servico')
    url_log = URL_CENTRAL.replace('report_data', 'alertas_ia')

    while True:
        try:
            # ----- 1. WATCHDOG REDE E ENERGIA (NUVEM) -----
            try:
                req = urllib.request.Request(url_get, method='GET')
                with urllib.request.urlopen(req, timeout=5) as response: alvos_nuvem = json.loads(response.read().decode('utf-8'))
            except: alvos_nuvem = []

            try:
                req_e = urllib.request.Request(url_get_energia, method='GET')
                with urllib.request.urlopen(req_e, timeout=5) as response: alvos_energia = json.loads(response.read().decode('utf-8'))
            except: alvos_energia = []

            conn = sqlite3.connect('sensor_local.db')
            alvos_locais = conn.execute("SELECT id, ip, descricao FROM alvos_locais").fetchall()
            alvos_energia_locais = conn.execute("SELECT id, ip, descricao FROM alvos_energia").fetchall()
            conn.close()

            for alvo in alvos_nuvem:
                ip = alvo['ip']; desc = alvo['descricao']; latencia = ping(ip); ta_online = latencia > 0
                try: urllib.request.urlopen(urllib.request.Request(url_report, data=json.dumps({"id": alvo['id'], "latencia": latencia}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5)
                except: pass
                
                estado_anterior = cache_alvos.get(ip, {}).get('online', True)
                if ta_online and not estado_anterior:
                    try: urllib.request.urlopen(urllib.request.Request(url_log, data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "Alvo Restaurado", "gravidade": "OK", "detalhes": f"{desc} ({ip}) voltou."}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5)
                    except: pass
                elif not ta_online and estado_anterior:
                    try: urllib.request.urlopen(urllib.request.Request(url_log, data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "Queda de Alvo", "gravidade": "Crítica", "detalhes": f"{desc} ({ip}) parou!"}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5)
                    except: pass
                cache_alvos[ip] = {'online': ta_online, 'latencia': latencia}

            for alvo in alvos_energia:
                ip = alvo['ip']; desc = alvo['descricao']; latencia = ping(ip); ta_online = latencia > 0
                try: urllib.request.urlopen(urllib.request.Request(url_report_energia, data=json.dumps({"id": alvo['id'], "latencia": latencia}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5)
                except: pass
                
                estado_anterior = cache_alvos.get('ENERGIA_'+ip, {}).get('online', True)
                if ta_online and not estado_anterior:
                    try: urllib.request.urlopen(urllib.request.Request(url_log, data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "Energia Restaurada", "gravidade": "OK", "detalhes": f"Energia em {desc} ({ip})."}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5)
                    except: pass
                elif not ta_online and estado_anterior:
                    try: urllib.request.urlopen(urllib.request.Request(url_log, data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "Queda de Energia", "gravidade": "Crítica", "detalhes": f"FALTA DE ENERGIA em {desc} ({ip})!"}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5)
                    except: pass
                cache_alvos['ENERGIA_'+ip] = {'online': ta_online, 'latencia': latencia}

            for id_alvo, ip, desc in alvos_locais:
                if ip not in [a['ip'] for a in alvos_nuvem]: 
                    latencia = ping(ip); ta_online = latencia > 0
                    estado_anterior = cache_alvos.get(ip, {}).get('online', True)
                    if ta_online and not estado_anterior: log_local_event("Alvo Restaurado", f"{desc} ({ip}) voltou.", "OK")
                    elif not ta_online and estado_anterior: log_local_event("Queda de Alvo Local", f"{desc} ({ip}) parou!", "Crítica")
                    cache_alvos[ip] = {'online': ta_online, 'latencia': latencia}
                    
            for id_alvo, ip, desc in alvos_energia_locais:
                if ip not in [a['ip'] for a in alvos_energia]: 
                    latencia = ping(ip); ta_online = latencia > 0
                    estado_anterior = cache_alvos.get('ENERGIA_'+ip, {}).get('online', True)
                    if ta_online and not estado_anterior: log_local_event("Energia Restaurada", f"Energia em {desc} voltou.", "OK")
                    elif not ta_online and estado_anterior: log_local_event("Queda de Energia", f"Falta de energia em {desc} ({ip})!", "Crítica")
                    cache_alvos['ENERGIA_'+ip] = {'online': ta_online, 'latencia': latencia}

            # ----- 2. AUTO-CURA DE SERVIÇOS DO SO -----
            try:
                req = urllib.request.Request(url_get_srv, method='GET')
                with urllib.request.urlopen(req, timeout=5) as response: servicos_nuvem = json.loads(response.read().decode('utf-8'))
            except: servicos_nuvem = []

            conn = sqlite3.connect('sensor_local.db')
            servicos_locais = conn.execute("SELECT id, nome_servico, descricao FROM servicos_os").fetchall()
            conn.close()

            todos_servicos = servicos_nuvem.copy()
            nomes_na_nuvem = [s['nome_servico'] for s in servicos_nuvem]
            for srv_local in servicos_locais:
                if srv_local[1] not in nomes_na_nuvem:
                    todos_servicos.append({"id": srv_local[0], "nome_servico": srv_local[1], "descricao": srv_local[2], "is_local": True})

            for srv in todos_servicos:
                nome_srv = srv['nome_servico']; desc_srv = srv['descricao']; id_srv = srv['id']; is_local = srv.get('is_local', False)
                status_atual = 'OFFLINE'
                
                try:
                    if IS_WIN:
                        out = subprocess.check_output(f'sc query "{nome_srv}"', shell=True, universal_newlines=True, creationflags=C_FLAGS, stderr=subprocess.DEVNULL)
                        if "RUNNING" in out: status_atual = 'ONLINE'
                    else:
                        out = subprocess.check_output(f'systemctl is-active "{nome_srv}"', shell=True, universal_newlines=True, stderr=subprocess.DEVNULL)
                        if "active" in out.strip().lower(): status_atual = 'ONLINE'
                except: pass

                if status_atual == 'OFFLINE':
                    try:
                        if IS_WIN: subprocess.call(f'net start "{nome_srv}"', shell=True, creationflags=C_FLAGS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else: subprocess.call(f'sudo systemctl start "{nome_srv}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(2)
                        if IS_WIN:
                            out = subprocess.check_output(f'sc query "{nome_srv}"', shell=True, universal_newlines=True, creationflags=C_FLAGS, stderr=subprocess.DEVNULL)
                            if "RUNNING" in out: status_atual = 'ONLINE (Recuperado)'
                        else:
                            out = subprocess.check_output(f'systemctl is-active "{nome_srv}"', shell=True, universal_newlines=True, stderr=subprocess.DEVNULL)
                            if "active" in out.strip().lower(): status_atual = 'ONLINE (Recuperado)'
                    except: pass

                cache_alvos['SRV_'+nome_srv] = {'status': status_atual}

                if not is_local:
                    try: urllib.request.urlopen(urllib.request.Request(url_report_srv, data=json.dumps({"id": id_srv, "status": status_atual}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=3)
                    except: pass
                    
                    estado_anterior = cache_alvos.get('SRV_ANT_'+nome_srv, 'ONLINE')
                    if 'ONLINE' in status_atual and estado_anterior == 'OFFLINE':
                        msg = f"O serviço {desc_srv} ({nome_srv}) foi religado pela Auto-Cura." if "Recuperado" in status_atual else f"O serviço {desc_srv} foi restaurado."
                        try: urllib.request.urlopen(urllib.request.Request(url_log, data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "Serviço Restaurado", "gravidade": "OK", "detalhes": msg}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=3)
                        except: pass
                    elif status_atual == 'OFFLINE' and estado_anterior != 'OFFLINE':
                        try: urllib.request.urlopen(urllib.request.Request(url_log, data=json.dumps({"mac_id": mac, "alertas": [{"tipo": "Falha de Serviço Crítico", "gravidade": "Crítica", "detalhes": f"O serviço {desc_srv} parou! Requer intervenção."}]}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST'), timeout=3)
                        except: pass
                    cache_alvos['SRV_ANT_'+nome_srv] = status_atual

        except Exception as e: pass
        time.sleep(5)

# ==========================================
# 🖥️ MOTOR 2: PAINEL WEB LOCAL (FOREGROUND)
# ==========================================
def check_auth(username, password): return username == 'Admin' and password == 'Admin'

@app.route('/api/scada', methods=['GET'])
def api_scada():
    token = request.args.get('token')
    if token != TOKEN_SCADA: return jsonify({"erro": "Acesso Negado. Token invalido."}), 401
    conn = sqlite3.connect('sensor_local.db')
    alvos = [{"ip": r[1], "descricao": r[2], "latencia_ms": cache_alvos.get(r[1], {}).get('latencia', 0), "status": "ONLINE" if cache_alvos.get(r[1], {}).get('latencia', 0) > 0 else "OFFLINE"} for r in conn.execute("SELECT * FROM alvos_locais").fetchall()]
    alvos_energia = [{"ip": r[1], "descricao": r[2], "latencia_ms": cache_alvos.get('ENERGIA_'+r[1], {}).get('latencia', 0), "status": "COM ENERGIA" if cache_alvos.get('ENERGIA_'+r[1], {}).get('latencia', 0) > 0 else "SEM ENERGIA"} for r in conn.execute("SELECT * FROM alvos_energia").fetchall()]
    conn.close()
    return jsonify({
        "sensor_mac": get_mac(),
        "telemetria_host": { "cpu_percent": dados_sensores.get("cpu", 0), "ram_percent": dados_sensores.get("ram", 0), "temp_cpu": dados_sensores.get("temp", 0), "temp_gpu": dados_sensores.get("gpu_temp", 0) },
        "links_wan": { "google_ms": dados_sensores.get("pings", {}).get("Google", 0), "cloudflare_ms": dados_sensores.get("pings", {}).get("Cloudflare", 0), "status_geral_wan": "ONLINE" if (dados_sensores.get("pings", {}).get("Google", 0) > 0 or dados_sensores.get("pings", {}).get("Cloudflare", 0) > 0) else "OFFLINE" },
        "watchdog_rede": alvos, "watchdog_energia": alvos_energia
    })

@app.route('/api/local_data')
def api_local_data():
    conn = sqlite3.connect('sensor_local.db')
    nomes_salvos = {row[0]: row[1] for row in conn.execute("SELECT mac, nome FROM nomes_topologia").fetchall()}
    topologia_rica = []
    for d in dados_sensores['topologia']:
        d_rico = dict(d)
        if d_rico['mac'] in nomes_salvos: d_rico['nome'] = nomes_salvos[d_rico['mac']]
        topologia_rica.append(d_rico)
        
    alvos = [{"id": r[0], "ip": r[1], "descricao": r[2], "latencia": cache_alvos.get(r[1], {}).get('latencia', 0)} for r in conn.execute("SELECT * FROM alvos_locais ORDER BY id DESC").fetchall()]
    alvos_energia = [{"id": r[0], "ip": r[1], "descricao": r[2], "latencia": cache_alvos.get('ENERGIA_'+r[1], {}).get('latencia', 0)} for r in conn.execute("SELECT * FROM alvos_energia ORDER BY id DESC").fetchall()]
    servicos = [{"id": r[0], "nome_servico": r[1], "descricao": r[2], "status": cache_alvos.get('SRV_'+r[1], {}).get('status', 'Analisando...')} for r in conn.execute("SELECT * FROM servicos_os ORDER BY id DESC").fetchall()]
    logs = [{"tipo": r[0], "detalhes": r[1], "gravidade": r[2], "hora": r[3]} for r in conn.execute("SELECT tipo, detalhes, gravidade, time(data_hora, 'localtime') FROM logs_locais ORDER BY id DESC LIMIT 20").fetchall()]
    conn.close()
    
    dados_export = dict(dados_sensores)
    dados_export['topologia'] = topologia_rica
    dados_export['custom_ips'] = alvos
    dados_export['energia_ips'] = alvos_energia
    dados_export['servicos_os'] = servicos
    dados_export['logs'] = logs
    dados_export['mac'] = get_mac()
    dados_export['hora'] = datetime.now().strftime('%H:%M:%S')
    return jsonify(dados_export)

@app.route('/api/alvos', methods=['POST'])
def add_alvo():
    data = request.json; conn = sqlite3.connect('sensor_local.db')
    conn.execute("INSERT INTO alvos_locais (ip, descricao) VALUES (?, ?)", (data['ip'], data['descricao']))
    conn.commit(); conn.close(); return jsonify({"status": "OK"})

@app.route('/api/alvos/<int:id_alvo>', methods=['DELETE'])
def del_alvo(id_alvo):
    conn = sqlite3.connect('sensor_local.db'); conn.execute("DELETE FROM alvos_locais WHERE id = ?", (id_alvo,))
    conn.commit(); conn.close(); return jsonify({"status": "OK"})

@app.route('/api/energia', methods=['POST'])
def add_energia():
    data = request.json; conn = sqlite3.connect('sensor_local.db')
    conn.execute("INSERT INTO alvos_energia (ip, descricao) VALUES (?, ?)", (data['ip'], data['descricao']))
    conn.commit(); conn.close(); return jsonify({"status": "OK"})

@app.route('/api/energia/<int:id_alvo>', methods=['DELETE'])
def del_energia(id_alvo):
    conn = sqlite3.connect('sensor_local.db'); conn.execute("DELETE FROM alvos_energia WHERE id = ?", (id_alvo,))
    conn.commit(); conn.close(); return jsonify({"status": "OK"})

@app.route('/api/servicos_os', methods=['POST'])
def add_servico():
    data = request.json; conn = sqlite3.connect('sensor_local.db')
    conn.execute("INSERT INTO servicos_os (nome_servico, descricao) VALUES (?, ?)", (data['nome_servico'], data['descricao']))
    conn.commit(); conn.close(); return jsonify({"status": "OK"})

@app.route('/api/servicos_os/<int:id_srv>', methods=['DELETE'])
def del_servico(id_srv):
    conn = sqlite3.connect('sensor_local.db'); conn.execute("DELETE FROM servicos_os WHERE id = ?", (id_srv,))
    conn.commit(); conn.close(); return jsonify({"status": "OK"})

@app.route('/api/topologia/nome', methods=['POST'])
def rename_topo():
    data = request.json; conn = sqlite3.connect('sensor_local.db')
    conn.execute("INSERT OR REPLACE INTO nomes_topologia (mac, nome) VALUES (?, ?)", (data['mac'], data['nome']))
    conn.commit(); conn.close(); return jsonify({"status": "OK"})

@app.route('/')
def index():
    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password): return Response('Acesso Negado.', 401, {'WWW-Authenticate': 'Basic realm="NOC Sensor Local"'})

    HTML_CYBERPUNK = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Acesso Local - NOC Sensor</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            :root { --bg-base: #0b0b13; --bg-card: #151521; --bg-input: #232334; --border: #36364a; --text-main: #cdd6f4; --text-muted: #9399b2; --blue: #89b4fa; --green: #a6e3a1; --red: #f38ba8; --yellow: #f9e2af; --purple: #cba6f7; }
            body { margin: 0; font-family: 'Inter', sans-serif; background: var(--bg-base); color: var(--text-main); }
            .navbar { background: rgba(21,21,33,0.9); padding: 15px 30px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
            .container { padding: 30px; max-width: 1600px; margin: 0 auto; display: grid; grid-template-columns: repeat(3, 1fr); gap: 25px; }
            .painel-grid-duo { display: grid; grid-template-columns: repeat(2, 1fr); gap: 25px; grid-column: span 3; }
            .painel-grid-trio { display: grid; grid-template-columns: repeat(3, 1fr); gap: 25px; grid-column: span 3; }
            .card { background: linear-gradient(145deg, var(--bg-card) 0%, #11111b 100%); border: 1px solid var(--border); border-radius: 12px; padding: 22px; display: flex; flex-direction: column; box-shadow: 0 10px 30px rgba(0,0,0,0.5);}
            .card h3 { margin-top: 0; border-bottom: 1px solid var(--border); padding-bottom: 15px; display: flex; justify-content: space-between; color: var(--text-main);}
            .card-hw { border-top: 4px solid var(--blue); } .card-net { border-top: 4px solid var(--green); } .card-speed { border-top: 4px solid var(--purple); }
            .card-radar { border-top: 4px solid var(--blue); grid-column: 1 / -1; } .card-global { border-top: 4px solid var(--yellow); grid-column: span 3;}
            .card-custom { border-top: 4px solid var(--red); justify-content: flex-start; } .card-energia { border-top: 4px solid #fab387; justify-content: flex-start; } 
            .card-hist { border-top: 4px solid var(--text-muted); grid-column: span 3;}
            .data-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 1px dashed var(--bg-input); padding-bottom: 8px;}
            .highlight { font-family: 'JetBrains Mono', monospace; font-size: 2.2em; font-weight: bold; }
            .progress-bg { background: rgba(0,0,0,0.4); border: 1px solid var(--bg-input); border-radius: 10px; height: 12px; width: 100%; margin-top: 6px; overflow: hidden;}
            .progress-fill { background: linear-gradient(90deg, var(--blue), #b4befe); height: 100%; width: 0%; transition: 0.5s; box-shadow: 0 0 10px var(--blue); }
            .global-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-top: 10px;}
            .g-card { background: rgba(0,0,0,0.3); border: 1px solid var(--bg-input); border-radius: 8px; padding: 15px; text-align: center; }
            .ms { font-family: 'JetBrains Mono', monospace; font-size: 1.4em; font-weight: bold; margin-top: 5px; color: var(--green);}
            .pill-ok { background: rgba(166,227,161,0.1); color: var(--green); border: 1px solid var(--green); padding: 5px 12px; border-radius: 6px; font-size: 0.75em; font-weight: bold;}
            .pill-fail { background: rgba(243,139,168,0.1); color: var(--red); border: 1px solid var(--red); padding: 5px 12px; border-radius: 6px; font-size: 0.75em; font-weight: bold;}
            input { width: 100%; padding: 10px; background: rgba(0,0,0,0.3); border: 1px solid var(--border); color: var(--text-main); border-radius: 6px; box-sizing: border-box; outline: none; transition: 0.2s;} input:focus { border-color: var(--blue); }
            button.action-btn { cursor: pointer; padding: 10px 15px; border: none; border-radius: 6px; font-weight: bold; color: var(--bg-base); transition: 0.2s;}
            .topology-box { display: flex; flex-direction: column; align-items: center; justify-content: center; flex-grow: 1; background: rgba(0,0,0,0.2); padding: 30px; border-radius: 8px; border: 1px solid var(--bg-input); overflow-x: auto; min-height: 300px;}
            .t-card { background: var(--bg-card); padding: 12px; border-radius: 8px; border: 1px solid var(--border); width: 170px; text-align: center; position: relative; border-top: 4px solid var(--blue);}
            .t-gateway { border-top: 4px solid var(--red); } .t-sensor { border-top: 4px solid var(--purple); }
            .t-ip { font-family: 'JetBrains Mono', monospace; color: var(--green); font-weight: bold; } .t-mac { font-size: 0.7em; color: var(--text-muted); margin: 5px 0;} .t-name { font-size: 0.85em; color: var(--blue); font-weight: bold; }
            .t-line-v { width: 3px; height: 25px; background: var(--border); } .t-line-h { height: 3px; background: var(--border); }
            .speed-box { text-align: center; background: rgba(0,0,0,0.3); padding: 18px; border-radius: 8px; border: 1px solid var(--bg-input); box-shadow: inset 0 0 10px rgba(0,0,0,0.5);}
            .speed-val { font-family: 'JetBrains Mono', monospace; font-size: 2.5em; font-weight: bold; margin-top: 8px; }
        </style>
    </head>
    <body>
        <nav class="navbar">
            <div style="font-size: 1.3em; font-weight: bold; color: var(--blue);"><i class="fa-solid fa-tower-broadcast"></i> NOC SENSOR LOCAL</div>
            <div style="color: var(--text-muted);"><i class="fa-solid fa-microchip"></i> MAC: <strong style="color:var(--text-main)" id="mac-id">--</strong></div>
        </nav>
        <div class="container">
            <div class="card card-hw">
                <h3><span><i class="fa-solid fa-microchip"></i> Telemetria Local</span> <span class="pill-ok">ONLINE</span></h3>
                <div class="data-row" style="margin-top:10px;">
                    <div style="display: flex; gap: 15px; width: 100%;">
                        <span style="flex: 1;"><i class="fa-solid fa-temperature-half" style="color:var(--yellow);"></i> CPU: <strong id="temp-text" style="color:var(--yellow);">-- °C</strong></span> 
                        <span style="flex: 1;"><i class="fa-solid fa-fire-flame-curved" style="color:var(--red);"></i> GPU: <strong id="gpu-text" style="color:var(--red);">-- °C</strong></span>
                    </div>
                </div>
                <div style="margin-bottom: 15px;">
                    <div style="display: flex; justify-content: space-between;"><span><i class="fa-solid fa-microchip"></i> CPU</span> <strong id="cpu-text">0%</strong></div>
                    <div class="progress-bg"><div id="cpu-fill" class="progress-fill"></div></div>
                </div>
                <div style="margin-bottom: 15px;">
                    <div style="display: flex; justify-content: space-between;"><span><i class="fa-solid fa-memory"></i> RAM</span> <strong id="ram-text">0%</strong></div>
                    <div class="progress-bg"><div id="ram-fill" class="progress-fill"></div></div>
                </div>
                <div>
                    <div style="display: flex; justify-content: space-between;"><span><i class="fa-solid fa-hard-drive"></i> Disco</span> <strong id="disk-text">0%</strong></div>
                    <div class="progress-bg"><div id="disk-fill" class="progress-fill" style="background: linear-gradient(90deg, #cba6f7, #f5c2e7); box-shadow: 0 0 10px #cba6f7;"></div></div>
                </div>
            </div>

            <div class="card card-net">
                <h3><span><i class="fa-solid fa-shield-heart"></i> Integridade da Rede</span></h3>
                <div class="data-row"><span><i class="fa-solid fa-network-wired" style="color:var(--green)"></i> Gateway (<span id="gw-ip">--</span>):</span> <span id="status-local" class="pill-ok">ESTÁVEL</span></div>
                <div class="data-row"><span><i class="fa-solid fa-globe" style="color:var(--blue)"></i> Internet (WAN):</span> <span id="status-wan" class="pill-ok">ONLINE</span></div>
                <div class="data-row"><span><i class="fa-solid fa-door-open" style="color:var(--purple)"></i> Portas:</span> <span id="portas-text" style="font-family: 'JetBrains Mono'; font-size: 0.75em; color: var(--text-muted);">--</span></div>
                <div style="margin-top: auto; background: rgba(0,0,0,0.3); padding: 18px; border-radius: 8px; text-align: center; border: 1px solid var(--bg-input);">
                    <div style="font-size: 0.75em; color: var(--text-muted);">Latência Sensor ➔ Gateway</div>
                    <div id="ping-local" class="highlight" style="color: var(--green);">0 ms</div>
                </div>
            </div>

            <div class="card card-speed">
                <h3><span><i class="fa-solid fa-arrow-right-arrow-left"></i> Tráfego em Tempo Real</span></h3>
                <div style="flex-grow: 1; display: flex; justify-content: space-between; align-items: center; gap: 15px; margin-top: 10px;">
                    <div class="speed-box" style="flex: 1;">
                        <div style="font-size: 0.75em; color: var(--text-muted); letter-spacing: 1px;"><i class="fa-solid fa-arrow-down" style="color:var(--green)"></i> DOWNLOAD</div>
                        <div id="live-down" class="speed-val" style="color: var(--green); text-shadow: 0 0 15px rgba(166,227,161,0.4);">0.0</div><span style="font-size: 0.6em; color: var(--text-muted);">Mbps</span>
                    </div>
                    <div class="speed-box" style="flex: 1;">
                        <div style="font-size: 0.75em; color: var(--text-muted); letter-spacing: 1px;"><i class="fa-solid fa-arrow-up" style="color:var(--red)"></i> UPLOAD</div>
                        <div id="live-up" class="speed-val" style="color: var(--red); text-shadow: 0 0 15px rgba(243,139,168,0.4);">0.0</div><span style="font-size: 0.6em; color: var(--text-muted);">Mbps</span>
                    </div>
                </div>
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

            <div class="painel-grid-trio">
                <div class="card card-custom">
                    <h3 style="margin-bottom: 15px;"><span><i class="fa-solid fa-crosshairs"></i> Pings Locais</span></h3>
                    <div style="display: flex; gap: 5px; margin-bottom: 15px;">
                        <input type="text" id="new-ip" placeholder="IP Alvo">
                        <input type="text" id="new-desc" placeholder="Apelido">
                        <button class="action-btn" onclick="addIP()" style="background: var(--red); color: white;"><i class="fa-solid fa-plus"></i></button>
                    </div>
                    <div id="lista-custom-ips" style="max-height: 250px; overflow-y: auto; display: flex; flex-direction: column; gap: 5px;"></div>
                </div>

                <div class="card card-energia">
                    <h3 style="margin-bottom: 15px; color: #fab387;"><span><i class="fa-solid fa-plug-circle-bolt"></i> Energia</span></h3>
                    <div style="display: flex; gap: 5px; margin-bottom: 15px;">
                        <input type="text" id="new-ip-energia" placeholder="IP Tomada">
                        <input type="text" id="new-desc-energia" placeholder="Apelido">
                        <button class="action-btn" onclick="addEnergia()" style="background: #fab387; color: var(--bg-base);"><i class="fa-solid fa-plus"></i></button>
                    </div>
                    <div id="lista-energia-ips" style="max-height: 250px; overflow-y: auto; display: flex; flex-direction: column; gap: 5px;"></div>
                </div>

                <div class="card card-speed">
                    <h3 style="margin-bottom: 15px; color: var(--purple);"><span><i class="fa-solid fa-cogs"></i> Serviços SO</span></h3>
                    <div style="display: flex; gap: 5px; margin-bottom: 15px;">
                        <input type="text" id="new-srv-nome" placeholder="Serviço Exato">
                        <input type="text" id="new-srv-desc" placeholder="Apelido">
                        <button class="action-btn" onclick="addServicoOS()" style="background: var(--purple); color: var(--bg-base);"><i class="fa-solid fa-plus"></i></button>
                    </div>
                    <div id="lista-servicos-os" style="max-height: 250px; overflow-y: auto; display: flex; flex-direction: column; gap: 5px;"></div>
                </div>
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
            
            async function addEnergia() { const ip = document.getElementById('new-ip-energia').value; const desc = document.getElementById('new-desc-energia').value; if(!ip) return; await fetch('/api/energia', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ip, descricao:desc}) }); document.getElementById('new-ip-energia').value=''; document.getElementById('new-desc-energia').value=''; }
            async function excluirEnergia(id) { if(confirm("Parar monitoramento de energia?")) { await fetch('/api/energia/' + id, { method: 'DELETE' }); } }

            async function addServicoOS() { const nome = document.getElementById('new-srv-nome').value; const desc = document.getElementById('new-srv-desc').value; if(!nome) return; await fetch('/api/servicos_os', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({nome_servico:nome, descricao:desc}) }); document.getElementById('new-srv-nome').value=''; document.getElementById('new-srv-desc').value=''; }
            async function excluirServicoOS(id) { if(confirm("Parar de monitorar este serviço?")) { await fetch('/api/servicos_os/' + id, { method: 'DELETE' }); } }

            async function renomearTopo(mac, atual) { let n = prompt("Novo nome para este dispositivo na topologia:", atual); if(n) { await fetch('/api/topologia/nome', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mac, nome:n}) }); } }
            
            async function monitorarIP(ip, nome) {
                if(confirm(`Deseja adicionar ${nome} (${ip}) ao Watchdog de 5 segundos?`)) {
                    await fetch('/api/alvos', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ip: ip, descricao: nome}) });
                    atualizarLocal();
                }
            }

            async function atualizarLocal() {
                try {
                    const res = await fetch('/api/local_data'); const data = await res.json();
                    document.getElementById('mac-id').innerText = data.mac; document.getElementById('topo-mac').innerText = data.mac;
                    document.getElementById('meu-ip').innerText = data.meu_ip;
                    
                    document.getElementById('cpu-text').innerText = data.cpu + '%'; document.getElementById('cpu-fill').style.width = data.cpu + '%';
                    document.getElementById('ram-text').innerText = data.ram + '%'; document.getElementById('ram-fill').style.width = data.ram + '%';
                    
                    // AQUI ESTÁ A "FUGA ELEGANTE" NA TELA LOCAL:
                    document.getElementById('temp-text').innerText = (data.temp > 0 ? data.temp : '--') + ' °C'; 
                    document.getElementById('gpu-text').innerText = (data.gpu_temp > 0 ? data.gpu_temp : '--') + ' °C';
                    
                    if(document.getElementById('disk-text')) { document.getElementById('disk-text').innerText = data.disco + '%'; document.getElementById('disk-fill').style.width = data.disco + '%'; }
                    if(document.getElementById('portas-text')) document.getElementById('portas-text').innerText = data.portas || 'Nenhuma';
                    if(document.getElementById('live-down')) document.getElementById('live-down').innerText = data.net_down || '0.0';
                    if(document.getElementById('live-up')) document.getElementById('live-up').innerText = data.net_up || '0.0';

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
                        htmlAlvos += `<div style="background:var(--bg-input); padding:8px; border-radius:8px; display:flex; justify-content:space-between; align-items:center; border-left:4px solid ${cor};">
                            <div><b style="color:var(--blue); font-size:1.1em;">${item.descricao}</b><br><small style="color:var(--text-muted); font-family:monospace;">${item.ip}</small></div>
                            <div style="display:flex; align-items:center; gap:12px;"><b style="color:${cor};">${latText}</b><button onclick="excluirIP(${item.id})" style="background:none; border:none; color:var(--text-muted); cursor:pointer;" onmouseover="this.style.color='var(--red)'" onmouseout="this.style.color='var(--text-muted)'"><i class="fa-solid fa-trash"></i></button></div></div>`;
                    });
                    document.getElementById('lista-custom-ips').innerHTML = htmlAlvos || '<div style="text-align:center; padding:15px; color:var(--text-muted);">Nenhum alvo.</div>';

                    let htmlEnergia = '';
                    data.energia_ips.forEach(item => {
                        let isOnline = item.latencia > 0;
                        let cor = isOnline ? 'var(--green)' : 'var(--red)';
                        let statusText = isOnline ? 'COM ENERGIA' : 'SEM ENERGIA';
                        let icone = isOnline ? 'fa-plug-circle-check' : 'fa-plug-circle-xmark';
                        htmlEnergia += `<div style="background:var(--bg-input); padding:10px; border-radius:8px; display:flex; justify-content:space-between; align-items:center; border-left:4px solid ${cor};">
                            <div style="display:flex; align-items:center; gap:12px;">
                                <i class="fa-solid ${icone}" style="color:${cor}; font-size:1.5em;"></i>
                                <div><b style="color:var(--text-main); font-size:1.1em;">${item.descricao}</b><br><small style="color:var(--text-muted); font-family:monospace;">${item.ip}</small></div>
                            </div>
                            <div style="display:flex; align-items:center; gap:15px;"><b style="color:${cor}; font-size:0.9em;">${statusText}</b><button onclick="excluirEnergia(${item.id})" style="background:none; border:none; color:var(--text-muted); cursor:pointer;" onmouseover="this.style.color='var(--red)'" onmouseout="this.style.color='var(--text-muted)'"><i class="fa-solid fa-trash"></i></button></div></div>`;
                    });
                    document.getElementById('lista-energia-ips').innerHTML = htmlEnergia || '<div style="text-align:center; padding:15px; color:var(--text-muted);">Nenhum ponto monitorado.</div>';

                    let htmlServicos = '';
                    data.servicos_os.forEach(item => {
                        let isOnline = item.status.includes('ONLINE');
                        let cor = isOnline ? 'var(--green)' : 'var(--red)';
                        let icone = isOnline ? 'fa-check-circle' : 'fa-triangle-exclamation';
                        htmlServicos += `<div style="background:var(--bg-input); padding:8px; border-radius:8px; display:flex; justify-content:space-between; align-items:center; border-left:4px solid ${cor};">
                            <div><b style="color:var(--purple); font-size:1.1em;">${item.descricao}</b><br><small style="color:var(--text-muted); font-family:monospace;">${item.nome_servico}</small></div>
                            <div style="display:flex; align-items:center; gap:12px;"><i class="fa-solid ${icone}" style="color:${cor}; font-size:1.2em;" title="${item.status}"></i>
                            <button onclick="excluirServicoOS(${item.id})" style="background:none; border:none; color:var(--text-muted); cursor:pointer;"><i class="fa-solid fa-trash"></i></button></div></div>`;
                    });
                    document.getElementById('lista-servicos-os').innerHTML = htmlServicos || '<div style="text-align:center; padding:15px; color:var(--text-muted);">Nenhum serviço monitorado.</div>';

                    let gHtml = `<div class="t-card t-gateway"><div class="t-ip">${data.gateway_ip}</div><div class="t-mac">ROTEADOR</div><div class="t-name">GATEWAY PADRÃO</div></div>`;
                    let oHtml = '';
                    data.topologia.forEach(t => {
                        if(t.ip === data.gateway_ip || t.ip === data.meu_ip) return;
                        oHtml += `<div class="t-card"><div class="t-ip">${t.ip}</div><div class="t-mac">${t.mac}</div><div class="t-name">${t.nome} <button onclick="renomearTopo('${t.mac}','${t.nome}')" style="background:none; border:none; color:var(--yellow); cursor:pointer;" title="Renomear"><i class="fa-solid fa-pen-to-square"></i></button> <button onclick="monitorarIP('${t.ip}', '${t.nome}')" style="background:none; border:none; color:var(--blue); cursor:pointer;" title="Adicionar ao Watchdog"><i class="fa-solid fa-eye"></i></button></div></div>`;
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
    </body>
    </html>
    """
    return render_template_string(HTML_CYBERPUNK)

# ==========================================
# 🛠️ MOTOR 3: SYSTEM TRAY (ÍCONE NO RELÓGIO)
# ==========================================
def create_image():
    image = Image.new('RGB', (64, 64), color=(11, 11, 19))
    dc = ImageDraw.Draw(image)
    dc.ellipse((8, 8, 56, 56), fill=(137, 180, 250))
    return image

def on_quit(icon, item):
    icon.stop()
    os._exit(0)

def run_tray():
    image = create_image()
    menu = pystray.Menu(pystray.MenuItem('Encerrar Sensor NOC', on_quit))
    icon = pystray.Icon("NOC Sensor", image, "NOC Sensor (Ativo)", menu)
    icon.run()

if __name__ == "__main__":
    try:
        init_local_db()
        threading.Thread(target=lambda: app.run(host='0.0.0.0', port=PORTA_LOCAL, debug=False, use_reloader=False), daemon=True).start()
        threading.Thread(target=loop_telemetria, daemon=True).start()
        threading.Thread(target=loop_watchdog_local, daemon=True).start()
        run_tray() 
    except Exception as e: pass