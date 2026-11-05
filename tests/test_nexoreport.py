import json
from datetime import datetime

import pytest

import acessos
import database
import hub
import nexoreport

EMPRESA = 2
UFV, ESC = 'AA:00:00:00:50:01', 'AA:00:00:00:50:02'
OUT_INI, OUT_FIM = hub.limites_do_mes(2026, 10)
FECHAMENTO = datetime(2026, 11, 1, 0, 31, tzinfo=hub.FUSO_BR).timestamp()
H = 3600


def t(dia, hora, minuto=0):
    return datetime(2026, 10, dia, hora, minuto, tzinfo=hub.FUSO_BR).timestamp()


@pytest.fixture(autouse=True)
def _limpa(monkeypatch):
    monkeypatch.setattr(nexoreport, 'PRIMEIRO_MES', (2026, 1))
    conn = database.get_db()
    conn.execute('DELETE FROM relatorios_mensais')
    conn.execute('DELETE FROM empresa_aplicativos')
    conn.commit(); conn.close()
    yield


def _base(conn, contratar=True, meta=None):
    for mac, nome, prov in ((UFV, 'UFV Alto Paraná', 'Starlink'), (ESC, 'Escritório', 'Fibra X')):
        conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id, provedor, ip_gateway, ip_sensor, status) VALUES (?, ?, ?, ?, '192.168.0.1', '192.168.0.18', 'online')",
                     (mac, nome, EMPRESA, prov))
        # Medições do mês inteiro (o site já estava instalado em 01/10).
        for dia in (1, 10, 20, 31):
            conn.execute("INSERT INTO medicoes_rede (sensor_mac, coletado_em, tipo, papel, alvo, latencia_ms, jitter_ms, perda_pct, amostras) "
                         "VALUES (?, ?, 'icmp', 'externo', '1.1.1.1', ?, 2, 0.5, 10)", (mac, t(dia, 1), 30 + dia))
    if contratar:
        acessos.definir_contrato(conn, EMPRESA, 'report', True)
    if meta is not None:
        conn.execute('UPDATE clientes SET meta_disponibilidade = ? WHERE id = ?', (meta, EMPRESA))


def _queda(conn, mac, ini, dur_min, grupo=None):
    return conn.execute("INSERT INTO diagnosticos (sensor_mac, chave, camada, causa, gravidade, confianca, inicio, fim, estado, grupo_id) "
                        "VALUES (?, 'sem_comunicacao', 'comunicacao', 'Site sem comunicação', 'Crítica', 'alta', ?, ?, 'resolvido', ?) RETURNING id",
                        (mac, ini, ini + dur_min * 60, grupo)).fetchone()['id']


def _diag(conn, mac, chave, camada, ini, fim, confianca='alta', mensagem='msg', provedor=None):
    return conn.execute("INSERT INTO diagnosticos (sensor_mac, provedor, chave, camada, causa, gravidade, confianca, inicio, fim, estado, mensagem) "
                        "VALUES (?, ?, ?, ?, 'x', 'Alerta', ?, ?, ?, 'resolvido', ?) RETURNING id",
                        (mac, provedor, chave, camada, confianca, ini, fim, mensagem)).fetchone()['id']


# ------------------------------------------------------------------ período fechado
def test_closed_month_availability_and_target(client):
    conn = database.get_db()
    _base(conn, meta=99.9)
    _queda(conn, UFV, t(3, 16, 42), 60)
    d = hub.disponibilidade_periodo(conn, EMPRESA, OUT_INI, OUT_FIM, 99.9)
    conn.close()
    assert d['quedas'] == 1 and d['fora_s'] == 3600
    assert d['por_site'][UFV]['pct'] == 99.8 and d['por_site'][UFV]['situacao'] == 'atencao'
    assert d['por_site'][ESC]['pct'] == 100.0 and d['por_site'][ESC]['situacao'] == 'ok'


def test_site_installed_after_the_month_is_left_out(client):
    conn = database.get_db()
    _base(conn)
    novo = 'AA:00:00:00:50:09'
    conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id) VALUES (?, 'Novo', ?)", (novo, EMPRESA))
    conn.execute("INSERT INTO medicoes_rede (sensor_mac, coletado_em, tipo, papel, alvo) VALUES (?, ?, 'icmp', 'externo', 'x')",
                 (novo, OUT_FIM + 2 * 86400))
    comeco, _, _ = hub.janelas_periodo(conn, EMPRESA, OUT_INI, OUT_FIM)
    conn.close()
    assert novo not in comeco and UFV in comeco


