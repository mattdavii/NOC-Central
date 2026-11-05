import json
from datetime import datetime

import pytest

import acessos
import database
import hub
import nexoreport
import nexoreport_pdf

EMPRESA = 2
UFV, ESC = 'AA:00:00:00:60:01', 'AA:00:00:00:60:02'
FECHAMENTO = datetime(2026, 11, 1, 0, 31, tzinfo=hub.FUSO_BR).timestamp()


def t(dia, hora, minuto=0):
    return datetime(2026, 10, dia, hora, minuto, tzinfo=hub.FUSO_BR).timestamp()


@pytest.fixture(autouse=True)
def _limpa(monkeypatch):
    monkeypatch.setattr(nexoreport, 'PRIMEIRO_MES', (2026, 1))
    import app as central
    central._cache_pdf.clear()
    conn = database.get_db()
    conn.execute('DELETE FROM relatorios_mensais')
    conn.execute('DELETE FROM empresa_aplicativos')
    conn.commit(); conn.close()


def _mes(conn, quedas=True, meta=99.7):
    for mac, nome, prov in ((UFV, 'UFV Alto Paraná', 'Starlink'), (ESC, 'Escritório', 'Fibra X')):
        conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id, provedor, ip_gateway, ip_sensor, status) VALUES (?, ?, ?, ?, '192.168.0.1', '192.168.0.18', 'online')",
                     (mac, nome, EMPRESA, prov))
        for dia in (1, 15, 31):
            conn.execute("INSERT INTO medicoes_rede (sensor_mac, coletado_em, tipo, papel, alvo, latencia_ms, jitter_ms, perda_pct) VALUES (?, ?, 'icmp', 'externo', '1.1.1.1', 40, 3, 0.4)", (mac, t(dia, 2)))
    if quedas:
        for dia, hora, dur in ((3, 16, 31), (7, 17, 24), (12, 16, 28)):
            ini = t(dia, hora)
            conn.execute("INSERT INTO diagnosticos (sensor_mac, chave, camada, causa, gravidade, confianca, inicio, fim, estado) VALUES (?, 'sem_comunicacao', 'comunicacao', 'x', 'Crítica', 'alta', ?, ?, 'resolvido')", (UFV, ini, ini + dur * 60))
            conn.execute("INSERT INTO diagnosticos (sensor_mac, chave, camada, causa, gravidade, confianca, inicio, fim, estado, mensagem) VALUES (?, 'starlink_obstruida', 'starlink', 'x', 'Alerta', 'alta', ?, ?, 'resolvido', 'obstrução')", (UFV, ini - 60, ini + dur * 60))
        ini = t(9, 10)
        conn.execute("INSERT INTO diagnosticos (sensor_mac, chave, camada, causa, gravidade, confianca, inicio, fim, estado) VALUES (?, 'sem_comunicacao', 'comunicacao', 'x', 'Crítica', 'alta', ?, ?, 'resolvido')", (ESC, ini, ini + 2760))
        conn.execute("INSERT INTO diagnosticos (sensor_mac, chave, camada, causa, gravidade, confianca, inicio, fim, estado, mensagem) VALUES (?, 'provedor', 'provedor', 'x', 'Crítica', 'alta', ?, ?, 'resolvido', ?)",
                     (ESC, ini - 30, ini + 2760, 'perda de 100% a partir do 1º salto do provedor'))
        conn.execute("INSERT INTO starlink_medicoes (sensor_mac, coletado_em, dados) VALUES (?, ?, ?)",
                     (UFV, t(3, 17), json.dumps({'detectada': True, 'obstrucao': {'percentual': 5.2}, 'alinhamento': {'desvio_graus': 0.4}})))
        conn.execute("INSERT INTO dispositivos (sensor_mac, ip, mac, fabricante, status) VALUES (?, '192.168.0.27', '10:00:00:00:00:27', 'Intelbras', 'online')", (UFV,))
    acessos.definir_contrato(conn, EMPRESA, 'report', True)
    conn.execute('UPDATE clientes SET meta_disponibilidade = ? WHERE id = ?', (meta, EMPRESA))
    return nexoreport.consolidar(conn, EMPRESA, 2026, 10, agora=FECHAMENTO)


# ------------------------------------------------------------------ textos por regra
def test_summary_and_recommendations_follow_the_numbers(client):
    conn = database.get_db()
    dados = _mes(conn)
    conn.close()
    resumo = ' '.join(nexoreport_pdf.resumo(dados))
    assert 'Em outubro, a rede dos seus 2 locais ficou disponível' in resumo
    assert 'A causa mais frequente foi obstrução da starlink em UFV Alto Paraná (3 quedas)' in resumo
    assert 'indica sombra no fim da tarde' in resumo and '1 queda foi atribuída ao provedor' in resumo
    recs = nexoreport_pdf.recomendacoes(dados)
    assert recs[0]['prioridade'] == 'alta' and 'campo de visão' in recs[0]['titulo']
    assert any('provedor' in r['titulo'] for r in recs)


