"""Nome dos equipamentos da rede local, descoberto pelo sensor.

Três fontes, na ordem em que são tentadas:
  - DNS reverso (PTR) pelo resolvedor do sistema: o roteador costuma conhecer os nomes do DHCP;
  - NetBIOS (UDP 137, consulta de status): Windows, NAS e muitas impressoras;
  - mDNS (UDP 5353, consulta unicast direta ao equipamento): Apple, Android, impressoras, Chromecast.

A descoberta roda numa thread própria e o resultado fica em cache por MAC, para não atrasar o ciclo
do sensor nem repetir consultas a cada varredura: anotar() devolve o que já se sabe e agenda o resto.
"""
import queue
import random
import socket
import struct
import threading
import time

TIMEOUT_S = 1.5
VALIDADE_S = 6 * 3600        # nome encontrado: reconsulta a cada 6 h
VALIDADE_VAZIO_S = 1800      # nada encontrado: tenta de novo em 30 min
MAX_NOME = 120

_cache = {}                  # mac -> (nome, fonte, ip, consultado_em)
_pendentes = set()
_fila = queue.Queue()
_trava = threading.Lock()
_worker = None


def limpar_nome(nome):
    """Só caracteres imprimíveis, sem espaços nas pontas e sem o domínio local; None se vazio."""
    if not nome:
        return None
    texto = ''.join(c for c in str(nome) if c.isprintable()).strip().rstrip('.')
    for sufixo in ('.local', '.lan', '.home', '.localdomain', '.home.arpa'):
        if texto.lower().endswith(sufixo):
            texto = texto[:-len(sufixo)]
    return texto[:MAX_NOME] or None


# ---------------------------------------------------------------- DNS reverso
def nome_dns(ip):
    try:
        nome = socket.gethostbyaddr(ip)[0]
    except (OSError, UnicodeError):
        return None
    if not nome or nome == ip or nome.endswith('.in-addr.arpa'):
        return None
    return limpar_nome(nome)


# ---------------------------------------------------------------- NetBIOS
def _pacote_nbstat():
    ident = random.randint(0, 0xFFFF)
    # Nome "*" codificado em nível 1 (RFC 1002): 'CK' + 30 x 'A'; tipo NBSTAT (0x21), classe IN.
    nome = b'\x20' + b'CK' + b'A' * 30 + b'\x00'
    return ident, struct.pack('>HHHHHH', ident, 0, 1, 0, 0, 0) + nome + struct.pack('>HH', 0x21, 1)


def ler_nbstat(resposta, ident):
    """Primeiro nome único de estação (sufixo 0x00) da resposta NBSTAT."""
    if len(resposta) < 57 or struct.unpack('>H', resposta[:2])[0] != ident:
        return None
    pos = 12
    if resposta[pos] & 0xC0 == 0xC0:
        pos += 2
    else:
        while pos < len(resposta) and resposta[pos]:
            pos += resposta[pos] + 1
        pos += 1
    pos += 10                                  # tipo, classe, TTL, tamanho
    if pos >= len(resposta):
        return None
    quantidade = resposta[pos]
    pos += 1
    for _ in range(quantidade):
        registro = resposta[pos:pos + 18]
        if len(registro) < 18:
            break
        pos += 18
        sufixo, flags = registro[15], struct.unpack('>H', registro[16:18])[0]
        if sufixo == 0x00 and not flags & 0x8000:   # 0x8000 = nome de grupo (domínio/workgroup)
            return limpar_nome(registro[:15].decode('latin-1').rstrip())
    return None


def nome_netbios(ip, timeout=TIMEOUT_S):
    ident, pacote = _pacote_nbstat()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(pacote, (ip, 137))
            resposta, origem = s.recvfrom(2048)
    except OSError:
        return None
    return ler_nbstat(resposta, ident) if origem[0] == ip else None


# ---------------------------------------------------------------- mDNS
def _nome_reverso(ip):
    return '.'.join(reversed(ip.split('.'))) + '.in-addr.arpa'


def _codificar(nome):
    return b''.join(bytes([len(p)]) + p.encode('ascii') for p in nome.split('.')) + b'\x00'


