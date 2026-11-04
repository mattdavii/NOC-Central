"""NexoHub: resumos da página inicial (um card por aplicativo e a visão da MD com todos os clientes)."""
import math
import time
from datetime import datetime, timedelta, timezone

import acessos

# O mês fecha no horário de Brasília (sem horário de verão desde 2019).
FUSO_BR = timezone(timedelta(hours=-3))
MESES = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto',
         'setembro', 'outubro', 'novembro', 'dezembro')

DESCRICOES = {
    'pulse': 'Monitoramento da rede 24/7: sites, links, provedores e diagnóstico da causa de cada queda.',
    'guard': 'Postura de segurança: firewall, portas expostas, atualizações e uma nota de 0 a 100.',
    'report': 'Relatório mensal em PDF com o resumo da sua rede, pronto para a diretoria.',
}
# Aplicativos que já têm telas; os demais aparecem como "em breve" para quem contratou.
DISPONIVEIS = {'pulse'}


def resumo_pulse(conn, empresa_id=None):
    """Sites, diagnósticos ativos e última queda; empresa_id=None soma todos os clientes (MD)."""
    filtro, params = ('', ()) if empresa_id is None else (' WHERE cliente_id = ?', (empresa_id,))
    sites = conn.execute('SELECT mac_id, nome_local, status, em_manutencao FROM sensores' + filtro, params).fetchall()
    manutencao = sum(1 for s in sites if s['em_manutencao'])
    online = sum(1 for s in sites if s['status'] == 'online' and not s['em_manutencao'])
    offline = sum(1 for s in sites if s['status'] != 'online' and not s['em_manutencao'])
    junta = '' if empresa_id is None else ' AND s.cliente_id = ?'
    diags = conn.execute(
        "SELECT d.causa, d.gravidade, d.inicio, d.sensor_mac, s.nome_local FROM diagnosticos d "
        "JOIN sensores s ON s.mac_id = d.sensor_mac WHERE d.estado = 'ativo'" + junta +
        ' ORDER BY d.inicio DESC LIMIT 5', params).fetchall()
    total_diags = conn.execute(
        "SELECT COUNT(*) AS n FROM diagnosticos d JOIN sensores s ON s.mac_id = d.sensor_mac "
        "WHERE d.estado = 'ativo'" + junta, params).fetchone()['n']
    queda = conn.execute(
        "SELECT d.inicio, d.fim, d.estado, s.nome_local, d.sensor_mac FROM diagnosticos d "
        "JOIN sensores s ON s.mac_id = d.sensor_mac WHERE d.chave = 'sem_comunicacao'" + junta +
        ' ORDER BY d.inicio DESC LIMIT 1', params).fetchone()
    return {
        'sites': len(sites), 'online': online, 'offline': offline, 'manutencao': manutencao,
        'diagnosticos': [{'site': d['nome_local'] or d['sensor_mac'], 'causa': d['causa'], 'gravidade': d['gravidade'],
                          'inicio': d['inicio']} for d in diags],
        'total_diagnosticos': total_diags,
        'ultima_queda': ({'site': queda['nome_local'] or queda['sensor_mac'], 'inicio': queda['inicio'],
                          'fim': queda['fim'], 'ativa': queda['estado'] == 'ativo'} if queda else None),
    }


def inicio_do_mes(agora):
    local = datetime.fromtimestamp(agora, FUSO_BR)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp(), MESES[local.month - 1]


def _unir(intervalos):
    """Junta intervalos sobrepostos (duas quedas registradas no mesmo período contam uma vez)."""
    unidos = []
    for ini, fim in sorted(intervalos):
        if unidos and ini <= unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], fim)
        else:
            unidos.append([ini, fim])
    return unidos


