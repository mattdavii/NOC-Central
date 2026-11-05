"""Diagrama da rede do sensor: o que o sensor enxerga mais o que a equipe informa.

O sensor só vê a rede "achatada" (todo mundo responde pelo mesmo gateway). Quem esteve no
local sabe que a câmera passa por um switch, que o switch chega por um conversor de fibra etc.
Este módulo junta as duas coisas:

- a árvore parte do que foi observado (Internet → gateway → cada equipamento);
- o mapa salvo por sensor (`sensores.mapa_rede`, JSON) diz, para qualquer ponto, em quem ele
  liga, por qual meio e que tipo de equipamento é, e acrescenta equipamentos que o sensor não
  enxerga (switch não gerenciável, conversor de mídia, rádio...).

A mesma árvore alimenta o desenho no painel, o relatório (SVG feito aqui, sem depender de
JavaScript, para imprimir bem) e o levantamento.
"""
import html
import json
import re

TIPOS = {
    'roteador': 'Roteador / gateway', 'switch': 'Switch', 'conversor': 'Conversor de mídia',
    'ap': 'Access point (Wi-Fi)', 'radio': 'Rádio / enlace', 'onu': 'ONU / ONT (fibra)',
    'starlink': 'Antena Starlink', 'modem': 'Modem', 'firewall': 'Firewall',
    'camera': 'Câmera', 'nvr': 'NVR / DVR', 'computador': 'Computador', 'servidor': 'Servidor',
    'inversor': 'Inversor solar', 'datalogger': 'Datalogger', 'clp': 'CLP / controlador',
    'medidor': 'Medidor de energia', 'impressora': 'Impressora', 'telefone': 'Telefone IP',
    'iot': 'Dispositivo IoT', 'dispositivo': 'Dispositivo', 'outro': 'Outro',
}
# Tipos que só existem no diagrama (não são escolhidos pela pessoa).
TIPOS_FIXOS = {'internet': 'Internet', 'sensor': 'Sensor NexoPulse'}
INFRA = {'roteador', 'switch', 'conversor', 'ap', 'radio', 'onu', 'starlink', 'modem', 'firewall', 'internet'}
MEIOS = {'cabo': 'Cabo de rede', 'fibra': 'Fibra óptica', 'wifi': 'Wi-Fi', 'radio': 'Rádio', 'serial': 'Serial / RS-485', 'outro': 'Outro'}

ID_NO = re.compile(r'^(gateway|sensor|d:[0-9A-F]{2}(:[0-9A-F]{2}){5}|ip:[0-9]{1,3}(\.[0-9]{1,3}){3}|m:[a-z0-9]{1,12})$')
MAX_EXTRAS = 200
MAX_NOS = 1500


def _texto(valor, limite, campo):
    if valor in (None, ''):
        return None
    if not isinstance(valor, str):
        raise ValueError(f'{campo} inválido.')
    valor = ' '.join(valor.split())
    if len(valor) > limite:
        raise ValueError(f'{campo} pode ter até {limite} caracteres.')
    return valor or None


