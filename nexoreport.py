"""NexoReport: fechamento do mês.

No dia 1 (00:30, Brasília) a Central consolida o mês anterior de cada empresa com o NexoReport
contratado e grava o resultado em `relatorios_mensais` (JSON). O relatório é emitido uma vez e fica
como foi emitido: as medições de qualidade e da Starlink só ficam guardadas por algumas semanas, e o
inventário e o diagrama mudam com o tempo.

Escopo e regras: docs/nexoreport/ESCOPO.md. O PDF (entrega 3) é desenhado a partir deste JSON.
"""
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import acessos
import hub
import mapa_rede

VERSAO_DADOS = 1
FUSO_BR = hub.FUSO_BR
HORA_FECHAMENTO = (0, 30)  # dia 1, 00:30 de Brasília
# Primeiro mês fechado sozinho: antes dele, parte das medições já tinha sido descartada (retenção antiga de 30 dias).
PRIMEIRO_MES = (2026, 11)
TOLERANCIA_CAUSA_S = 600   # diagnóstico iniciado até 10 min antes da queda ainda a explica

# Camada mais baixa explica as de cima (a mesma lógica do diagnóstico ao vivo).
ORDEM_CAMADA = {'lan': 0, 'starlink': 1, 'provedor': 2, 'dns': 3}
CATEGORIA_DA_CHAVE = {
    'lan': 'rede_local', 'sem_gateway': 'rede_local',
    'starlink_obstruida': 'starlink_obstrucao',
    'starlink_desalinhada': 'starlink', 'starlink_degradada': 'starlink', 'starlink_alerta': 'starlink',
    'provedor': 'provedor', 'queda_provedor': 'provedor',
    'dns': 'dns',
}
CATEGORIAS = {
    'starlink_obstrucao': 'Obstrução da Starlink', 'starlink': 'Antena Starlink', 'provedor': 'Falha no provedor',
    'rede_local': 'Rede interna do local', 'dns': 'Falha de DNS', 'indefinida': 'Não determinada',
}
CONFIANCA = {'alta': 'alta', 'média': 'media', 'media': 'media', 'baixa': 'baixa'}


# ------------------------------------------------------------------ banco (tabela criada em database.py)
def relatorio(conn, empresa_id, ano_mes, versao=None):
    """A versão pedida, ou a mais recente; None se o mês não foi fechado."""
    sql = 'SELECT * FROM relatorios_mensais WHERE empresa_id = ? AND ano_mes = ?'
    params = [empresa_id, ano_mes]
    if versao is not None:
        sql += ' AND versao = ?'
        params.append(versao)
    row = conn.execute(sql + ' ORDER BY versao DESC LIMIT 1', params).fetchone()
    if not row:
        return None
    item = dict(row)
    item['dados'] = json.loads(item['dados'])
    return item


def listar(conn, empresa_id):
    return [dict(r) for r in conn.execute(
        'SELECT ano_mes, MAX(versao) AS versao FROM relatorios_mensais WHERE empresa_id = ? GROUP BY ano_mes ORDER BY ano_mes DESC',
        (empresa_id,)).fetchall()]


# ------------------------------------------------------------------ datas
def ano_mes(ano, mes):
    return f'{ano:04d}-{mes:02d}'


def mes_anterior(ano, mes):
    return (ano - 1, 12) if mes == 1 else (ano, mes - 1)


def mes_a_fechar(agora):
    """O mês anterior, a partir do dia 1 às 00:30 de Brasília; antes disso, o mês retrasado."""
    local = datetime.fromtimestamp(agora, FUSO_BR)
    ano, mes = mes_anterior(local.year, local.month)
    if local.day == 1 and (local.hour, local.minute) < HORA_FECHAMENTO:
        ano, mes = mes_anterior(ano, mes)
    return ano, mes


def _hora_br(epoch):
    return datetime.fromtimestamp(epoch, FUSO_BR)


