"""Levantamento de rede (fase 1): marca pelo MAC, nome dos equipamentos e exportação JSON/XLSX."""
import io
import json
import struct

import openpyxl

import database
import nomes_rede
import oui

MAC_SENSOR = 'AA:BB:CC:00:00:01'


# ===== Marca pelo MAC (oui.py) =====
def test_fabricante_pelo_registro_ieee():
    assert oui.fabricante('00:1a:3f:12:34:56') == ('Intelbras', 'ieee')
    assert oui.fabricante('B8-27-EB-11-22-33') == ('Raspberry Pi Foundation', 'ieee')
    # MA-M (28 bits) vale mais que o bloco de 24 bits do IEEE que ele subdivide.
    assert oui.fabricante('0C:CC:47:0A:BB:CC') == ('Shenzhen Jooan Technology Co., Ltd', 'ieee')


def test_fabricante_mac_privado_multicast_e_invalido():
    assert oui.fabricante('DA:A1:19:00:00:01') == (oui.MAC_PRIVADO, 'mac_privado')
    assert oui.mac_privado('62:49:32:2b:71:fe') and not oui.mac_privado('00:1A:3F:00:00:01')
    assert oui.fabricante('FF:FF:FF:FF:FF:FF') == (None, None)
    assert oui.fabricante('01:00:5E:00:00:01') == (None, None)
    assert oui.fabricante('nao-e-mac') == (None, None) and oui.normalizar('00:11:22') is None


def test_marca_curta():
    assert oui.marca('TP-LINK TECHNOLOGIES CO.,LTD.') == 'TP-Link'
    assert oui.marca('Hangzhou Hikvision Digital Technology Co.,Ltd.') == 'Hikvision'
    assert oui.marca('Routerboard.com') == 'MikroTik'
    assert oui.marca('Katun Corporation') == 'Katun'
    assert oui.marca(None) is None


def test_base_sem_entradas_genericas_do_ieee():
    base = oui._carregar()
    assert len(base) > 50000
    assert 'IEEE Registration Authority' not in base.values()


# ===== Nome dos equipamentos (nomes_rede.py, roda no sensor) =====
def test_limpar_nome():
    assert nomes_rede.limpar_nome('impressora-sala.local.') == 'impressora-sala'
    assert nomes_rede.limpar_nome('nas.lan') == 'nas'
    assert nomes_rede.limpar_nome('tv\x00\x07 ') == 'tv'
    assert nomes_rede.limpar_nome('') is None and nomes_rede.limpar_nome(None) is None
    assert len(nomes_rede.limpar_nome('x' * 500)) == nomes_rede.MAX_NOME


def _resposta_nbstat(ident, nomes):
    corpo = struct.pack('>HHHHHH', ident, 0x8400, 0, 1, 0, 0)
    corpo += b'\x20' + b'CK' + b'A' * 30 + b'\x00' + struct.pack('>HHIH', 0x21, 1, 0, 1 + 18 * len(nomes) + 46)
    corpo += bytes([len(nomes)])
    for nome, sufixo, flags in nomes:
        corpo += nome.ljust(15).encode('latin-1') + bytes([sufixo]) + struct.pack('>H', flags)
    return corpo + b'\x00' * 46


def test_ler_nbstat_escolhe_nome_da_estacao():
    resposta = _resposta_nbstat(0x1234, [('WORKGROUP', 0x00, 0x8400), ('SERVIDOR-NAS', 0x00, 0x0400), ('SERVIDOR-NAS', 0x20, 0x0400)])
    assert nomes_rede.ler_nbstat(resposta, 0x1234) == 'SERVIDOR-NAS'
    assert nomes_rede.ler_nbstat(resposta, 0x9999) is None
    assert nomes_rede.ler_nbstat(b'\x00' * 10, 0) is None