def validar(dados):
    """Normaliza o mapa enviado pela tela. Levanta ValueError com mensagem para a pessoa."""
    if not isinstance(dados, dict):
        raise ValueError('Mapa inválido.')
    extras_in, nos_in = dados.get('extras') or [], dados.get('nos') or {}
    if not isinstance(extras_in, list) or not isinstance(nos_in, dict):
        raise ValueError('Mapa inválido.')
    if len(extras_in) > MAX_EXTRAS:
        raise ValueError(f'Use até {MAX_EXTRAS} equipamentos adicionados.')
    if len(nos_in) > MAX_NOS:
        raise ValueError('Mapa grande demais.')
    extras, ids = [], set()
    for e in extras_in:
        if not isinstance(e, dict) or not isinstance(e.get('id'), str) or not e['id'].startswith('m:') or not ID_NO.match(e['id']):
            raise ValueError('Equipamento adicionado com identificador inválido.')
        if e['id'] in ids:
            raise ValueError('Equipamento repetido no mapa.')
        ids.add(e['id'])
        nome = _texto(e.get('nome'), 60, 'Nome do equipamento')
        if not nome:
            raise ValueError('Dê um nome a cada equipamento adicionado.')
        extras.append({'id': e['id'], 'nome': nome, 'obs': _texto(e.get('obs'), 120, 'Observação')})
    nos = {}
    for chave, valor in nos_in.items():
        if not isinstance(chave, str) or not ID_NO.match(chave) or not isinstance(valor, dict):
            raise ValueError('Ligação com identificador inválido.')
        item = {}
        pai = valor.get('pai')
        if pai not in (None, ''):
            if not isinstance(pai, str) or not (pai == 'internet' or ID_NO.match(pai)) or pai == chave:
                raise ValueError('Ligação com destino inválido.')
            item['pai'] = pai
        for campo, opcoes in (('tipo', TIPOS), ('meio', MEIOS)):
            v = valor.get(campo)
            if v not in (None, ''):
                if v not in opcoes:
                    raise ValueError(f'{campo.capitalize()} desconhecido: {v}.')
                item[campo] = v
        if chave.startswith('m:') and chave not in ids:
            continue  # sobra de um equipamento removido
        if item:
            nos[chave] = item
    return {'versao': 1, 'extras': extras, 'nos': nos}


def carregar(texto):
    try:
        dados = json.loads(texto) if texto else None
        return validar(dados) if dados else {'versao': 1, 'extras': [], 'nos': {}}
    except (ValueError, TypeError):
        return {'versao': 1, 'extras': [], 'nos': {}}


def id_dispositivo(d):
    mac = (d.get('mac') or '').strip().upper().replace('-', ':')
    if re.match(r'^[0-9A-F]{2}(:[0-9A-F]{2}){5}$', mac):
        return 'd:' + mac
    ip = (d.get('ip') or '').strip()
    return 'ip:' + ip if re.match(r'^[0-9]{1,3}(\.[0-9]{1,3}){3}$', ip) else None


def _tipo_observado(d):
    if d.get('cftv'):
        return 'camera'
    tipo = (d.get('tipo_equipamento') or '').lower()
    for chave, palavras in (('nvr', ('nvr', 'dvr')), ('camera', ('câmera', 'camera')), ('inversor', ('inversor',)),
                            ('datalogger', ('datalogger', 'logger')), ('clp', ('clp', 'plc')), ('switch', ('switch',)),
                            ('ap', ('access point', 'wi-fi')), ('impressora', ('impressora', 'printer'))):
        if any(p in tipo for p in palavras):
            return chave
    return 'dispositivo'