def test_site_installed_mid_month_counts_from_first_measurement(client):
    conn = database.get_db()
    meio = 'AA:00:00:00:50:08'
    conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id) VALUES (?, 'Meio', ?)", (meio, EMPRESA))
    conn.execute("INSERT INTO medicoes_rede (sensor_mac, coletado_em, tipo, papel, alvo) VALUES (?, ?, 'icmp', 'externo', 'x')", (meio, t(15, 12)))
    comeco, _, _ = hub.janelas_periodo(conn, EMPRESA, OUT_INI, OUT_FIM)
    conn.close()
    assert comeco[meio] == t(15, 12)


def test_situation_bands():
    assert hub.situacao(99.7, 99.7) == 'ok'
    assert hub.situacao(98.8, 99.7) == 'atencao'
    assert hub.situacao(98.6, 99.7) == 'critico'
    assert hub.situacao(99.5) == 'ok' and hub.situacao(99.4) == 'atencao'   # sem meta: 99,5%


# ------------------------------------------------------------------ causa de cada queda
def test_lowest_layer_explains_the_outage(client):
    conn = database.get_db()
    _base(conn)
    ini = t(5, 10)
    q = _queda(conn, UFV, ini, 30)
    _diag(conn, UFV, 'provedor', 'provedor', ini - 60, ini + 1800)
    _diag(conn, UFV, 'starlink_obstruida', 'starlink', ini - 300, ini + 1700)
    row = dict(conn.execute('SELECT * FROM diagnosticos WHERE id = ?', (q,)).fetchone())
    causa = nexoreport.causa_da_queda(conn, row, ini, ini + 1800)
    conn.close()
    assert causa['categoria'] == 'starlink_obstrucao' and causa['confianca'] == 'alta'


def test_outage_without_diagnosis_is_not_guessed(client):
    conn = database.get_db()
    _base(conn)
    ini = t(5, 10)
    q = _queda(conn, UFV, ini, 30)
    _diag(conn, UFV, 'starlink_obstruida', 'starlink', ini - 7200, ini - 3600)   # terminou antes
    row = dict(conn.execute('SELECT * FROM diagnosticos WHERE id = ?', (q,)).fetchone())
    assert nexoreport.causa_da_queda(conn, row, ini, ini + 1800)['categoria'] == 'indefinida'
    conn.close()


def test_correlated_provider_outage(client):
    conn = database.get_db()
    _base(conn)
    grupo = conn.execute("INSERT INTO diagnosticos (sensor_mac, provedor, chave, camada, causa, gravidade, confianca, inicio, estado, mensagem) "
                         "VALUES (NULL, 'Fibra X', 'queda_provedor', 'provedor', 'x', 'Crítica', 'média', ?, 'resolvido', 'Fibra X caiu em 3 locais') RETURNING id",
                         (t(9, 10),)).fetchone()['id']
    q = _queda(conn, ESC, t(9, 10), 46, grupo)
    row = dict(conn.execute('SELECT * FROM diagnosticos WHERE id = ?', (q,)).fetchone())
    causa = nexoreport.causa_da_queda(conn, row, t(9, 10), t(9, 10, 46))
    conn.close()
    assert causa['categoria'] == 'provedor' and causa['confianca'] == 'media' and 'Fibra X' in causa['evidencia']