# ------------------------------------------------------------------ causa de cada queda
def causa_da_queda(conn, queda, ini, fim):
    """Categoria, confiança e evidência da queda; 'indefinida' quando nenhum diagnóstico a explica."""
    if queda.get('grupo_id'):
        g = conn.execute('SELECT chave, confianca, mensagem, provedor FROM diagnosticos WHERE id = ?', (queda['grupo_id'],)).fetchone()
        if g:
            return {'categoria': 'provedor', 'chave': g['chave'], 'confianca': CONFIANCA.get(g['confianca'] or '', 'media'),
                    'evidencia': g['mensagem'] or f"Queda simultânea em outros locais do provedor {g['provedor'] or ''}".strip()}
    candidatos = conn.execute(
        "SELECT chave, camada, confianca, mensagem, inicio FROM diagnosticos WHERE sensor_mac = ? "
        "AND chave NOT IN ('sem_comunicacao', 'queda_provedor') AND inicio < ? AND (fim IS NULL OR fim > ?)",
        (queda['sensor_mac'], fim, ini - TOLERANCIA_CAUSA_S)).fetchall()
    candidatos = [c for c in candidatos if c['chave'] in CATEGORIA_DA_CHAVE]
    if not candidatos:
        return {'categoria': 'indefinida', 'chave': None, 'confianca': None, 'evidencia': None}
    peso_conf = {'alta': 0, 'media': 1, 'baixa': 2}
    melhor = min(candidatos, key=lambda c: (ORDEM_CAMADA.get(c['camada'], 9),
                                            peso_conf.get(CONFIANCA.get(c['confianca'] or '', 'baixa'), 3), c['inicio']))
    return {'categoria': CATEGORIA_DA_CHAVE[melhor['chave']], 'chave': melhor['chave'],
            'confianca': CONFIANCA.get(melhor['confianca'] or '', 'baixa'), 'evidencia': melhor['mensagem']}


# ------------------------------------------------------------------ partes do relatório
def _por_dia(inicio, fim, comeco, trechos, lista_quedas):
    dias = []
    dia_ini = inicio
    while dia_ini < fim:
        local = _hora_br(dia_ini)
        dia_fim = min((local + timedelta(days=1)).timestamp(), fim)
        monitorado = sum(max(0.0, dia_fim - max(dia_ini, c)) for c in comeco.values())
        fora = sum(max(0.0, min(b, dia_fim) - max(a, dia_ini)) for t in trechos.values() for a, b in t)
        dias.append({'dia': local.day, 'fora_s': int(fora), 'quedas': sum(1 for q in lista_quedas if dia_ini <= q['inicio'] < dia_fim),
                     'pct': hub._pct_exibido(fora, monitorado) if monitorado > 0 else None})
        dia_ini = (local + timedelta(days=1)).timestamp()
    return dias


def _percentil(valores, p):
    if not valores:
        return None
    ordenados = sorted(valores)
    k = (len(ordenados) - 1) * p
    baixo, alto = math.floor(k), math.ceil(k)
    return ordenados[baixo] + (ordenados[alto] - ordenados[baixo]) * (k - baixo)


def _media(valores):
    return sum(valores) / len(valores) if valores else None


def _arred(v, casas=1):
    return round(v, casas) if v is not None else None


def _qualidade(conn, mac, inicio, fim):
    """Internet do dia a dia: alvos externos (papel 'externo') medidos pelo sensor."""
    linhas = conn.execute(
        "SELECT latencia_ms, jitter_ms, perda_pct FROM medicoes_rede WHERE sensor_mac = ? AND tipo = 'icmp' "
        "AND papel = 'externo' AND coletado_em >= ? AND coletado_em < ?", (mac, inicio, fim)).fetchall()
    lat = [r['latencia_ms'] for r in linhas if r['latencia_ms'] is not None]
    jit = [r['jitter_ms'] for r in linhas if r['jitter_ms'] is not None]
    perda = [r['perda_pct'] for r in linhas if r['perda_pct'] is not None]
    primeira = conn.execute('SELECT MIN(coletado_em) AS t FROM medicoes_rede WHERE sensor_mac = ? AND coletado_em >= ? AND coletado_em < ?',
                            (mac, inicio, fim)).fetchone()['t']
    return {'amostras': len(linhas), 'latencia_ms': _arred(_media(lat)), 'latencia_p95_ms': _arred(_percentil(lat, .95)),
            'jitter_ms': _arred(_media(jit)), 'perda_pct': _arred(_media(perda), 2), 'medicoes_desde': primeira}