def test_quiet_month_says_so(client):
    conn = database.get_db()
    dados = _mes(conn, quedas=False)
    conn.close()
    assert 'Não houve nenhuma queda no mês.' in nexoreport_pdf.resumo(dados)[0]
    assert nexoreport_pdf.recomendacoes(dados) == []
    ctx = nexoreport_pdf.contexto(dados)
    assert ctx['kpi']['causa_principal'] == 'Nenhuma queda' and ctx['kpi']['situacao'] == 'ok'


def test_formats():
    assert nexoreport_pdf.pct_txt(99.75) == '99,8%' and nexoreport_pdf.pct_txt(None) == '—'
    assert nexoreport_pdf.dur_txt(261 * 60) == '4h21' and nexoreport_pdf.dur_txt(30) == 'menos de 1 min'
    assert nexoreport_pdf.dur_txt(3 * 86400) == '3 d 0 h'


# ------------------------------------------------------------------ PDF
def test_pdf_is_generated_from_the_closed_month(client):
    pytest.importorskip('weasyprint')
    conn = database.get_db()
    dados = _mes(conn)
    conn.close()
    pdf = nexoreport_pdf.gerar_pdf(json.loads(json.dumps(dados)), versao=1)   # o mesmo caminho do banco (JSON)
    assert pdf.startswith(b'%PDF') and len(pdf) > 20000


# ------------------------------------------------------------------ página e permissões
def _emitir(conn):
    dados = _mes(conn)
    nexoreport.gravar(conn, EMPRESA, 2026, 10, dados)
    conn.commit()


def test_client_sees_its_reports_and_downloads(client, login, monkeypatch):
    monkeypatch.setattr(nexoreport_pdf, 'gerar_pdf', lambda dados, versao=None: b'%PDF-fake')
    conn = database.get_db()
    _emitir(conn)
    conn.close()
    cli = login(EMPRESA, 'Cliente')
    pagina = cli.get('/report').get_data(as_text=True)
    assert 'Outubro de 2026' in pagina and '/report/2/2026-10.pdf' in pagina and 'Emitir de novo' not in pagina
    r = cli.get('/report/2/2026-10.pdf?baixar=1')
    assert r.status_code == 200 and r.mimetype == 'application/pdf' and 'attachment' in r.headers['Content-Disposition']
    conn = database.get_db()
    assert conn.execute("SELECT 1 FROM auditoria_acessos WHERE acao = 'relatorio_baixado'").fetchone()
    conn.close()


def test_other_company_and_uncontracted_get_nothing(client, login, monkeypatch):
    monkeypatch.setattr(nexoreport_pdf, 'gerar_pdf', lambda dados, versao=None: b'%PDF-fake')
    conn = database.get_db()
    _emitir(conn)
    conn.close()
    assert login(3, 'Cliente').get('/report/2/2026-10.pdf').status_code == 403
    conn = database.get_db()
    acessos.definir_contrato(conn, EMPRESA, 'report', False)
    conn.commit(); conn.close()
    assert login(EMPRESA, 'Cliente').get('/report/2/2026-10.pdf').status_code == 403
    assert 'não está liberado' in login(EMPRESA, 'Cliente').get('/report').get_data(as_text=True)


def test_reader_level_is_enough_but_no_level_is_not(client, login, monkeypatch):
    monkeypatch.setattr(nexoreport_pdf, 'gerar_pdf', lambda dados, versao=None: b'%PDF-fake')
    conn = database.get_db()
    _emitir(conn)
    acessos.definir_nivel(conn, 4, 'report', 'leitura')
    conn.commit(); conn.close()
    assert login(4, 'Administrador Cliente').get('/report/2/2026-10.pdf').status_code == 200
    conn = database.get_db()
    acessos.definir_nivel(conn, 4, 'report', 'nenhum')
    conn.commit(); conn.close()
    assert login(4, 'Administrador Cliente').get('/report/2/2026-10.pdf').status_code == 403


def test_master_reissues_as_new_version_and_previews(client, login, monkeypatch):
    monkeypatch.setattr(nexoreport_pdf, 'gerar_pdf', lambda dados, versao=None: b'%PDF-fake')
    conn = database.get_db()
    _emitir(conn)
    conn.close()
    m = login()
    assert 'Emitir de novo' in m.get('/report?empresa=2').get_data(as_text=True)
    r = m.post('/api/v2/report/2/2026-10/emitir', json={})
    assert r.status_code == 201 and r.json['versao'] == 2
    assert m.post('/api/v2/report/2/2099-01/emitir', json={}).status_code == 400      # mês não terminou
    assert m.post('/api/v2/report/2/2026-13/emitir', json={}).status_code == 400
    assert m.get('/report/2/previa.pdf').status_code == 200
    assert login(EMPRESA, 'Cliente').get('/report/2/previa.pdf').status_code == 403
    assert login(EMPRESA, 'Cliente').post('/api/v2/report/2/2026-10/emitir', json={}).status_code == 403


def test_hub_card_and_launcher_open_the_reports(client, login):
    conn = database.get_db()
    _emitir(conn)
    conn.close()
    pagina = login(EMPRESA, 'Cliente').get('/').get_data(as_text=True)
    assert 'Abrir os relatórios' in pagina and 'href="/report"' in pagina