# ------------------------------------------------------------------ consolidação
def test_consolidated_month_has_everything_the_pdf_needs(client):
    conn = database.get_db()
    _base(conn, meta=99.7)
    for dia, hora, minuto, dur in ((3, 16, 42, 31), (7, 17, 5, 24), (12, 16, 55, 28)):
        ini = t(dia, hora, minuto)
        _queda(conn, UFV, ini, dur)
        _diag(conn, UFV, 'starlink_obstruida', 'starlink', ini - 120, ini + dur * 60)
    _queda(conn, ESC, t(9, 10, 18), 46)
    _diag(conn, ESC, 'provedor', 'provedor', t(9, 10, 17), t(9, 11, 5), confianca='alta', mensagem='perda no 1º salto do provedor')
    _queda(conn, ESC, t(29, 11, 47), 23)   # sem diagnóstico
    conn.execute("INSERT INTO starlink_medicoes (sensor_mac, coletado_em, dados) VALUES (?, ?, ?)",
                 (UFV, t(3, 17), json.dumps({'detectada': True, 'obstrucao': {'percentual': 6.0}, 'alinhamento': {'desvio_graus': 0.4}})))
    conn.execute("INSERT INTO dispositivos (sensor_mac, ip, mac, fabricante, status, cftv) VALUES (?, '192.168.0.27', '10:00:00:00:00:27', 'Intelbras', 'online', '{\"tipo\": \"Câmera\"}')", (UFV,))
    conn.execute("INSERT INTO dispositivos (sensor_mac, ip, mac, status) VALUES (?, '192.168.0.99', '10:00:00:00:00:99', 'online')", (UFV,))
    dados = nexoreport.consolidar(conn, EMPRESA, 2026, 10, agora=FECHAMENTO)
    conn.close()

    json.dumps(dados)  # vai para o banco como JSON
    assert dados['periodo']['rotulo'] == 'Outubro de 2026' and not dados['periodo']['parcial']
    assert dados['meta'] == 99.7 and dados['meta_contratada']
    assert dados['totais']['quedas'] == 5 and dados['totais']['fora_s'] == (31 + 24 + 28 + 46 + 23) * 60
    assert len(dados['por_dia']) == 31
    assert list(dados['causas'])[0] == 'starlink_obstrucao' and dados['causas']['starlink_obstrucao']['quedas'] == 3
    assert dados['causas']['indefinida']['quedas'] == 1
    assert [q['local'] for q in dados['provedor']] == ['Escritório']
    assert sum(dados['horarios']) == 5 and dados['horarios'][4] == 3   # 16h às 20h
    ufv = next(l for l in dados['locais'] if l['sensor_mac'] == UFV)
    assert ufv['starlink']['obstrucao_media_pct'] == 6.0 and ufv['starlink']['quedas_obstrucao'] == 3
    assert ufv['qualidade']['amostras'] == 4 and ufv['qualidade']['latencia_ms'] == 45.5
    assert ufv['inventario'] == {'total': 2, 'cftv': 1, 'por_tipo': {'camera': 1, 'dispositivo': 1}, 'sem_identificacao': 1}
    assert any(n['id'] == 'gateway' for n in ufv['diagrama'])
    # Setembro sem relatório emitido e com parte das medições já descartada: sem comparação, em vez de um número errado.
    assert dados['anterior'] is None


# ------------------------------------------------------------------ fechamento
def test_month_to_close_waits_for_00h30_on_day_1():
    antes = datetime(2026, 11, 1, 0, 29, tzinfo=hub.FUSO_BR).timestamp()
    depois = datetime(2026, 11, 1, 0, 31, tzinfo=hub.FUSO_BR).timestamp()
    assert nexoreport.mes_a_fechar(antes) == (2026, 9)
    assert nexoreport.mes_a_fechar(depois) == (2026, 10)
    assert nexoreport.mes_a_fechar(datetime(2027, 1, 5, tzinfo=hub.FUSO_BR).timestamp()) == (2026, 12)


def test_close_only_contracted_companies_and_only_once(client):
    conn = database.get_db()
    _base(conn, contratar=False)
    assert nexoreport.fechar_pendentes(conn, FECHAMENTO) == ([], [])
    acessos.definir_contrato(conn, EMPRESA, 'report', True)
    assert nexoreport.fechar_pendentes(conn, FECHAMENTO)[0] == [EMPRESA]
    assert nexoreport.fechar_pendentes(conn, FECHAMENTO + 900)[0] == []   # roda de novo: não duplica
    assert nexoreport.listar(conn, EMPRESA) == [{'ano_mes': '2026-10', 'versao': 1}]
    emitido = nexoreport.relatorio(conn, EMPRESA, '2026-10')
    conn.close()
    assert emitido['criado_por'] == 'automático' and emitido['dados']['totais']['pct'] == 100.0


def test_new_version_keeps_the_previous_one(client):
    conn = database.get_db()
    _base(conn)
    dados = nexoreport.consolidar(conn, EMPRESA, 2026, 10, agora=FECHAMENTO)
    assert nexoreport.gravar(conn, EMPRESA, 2026, 10, dados) == 1
    assert nexoreport.gravar(conn, EMPRESA, 2026, 10, dados) is None
    assert nexoreport.gravar(conn, EMPRESA, 2026, 10, dados, criado_por='admin', nova_versao=True) == 2
    assert nexoreport.relatorio(conn, EMPRESA, '2026-10', versao=1)['versao'] == 1
    assert nexoreport.relatorio(conn, EMPRESA, '2026-10')['criado_por'] == 'admin'
    conn.close()