def montar(sensor, dispositivos, mapa):
    """Lista de nós {id, nome, sub, tipo, pai, meio, status, informado} já sem ciclos."""
    sensor = dict(sensor)
    ip_gw, ip_sensor = sensor.get('ip_gateway'), sensor.get('ip_sensor')
    cfg = mapa.get('nos', {})
    nos = {'internet': {'id': 'internet', 'nome': 'Internet', 'sub': sensor.get('provedor'), 'tipo': 'internet', 'status': None}}
    ping_gw = sensor.get('ping_gateway')
    if ip_gw:
        nos['gateway'] = {'id': 'gateway', 'nome': 'Gateway', 'sub': ip_gw, 'tipo': 'roteador',
                          'status': 'online' if ping_gw and 0 < float(ping_gw) < 1000 else ('offline' if ping_gw is not None else None)}
    nos['sensor'] = {'id': 'sensor', 'nome': sensor.get('nome_local') or 'Sensor NexoPulse', 'sub': ip_sensor,
                     'tipo': 'sensor', 'status': 'online' if sensor.get('status') == 'ONLINE' else None}
    for d in (dict(x) for x in dispositivos):
        if d.get('ip') in (ip_gw, ip_sensor):
            if d.get('ip') == ip_gw and 'gateway' in nos and d.get('mac'):
                nos['gateway']['mac'] = d.get('mac')
            continue
        ident = id_dispositivo(d)
        if not ident or ident in nos:
            continue
        nome = d.get('nome_custom') or d.get('hostname') or d.get('fabricante') or 'Desconhecido'
        if nome in ('Desconhecido', 'Gateway / Roteador') and d.get('fabricante'):
            nome = d['fabricante']
        nos[ident] = {'id': ident, 'nome': nome, 'sub': d.get('ip'), 'tipo': _tipo_observado(d),
                      'status': d.get('status'), 'mac': d.get('mac'), 'fabricante': d.get('fabricante')}
    for e in mapa.get('extras', []):
        nos[e['id']] = {'id': e['id'], 'nome': e['nome'], 'sub': e.get('obs'), 'tipo': 'outro', 'status': None, 'adicionado': True}

    padrao = 'gateway' if 'gateway' in nos else 'internet'
    for no in nos.values():
        c = cfg.get(no['id'], {})
        if c.get('tipo') and no['tipo'] not in TIPOS_FIXOS:
            no['tipo'] = c['tipo']
        no['meio'] = c.get('meio')
        no['informado'] = bool(c.get('pai'))
        if no['id'] == 'internet':
            no['pai'] = None
        elif c.get('pai') in nos:
            no['pai'] = c['pai']
        else:
            no['pai'] = 'internet' if no['id'] == 'gateway' else padrao
    # Ligação que fecha um ciclo (A liga em B que liga em A) volta para o padrão.
    for no in nos.values():
        vistos, atual = {no['id']}, no['pai']
        while atual:
            if atual in vistos:
                no['pai'] = 'internet' if no['id'] == 'gateway' else (padrao if padrao != no['id'] else 'internet')
                no['informado'] = False
                break
            vistos.add(atual)
            atual = nos[atual]['pai']
    for no in nos.values():
        no['tipo_rotulo'] = TIPOS_FIXOS.get(no['tipo']) or TIPOS.get(no['tipo'], no['tipo'])
        no['meio_rotulo'] = MEIOS.get(no['meio']) if no['meio'] else None
    return list(nos.values())


def _ordem(no):
    infra = 0 if no['tipo'] in INFRA or no.get('adicionado') else (1 if no['tipo'] == 'sensor' else 2)
    ip = no.get('sub') or ''
    partes = [int(p) for p in ip.split('.')] if re.match(r'^[0-9]{1,3}(\.[0-9]{1,3}){3}$', ip) else [999]
    return (infra, partes, no['nome'].lower())


def linhas(arvore):
    """Ordem de leitura (pai antes dos filhos) com a profundidade de cada nó."""
    filhos = {}
    for no in arvore:
        filhos.setdefault(no['pai'], []).append(no)
    saida = []

    def descer(pai, nivel):
        for no in sorted(filhos.get(pai, []), key=_ordem):
            saida.append((no, nivel))
            descer(no['id'], nivel + 1)
    descer(None, 0)
    return saida


COR_STATUS = {'online': '#05A88C', 'sem_icmp': '#E39B2D', 'offline': '#DC2626'}


