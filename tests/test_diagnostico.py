"""Motor de diagnóstico: cenários simulados (puros) e o fluxo pela API."""
import time

import app as central
import database
import diagnostico
import starlink

LIM = diagnostico.limiares()


def alvo(papel, ip, perda=0.0, lat=10.0, jitter=1.0):
    return {'papel': papel, 'alvo': ip, 'enviados': 30, 'recebidos': round(30 * (1 - perda / 100)),
            'perda_pct': perda, 'latencia_ms': lat, 'jitter_ms': jitter}


def janela(gw=0.0, prov=0.0, ext=0.0, dns_sistema=0.0, dns_publico=0.0, ts=None, gw_lat=1.0, prov_lat=8.0, sem_prov=False):
    ts = ts or time.time()
    alvos = [alvo('gateway', '192.168.1.1', gw, gw_lat)]
    if not sem_prov:
        alvos.append(alvo('provedor', '100.64.0.1', prov, prov_lat))
    alvos += [alvo('externo', '1.1.1.1', ext, 30.0), alvo('externo', '8.8.8.8', ext, 32.0)]
    return {'tipo': 'rede', 'inicio': ts - 60, 'coletado_em': ts, 'gateway': '192.168.1.1', 'alvos': alvos,
            'dns': [{'servidor': 'sistema', 'consultas': 6, 'falhas': 0, 'falha_pct': dns_sistema, 'tempo_ms': 15.0},
                    {'servidor': '1.1.1.1', 'consultas': 6, 'falhas': 0, 'falha_pct': dns_publico, 'tempo_ms': 20.0}]}


def chaves(resultado):
    return sorted(a['chave'] for a in resultado['achados'])


# ---------------------------------------------------------------- cenários puros
def test_healthy_site_has_no_findings():
    r = diagnostico.avaliar(janela(), starlink._coletar_mock('normal'), LIM)
    assert r['achados'] == []
    assert r['camadas'] == {'lan': 'ok', 'starlink': 'ok', 'provedor': 'ok', 'dns': 'ok'}


def test_starlink_obstructed():
    sl = starlink._coletar_mock('obstruida')
    r = diagnostico.avaliar(janela(prov=12.0, ext=12.0), sl, LIM)
    assert chaves(r) == ['starlink_obstruida']  # a obstrução explica a perda: sem alerta de provedor
    obs = r['achados'][0]
    assert obs['confianca'] == 'alta' and obs['evidencias']['obstrucao_pct'] == 6.0
    assert r['camadas']['starlink'] == 'critico' and r['camadas']['provedor'] == 'desconhecido'  # 6% = 3x o limiar


def test_starlink_misaligned():
    r = diagnostico.avaliar(janela(), starlink._coletar_mock('desalinhada'), LIM)
    assert chaves(r) == ['starlink_desalinhada']
    assert r['achados'][0]['confianca'] == 'alta' and r['achados'][0]['evidencias']['desvio_graus'] > LIM['sl_desvio_graus']


def test_starlink_degraded_and_native_alert():
    r = diagnostico.avaliar(janela(), starlink._coletar_mock('degradada'), LIM)
    assert chaves(r) == ['starlink_alerta', 'starlink_degradada']
    alerta = next(a for a in r['achados'] if a['chave'] == 'starlink_alerta')
    assert 'temperatura' in alerta['descricao']


def test_unstable_provider():
    r = diagnostico.avaliar(janela(prov=15.0, ext=15.0), None, LIM)
    assert chaves(r) == ['provedor']
    p = r['achados'][0]
    assert p['confianca'] == 'alta' and p['evidencias']['gateway_perda_pct'] == 0.0 and p['evidencias']['provedor_perda_pct'] == 15.0
    assert r['camadas'] == {'lan': 'ok', 'starlink': None, 'provedor': 'alerta', 'dns': 'desconhecido'}


def test_provider_without_first_hop_has_low_confidence():
    r = diagnostico.avaliar(janela(ext=20.0, sem_prov=True), None, LIM)
    assert chaves(r) == ['provedor'] and r['achados'][0]['confianca'] == 'baixa'


def test_one_bad_external_is_not_a_provider_problem():
    j = janela()
    j['alvos'][-1]['perda_pct'] = 40.0  # só o 8.8.8.8
    assert diagnostico.avaliar(j, None, LIM)['achados'] == []


def test_dns_failing_with_icmp_ok():
    r = diagnostico.avaliar(janela(dns_sistema=100.0), None, LIM)
    assert chaves(r) == ['dns'] and 'públicos respondem' in r['achados'][0]['detalhe']
    total = diagnostico.avaliar(janela(dns_sistema=100.0, dns_publico=100.0), None, LIM)
    assert 'todos' in total['achados'][0]['detalhe'] and total['achados'][0]['confianca'] == 'média'