def test_ler_ptr_com_compressao():
    pergunta = nomes_rede._nome_reverso('192.168.0.20')
    q = nomes_rede._codificar(pergunta)
    # Resposta com a pergunta e um PTR cujo nome é um ponteiro para a pergunta (compressão).
    rdata = nomes_rede._codificar('MacBook-da-Recepcao.local')
    pacote = struct.pack('>HHHHHH', 0, 0x8400, 1, 1, 0, 0) + q + struct.pack('>HH', 12, 1)
    pacote += b'\xc0\x0c' + struct.pack('>HHIH', 12, 1, 120, len(rdata)) + rdata
    assert nomes_rede.ler_ptr(pacote, 4321, pergunta) == 'MacBook-da-Recepcao'
    assert nomes_rede.ler_ptr(pacote, 4321, 'outra.in-addr.arpa') is None
    assert nomes_rede.ler_ptr(pacote[:20], 4321, pergunta) is None
    laco = struct.pack('>HHHHHH', 0, 0x8400, 0, 1, 0, 0) + b'\xc0\x0c'  # ponteiro para si mesmo
    assert nomes_rede.ler_ptr(laco, 0, pergunta) is None


def test_anotar_usa_cache_e_agenda_o_resto(monkeypatch):
    consultas = []

    def falso(ip):
        consultas.append(ip)
        return ('camera-portaria', 'mdns') if ip == '10.0.0.5' else (None, None)

    monkeypatch.setattr(nomes_rede, 'descobrir', falso)
    nomes_rede._cache.clear()
    dispositivos = [{'ip': '10.0.0.5', 'mac': '00:1A:3F:00:00:05'}, {'ip': '10.0.0.6', 'mac': '00:1A:3F:00:00:06'}]
    nomes_rede.anotar(dispositivos)
    assert 'hostname' not in dispositivos[0]            # primeira varredura só agenda
    nomes_rede._fila.join()
    segunda = [dict(d) for d in dispositivos]
    nomes_rede.anotar(segunda)
    assert segunda[0]['hostname'] == 'camera-portaria' and segunda[0]['hostname_fonte'] == 'mdns'
    assert 'hostname' not in segunda[1]
    nomes_rede._fila.join()
    assert sorted(consultas) == ['10.0.0.5', '10.0.0.6']  # dentro da validade, não consulta de novo
    nomes_rede._cache.clear()


# ===== Central: recepção e exportação =====
def _sensor(cliente_id=2):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, ip_sensor, ip_gateway, rede_cidr, scan_rede, interface_mac, cliente_id) "
                 "VALUES (?, 'Escritório Centro', '192.168.0.10', '192.168.0.1', '192.168.0.0/24', '192.168.0.0/24', 'B8:27:EB:00:00:10', ?)",
                 (MAC_SENSOR, cliente_id))
    conn.commit()
    conn.close()


LISTA = [
    {'ip': '192.168.0.1', 'mac': 'F4:F2:6D:AA:BB:CC', 'status': 'online', 'latencia': 1.2, 'neighbor_state': 'REACHABLE',
     'fabricante': 'Desconhecido', 'hostname': 'roteador', 'hostname_fonte': 'dns'},
    {'ip': '192.168.0.30', 'mac': '00:1A:3F:00:00:30', 'status': 'sem_icmp', 'neighbor_state': 'STALE',
     'hostname': '=HYPERLINK("http://x")<script>', 'hostname_fonte': 'netbios'},
    {'ip': '192.168.0.40', 'mac': 'DA:A1:19:00:00:40', 'status': 'online', 'latencia': 8, 'hostname_fonte': 'inventada'},
]


def test_recebe_dispositivos_com_marca_e_nome(client, login):
    _sensor()
    assert client.post('/api/v2/atualizar_dispositivos', json={'mac_id': MAC_SENSOR, 'lista': LISTA}).status_code == 200
    conn = database.get_db()
    linhas = {r['ip']: dict(r) for r in conn.execute('SELECT * FROM dispositivos WHERE sensor_mac = ?', (MAC_SENSOR,)).fetchall()}
    conn.close()
    assert linhas['192.168.0.1']['fabricante'] == 'TP-Link'
    assert linhas['192.168.0.1']['fabricante_registro'] == 'TP-LINK TECHNOLOGIES CO.,LTD.'
    assert linhas['192.168.0.1']['nome_custom'] == 'Gateway / Roteador'
    assert linhas['192.168.0.30']['hostname'] == '=HYPERLINK("http://x")script'   # limpo, não recusado
    assert linhas['192.168.0.30']['nome_custom'] is None
    assert linhas['192.168.0.40']['fabricante'] == oui.MAC_PRIVADO and linhas['192.168.0.40']['hostname_fonte'] is None
    assert linhas['192.168.0.40']['visto_em'].endswith('Z')


