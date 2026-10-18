import json
import socket
import struct
import threading

import netdiag

PING_OK = """PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
64 bytes from 1.1.1.1: icmp_seq=1 ttl=48 time=23.3 ms
64 bytes from 1.1.1.1: icmp_seq=2 ttl=48 time=24.4 ms
64 bytes from 1.1.1.1: icmp_seq=4 ttl=48 time=26.6 ms

--- 1.1.1.1 ping statistics ---
4 packets transmitted, 3 received, 25% packet loss, time 3004ms
rtt min/avg/max/mdev = 23.258/24.766/26.600/1.370 ms
"""
PING_FALHA = """PING 10.255.255.1 (10.255.255.1) 56(84) bytes of data.

--- 10.255.255.1 ping statistics ---
2 packets transmitted, 0 received, 100% packet loss, time 1040ms
"""
TRACEPATH = """ 1?: [LOCALHOST]                      pmtu 1500
 1:  192.168.76.1                                          0.915ms
 1:  192.168.76.1                                          0.795ms
 2:  192.168.76.1                                          0.888ms pmtu 1492
 2:  no reply
 3:  172.16.138.238                                        8.365ms
 4:  172.16.128.210                                        8.668ms
     Too many hops: pmtu 1492
"""
TRACEROUTE = """traceroute to 8.8.8.8 (8.8.8.8), 20 hops max, 60 byte packets
 1  192.168.1.1  0.512 ms  0.480 ms  0.470 ms
 2  100.64.0.1  25.1 ms  *  27.3 ms
 3  * * *
 4  8.8.8.8  30.0 ms  31.0 ms  29.0 ms
"""


def test_parse_ping_metrics():
    r = netdiag.parse_ping(PING_OK)
    assert r['enviados'] == 4 and r['recebidos'] == 3 and r['perda_pct'] == 25.0
    assert r['latencia_ms'] == 24.8 and r['jitter_ms'] == 1.7  # média de |24.4-23.3| e |26.6-24.4|
    falha = netdiag.parse_ping(PING_FALHA)
    assert falha['perda_pct'] == 100.0 and falha['latencia_ms'] is None


def test_ping_rejects_option_injection():
    assert netdiag.pingar('-f', 3, 1)['erro'] == 'alvo inválido'


def test_parse_traceroute_and_tracepath():
    t = netdiag.parse_traceroute(TRACEROUTE)
    assert [s['ip'] for s in t] == ['192.168.1.1', '100.64.0.1', None, '8.8.8.8']
    assert t[1]['perda_pct'] == 33.3 and t[1]['rtt_ms'] == 26.2 and t[2]['perda_pct'] == 100.0
    p = netdiag.parse_tracepath(TRACEPATH)
    assert [s['ttl'] for s in p] == [1, 2, 3, 4] and p[2]['ip'] == '172.16.138.238'
    assert netdiag.candidatos_primeiro_salto(p, '192.168.76.1') == ['172.16.138.238', '172.16.128.210']


def test_parse_mtr_json():
    saida = json.dumps({'report': {'hubs': [
        {'count': 1, 'host': '192.168.1.1', 'Loss%': 0.0, 'Avg': 0.6},
        {'count': 2, 'host': '???', 'Loss%': 100.0, 'Avg': 0.0},
        {'count': 3, 'host': '200.1.1.1', 'Loss%': 20.0, 'Avg': 80.5}]}})
    hops = netdiag.parse_mtr_json(saida)
    assert hops[1] == {'ttl': 2, 'ip': None, 'rtt_ms': None, 'perda_pct': 100.0}
    assert hops[2]['perda_pct'] == 20.0
    assert netdiag.parse_mtr_json('lixo') == []


def test_compare_routes_finds_worst_hop():
    base = [{'ttl': 1, 'ip': 'a', 'rtt_ms': 1, 'perda_pct': 0}, {'ttl': 2, 'ip': 'b', 'rtt_ms': 10, 'perda_pct': 0},
            {'ttl': 3, 'ip': 'c', 'rtt_ms': 20, 'perda_pct': 0}]
    pior = [{'ttl': 1, 'ip': 'a', 'rtt_ms': 1, 'perda_pct': 0}, {'ttl': 2, 'ip': 'b', 'rtt_ms': 120, 'perda_pct': 0},
            {'ttl': 3, 'ip': 'c', 'rtt_ms': 130, 'perda_pct': 0}]
    r = netdiag.comparar_rotas(base, pior)
    assert r['ttl'] == 2 and r['motivo'] == 'latência'
    cortada = [base[0], {'ttl': 2, 'ip': None, 'rtt_ms': None, 'perda_pct': 100}, {'ttl': 3, 'ip': None, 'rtt_ms': None, 'perda_pct': 100}]
    assert netdiag.comparar_rotas(base, cortada)['ttl'] == 2
    assert netdiag.comparar_rotas(base, base) is None
    assert netdiag.comparar_rotas(None, base) is None