def svg(arvore, largura_caixa=200, altura_caixa=42, coluna=250, linha=54):
    """Árvore da esquerda (Internet) para a direita, uma folha por linha."""
    ordem = linhas(arvore)
    if not ordem:
        return ''
    filhos = {}
    for no in arvore:
        filhos.setdefault(no['pai'], []).append(no['id'])
    pos, prox = {}, [0]

    def y_de(ident):
        if ident in pos:
            return pos[ident]
        if not filhos.get(ident):
            pos[ident] = prox[0] * linha
            prox[0] += 1
        return pos.get(ident)

    profundidade = {}
    for no, nivel in ordem:
        profundidade[no['id']] = nivel
    # Folhas na ordem de leitura; o pai fica no meio do primeiro e do último filho.
    for no, _ in ordem:
        y_de(no['id'])
    for no, _ in reversed(ordem):
        if no['id'] not in pos:
            ys = [pos[f] for f in filhos.get(no['id'], [])]
            pos[no['id']] = (min(ys) + max(ys)) / 2
    margem = 10
    largura = (max(profundidade.values()) * coluna) + largura_caixa + margem * 2
    altura = max(prox[0], 1) * linha + margem * 2 - (linha - altura_caixa)
    por_id = {no['id']: no for no in arvore}
    e = html.escape
    partes = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura} {altura}" width="100%" '
              f'role="img" aria-label="Diagrama da rede" font-family="IBM Plex Sans, Inter, Arial, sans-serif">']
    for no, nivel in ordem:
        if not no['pai']:
            continue
        x1 = margem + profundidade[no['pai']] * coluna + largura_caixa
        y1 = margem + pos[no['pai']] + altura_caixa / 2
        x2 = margem + nivel * coluna
        y2 = margem + pos[no['id']] + altura_caixa / 2
        meio = (x1 + x2) / 2
        cor = '#E39B2D' if no['meio'] == 'fibra' else '#8EA3B8'
        traco = ' stroke-dasharray="6 4"' if no['meio'] in ('wifi', 'radio') else (' stroke-dasharray="2 3"' if no['meio'] == 'serial' else '')
        partes.append(f'<path d="M{x1:.0f} {y1:.0f} H{meio:.0f} V{y2:.0f} H{x2:.0f}" fill="none" stroke="{cor}" stroke-width="1.6"{traco}/>')
        if no['meio_rotulo']:
            partes.append(f'<text x="{meio + 4:.0f}" y="{y2 - 4:.0f}" font-size="9" fill="#526077">{e(no["meio_rotulo"])}</text>')
    for no, nivel in ordem:
        x, y = margem + nivel * coluna, margem + pos[no['id']]
        infra = no['tipo'] in INFRA or no.get('adicionado')
        fundo, borda, cor_txt, cor_sub = (('#082E56', '#082E56', '#FFFFFF', '#BBD3E6') if infra else
                                         ('#E6F6F3', '#05A88C', '#082E56', '#3E5A73') if no['tipo'] == 'sensor' else
                                         ('#FFFFFF', '#CFDCE8', '#082E56', '#526077'))
        partes.append(f'<rect x="{x}" y="{y:.0f}" width="{largura_caixa}" height="{altura_caixa}" rx="7" fill="{fundo}" stroke="{borda}" stroke-width="1.2"/>')
        nome = no['nome'] if len(no['nome']) <= 28 else no['nome'][:27] + '…'
        sub = ' · '.join(p for p in (no['tipo_rotulo'] if no['tipo'] not in ('dispositivo', 'internet') else None, no.get('sub')) if p)
        sub = sub if len(sub) <= 36 else sub[:35] + '…'
        if no.get('status') in COR_STATUS:
            partes.append(f'<circle cx="{x + largura_caixa - 10}" cy="{y + 10:.0f}" r="4" fill="{COR_STATUS[no["status"]]}"/>')
        partes.append(f'<text x="{x + 10}" y="{y + 18:.0f}" font-size="12" font-weight="600" fill="{cor_txt}">{e(nome)}</text>')
        if sub:
            partes.append(f'<text x="{x + 10}" y="{y + 33:.0f}" font-size="10" fill="{cor_sub}">{e(sub)}</text>')
    partes.append('</svg>')
    return ''.join(partes)


def resumo_ligacoes(arvore):
    """Linhas "equipamento → liga em" para planilha e JSON."""
    por_id = {no['id']: no for no in arvore}
    saida = []
    for no, nivel in linhas(arvore):
        if not no['pai']:
            continue
        pai = por_id[no['pai']]
        saida.append({'equipamento': no['nome'], 'endereco': no.get('sub'), 'tipo': no['tipo_rotulo'],
                      'liga_em': pai['nome'], 'meio': no['meio_rotulo'], 'nivel': nivel,
                      'origem': 'informada' if no['informado'] else 'observada', 'adicionado': bool(no.get('adicionado'))})
    return saida