def test_dns_not_blamed_when_internet_is_down():
    r = diagnostico.avaliar(janela(prov=100.0, ext=100.0, dns_sistema=100.0, dns_publico=100.0), None, LIM)
    assert chaves(r) == ['provedor'] and r['camadas']['dns'] == 'desconhecido'


def test_lan_problem_hides_upper_layers():
    r = diagnostico.avaliar(janela(gw=40.0, prov=40.0, ext=40.0, dns_sistema=100.0), None, LIM)
    assert chaves(r) == ['lan']
    assert r['achados'][0]['confianca'] == 'alta'
    assert r['camadas'] == {'lan': 'alerta', 'starlink': None, 'provedor': 'desconhecido', 'dns': 'desconhecido'}


def test_missing_gateway_is_critical_lan():
    j = janela()
    j['sem_gateway'] = True
    r = diagnostico.avaliar(j, None, LIM)
    assert chaves(r) == ['sem_gateway'] and r['camadas']['lan'] == 'critico'


def test_correlation_groups_sites_of_same_provider():
    agora = time.time()
    ativos = [{'sensor_mac': f'S{i}', 'provedor': 'Vivo', 'chave': 'provedor', 'inicio': agora + i * 30} for i in range(3)]
    ativos.append({'sensor_mac': 'X', 'provedor': 'Claro', 'chave': 'provedor', 'inicio': agora})
    ativos.append({'sensor_mac': 'Y', 'provedor': 'Vivo', 'chave': 'starlink_obstruida', 'inicio': agora})
    grupos = diagnostico.correlacionar(ativos, LIM)
    assert list(grupos) == ['Vivo'] and [m['sensor_mac'] for m in grupos['Vivo']] == ['S0', 'S1', 'S2']
    espalhados = [{'sensor_mac': f'S{i}', 'provedor': 'Vivo', 'chave': 'provedor', 'inicio': agora + i * 3600} for i in range(3)]
    assert diagnostico.correlacionar(espalhados, LIM) == {}
    assert 'Vivo' in diagnostico.correlacionar(espalhados[:2], LIM, provedores_com_grupo=['vivo'])


def test_field_tech_message():
    sl = starlink._coletar_mock('obstruida')
    achado = diagnostico.avaliar(janela(), sl, LIM)['achados'][0]
    texto = diagnostico.mensagem({**achado, 'inicio': time.time()}, 'Loja Centro')
    assert texto.startswith('Starlink do site Loja Centro com 6% de obstrução desde ')
    assert 'árvores ou estruturas' in texto


# ---------------------------------------------------------------- fluxo pela API
MAC = 'AA:00:00:00:30:01'


def _sensor(mac=MAC, nome='Loja Centro', provedor=None, cliente=None):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, status, provedor, cliente_id, last_seen) VALUES (?, ?, 'online', ?, ?, CURRENT_TIMESTAMP)",
                 (mac, nome, provedor, cliente))
    conn.commit(); conn.close()


def _enviar(client, itens, mac=MAC, **extra):
    r = client.post('/api/v2/medicoes', json={'mac_id': mac, 'itens': itens, **extra})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _linhas(sql, params=()):
    conn = database.get_db()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _telegrams():
    return [r['message'] for r in _linhas('SELECT message FROM telegram_outbox')]


def test_diagnosis_opens_after_confirmation_and_resolves(client):
    _sensor()
    agora = time.time()
    sl = {'tipo': 'starlink', **starlink._coletar_mock('obstruida'), 'coletado_em': agora - 170}
    _enviar(client, [sl, janela(ts=agora - 120)])
    ativo = _linhas("SELECT * FROM diagnosticos WHERE estado='ativo'")
    assert [d['chave'] for d in ativo] == ['starlink_obstruida'] and not _telegrams()  # 1 janela: ainda não confirma
    _enviar(client, [janela(ts=agora - 60)])
    msgs = _telegrams()
    assert len(msgs) == 1 and 'STARLINK OBSTRUÍDA' in msgs[0] and '6% de obstrução' in msgs[0]

    normal = {'tipo': 'starlink', **starlink._coletar_mock('normal'), 'coletado_em': agora - 30}
    _enviar(client, [normal, janela(ts=agora - 20), janela(ts=agora - 10)])
    d = _linhas('SELECT * FROM diagnosticos')[0]
    assert d['estado'] == 'resolvido' and d['fim'] is not None
    assert any('NORMALIZADO' in m for m in _telegrams())


def test_backfilled_windows_go_to_timeline_without_telegram(client):
    _sensor()
    velho = time.time() - 3 * 3600
    _enviar(client, [janela(ts=velho + i * 60, dns_sistema=100.0) for i in range(3)])
    d = _linhas("SELECT * FROM diagnosticos WHERE chave='dns'")[0]
    assert d['estado'] == 'ativo' and abs(d['inicio'] - (velho - 60)) < 1  # horário original da medição
    assert d['notificado'] == 2 and not _telegrams()
    assert any('após reconexão' in r['detalhes'] for r in _linhas("SELECT detalhes FROM logs_ia WHERE tipo_evento='Diagnóstico de rede'"))