def _ler_nome(pacote, pos, saltos=0):
    """Nome DNS com compressão (RFC 1035 4.1.4). Devolve (nome, posição após o nome)."""
    partes = []
    fim = None
    while True:
        if pos >= len(pacote) or saltos > 20:
            raise ValueError('nome DNS inválido')
        tamanho = pacote[pos]
        if tamanho & 0xC0 == 0xC0:
            if pos + 1 >= len(pacote):
                raise ValueError('ponteiro DNS truncado')
            if fim is None:
                fim = pos + 2
            pos = ((tamanho & 0x3F) << 8) | pacote[pos + 1]
            saltos += 1
            continue
        if tamanho == 0:
            return '.'.join(partes), (fim if fim is not None else pos + 1)
        partes.append(pacote[pos + 1:pos + 1 + tamanho].decode('utf-8', 'replace'))
        pos += tamanho + 1


def ler_ptr(resposta, ident, pergunta):
    """Alvo do primeiro registro PTR para `pergunta` numa resposta DNS/mDNS."""
    if len(resposta) < 12:
        return None
    rid, flags, qd, an = struct.unpack('>HHHH', resposta[:8])
    # mDNS responde com ID 0 quando ignora o da pergunta; DNS comum devolve o mesmo ID.
    if rid not in (ident, 0) or not flags & 0x8000 or not an:
        return None
    try:
        pos = 12
        for _ in range(qd):
            _, pos = _ler_nome(resposta, pos)
            pos += 4
        for _ in range(an):
            nome, pos = _ler_nome(resposta, pos)
            tipo, _, _, tamanho = struct.unpack('>HHIH', resposta[pos:pos + 10])
            pos += 10
            if tipo == 12 and nome.lower() == pergunta.lower():
                return limpar_nome(_ler_nome(resposta, pos)[0])
            pos += tamanho
    except (ValueError, struct.error):
        return None
    return None


def nome_mdns(ip, timeout=TIMEOUT_S):
    pergunta = _nome_reverso(ip)
    ident = random.randint(1, 0xFFFF)
    # Bit QU (0x8000 na classe): pede resposta unicast, de volta para a porta de origem.
    pacote = struct.pack('>HHHHHH', ident, 0, 1, 0, 0, 0) + _codificar(pergunta) + struct.pack('>HH', 12, 0x8001)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(pacote, (ip, 5353))
            resposta, origem = s.recvfrom(4096)
    except OSError:
        return None
    return ler_ptr(resposta, ident, pergunta) if origem[0] == ip else None


FONTES = (('dns', nome_dns), ('netbios', nome_netbios), ('mdns', nome_mdns))


def descobrir(ip):
    """(nome, fonte) pela primeira fonte que responder; (None, None) se nenhuma."""
    for fonte, consulta in FONTES:
        nome = consulta(ip)
        if nome:
            return nome, fonte
    return None, None


# ---------------------------------------------------------------- cache e thread
def _trabalhar():
    while True:
        mac, ip = _fila.get()
        try:
            nome, fonte = descobrir(ip)
        except Exception:
            nome, fonte = None, None
        with _trava:
            _cache[mac] = (nome, fonte, ip, time.time())
            _pendentes.discard(mac)
        _fila.task_done()


def _garantir_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_trabalhar, name='nomes-rede', daemon=True)
        _worker.start()


def anotar(dispositivos, agora=None):
    """Põe 'hostname' e 'hostname_fonte' em cada dispositivo com o que o cache já sabe e agenda
    a consulta dos que faltam ou venceram (o nome aparece na varredura seguinte)."""
    agora = agora or time.time()
    agendar = []
    with _trava:
        for d in dispositivos:
            mac, ip = d.get('mac'), d.get('ip')
            if not mac or not ip:
                continue
            nome, fonte, ip_cache, quando = _cache.get(mac, (None, None, None, 0))
            if nome:
                d['hostname'], d['hostname_fonte'] = nome, fonte
            validade = VALIDADE_S if nome else VALIDADE_VAZIO_S
            if (ip_cache != ip or agora - quando > validade) and mac not in _pendentes:
                _pendentes.add(mac)
                agendar.append((mac, ip))
    if agendar:
        _garantir_worker()
        for item in agendar:
            _fila.put(item)
    return dispositivos
