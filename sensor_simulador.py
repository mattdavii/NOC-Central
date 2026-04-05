import requests
import psutil
import uuid
import time
import socket
import subprocess
import re
import threading
import json
import speedtest # Requer: pip install speedtest-cli

CENTRAL_URL = "http://localhost:10000"
MANUAL_SPEEDTEST = False # Variável global para receber ordem da central

# ==========================================
# 🛠️ FUNÇÕES DE APOIO E HARDWARE
# ==========================================

def get_mac_address():
    return ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0, 8*6, 8)][::-1])

def get_geo_location():
    try:
        res = requests.get("http://ip-api.com/json/", timeout=5).json()
        return res.get('lat', -14.2350), res.get('lon', -51.9253), res.get('city', 'Desconhecido')
    except:
        return -14.2350, -51.9253, "Desconhecido"

def get_local_network():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        meu_ip = s.getsockname()[0]
    finally:
        s.close()
    base_ip = meu_ip.rsplit('.', 1)[0]
    return f"{base_ip}.0/24", f"{base_ip}.1", base_ip, meu_ip

def ping_rapido(ip):
    """ Pinga um IP e retorna o tempo em milissegundos usando o SO nativo """
    try:
        res = subprocess.run(['ping', '-n', '1', '-w', '1000', ip], capture_output=True, text=True)
        match = re.search(r"(tempo|time)[=<](\d+)ms", res.stdout, re.IGNORECASE)
        return float(match.group(2)) if match else 0.0
    except: return 0.0

def obter_temperatura():
    """ Tenta ler a temperatura física. Se for Windows (ou sem sensor), simula 45C para testes """
    try:
        temps = psutil.sensors_temperatures()
        if not temps: return 45.0
        for name, entries in temps.items(): return entries[0].current
    except: return 45.0

# ==========================================
# 🧠 MOTOR DE INTELIGÊNCIA ARTIFICIAL E SCAN
# ==========================================

class MotorIA:
    def __init__(self, gateway_ip):
        self.gateway_ip = gateway_ip
        self.gateway_mac_original = self.pegar_mac_gateway(self.gateway_ip)

    def pegar_mac_gateway(self, ip):
        resultado = subprocess.run(['arp', '-a', ip], capture_output=True, text=True, encoding='cp850', errors='ignore')
        padrao = re.compile(r"([0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2})")
        match = padrao.search(resultado.stdout)
        if match: return match.group(1).replace('-', ':').lower()
        return None

    def analisar_gargalo(self):
        latencia = ping_rapido(self.gateway_ip)
        if latencia > 30.0:
            return {"tipo": "Gargalo Interno", "gravidade": "Média", "detalhes": f"Latência para o roteador local está alta: {latencia}ms"}
        return None

    def verificar_troca_roteador(self):
        mac_atual = self.pegar_mac_gateway(self.gateway_ip)
        if mac_atual and self.gateway_mac_original and mac_atual != self.gateway_mac_original:
            self.gateway_mac_original = mac_atual
            return {"tipo": "Troca de Roteador", "gravidade": "Crítica", "detalhes": f"Roteador trocado! Novo MAC: {mac_atual}"}
        return None

def acordar_dispositivos(base_ip):
    """ Dispara o 'Sonar' pingando toda a rede via Threads para forçar o Windows a memorizar os MACs """
    def pingar(ip): subprocess.run(['ping', '-n', '1', '-w', '100', ip], capture_output=True)
    threads = []
    for i in range(1, 255):
        t = threading.Thread(target=pingar, args=(f"{base_ip}.{i}",))
        threads.append(t)
        t.start()
    for t in threads: t.join()

def scan_rede_local(base_ip):
    acordar_dispositivos(base_ip)
    dispositivos = []
    resultado = subprocess.run(['arp', '-a'], capture_output=True, text=True, encoding='cp850', errors='ignore')
    padrao = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2})")
    
    for linha in resultado.stdout.split('\n'):
        match = padrao.search(linha)
        if match:
            ip = match.group(1)
            mac = match.group(2).replace('-', ':').lower()
            if ip.startswith('224.') or ip.startswith('239.') or ip.endswith('.255'): continue

            prefixo = mac[:8]
            fabricante = "Desconhecido"
            if prefixo in ["00:0c:29", "00:50:56"]: fabricante = "VMware"
            elif prefixo in ["b8:27:eb", "dc:a6:32"]: fabricante = "Raspberry Pi"
            elif prefixo in ["00:15:5d"]: fabricante = "Microsoft"
            elif prefixo in ["48:2c:a0", "f8:ff:c2", "00:fc:8b", "b4:f0:22"]: fabricante = "Apple"
            elif prefixo in ["00:1a:3f", "cc:b8:a8", "18:b4:30"]: fabricante = "Samsung"
            elif prefixo in ["2c:ea:7f", "10:ae:60", "c8:3a:35"]: fabricante = "TP-Link"
            elif prefixo in ["00:1a:3b", "4c:11:bf"]: fabricante = "Intelbras"
            
            dispositivos.append({"ip": ip, "mac": mac, "fabricante": fabricante})
    return dispositivos

# ==========================================
# 🚦 MULTI-THREADS (MOTORES PARALELOS)
# ==========================================

