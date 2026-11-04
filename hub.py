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
        "LEFT JOIN sensores s ON s.mac_id = d.sensor_mac WHERE d.estado = 'ativo'" + junta +
        ' ORDER BY d.inicio DESC LIMIT 5', params).fetchall()
    total_diags = conn.execute(
        "SELECT COUNT(*) AS n FROM diagnosticos d LEFT JOIN sensores s ON s.mac_id = d.sensor_mac "
        "WHERE d.estado = 'ativo'" + junta, params).fetchone()['n']
    queda = conn.execute(
        "SELECT d.inicio, d.fim, d.estado, s.nome_local, d.sensor_mac FROM diagnosticos d "
        "LEFT JOIN sensores s ON s.mac_id = d.sensor_mac WHERE d.chave = 'sem_comunicacao'" + junta +
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


def disponibilidade_mes(conn, empresa_id=None, agora=None):
    """Percentual do mês corrente em que os sites tiveram comunicação com a Central.

    Conta as quedas "sem comunicação" de cada site fora de manutenção, cortadas no início do período do site
    (início do mês ou primeira medição dele no mês). Devolve None quando não há site para medir."""
    agora = agora or time.time()
    inicio, mes = inicio_do_mes(agora)
    filtro, params = ('', ()) if empresa_id is None else (' AND cliente_id = ?', (empresa_id,))
    macs = [r['mac_id'] for r in conn.execute(
        'SELECT mac_id FROM sensores WHERE COALESCE(em_manutencao, 0) = 0' + filtro, params).fetchall()]
    # Cada site conta do início do mês ou da primeira medição dele no mês: sensor instalado no meio do mês
    # não ganha dias que não monitorou. Uma queda aberta antes do mês prova que o site já existia no dia 1.
    comeco = {}
    for mac in macs:
        aberta = conn.execute("SELECT 1 FROM diagnosticos WHERE sensor_mac = ? AND chave = 'sem_comunicacao' "
                              'AND inicio < ? AND (fim IS NULL OR fim > ?) LIMIT 1', (mac, inicio, inicio)).fetchone()
        primeira = conn.execute('SELECT MIN(coletado_em) AS t FROM medicoes_rede WHERE sensor_mac = ? AND coletado_em >= ?',
                                (mac, inicio)).fetchone()['t']
        # Sem medição no mês (agente antigo, que não envia medições): conta o mês inteiro, como antes.
        comeco[mac] = inicio if aberta or primeira is None else min(agora, max(inicio, primeira))
    periodo = sum(agora - c for c in comeco.values())
    if periodo <= 0:
        return None
    marcas = ','.join('?' * len(comeco))
    quedas = conn.execute(
        "SELECT sensor_mac, inicio, fim FROM diagnosticos WHERE chave = 'sem_comunicacao' "
        f'AND sensor_mac IN ({marcas}) AND inicio < ? AND (fim IS NULL OR fim > ?)',
        (*comeco, agora, inicio)).fetchall()
    por_site = {}
    for q in quedas:
        ini, fim = max(q['inicio'], comeco[q['sensor_mac']]), min(q['fim'] or agora, agora)
        if fim > ini:
            por_site.setdefault(q['sensor_mac'], []).append((ini, fim))
    quedas = [q for q in quedas if q['sensor_mac'] in por_site and (q['fim'] or agora) > comeco[q['sensor_mac']]]
    fora = sum(fim - ini for trechos in por_site.values() for ini, fim in _unir(trechos))
    pct = 100 * (1 - fora / periodo)
    # Arredonda para baixo: com qualquer queda no mês o número nunca chega a 100%.
    exibido = math.floor(pct * 10) / 10
    if fora > 0 and exibido >= 100:
        exibido = 99.9
    return {'pct': exibido, 'mes': mes, 'quedas': len(quedas), 'fora_s': int(fora), 'fora_txt': duracao(fora),
            'faixa': 'ok' if exibido >= 99.5 else ('atencao' if exibido >= 98 else 'critico')}


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