def duracao(segundos):
    """3720 -> '1 h 2 min'; abaixo de um minuto, 'menos de 1 min'."""
    minutos = int(segundos // 60)
    if minutos < 1:
        return 'menos de 1 min'
    dias, resto = divmod(minutos, 1440)
    horas, minutos = divmod(resto, 60)
    partes = [f'{dias} d' if dias else '', f'{horas} h' if horas else '', f'{minutos} min' if minutos and not dias else '']
    return ' '.join(p for p in partes if p)


def _janelas_do_mes(conn, empresa_id, agora):
    """Base das contas de disponibilidade: de quando cada site conta no mês e os trechos sem comunicação dele.

    Cada site fora de manutenção conta do início do mês ou da primeira medição dele no mês (sensor instalado no
    meio do mês não ganha dias que não monitorou). Uma queda aberta antes do mês prova que o site já existia no
    dia 1; sem medição nenhuma (agente antigo, que não envia medições), conta o mês inteiro."""
    inicio, mes = inicio_do_mes(agora)
    filtro, params = ('', ()) if empresa_id is None else (' AND cliente_id = ?', (empresa_id,))
    macs = [r['mac_id'] for r in conn.execute(
        'SELECT mac_id FROM sensores WHERE COALESCE(em_manutencao, 0) = 0' + filtro, params).fetchall()]
    comeco = {}
    for mac in macs:
        aberta = conn.execute("SELECT 1 FROM diagnosticos WHERE sensor_mac = ? AND chave = 'sem_comunicacao' "
                              'AND inicio < ? AND (fim IS NULL OR fim > ?) LIMIT 1', (mac, inicio, inicio)).fetchone()
        primeira = conn.execute('SELECT MIN(coletado_em) AS t FROM medicoes_rede WHERE sensor_mac = ? AND coletado_em >= ?',
                                (mac, inicio)).fetchone()['t']
        comeco[mac] = inicio if aberta or primeira is None else min(agora, max(inicio, primeira))
    quedas, por_site = [], {}
    if comeco:
        marcas = ','.join('?' * len(comeco))
        quedas = conn.execute(
            "SELECT sensor_mac, inicio, fim FROM diagnosticos WHERE chave = 'sem_comunicacao' "
            f'AND sensor_mac IN ({marcas}) AND inicio < ? AND (fim IS NULL OR fim > ?)',
            (*comeco, agora, inicio)).fetchall()
    for q in quedas:
        ini, fim = max(q['inicio'], comeco[q['sensor_mac']]), min(q['fim'] or agora, agora)
        if fim > ini:
            por_site.setdefault(q['sensor_mac'], []).append((ini, fim))
    quedas = [q for q in quedas if q['sensor_mac'] in por_site]
    trechos = {mac: _unir(t) for mac, t in por_site.items()}
    return inicio, mes, comeco, trechos, quedas


def _faixa(pct):
    return 'ok' if pct >= 99.5 else ('atencao' if pct >= 98 else 'critico')


def _pct_exibido(fora, periodo):
    # Arredonda para baixo: com qualquer queda o número nunca chega a 100%.
    exibido = math.floor(100 * (1 - fora / periodo) * 10) / 10
    return 99.9 if fora > 0 and exibido >= 100 else exibido


def disponibilidade_mes(conn, empresa_id=None, agora=None):
    """Percentual do mês corrente em que os sites tiveram comunicação com a Central (None sem site para medir)."""
    agora = agora or time.time()
    _, mes, comeco, trechos, quedas = _janelas_do_mes(conn, empresa_id, agora)
    periodo = sum(agora - c for c in comeco.values())
    if periodo <= 0:
        return None
    fora = sum(fim - ini for t in trechos.values() for ini, fim in t)
    exibido = _pct_exibido(fora, periodo)
    return {'pct': exibido, 'mes': mes, 'quedas': len(quedas), 'fora_s': int(fora), 'fora_txt': duracao(fora),
            'faixa': _faixa(exibido)}


def disponibilidade_por_dia(conn, empresa_id=None, agora=None):
    """Um item por dia do mês até hoje: tempo sem comunicação, quedas iniciadas e disponibilidade do dia.

    Dia sem nenhum site monitorado volta com pct None (o gráfico mostra o dia vazio, sem inventar 100%)."""
    agora = agora or time.time()
    inicio, mes, comeco, trechos, quedas = _janelas_do_mes(conn, empresa_id, agora)
    dias = []
    dia_ini = inicio
    while dia_ini < agora:
        local = datetime.fromtimestamp(dia_ini, FUSO_BR)
        dia_fim = min((local + timedelta(days=1)).timestamp(), agora)
        monitorado = sum(max(0.0, dia_fim - max(dia_ini, c)) for c in comeco.values())
        fora = sum(max(0.0, min(fim, dia_fim) - max(ini, dia_ini)) for t in trechos.values() for ini, fim in t)
        iniciadas = sum(1 for q in quedas if dia_ini <= q['inicio'] < dia_fim)
        dias.append({'dia': local.day, 'data': local.strftime('%d/%m'), 'fora_s': int(fora), 'fora_txt': duracao(fora) if fora else '',
                     'quedas': iniciadas, 'pct': _pct_exibido(fora, monitorado) if monitorado > 0 else None,
                     'hoje': dia_fim >= agora})
        dia_ini = (local + timedelta(days=1)).timestamp()
    # Dias que ainda vão chegar: o gráfico mostra o mês inteiro, com o futuro em branco.
    proximo = datetime.fromtimestamp(inicio, FUSO_BR)
    proximo = (proximo.replace(year=proximo.year + 1, month=1) if proximo.month == 12 else proximo.replace(month=proximo.month + 1))
    while dia_ini < proximo.timestamp():
        local = datetime.fromtimestamp(dia_ini, FUSO_BR)
        dias.append({'dia': local.day, 'data': local.strftime('%d/%m'), 'futuro': True, 'fora_s': 0, 'quedas': 0, 'pct': None})
        dia_ini = (local + timedelta(days=1)).timestamp()
    medidos = [d for d in dias if d['pct'] is not None]
    return {'mes': mes, 'dias': dias, 'pior_s': max((d['fora_s'] for d in dias), default=0),
            'dias_sem_queda': sum(1 for d in medidos if not d['fora_s']), 'dias_medidos': len(medidos)}


def cards(conn, empresa_id, acesso):
    """Cards da página inicial do cliente: liberado, em breve (contratado sem telas) ou vitrine (não contratado).

    Aplicativo contratado sem nível para o usuário não aparece (decisão "Sem acesso" do escopo)."""
    contratado = acessos.contrato(conn, empresa_id)
    saida = []
    for app, nome in acessos.APLICATIVOS.items():
        card = {'app': app, 'nome': nome, 'descricao': DESCRICOES[app]}
        if not contratado.get(app):
            card['estado'] = 'vitrine'
        elif app not in acesso:
            continue
        elif app not in DISPONIVEIS:
            card['estado'] = 'em_breve'
        else:
            card.update(estado='liberado', nivel=acesso[app])
            if app == 'pulse':
                card['resumo'] = resumo_pulse(conn, empresa_id)
                card['disponibilidade'] = disponibilidade_mes(conn, empresa_id)
        saida.append(card)
    # Liberados primeiro, depois "em breve", vitrine por último.
    ordem = {'liberado': 0, 'em_breve': 1, 'vitrine': 2}
    return sorted(saida, key=lambda c: ordem[c['estado']])


def visao_md(conn):
    """Uma linha por empresa cliente: aplicativos contratados, sites e alertas ativos."""
    empresas = conn.execute("SELECT id, nome, usuario, ativo, empresa_master FROM clientes WHERE role = 'Cliente' ORDER BY nome").fetchall()
    linhas = []
    for e in empresas:
        r = resumo_pulse(conn, e['id'])
        linhas.append({'id': e['id'], 'nome': e['nome'] or e['usuario'], 'ativo': bool(e['ativo']), 'master': bool(e['empresa_master']),
                       'contrato': acessos.contrato(conn, e['id']), 'sites': r['sites'], 'online': r['online'],
                       'offline': r['offline'], 'manutencao': r['manutencao'], 'alertas': r['total_diagnosticos'],
                       'ultima_queda': r['ultima_queda'], 'disponibilidade': disponibilidade_mes(conn, e['id'])})
    # A Empresa Master no topo; depois, quem tem problema agora.
    return sorted(linhas, key=lambda l: (not l['master'], -(l['offline'] + l['alertas']), l['nome'].lower()))


def sem_empresa(conn):
    """Sensores ainda não alocados a uma empresa cliente (a MD precisa ver)."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM sensores s LEFT JOIN clientes c ON c.id = s.cliente_id "
        "WHERE c.id IS NULL OR c.role <> 'Cliente'").fetchone()['n']


GRAVIDADE = {'Crítica': 'critico', 'Critica': 'critico', 'Alerta': 'atencao', 'Aviso': 'info'}


def _epoch(valor):
    """data_hora do banco (TIMESTAMP gravado em UTC, sem fuso) em epoch; aceita datetime ou texto do SQLite."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            valor = datetime.fromisoformat(valor.strip().replace(' ', 'T'))
        except ValueError:
            return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.timestamp()


def precisa_de_atencao(conn, build_atual=None):
    """O que a equipe da MD precisa olhar agora, do mais grave para o menos grave, cada item com link para resolver."""
    itens = []
    diags = conn.execute(
        "SELECT d.sensor_mac, d.causa, d.gravidade, d.inicio, d.chave, s.nome_local, c.nome AS empresa FROM diagnosticos d "
        "JOIN sensores s ON s.mac_id = d.sensor_mac LEFT JOIN clientes c ON c.id = s.cliente_id "
        "WHERE d.estado = 'ativo' AND COALESCE(s.em_manutencao, 0) = 0 ORDER BY d.inicio DESC LIMIT 50").fetchall()
    com_queda = set()
    for d in diags:
        if d['chave'] == 'sem_comunicacao':
            com_queda.add(d['sensor_mac'])
        itens.append({'nivel': GRAVIDADE.get(d['gravidade'], 'atencao'), 'titulo': d['causa'] or 'Diagnóstico ativo',
                      'site': d['nome_local'] or d['sensor_mac'], 'empresa': d['empresa'], 'desde': d['inicio'],
                      'link': f"/sensor/{d['sensor_mac']}"})
    for s in conn.execute("SELECT s.mac_id, s.nome_local, s.last_seen, c.nome AS empresa FROM sensores s "
                          "LEFT JOIN clientes c ON c.id = s.cliente_id "
                          "WHERE COALESCE(s.status, '') <> 'online' AND COALESCE(s.em_manutencao, 0) = 0").fetchall():
        if s['mac_id'] not in com_queda:
            itens.append({'nivel': 'critico', 'titulo': 'Sensor offline', 'site': s['nome_local'] or s['mac_id'],
                          'empresa': s['empresa'], 'desde': _epoch(s['last_seen']), 'link': f"/sensor/{s['mac_id']}"})
    soltos = sem_empresa(conn)
    if soltos:
        itens.append({'nivel': 'atencao', 'titulo': f"{soltos} sensor{'es' if soltos != 1 else ''} sem empresa",
                      'site': 'Alocar a um cliente ou à MD', 'empresa': None, 'desde': None, 'link': '/sensores'})
    if build_atual:
        antigos = conn.execute(
            "SELECT COUNT(*) AS n FROM sensores WHERE agent_build IS NOT NULL AND agent_build <> '' AND agent_build <> ? "
            "AND COALESCE(status, '') = 'online'", (build_atual,)).fetchone()['n']
        if antigos:
            itens.append({'nivel': 'info', 'titulo': f"{antigos} sensor{'es' if antigos != 1 else ''} com versão antiga",
                          'site': 'Atualizar pela tela de sensores', 'empresa': None, 'desde': None, 'link': '/sensores'})
    ordem = {'critico': 0, 'atencao': 1, 'info': 2}
    return sorted(itens, key=lambda i: (ordem[i['nivel']], -(i['desde'] or 0)))


TIPOS_EVENTO = {'Queda de Conexão': 'queda', 'Conexão Restaurada': 'volta', 'Comando Remoto': 'acao',
                'Máquina reiniciada': 'acao', 'Acknowledge (Ciente)': 'acao'}


def eventos_recentes(conn, limite=12, agora=None):
    """Linha do tempo: registros do log e diagnósticos abertos/resolvidos nos últimos 7 dias, mais recentes primeiro."""
    agora = agora or time.time()
    desde = agora - 7 * 86400
    eventos = []
    for l in conn.execute(
            "SELECT l.sensor_mac, l.tipo_evento, l.gravidade, l.detalhes, l.data_hora, s.nome_local FROM logs_ia l "
            "LEFT JOIN sensores s ON s.mac_id = l.sensor_mac ORDER BY l.id DESC LIMIT ?", (limite * 2,)).fetchall():
        quando = _epoch(l['data_hora'])
        if quando is None or quando < desde:
            continue
        tipo = TIPOS_EVENTO.get(l['tipo_evento']) or ('queda' if GRAVIDADE.get(l['gravidade']) == 'critico' else 'alerta')
        sistema = l['sensor_mac'] in (None, '', 'SISTEMA')
        eventos.append({'quando': quando, 'tipo': tipo, 'titulo': l['tipo_evento'], 'detalhe': l['detalhes'],
                        'site': None if sistema else (l['nome_local'] or l['sensor_mac']),
                        'link': None if sistema else f"/sensor/{l['sensor_mac']}"})
    for d in conn.execute(
            "SELECT d.sensor_mac, d.causa, d.inicio, d.fim, d.estado, s.nome_local FROM diagnosticos d "
            "LEFT JOIN sensores s ON s.mac_id = d.sensor_mac WHERE d.chave <> 'sem_comunicacao' "
            "AND (d.inicio >= ? OR d.fim >= ?) ORDER BY COALESCE(d.fim, d.inicio) DESC LIMIT 200", (desde, desde)).fetchall():
        # Um evento por diagnóstico: aberto agora, ou resolvido (com quanto durou).
        resolvido = d['fim'] and d['estado'] != 'ativo'
        eventos.append({'quando': d['fim'] if resolvido else d['inicio'], 'tipo': 'volta' if resolvido else 'alerta',
                        'titulo': d['causa'], 'detalhe': f"Resolvido após {duracao(d['fim'] - d['inicio'])}" if resolvido else 'Em aberto',
                        'site': d['sensor_mac'] and (d['nome_local'] or d['sensor_mac']), 'link': f"/sensor/{d['sensor_mac']}"})
    eventos.sort(key=lambda e: e['quando'], reverse=True)
    # O mesmo problema repetido no mesmo site (link instável que cai e volta várias vezes) vira uma linha só,
    # com quantas vezes aconteceu: a linha do tempo não fica tomada por um único site.
    agrupados = []
    for e in eventos:
        anterior = agrupados[-1] if agrupados else None
        if anterior and anterior['site'] == e['site'] and anterior['titulo'] == e['titulo']:
            anterior['vezes'] += 1
            anterior['primeiro'] = e['quando']
            if e['tipo'] == 'alerta' and e['detalhe'] == 'Em aberto':
                anterior['tipo'], anterior['detalhe'] = 'alerta', 'Em aberto'
            continue
        agrupados.append({**e, 'vezes': 1, 'primeiro': e['quando']})
    return agrupados[:limite]