def test_trend_and_comparison_come_from_issued_reports(client):
    conn = database.get_db()
    _base(conn)
    setembro = nexoreport.consolidar(conn, EMPRESA, 2026, 9, agora=FECHAMENTO)
    setembro['totais'].update(pct=98.5, quedas=12, fora_s=36000)
    nexoreport.gravar(conn, EMPRESA, 2026, 9, setembro)
    # Muito depois: as medições de outubro já teriam sido descartadas, mas a comparação usa o emitido.
    dados = nexoreport.consolidar(conn, EMPRESA, 2026, 10, agora=FECHAMENTO + 90 * 86400)
    conn.close()
    assert dados['anterior']['pct'] == 98.5 and dados['anterior']['quedas'] == 12
    assert [p['pct'] for p in dados['tendencia']][-2:] == [98.5, dados['totais']['pct']]
    assert dados['qualidade_desde'] is not None   # avisa que parte das medições já tinha sido descartada


def test_failure_in_one_company_does_not_block_the_others(client, monkeypatch):
    conn = database.get_db()
    _base(conn)
    acessos.definir_contrato(conn, 3, 'report', True)
    original = nexoreport.consolidar

    def quebra(conn, empresa_id, *a, **k):
        if empresa_id == EMPRESA:
            raise RuntimeError('banco ocupado')
        return original(conn, empresa_id, *a, **k)
    monkeypatch.setattr(nexoreport, 'consolidar', quebra)
    emitidos, falhas = nexoreport.fechar_pendentes(conn, FECHAMENTO, log=lambda *_: None)
    conn.close()
    assert emitidos == [3] and falhas[0][0] == EMPRESA


# ------------------------------------------------------------------ meta no cadastro
def test_only_master_sets_the_contract_target(client, login):
    m = login()
    assert m.put(f'/api/v2/usuarios/{EMPRESA}/info', json={'nome': 'Cliente', 'usuario': 'user2', 'meta_disponibilidade': '99,7'}).status_code == 200
    conn = database.get_db()
    assert hub.meta_da_empresa(conn, EMPRESA) == 99.7
    assert conn.execute("SELECT detalhes FROM auditoria_acessos WHERE acao = 'meta_disponibilidade' ORDER BY id DESC LIMIT 1").fetchone()['detalhes'] == 'padrão → 99.7'
    conn.close()
    assert m.put(f'/api/v2/usuarios/{EMPRESA}/info', json={'nome': 'Cliente', 'usuario': 'user2', 'meta_disponibilidade': '120'}).status_code == 400
    # O próprio cliente edita o cadastro, mas não a meta.
    assert login(EMPRESA, 'Cliente').put(f'/api/v2/usuarios/{EMPRESA}/info', json={'nome': 'Cliente', 'usuario': 'user2', 'meta_disponibilidade': '90'}).status_code == 200
    conn = database.get_db()
    assert hub.meta_da_empresa(conn, EMPRESA) == 99.7
    conn.close()
    m = login()   # o mesmo navegador de teste: volta a ser o Master
    assert m.put(f'/api/v2/usuarios/{EMPRESA}/info', json={'nome': 'Cliente', 'usuario': 'user2', 'meta_disponibilidade': ''}).status_code == 200
    conn = database.get_db()
    assert hub.meta_da_empresa(conn, EMPRESA) is None
    conn.close()


def test_months_before_the_first_complete_one_are_not_closed_automatically(client, monkeypatch):
    monkeypatch.setattr(nexoreport, 'PRIMEIRO_MES', (2026, 11))
    conn = database.get_db()
    _base(conn)
    assert nexoreport.fechar_pendentes(conn, FECHAMENTO) == ([], [])          # outubro: não
    dezembro = datetime(2026, 12, 1, 0, 31, tzinfo=hub.FUSO_BR).timestamp()
    assert nexoreport.fechar_pendentes(conn, dezembro)[0] == [EMPRESA]        # novembro: sim
    conn.close()