def _servidor_dns(rcode=0, respostas=1):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))

    def responder():
        dados, origem = s.recvfrom(512)
        cab = dados[:2] + struct.pack('>HHHHH', 0x8180 | rcode, 1, respostas, 0, 0)
        s.sendto(cab + dados[12:], origem)
        s.close()
    threading.Thread(target=responder, daemon=True).start()
    return s.getsockname()[1]


def test_dns_query_success_and_failure():
    assert netdiag.consulta_dns('127.0.0.1', 'exemplo.com', 2, porta=_servidor_dns()) >= 0
    try:
        netdiag.consulta_dns('127.0.0.1', 'exemplo.com', 2, porta=_servidor_dns(rcode=3))  # NXDOMAIN
        assert False, 'deveria falhar'
    except ValueError as e:
        assert 'rcode 3' in str(e)


def test_sum_dns_rounds():
    rodadas = [[{'servidor': 'sistema', 'consultas': 2, 'falhas': 0, 'tempo_ms': 10.0}],
               [{'servidor': 'sistema', 'consultas': 2, 'falhas': 2, 'tempo_ms': None, 'erro': 'timeout'}]]
    r = netdiag.somar_dns(rodadas)[0]
    assert r['consultas'] == 4 and r['falhas'] == 2 and r['falha_pct'] == 50.0 and r['tempo_ms'] == 10.0


def test_cycle_measures_layers_and_traces_on_degradation(monkeypatch):
    cfg = {**netdiag.CONFIG, 'janela_s': 0.01, 'ping_intervalo_s': 1, 'externos': ['1.1.1.1'],
           'dns_nomes': ['a.com'], 'dns_servidores': ['1.1.1.1']}
    rotas = []
    saltos = [{'ttl': 1, 'ip': '192.168.1.1', 'rtt_ms': 1, 'perda_pct': 0}, {'ttl': 2, 'ip': '100.64.0.1', 'rtt_ms': 20, 'perda_pct': 0}]

    def rota(alvo):
        rotas.append(alvo)
        return {'ferramenta': 'fake', 'saltos': saltos}
    perda = {'1.1.1.1': 0.0}
    monkeypatch.setattr(netdiag, 'rota', rota)
    monkeypatch.setattr(netdiag, 'pingar', lambda alvo, q, i: {'enviados': 3, 'recebidos': 3, 'perda_pct': perda.get(alvo, 0.0),
                                                               'latencia_ms': 10.0, 'jitter_ms': 1.0})
    monkeypatch.setattr(netdiag, 'testar_dns', lambda n, s, t: [{'servidor': 'sistema', 'consultas': 1, 'falhas': 0, 'tempo_ms': 5.0}])
    m = netdiag.Monitor(cfg, gateway_fn=lambda: '192.168.1.1')
    itens = m.ciclo()
    assert [i['tipo'] for i in itens] == ['rota', 'rede'] and m.primeiro_salto == '100.64.0.1'
    janela = itens[1]
    assert [a['papel'] for a in janela['alvos']] == ['gateway', 'provedor', 'externo']
    assert janela['dns'][0]['servidor'] == 'sistema'
    assert m.rota_base == saltos

    perda['1.1.1.1'] = 40.0
    m.ultima_rota = 0
    itens = m.ciclo()
    assert itens[-1]['tipo'] == 'rota' and itens[-1]['motivo'] == 'degradacao'


def test_manual_provider(monkeypatch):
    monkeypatch.setenv('NOC_PROVEDOR', 'Fibra Local')
    assert netdiag.provedor() == {'nome': 'Fibra Local', 'origem': 'manual'}
    monkeypatch.delenv('NOC_PROVEDOR')
    monkeypatch.setenv('NOC_PROVEDOR_LOOKUP', '0')
    assert netdiag.provedor() is None
