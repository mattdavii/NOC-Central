"""Métricas estendidas do host para o NexoPulse Edge (Linux / WSL).

`coletar()` devolve um dicionário enviado à Central como `host_info`. Cada bloco
é independente: o que não existir ou falhar nesta máquina vem como None, sem
derrubar os demais. Itens caros (atualizações pendentes, SMART) ficam em cache.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import time

try:
    import psutil
except ImportError:  # pragma: no cover - o agente sempre instala o psutil
    psutil = None

# Sistemas de arquivos que não são discos de verdade.
FS_IGNORADOS = {'tmpfs', 'devtmpfs', 'squashfs', 'overlay', 'proc', 'sysfs', 'cgroup', 'cgroup2',
                'devpts', 'tracefs', 'debugfs', 'securityfs', 'pstore', 'bpf', 'autofs', 'mqueue',
                'hugetlbfs', 'fusectl', 'configfs', 'efivarfs', 'ramfs', 'nsfs', 'binfmt_misc', '9p'}
# Interfaces virtuais que só poluem a lista.
IFACE_IGNORADAS = re.compile(r'^(lo|veth|docker|br-|virbr|vnet|tap|tun\d|cni|flannel|kube)')

_cache = {}
_rede_anterior = {}


def _rodar(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                           timeout=timeout, text=True, errors='replace')
        return r.returncode, r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, ''


def _cacheado(chave, validade, funcao):
    agora = time.time()
    if chave in _cache and agora - _cache[chave][0] < validade:
        return _cache[chave][1]
    try:
        valor = funcao()
    except Exception:
        valor = None
    _cache[chave] = (agora, valor)
    return valor


def _seguro(funcao):
    try:
        return funcao()
    except Exception:
        return None


# ---------------------------------------------------------------- máquina
def sistema():
    boot = psutil.boot_time()
    carga = os.getloadavg()
    return {
        'boot_time': int(boot),
        'uptime_s': int(time.time() - boot),
        'load': [round(c, 2) for c in carga],
        'nucleos': psutil.cpu_count(logical=True),
        'cpu_por_nucleo': [round(p) for p in psutil.cpu_percent(percpu=True)],
    }


def swap():
    s = psutil.swap_memory()
    return {'total_mb': round(s.total / 2**20), 'usado_mb': round(s.used / 2**20), 'percentual': round(s.percent, 1)}


def discos():
    vistos, saida = set(), []
    for p in psutil.disk_partitions(all=False):
        if p.fstype in FS_IGNORADOS or p.device in vistos or p.mountpoint.startswith(('/snap', '/var/lib/docker', '/boot/efi')):
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        if u.total < 512 * 2**20:
            continue
        vistos.add(p.device)
        saida.append({'ponto': p.mountpoint, 'dispositivo': p.device, 'fs': p.fstype,
                      'total_gb': round(u.total / 2**30, 1), 'usado_gb': round(u.used / 2**30, 1),
                      'percentual': round(u.percent, 1)})
    return sorted(saida, key=lambda d: d['ponto'])


# ---------------------------------------------------------------- rede
def _wifi_sinal():
    sinais = {}
    try:
        with open('/proc/net/wireless', encoding='utf-8') as arquivo:
            for linha in arquivo.readlines()[2:]:
                partes = linha.split()
                if len(partes) >= 4:
                    sinais[partes[0].rstrip(':')] = round(float(partes[3].rstrip('.')))
    except OSError:
        pass
    return sinais


def interfaces():
    agora = time.time()
    contadores = psutil.net_io_counters(pernic=True)
    estados = psutil.net_if_stats()
    wifi = _wifi_sinal()
    saida = []
    for nome, c in contadores.items():
        if IFACE_IGNORADAS.match(nome):
            continue
        anterior = _rede_anterior.get(nome)
        rx = tx = None
        if anterior:
            dt = max(agora - anterior[0], 1e-3)
            rx = round(max(c.bytes_recv - anterior[1], 0) * 8 / dt / 1e6, 2)
            tx = round(max(c.bytes_sent - anterior[2], 0) * 8 / dt / 1e6, 2)
        _rede_anterior[nome] = (agora, c.bytes_recv, c.bytes_sent)
        st = estados.get(nome)
        saida.append({
            'nome': nome, 'ativa': bool(st and st.isup), 'velocidade_mbps': (st.speed if st and st.speed else None),
            'rx_mbps': rx, 'tx_mbps': tx, 'erros': c.errin + c.errout, 'descartes': c.dropin + c.dropout,
            'wifi_dbm': wifi.get(nome),
        })
    return sorted(saida, key=lambda i: (not i['ativa'], i['nome']))


def dns():
    inicio = time.time()
    try:
        socket.getaddrinfo('cloudflare.com', 443)
        return {'ok': True, 'ms': round((time.time() - inicio) * 1000)}
    except OSError:
        return {'ok': False, 'ms': None}


def hora_sincronizada():
    codigo, saida = _rodar(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'], 5)
    return None if codigo is None else saida.strip() == 'yes'


# ---------------------------------------------------------------- sistema
def servicos_com_falha():
    codigo, saida = _rodar(['systemctl', '--failed', '--plain', '--no-legend', '--no-pager'], 10)
    if codigo is None:
        return None
    return [linha.split()[0] for linha in saida.splitlines() if linha.strip()]


def reinicio_necessario():
    if os.path.exists('/var/run/reboot-required'):
        return True
    # Arch e derivados: o kernel em uso foi substituído e seus módulos sumiram.
    return not os.path.isdir(f'/usr/lib/modules/{os.uname().release}') and os.path.isdir('/usr/lib/modules')


def atualizacoes_pendentes():
    if shutil.which('apt-get'):
        codigo, saida = _rodar(['apt-get', '-s', '-o', 'Debug::NoLocking=1', 'upgrade'], 120)
        return None if codigo is None else sum(1 for l in saida.splitlines() if l.startswith('Inst '))
    if shutil.which('checkupdates'):
        codigo, saida = _rodar(['checkupdates'], 120)
        return None if codigo not in (0, 2) else len([l for l in saida.splitlines() if l.strip()])
    if shutil.which('dnf'):
        codigo, saida = _rodar(['dnf', '-q', 'check-update'], 180)
        return None if codigo not in (0, 100) else len([l for l in saida.splitlines() if l.strip() and not l.startswith(' ')])
    return None


def processos(limite=5):
    lista = []
    for p in psutil.process_iter(['pid', 'name', 'memory_percent']):
        try:
            cpu = p.cpu_percent(None)  # desde a coleta anterior
            lista.append({'pid': p.info['pid'], 'nome': p.info['name'], 'cpu': round(cpu, 1),
                          'memoria': round(p.info['memory_percent'] or 0, 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {
        'cpu': sorted(lista, key=lambda x: x['cpu'], reverse=True)[:limite],
        'memoria': sorted(lista, key=lambda x: x['memoria'], reverse=True)[:limite],
    }


# ---------------------------------------------------------------- hardware
def bateria():
    b = psutil.sensors_battery() if hasattr(psutil, 'sensors_battery') else None
    if b is None:
        return None
    return {'percentual': round(b.percent), 'na_tomada': bool(b.power_plugged),
            'restante_min': (round(b.secsleft / 60) if b.secsleft and b.secsleft > 0 else None)}


def ventoinhas():
    if not hasattr(psutil, 'sensors_fans'):
        return None
    saida = [{'nome': (f.label or nome), 'rpm': f.current} for nome, lista in (psutil.sensors_fans() or {}).items() for f in lista]
    return saida or None


def smart():
    """Saúde dos discos pelo smartctl (smartmontools). Sem acesso, devolve o motivo."""
    if not shutil.which('smartctl'):
        return {'disponivel': False, 'motivo': 'smartmontools não instalado'}
    codigo, saida = _rodar(['smartctl', '--scan', '-j'], 15)
    try:
        dispositivos = [d['name'] for d in json.loads(saida or '{}').get('devices', [])]
    except ValueError:
        dispositivos = []
    if not dispositivos:
        return {'disponivel': False, 'motivo': 'nenhum disco acessível (pode exigir root)'}
    discos_smart = []
    for dev in dispositivos[:8]:
        _, out = _rodar(['smartctl', '-H', '-A', '-j', dev], 20)
        try:
            dados = json.loads(out or '{}')
        except ValueError:
            continue
        status = dados.get('smart_status', {}).get('passed')
        if status is None:
            continue
        discos_smart.append({'dispositivo': dev, 'modelo': dados.get('model_name'), 'ok': bool(status),
                             'temperatura': (dados.get('temperature') or {}).get('current'),
                             'horas_ligado': (dados.get('power_on_time') or {}).get('hours')})
    if not discos_smart:
        return {'disponivel': False, 'motivo': 'sem permissão para ler o SMART (exige root)'}
    return {'disponivel': True, 'discos': discos_smart}


def coletar():
    if psutil is None:
        return {}
    return {
        'coletado_em': int(time.time()),
        'sistema': _seguro(sistema),
        'swap': _seguro(swap),
        'discos': _seguro(discos),
        'interfaces': _seguro(interfaces),
        'dns': _seguro(dns),
        'hora_sincronizada': _seguro(hora_sincronizada),
        'servicos_falha': _seguro(servicos_com_falha),
        'reinicio_necessario': _seguro(reinicio_necessario),
        'atualizacoes_pendentes': _cacheado('atualizacoes', 1800, atualizacoes_pendentes),
        'processos': _seguro(processos),
        'bateria': _seguro(bateria),
        'ventoinhas': _seguro(ventoinhas),
        'smart': _cacheado('smart', 1800, smart),
    }


if __name__ == '__main__':
    coletar()
    time.sleep(1)
    print(json.dumps(coletar(), indent=2, ensure_ascii=False))
