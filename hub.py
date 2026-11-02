"""NexoHub: resumos da página inicial (um card por aplicativo e a visão da MD com todos os clientes)."""
import acessos

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
        saida.append(card)
    # Liberados primeiro, depois "em breve", vitrine por último.
    ordem = {'liberado': 0, 'em_breve': 1, 'vitrine': 2}
    return sorted(saida, key=lambda c: ordem[c['estado']])


def visao_md(conn):
    """Uma linha por empresa cliente: aplicativos contratados, sites e alertas ativos."""
    empresas = conn.execute("SELECT id, nome, usuario, ativo FROM clientes WHERE role = 'Cliente' ORDER BY nome").fetchall()
    linhas = []
    for e in empresas:
        r = resumo_pulse(conn, e['id'])
        linhas.append({'id': e['id'], 'nome': e['nome'] or e['usuario'], 'ativo': bool(e['ativo']),
                       'contrato': acessos.contrato(conn, e['id']), 'sites': r['sites'], 'online': r['online'],
                       'offline': r['offline'], 'manutencao': r['manutencao'], 'alertas': r['total_diagnosticos'],
                       'ultima_queda': r['ultima_queda']})
    # Quem tem problema agora aparece primeiro.
    return sorted(linhas, key=lambda l: (-(l['offline'] + l['alertas']), l['nome'].lower()))


def sem_empresa(conn):
    """Sensores ainda não alocados a uma empresa cliente (a MD precisa ver)."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM sensores s LEFT JOIN clientes c ON c.id = s.cliente_id "
        "WHERE c.id IS NULL OR c.role <> 'Cliente'").fetchone()['n']