def test_exporta_json_para_ia(client, login):
    _sensor()
    client.post('/api/v2/atualizar_dispositivos', json={'mac_id': MAC_SENSOR, 'lista': LISTA})
    resposta = login(1).get(f'/api/v2/levantamento/{MAC_SENSOR}/json')
    assert resposta.status_code == 200 and resposta.mimetype == 'application/json'
    assert 'attachment; filename="levantamento-Escritorio-Centro-' in resposta.headers['Content-Disposition']
    dados = json.loads(resposta.data)
    assert dados['formato'] == 'nexopulse.levantamento-rede' and dados['versao_formato'] == 1
    assert dados['escopo']['rede'] == '192.168.0.0/24' and dados['escopo']['limitacoes']
    por_ip = {d['ip']: d for d in dados['dispositivos']}
    assert list(por_ip) == ['192.168.0.1', '192.168.0.10', '192.168.0.30', '192.168.0.40']  # sensor entra, ordem por IP
    gw = por_ip['192.168.0.1']
    assert gw['papel'] == 'gateway' and gw['marca'] == {'valor': 'TP-Link', 'razao_social': 'TP-LINK TECHNOLOGIES CO.,LTD.',
                                                        'fonte': 'Registro IEEE (prefixo do MAC)', 'confianca': 'alta'}
    assert gw['nome'] == {'valor': 'roteador', 'fonte': 'DNS reverso'} and gw['apelido'] is None
    assert por_ip['192.168.0.10']['papel'] == 'sensor' and por_ip['192.168.0.10']['marca']['valor'] == 'Raspberry Pi'
    privado = por_ip['192.168.0.40']
    assert privado['mac_privado'] and privado['marca']['valor'] is None and privado['marca']['confianca'] == 'nenhuma'
    assert por_ip['192.168.0.30']['latencia_ms'] is None
    assert dados['resumo']['total'] == 4 and dados['resumo']['mac_privado'] == 1
    assert dados['resumo']['por_marca']['Intelbras'] == 1


def test_exporta_ocultando_nomes(client, login):
    _sensor()
    client.post('/api/v2/atualizar_dispositivos', json={'mac_id': MAC_SENSOR, 'lista': LISTA})
    dados = json.loads(login(1).get(f'/api/v2/levantamento/{MAC_SENSOR}/json?ocultar_nomes=1').data)
    assert dados['nomes_ocultos'] is True
    assert all(d['nome']['valor'] is None and d['apelido'] is None for d in dados['dispositivos'])
    assert '"roteador"' not in json.dumps(dados)


def test_exporta_planilha_sem_formulas(client, login):
    _sensor()
    client.post('/api/v2/atualizar_dispositivos', json={'mac_id': MAC_SENSOR, 'lista': LISTA})
    resposta = login(1).get(f'/api/v2/levantamento/{MAC_SENSOR}/xlsx')
    assert resposta.status_code == 200
    livro = openpyxl.load_workbook(io.BytesIO(resposta.data))
    assert livro.sheetnames == ['Dispositivos', 'Resumo', 'Sobre']
    aba = livro['Dispositivos']
    assert aba['A1'].value == 'IP' and aba['F2'].value == 'TP-Link' and aba.freeze_panes == 'A2'
    nomes = [c for c in aba['D'] if c.value and str(c.value).startswith('=')]
    assert nomes and all(c.data_type == 's' for c in nomes)   # texto, nunca fórmula
    assert livro['Resumo']['B3'].value == 'Escritório Centro'


def test_exportacao_respeita_cliente_e_formato(client, login):
    _sensor(cliente_id=2)
    assert client.get(f'/api/v2/levantamento/{MAC_SENSOR}/json').status_code == 403          # sem sessão
    assert login(3, 'Cliente').get(f'/api/v2/levantamento/{MAC_SENSOR}/json').status_code == 403
    assert login(2, 'Cliente').get(f'/api/v2/levantamento/{MAC_SENSOR}/xlsx').status_code == 200
    assert login(1).get(f'/api/v2/levantamento/{MAC_SENSOR}/csv').status_code == 400
    assert login(1).get('/api/v2/levantamento/00:00:00:00:00:99/json').status_code == 404