# ------------------------------------------------------------------ atendimento da MD (NexoDesk)
def test_month_service_summary_from_the_desk(client):
    import nexodesk
    import nexoreport_pdf
    conn = database.get_db()
    for tabela in ('chamado_anexos', 'chamado_mensagens', 'chamados'):
        conn.execute(f'DELETE FROM {tabela}')
    _base(conn)
    md = acessos._usuario(conn, 1)
    cli = acessos._usuario(conn, EMPRESA)

    def abrir(titulo, impacto, quando, **extra):
        return nexodesk.chamado(conn, nexodesk.abrir(conn, cli, {'categoria': 'rede', 'impacto': impacto, 'titulo': titulo,
                                                                 'descricao': '.', **extra}, agora=quando))

    # parou tudo (crítica: 1 h para responder, 8 h úteis para resolver), resolvido em 2 h e avaliado com 5
    a = abrir('Sem internet', 'parou', t(6, 9))
    nexodesk.responder(conn, md, a, 'Vendo.', agora=t(6, 9, 20))
    nexodesk.responder(conn, md, nexodesk.chamado(conn, a['numero']), 'Ok.', depois='resolvido', agora=t(6, 11))
    nexodesk.confirmar(conn, cli, nexodesk.chamado(conn, a['numero']), nota=5, agora=t(6, 12))
    # atrapalha (alta: 2 h / 16 h úteis): resposta atrasada e solução fora do prazo
    b = abrir('Câmera sem imagem', 'atrapalha', t(13, 9))
    nexodesk.responder(conn, md, b, 'Vendo.', agora=t(13, 14))
    nexodesk.responder(conn, md, nexodesk.chamado(conn, b['numero']), 'Ok.', depois='resolvido', agora=t(16, 9))
    # ainda em atendimento no fim do mês; um cancelado; um chamado interno da MD (não entra)
    abrir('Wi-Fi fraco', 'duvida', t(28, 10))
    nexodesk.cancelar_pelo_cliente(conn, cli, abrir('Engano', 'duvida', t(29, 10)), agora=t(29, 11))
    conn.execute("INSERT INTO clientes (id, usuario, senha, role, ativo, empresa_master) VALUES (5, 'md', 'x', 'Cliente', 1, 1)")
    nexodesk.abrir(conn, md, {'empresa_id': 5, 'categoria': 'sistemas', 'impacto': 'duvida', 'titulo': 'Interno', 'descricao': '.'}, agora=t(10, 10))
    # de setembro e ainda aberto: aparece como pendente de meses anteriores
    nexodesk.abrir(conn, cli, {'categoria': 'energia', 'impacto': 'duvida', 'titulo': 'Antigo', 'descricao': '.'},
                   agora=datetime(2026, 9, 20, 10, tzinfo=hub.FUSO_BR).timestamp())
    conn.commit()

    a = nexoreport.consolidar(conn, EMPRESA, 2026, 10, FECHAMENTO)['atendimento']
    assert (a['abertos'], a['resolvidos'], a['cancelados'], a['pendentes']) == (3, 2, 1, 2)
    assert (a['resposta']['total'], a['resposta']['no_prazo']) == (2, 1)
    assert (a['solucao']['total'], a['solucao']['no_prazo'], a['solucao']['pct']) == (2, 1, 50)
    assert a['avaliacao'] == {'media': 5.0, 'total': 1, 'distribuicao': [0, 0, 0, 0, 1]}
    assert [c['titulo'] for c in a['chamados']] == ['Sem internet', 'Câmera sem imagem', 'Wi-Fi fraco']
    assert [c['titulo'] for c in a['pendentes_lista']] == ['Antigo']

    ctx = nexoreport_pdf.contexto(json.loads(json.dumps(nexoreport.consolidar(conn, EMPRESA, 2026, 10, FECHAMENTO))))
    assert ctx['atendimento']['solucao']['pct'] == '50%'
    assert [c['prazo_rotulo'] for c in ctx['atendimento']['chamados']] == ['No prazo', 'Fora do prazo', 'Novo']
    assert any(m['titulo'] == 'Atendimento' for m in ctx['metodo'])

    # sem o NexoDesk no contrato, a seção não entra
    acessos.definir_contrato(conn, EMPRESA, 'desk', False)
    assert nexoreport.consolidar(conn, EMPRESA, 2026, 10, FECHAMENTO)['atendimento'] is None
    assert nexoreport_pdf.contexto(nexoreport.consolidar(conn, EMPRESA, 2026, 10, FECHAMENTO))['atendimento'] is None
    conn.close()