def _velocidade(conn, mac, inicio, fim):
    """Média dos testes de velocidade do mês. data_hora não tem fuso fixo: a janela aceita até 3 h de diferença nas bordas."""
    de = datetime.fromtimestamp(inicio, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    ate = datetime.fromtimestamp(fim, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    linhas = conn.execute('SELECT download, upload FROM historico_telemetria WHERE sensor_mac = ? AND data_hora >= ? AND data_hora < ?',
                          (mac, de, ate)).fetchall()
    down = [float(r['download']) for r in linhas if r['download'] not in (None, '')]
    up = [float(r['upload']) for r in linhas if r['upload'] not in (None, '')]
    return {'testes': len(linhas), 'download_mbps': _arred(_media(down)), 'upload_mbps': _arred(_media(up))}


def _starlink(conn, mac, inicio, fim):
    linhas = conn.execute('SELECT dados FROM starlink_medicoes WHERE sensor_mac = ? AND coletado_em >= ? AND coletado_em < ?',
                          (mac, inicio, fim)).fetchall()
    obstr, desvio = [], []
    for r in linhas:
        try:
            d = json.loads(r['dados'] or '{}')
        except ValueError:
            continue
        if not d.get('detectada'):
            continue
        p = (d.get('obstrucao') or {}).get('percentual')
        if p is not None:
            obstr.append(float(p))
        g = (d.get('alinhamento') or {}).get('desvio_graus')
        if g is not None:
            desvio.append(float(g))
    if not obstr and not desvio:
        return None
    return {'leituras': len(linhas), 'obstrucao_media_pct': _arred(_media(obstr), 2),
            'desvio_medio_graus': _arred(_media(desvio), 2)}


def _inventario(conn, mac):
    linhas = [dict(r) for r in conn.execute('SELECT * FROM dispositivos WHERE sensor_mac = ?', (mac,)).fetchall()]
    tipos = Counter()
    for d in linhas:
        tipos[mapa_rede._tipo_observado(d)] += 1
    sem_nome = sum(1 for d in linhas if not (d.get('nome_custom') or d.get('hostname')) and (d.get('fabricante') in (None, '', 'Desconhecido')))
    return {'total': len(linhas), 'cftv': sum(1 for d in linhas if d.get('cftv')), 'por_tipo': dict(tipos.most_common()),
            'sem_identificacao': sem_nome}


def _totais_de(conn, empresa_id, ano, mes, retencao_s, agora):
    """Números do mês anterior para comparar: o relatório emitido; senão, recalcula se as medições ainda existem."""
    anterior = relatorio(conn, empresa_id, ano_mes(ano, mes))
    if anterior:
        return anterior['dados']['totais']
    inicio, fim = hub.limites_do_mes(ano, mes)
    if inicio < agora - retencao_s:
        return None  # parte das medições já foi descartada: comparar seria comparar com um número errado
    d = hub.disponibilidade_periodo(conn, empresa_id, inicio, fim)
    return {'pct': d['pct'], 'fora_s': d['fora_s'], 'quedas': d['quedas']} if d['pct'] is not None else None


def consolidar(conn, empresa_id, ano, mes, agora=None, retencao_s=40 * 86400):
    """Todos os números do relatório do mês, prontos para o PDF (JSON serializável)."""
    agora = agora or time.time()
    inicio, fim = hub.limites_do_mes(ano, mes)
    parcial = fim > agora
    fim_calc = min(fim, agora)
    meta = hub.meta_da_empresa(conn, empresa_id)
    disp = hub.disponibilidade_periodo(conn, empresa_id, inicio, fim_calc, meta)
    sites = {r['mac_id']: dict(r) for r in conn.execute(
        'SELECT * FROM sensores WHERE cliente_id = ?', (empresa_id,)).fetchall()}

    quedas, por_categoria, horarios = [], defaultdict(lambda: {'fora_s': 0, 'quedas': 0}), Counter()
    for q in disp['lista_quedas']:
        mac = q['sensor_mac']
        ini = max(q['inicio'], disp['comeco'][mac])
        fim_q = min(q['fim'] or fim_calc, fim_calc)
        causa = causa_da_queda(conn, q, ini, fim_q)
        dur = max(0, fim_q - ini)
        quedas.append({'local': sites.get(mac, {}).get('nome_local') or mac, 'sensor_mac': mac, 'inicio': ini, 'fim': fim_q,
                       'em_aberto': q['fim'] is None or q['fim'] > fim_calc, 'duracao_s': int(dur), **causa})
        por_categoria[causa['categoria']]['fora_s'] += int(dur)
        por_categoria[causa['categoria']]['quedas'] += 1
        horarios[_hora_br(ini).hour // 4] += 1
    quedas.sort(key=lambda x: x['inicio'])

    locais = []
    for mac, info in disp['por_site'].items():
        s = sites.get(mac, {})
        local = {'sensor_mac': mac, 'nome': s.get('nome_local') or mac, 'provedor': s.get('provedor'),
                 'monitorado_desde': info['desde'] if info['desde'] > inicio else None,
                 'pct': info['pct'], 'fora_s': info['fora_s'], 'quedas': info['quedas'], 'situacao': info['situacao'],
                 'qualidade': _qualidade(conn, mac, inicio, fim_calc), 'velocidade': _velocidade(conn, mac, inicio, fim_calc),
                 'starlink': _starlink(conn, mac, inicio, fim_calc), 'inventario': _inventario(conn, mac)}
        if local['starlink'] is not None:
            local['starlink']['quedas_obstrucao'] = sum(1 for x in quedas if x['sensor_mac'] == mac and x['categoria'] == 'starlink_obstrucao')
        # Diagrama como estava no fechamento: o PDF desenha esta árvore, não a de hoje.
        dispositivos = conn.execute('SELECT * FROM dispositivos WHERE sensor_mac = ?', (mac,)).fetchall()
        local['diagrama'] = mapa_rede.montar(s, dispositivos, mapa_rede.carregar(s.get('mapa_rede')))
        local['diagrama_informado'] = bool(s.get('mapa_rede'))
        locais.append(local)
    locais.sort(key=lambda l: l['nome'].lower())

    totais = {'pct': disp['pct'], 'fora_s': disp['fora_s'], 'quedas': disp['quedas'], 'situacao': disp['situacao']}
    ano_a, mes_a = mes_anterior(ano, mes)
    empresa = conn.execute('SELECT nome, usuario FROM clientes WHERE id = ?', (empresa_id,)).fetchone()
    return {
        'versao_dados': VERSAO_DADOS,
        'empresa': {'id': empresa_id, 'nome': (empresa['nome'] or empresa['usuario']) if empresa else str(empresa_id)},
        'periodo': {'ano': ano, 'mes': mes, 'rotulo': f'{hub.MESES[mes - 1].capitalize()} de {ano}', 'inicio': inicio, 'fim': fim,
                    'calculado_ate': fim_calc, 'parcial': parcial},
        'meta': meta if meta is not None else hub.META_PADRAO, 'meta_contratada': meta is not None,
        # Medições de qualidade descartadas antes do fechamento (fechamento atrasado ou primeiro mês).
        'qualidade_desde': max(inicio, agora - retencao_s) if inicio < agora - retencao_s else None,
        'totais': totais,
        'anterior': _totais_de(conn, empresa_id, ano_a, mes_a, retencao_s, agora),
        'locais': locais,
        'por_dia': _por_dia(inicio, fim_calc, disp['comeco'], disp['trechos'], disp['lista_quedas']),
        'quedas': quedas,
        'causas': {k: dict(v, rotulo=CATEGORIAS[k]) for k, v in sorted(por_categoria.items(), key=lambda kv: -kv[1]['fora_s'])},
        'horarios': [horarios.get(b, 0) for b in range(6)],
        'provedor': [q for q in quedas if q['categoria'] == 'provedor' and q['confianca'] in ('alta', 'media')],
        'tendencia': tendencia(conn, empresa_id, ano, mes, totais['pct']),
        'gerado_em': agora,
    }


def tendencia(conn, empresa_id, ano, mes, pct_atual, meses=6):
    """Últimos meses a partir dos relatórios emitidos (não recalcula: as medições antigas já foram descartadas)."""
    pontos, a, m = [], ano, mes
    for i in range(meses):
        if i == 0:
            pontos.append({'ano_mes': ano_mes(a, m), 'pct': pct_atual})
        else:
            r = relatorio(conn, empresa_id, ano_mes(a, m))
            pontos.append({'ano_mes': ano_mes(a, m), 'pct': r['dados']['totais']['pct'] if r else None})
        a, m = mes_anterior(a, m)
    return list(reversed(pontos))


# ------------------------------------------------------------------ fechamento
def empresas_com_report(conn):
    ids = [r['id'] for r in conn.execute("SELECT id FROM clientes WHERE role = 'Cliente' AND COALESCE(ativo, 1) = 1").fetchall()]
    return [e for e in ids if acessos.contrato(conn, e).get('report')]


def gravar(conn, empresa_id, ano, mes, dados, criado_por='automático', nova_versao=False):
    """Grava o mês; sem nova_versao, não faz nada se ele já foi emitido (o fechamento pode rodar mais de uma vez)."""
    chave = ano_mes(ano, mes)
    atual = conn.execute('SELECT MAX(versao) AS v FROM relatorios_mensais WHERE empresa_id = ? AND ano_mes = ?',
                         (empresa_id, chave)).fetchone()['v']
    if atual and not nova_versao:
        return None
    versao = (atual or 0) + 1
    conn.execute('INSERT INTO relatorios_mensais (empresa_id, ano_mes, versao, parcial, criado_em, criado_por, dados) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING',
                 (empresa_id, chave, versao, 1 if dados['periodo']['parcial'] else 0, dados['gerado_em'], criado_por,
                  json.dumps(dados, ensure_ascii=False)))
    return versao


def fechar_pendentes(conn, agora=None, retencao_s=40 * 86400, log=print):
    """Fecha o mês anterior de cada empresa com o NexoReport que ainda não o tem. Devolve (emitidos, falhas)."""
    agora = agora or time.time()
    ano, mes = mes_a_fechar(agora)
    emitidos, falhas = [], []
    if (ano, mes) < PRIMEIRO_MES:
        return emitidos, falhas
    for empresa_id in empresas_com_report(conn):
        if relatorio(conn, empresa_id, ano_mes(ano, mes)):
            continue
        try:
            dados = consolidar(conn, empresa_id, ano, mes, agora, retencao_s)
            if gravar(conn, empresa_id, ano, mes, dados):
                emitidos.append(empresa_id)
            conn.commit()
        except Exception as erro:  # uma empresa com problema não impede as outras
            conn.rollback()
            falhas.append((empresa_id, str(erro)))
            log(f'⚠️ NexoReport: falha ao fechar {ano_mes(ano, mes)} da empresa {empresa_id}: {erro}')
    return emitidos, falhas