def thread_instantanea(mac, meu_ip, ip_gateway):
    """ Envia dados vitais a cada 1.5s e ouve ordens da Central """
    global MANUAL_SPEEDTEST
    while True:
        payload = {
            "mac_id": mac, "cpu": psutil.cpu_percent(), "ram": psutil.virtual_memory().percent,
            "temp": obter_temperatura(), "ping_gw": ping_rapido(ip_gateway), 
            "ip_sensor": meu_ip, "ip_gateway": ip_gateway
        }
        try:
            res = requests.post(f"{CENTRAL_URL}/api/v2/telemetria_instantanea", json=payload)
            # Se a Central disser que há um teste na fila, ativa a flag!
            if res.json().get("run_speedtest"): 
                MANUAL_SPEEDTEST = True
                print("⚡ Ordem recebida: Iniciando Speedtest Manual!")
        except: pass
        time.sleep(1.5)

def thread_velocidade(mac):
    """ Roda o Speedtest real a cada 15 min ou quando ordenado """
    global MANUAL_SPEEDTEST
    contador = 900 
    while True:
        if MANUAL_SPEEDTEST or contador >= 900:
            try:
                print("🚀 Executando Teste de Link (Speedtest real)... Aguarde.")
                # O 'secure=True' FORÇA o uso de HTTPS e impede o Erro 403 Forbidden da Ookla!
                st = speedtest.Speedtest(secure=True) 
                st.get_best_server()
                down = st.download() / 1_000_000
                up = st.upload() / 1_000_000
                requests.post(f"{CENTRAL_URL}/api/v2/reportar_velocidade", json={"mac_id": mac, "down": round(down, 2), "up": round(up, 2)})
                print(f"✅ Velocidade real: ↓{down:.1f} Mbps | ↑{up:.1f} Mbps")
            except Exception as e: 
                print(f"❌ Erro no Speedtest real: {e}")
            
            MANUAL_SPEEDTEST = False 
            contador = 0 
        
        time.sleep(1)
        contador += 1

def thread_global(mac):
    """ Testa a latência para os maiores servidores mundiais a cada 60s """
    while True:
        pings = {
            "Google": ping_rapido("8.8.8.8"),
            "Cloudflare": ping_rapido("1.1.1.1"),
            "AWS": ping_rapido("dynamodb.us-east-1.amazonaws.com"),
            "Quad9": ping_rapido("9.9.9.9")
        }
        try: 
            requests.post(f"{CENTRAL_URL}/api/v2/telemetria_global", json={"mac_id": mac, "pings": json.dumps(pings), "tracert": ""})
        except: pass
        time.sleep(60)

def thread_ips_custom(mac):
    """ Busca os IPs que o usuário cadastrou no Dashboard e testa a latência a cada 10s """
    while True:
        try:
            res = requests.get(f"{CENTRAL_URL}/api/v2/ips_customizados/{mac}")
            ips = res.json()
            for item in ips:
                lat = ping_rapido(item['ip'])
                requests.post(f"{CENTRAL_URL}/api/v2/reportar_latencia_custom", json={"id": item['id'], "latencia": lat})
        except: pass
        time.sleep(10)

# ==========================================
# 🚀 PONTO DE PARTIDA PRINCIPAL
# ==========================================

def simular_sensor():
    mac = get_mac_address()
    lat, lon, cidade = get_geo_location()
    rede_local, ip_gateway, base_ip, meu_ip = get_local_network()
    
    print(f"🚀 Iniciando NOC Sensor PRO (Enterprise Edition)")
    print(f"🔹 IP: {meu_ip} | Gateway: {ip_gateway} | MAC: {mac}")
    
    # ZTP: Auto-Registro na Central
    requests.post(f"{CENTRAL_URL}/api/v2/registrar_sensor", json={"mac_id": mac, "lat": lat, "lon": lon, "nome_local": "Cobaia Windows"})
    
    # Inicia Inteligência Artificial
    ia = MotorIA(ip_gateway)

    # Inicia todos os motores paralelos
    threading.Thread(target=thread_instantanea, args=(mac, meu_ip, ip_gateway), daemon=True).start()
    threading.Thread(target=thread_velocidade, args=(mac,), daemon=True).start()
    threading.Thread(target=thread_global, args=(mac,), daemon=True).start()
    threading.Thread(target=thread_ips_custom, args=(mac,), daemon=True).start()

    # O ciclo principal foca apenas na varredura profunda e logs de IA
    while True:
        print("🔍 IA executando varredura e diagnósticos...")
        lista_disp = scan_rede_local(base_ip)
        try: requests.post(f"{CENTRAL_URL}/api/v2/atualizar_dispositivos", json={"mac_id": mac, "lista": lista_disp})
        except: pass
        
        alertas = []
        alerta_gargalo = ia.analisar_gargalo()
        alerta_router = ia.verificar_troca_roteador()
        
        if alerta_gargalo: alertas.append(alerta_gargalo)
        if alerta_router: alertas.append(alerta_router)

        if alertas:
            try: requests.post(f"{CENTRAL_URL}/api/v2/alertas_ia", json={"mac_id": mac, "alertas": alertas})
            except: pass

        time.sleep(25) # A cada 25s ele escaneia a rede inteira

if __name__ == "__main__":
    simular_sensor()