def test_provider_outage_on_many_sites_becomes_one_alert(client):
    macs = [f'AA:00:00:00:31:0{i}' for i in range(3)]
    for i, mac in enumerate(macs):
        _sensor(mac, f'Site {i}', provedor='Vivo Fibra')
    agora = time.time()
    for n in range(3):  # 3 janelas: provedor confirma na 3ª
        for mac in macs:
            _enviar(client, [janela(ts=agora - 200 + n * 60, prov=30.0, ext=30.0)], mac=mac)
    grupo = _linhas("SELECT * FROM diagnosticos WHERE chave='queda_provedor'")
    assert len(grupo) == 1 and grupo[0]['provedor'] == 'Vivo Fibra'
    individuais = _linhas("SELECT * FROM diagnosticos WHERE chave='provedor'")
    assert len(individuais) == 3 and all(d['grupo_id'] == grupo[0]['id'] for d in individuais)
    msgs = _telegrams()
    assert len(msgs) == 1 and 'POSSÍVEL QUEDA DO PROVEDOR' in msgs[0] and '3 sites' in msgs[0]


def test_provider_name_from_edge_respects_manual(client, login):
    _sensor()
    _enviar(client, [], provedor={'nome': 'Starlink', 'origem': 'antena'})
    assert _linhas('SELECT provedor, provedor_origem FROM sensores')[0] == {'provedor': 'Starlink', 'provedor_origem': 'edge-antena'}
    master = login()
    assert master.post(f'/api/v2/sensor/{MAC}/provedor', json={'provedor': 'Link dedicado X'}).status_code == 200
    with client.session_transaction() as s:
        s.clear()
    _enviar(client, [], provedor={'nome': 'Outro', 'origem': 'ip'})
    assert _linhas('SELECT provedor FROM sensores')[0]['provedor'] == 'Link dedicado X'


def test_invalid_items_are_dropped_not_rejected(client):
    _sensor()
    r = _enviar(client, [{'tipo': 'rede'}, {'tipo': 'xpto', 'coletado_em': time.time()},
                         {'tipo': 'rede', 'coletado_em': time.time() - 30 * 86400}, janela()])
    assert r == {'status': 'OK', 'aceitos': 1, 'descartados': 3}


def test_unknown_sensor_is_404(client):
    assert client.post('/api/v2/medicoes', json={'mac_id': 'ZZ:00', 'itens': []}).status_code == 404


def test_sensor_diagnosis_endpoint_and_tenant_scope(client, login):
    _sensor(cliente=2)
    _sensor('AA:00:00:00:30:99', 'Outro cliente', cliente=3)
    agora = time.time()
    _enviar(client, [{'tipo': 'starlink', **starlink._coletar_mock('desalinhada'), 'coletado_em': agora - 5},
                     {'tipo': 'rota', 'coletado_em': agora - 5, 'motivo': 'degradacao', 'ferramenta': 'mtr',
                      'saltos': [{'ttl': 1, 'ip': '192.168.1.1', 'rtt_ms': 1, 'perda_pct': 0}],
                      'pior_salto': {'ttl': 2, 'ip': '100.64.0.1', 'motivo': 'perda'}},
                     janela(ts=agora)])
    cli = login(2, 'Cliente')
    dados = cli.get(f'/api/v2/sensor/{MAC}/diagnostico').get_json()
    assert dados['camadas']['starlink'] == 'alerta' and dados['starlink']['firmware'] == 'mock-2026.10.0'
    assert [d['chave'] for d in dados['ativos']] == ['starlink_desalinhada']
    assert dados['rota']['pior_salto']['ttl'] == 2 and len(dados['serie']) == 4
    assert cli.get('/api/v2/sensor/AA:00:00:00:30:99/diagnostico').status_code == 403
    assert [d['sensor_mac'] for d in cli.get('/api/v2/diagnosticos').get_json()['ativos']] == [MAC]


def test_resent_batch_is_ignored(client):
    _sensor()
    agora = time.time()
    lote = [{'tipo': 'starlink', **starlink._coletar_mock('obstruida'), 'coletado_em': agora - 100},
            {'tipo': 'rota', 'coletado_em': agora - 90, 'motivo': 'periodica', 'ferramenta': 'mtr', 'saltos': []},
            janela(ts=agora - 60)]
    _enviar(client, lote)
    _enviar(client, lote)  # o agente desistiu de esperar e mandou de novo
    assert len(_linhas('SELECT id FROM medicoes_rede')) == 6
    assert len(_linhas('SELECT id FROM starlink_medicoes')) == 1 and len(_linhas('SELECT id FROM rotas_rede')) == 1
    assert _linhas("SELECT janelas FROM diagnosticos WHERE chave='starlink_obstruida'")[0]['janelas'] == 1
    assert not _telegrams()  # uma janela só: ainda não confirmou
