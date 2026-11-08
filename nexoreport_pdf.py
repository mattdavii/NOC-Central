"""NexoReport: o PDF do relatório mensal, desenhado a partir do JSON do fechamento (nexoreport.consolidar).

Nada aqui consulta o banco: o mesmo JSON gera sempre o mesmo PDF. Os textos ("Em resumo" e as
recomendações) saem de regras fixas sobre os números; não há texto livre nem IA.
"""
import math
import os
from collections import Counter
from datetime import datetime

import hub
import mapa_rede
import nexoreport

RAIZ = os.path.dirname(os.path.abspath(__file__))
FUSO_BR = hub.FUSO_BR
CORES_SITUACAO = {'ok': '#0BA98F', 'atencao': '#E39B2D', 'critico': '#D64545'}
ROTULO_SITUACAO = {'ok': 'Dentro da meta', 'atencao': 'Abaixo da meta', 'critico': 'Crítico'}
CONFIANCA = {'alta': 'Alta', 'media': 'Média', 'baixa': 'Baixa', None: '—'}
MESES_CURTOS = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez')


# ------------------------------------------------------------------ formatos
def pct_txt(v):
    return '—' if v is None else f'{v:.1f}%'.replace('.', ',')


def num_txt(v, casas=1, sufixo=''):
    return '—' if v is None else f'{v:.{casas}f}'.replace('.', ',') + sufixo


def dur_txt(segundos):
    m = int(round((segundos or 0) / 60))
    if m < 1:
        return 'menos de 1 min'
    if m < 60:
        return f'{m} min'
    h, m = divmod(m, 60)
    if h < 48:
        return f'{h}h{m:02d}'
    d, h = divmod(h, 24)
    return f'{d} d {h} h'


def data_txt(epoch, hora=True):
    d = datetime.fromtimestamp(epoch, FUSO_BR)
    return d.strftime('%d/%m %H:%M' if hora else '%d/%m/%Y')


def plural(n, um, varios):
    return f'{n} {um if n == 1 else varios}'


# ------------------------------------------------------------------ gráficos (SVG)
def svg_fundo_capa():
    """Prancheta técnica: grade que some para baixo, dois brilhos e um traço de sinal nas cores da marca."""
    w, h, passo = 210, 297, 7
    p = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297" width="210mm" height="297mm">',
         '<defs><radialGradient id="g1" cx="0.15" cy="0.12" r="0.55"><stop offset="0" stop-color="#12B5E5" stop-opacity=".28"/><stop offset="1" stop-color="#12B5E5" stop-opacity="0"/></radialGradient>',
         '<radialGradient id="g2" cx="0.95" cy="0.55" r="0.5"><stop offset="0" stop-color="#7A3FE0" stop-opacity=".26"/><stop offset="1" stop-color="#7A3FE0" stop-opacity="0"/></radialGradient></defs>',
         f'<rect width="{w}" height="{h}" fill="url(#g1)"/><rect width="{w}" height="{h}" fill="url(#g2)"/>']
    for x in range(0, w + 1, passo):
        p.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="#A9BED3" stroke-opacity=".06" stroke-width=".15"/>')
    for y in range(0, h + 1, passo):
        op = max(0.0, 0.09 * (1 - y / 190))
        if op > 0:
            p.append(f'<line x1="0" y1="{y}" x2="{w}" y2="{y}" stroke="#A9BED3" stroke-opacity="{op:.3f}" stroke-width=".15"/>')
    pontos = [(118, 47), (150, 47), (154, 42), (159, 55), (164, 34), (169, 51), (173, 47), (210, 47)]
    cores = ['#7A3FE0', '#5A62E2', '#3A85E4', '#12B5E5', '#0FB5CF', '#0DB4B4', '#0BB39A']
    for k in range(len(pontos) - 1):  # um segmento por cor: o PDF não pinta degradê em linha
        (x1, y1), (x2, y2) = pontos[k], pontos[k + 1]
        p.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{cores[k]}" stroke-width=".8" stroke-linecap="round" stroke-opacity=".9"/>')
    p.append('<circle cx="164" cy="34" r="1.2" fill="#12B5E5"/><circle cx="164" cy="34" r="2.8" fill="#12B5E5" fill-opacity=".18"/>')
    for x in (124, 138, 186, 198):
        p.append(f'<circle cx="{x}" cy="47" r=".7" fill="#A9BED3" fill-opacity=".5"/>')
    p.append('</svg>')
    return ''.join(p)


def svg_medidor(valor, meta):
    """Semicírculo de escala técnica: zonas pela meta do contrato, marca da meta e o valor do mês sobre o arco."""
    if valor is None:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 140"><text x="120" y="80" font-size="16" text-anchor="middle" fill="#A9BED3" font-family="Plex">Sem medição no mês</text></svg>'
    mini = math.floor(min(meta - 1.5, valor - 0.3) * 2) / 2
    cx, cy, r = 120, 112, 92

    def ponto(v, raio):
        ang = math.pi * (1 - (min(max(v, mini), 100) - mini) / (100 - mini))
        return cx + raio * math.cos(ang), cy - raio * math.sin(ang)

    def arco(v1, v2, cor, larg=12):
        v1, v2 = max(v1, mini), min(v2, 100)
        if v2 <= v1:
            return ''
        x1, y1 = ponto(v1, r)
        x2, y2 = ponto(v2, r)
        return f'<path d="M{x1:.1f} {y1:.1f} A{r} {r} 0 0 1 {x2:.1f} {y2:.1f}" fill="none" stroke="{cor}" stroke-width="{larg}"/>'

    p = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 140" font-family="Plex">',
         arco(mini, 100, 'rgba(169,190,211,.14)', 14), arco(mini, meta - 1, '#D64545'), arco(meta - 1, meta, '#E39B2D'), arco(meta, 100, '#0BB39A')]
    v = mini
    while v <= 100.001:
        inteiro = abs(v - round(v)) < 1e-6
        x1, y1 = ponto(v, r - 11)
        x2, y2 = ponto(v, r - (17 if inteiro else 14))
        p.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#A9BED3" stroke-opacity=".7" stroke-width=".8"/>')
        if inteiro or v <= mini + 1e-6:
            tx, ty = ponto(v, r - 25)
            if v <= mini + 1e-6 or v >= 100 - 1e-6:  # nas pontas, o número vai embaixo do arco
                tx, ty = ponto(v, r)
                ty += 14
            rotulo = str(int(round(v))) if inteiro else pct_txt(v).rstrip('%')
            p.append(f'<text x="{tx:.1f}" y="{ty + 3:.1f}" font-size="7.5" text-anchor="middle" fill="#A9BED3">{rotulo}</text>')
        v += 0.5
    mx1, my1 = ponto(meta, r - 9)
    mx2, my2 = ponto(meta, r + 11)
    lx, ly = ponto(meta, r + 19)
    p.append(f'<line x1="{mx1:.1f}" y1="{my1:.1f}" x2="{mx2:.1f}" y2="{my2:.1f}" stroke="#fff" stroke-width="1.6"/>')
    p.append(f'<text x="{lx:.1f}" y="{ly + 2:.1f}" font-size="7.5" font-weight="600" text-anchor="middle" fill="#fff">meta {pct_txt(meta)}</text>')
    vx, vy = ponto(valor, r)
    p.append(f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="9" fill="#fff" fill-opacity=".18"/>')
    p.append(f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="5.5" fill="#061529" stroke="#fff" stroke-width="2.2"/>')
    p.append(f'<text x="{cx}" y="{cy - 6}" font-size="34" font-weight="800" font-family="Bricolage" text-anchor="middle" fill="#fff">{pct_txt(valor)}</text>')
    p.append('</svg>')
    return ''.join(p)


def svg_tendencia(pontos, meta):
    validos = [x['pct'] for x in pontos if x['pct'] is not None]
    base = math.floor(min(validos + [meta]) - 1) if validos else meta - 2
    w, h = 300, 120
    passo = w / len(pontos)
    p = [f'<svg viewBox="0 0 {w} {h + 22}" xmlns="http://www.w3.org/2000/svg" font-family="Plex">']
    ym = h - (meta - base) / (100 - base) * h
    p.append(f'<line x1="0" x2="{w}" y1="{ym:.1f}" y2="{ym:.1f}" stroke="#fff" stroke-opacity=".55" stroke-width=".8" stroke-dasharray="3 3"/>')
    for i, ponto in enumerate(pontos):
        ano, mes = map(int, ponto['ano_mes'].split('-'))
        x = i * passo + 8
        centro = x + (passo - 16) / 2
        p.append(f'<text x="{centro:.1f}" y="{h + 14}" font-size="9" text-anchor="middle" fill="#A9BED3">{MESES_CURTOS[mes - 1]}</text>')
        v = ponto['pct']
        if v is None:
            p.append(f'<rect x="{x:.1f}" y="{h - 4}" width="{passo - 16:.1f}" height="4" rx="2" fill="rgba(169,190,211,.2)"/>')
            continue
        ultimo = i == len(pontos) - 1
        altura = max(4, (v - base) / (100 - base) * h)
        cor = '#12B5E5' if ultimo else ('rgba(169,190,211,.45)' if v >= meta else 'rgba(227,155,45,.6)')
        p.append(f'<rect x="{x:.1f}" y="{h - altura:.1f}" width="{passo - 16:.1f}" height="{altura:.1f}" rx="3" fill="{cor}"/>')
        p.append(f'<text x="{centro:.1f}" y="{h - altura - 4:.1f}" font-size="9" text-anchor="middle" fill="{"#fff" if ultimo else "#A9BED3"}">{pct_txt(v)}</text>')
    p.append('</svg>')
    return ''.join(p)


def svg_diario(dias, meta):
    validos = [d['pct'] for d in dias if d['pct'] is not None]
    base = min(96.0, math.floor(min(validos)) if validos else 96.0)
    w, h = 520, 120
    passo = w / max(len(dias), 1)
    p = [f'<svg viewBox="-28 -8 {w + 30} {h + 28}" xmlns="http://www.w3.org/2000/svg" font-family="Plex">']
    for ref in sorted({base, (base + 100) / 2, 100.0}):
        y = h - (ref - base) / (100 - base) * h
        p.append(f'<line x1="0" x2="{w}" y1="{y:.1f}" y2="{y:.1f}" stroke="#D5E0EA" stroke-width=".8"/>')
        p.append(f'<text x="-4" y="{y + 3:.1f}" font-size="8" text-anchor="end" fill="#6B7F95">{num_txt(ref, 0)}%</text>')
    ym = h - (meta - base) / (100 - base) * h
    p.append(f'<line x1="0" x2="{w}" y1="{ym:.1f}" y2="{ym:.1f}" stroke="#0B3460" stroke-width=".8" stroke-dasharray="3 3"/>')
    for i, d in enumerate(dias):
        x = i * passo + 1.5
        if d['pct'] is None:
            p.append(f'<rect x="{x:.1f}" y="{h - 2}" width="{passo - 3:.1f}" height="2" fill="#D5E0EA"/>')
        else:
            altura = max(2, (min(d['pct'], 100) - base) / (100 - base) * h)
            p.append(f'<rect x="{x:.1f}" y="{h - altura:.1f}" width="{passo - 3:.1f}" height="{altura:.1f}" rx="1.5" fill="{CORES_SITUACAO[hub.situacao(d["pct"], meta)]}"/>')
        if d['dia'] in (1, 5, 10, 15, 20, 25, 30):
            p.append(f'<text x="{i * passo + passo / 2:.1f}" y="{h + 13}" font-size="8" text-anchor="middle" fill="#6B7F95">{d["dia"]}</text>')
    p.append('</svg>')
    return ''.join(p)


def svg_barras_h(itens, cor='#0B3460', destaque=None):
    if not itens:
        return ''
    w, linha = 380, 28
    maximo = max(v for _, v, _ in itens) or 1
    p = [f'<svg viewBox="0 0 {w} {len(itens) * linha}" xmlns="http://www.w3.org/2000/svg" font-family="Plex">']
    for i, (rotulo, valor, texto) in enumerate(itens):
        y = i * linha
        largura = max(3, valor / maximo * 120)
        p.append(f'<text x="0" y="{y + 16}" font-size="11" fill="#142B45">{rotulo}</text>')
        p.append(f'<rect x="132" y="{y + 6}" width="{largura:.1f}" height="13" rx="3" fill="{destaque if destaque and i == 0 else cor}"/>')
        p.append(f'<text x="{132 + largura + 6:.1f}" y="{y + 16}" font-size="11" font-weight="600" fill="#142B45">{texto}</text>')
    p.append('</svg>')
    return ''.join(p)


# ------------------------------------------------------------------ textos por regra
def _janela_concentrada(quedas):
    """Faixa de 3 horas com a maioria das quedas (ex.: obstrução no fim da tarde), ou None."""
    if len(quedas) < 3:
        return None
    horas = Counter(datetime.fromtimestamp(q['inicio'], FUSO_BR).hour for q in quedas)
    melhor = max(range(24), key=lambda h: sum(horas.get((h + k) % 24, 0) for k in range(3)))
    total = sum(horas.get((melhor + k) % 24, 0) for k in range(3))
    return (melhor, (melhor + 3) % 24, total) if total / len(quedas) >= 0.6 else None


def resumo(d):
    t, n_locais = d['totais'], len(d['locais'])
    mes = hub.MESES[d['periodo']['mes'] - 1]
    if t['pct'] is None:
        return [f'Nenhum local teve medições em {mes}.']
    frase = f'Em {mes}, a rede {"do seu local" if n_locais == 1 else f"dos seus {n_locais} locais"} ficou disponível {pct_txt(t["pct"])} do tempo'
    if d['anterior'] and d['anterior'].get('pct') is not None:
        a = d['anterior']['pct']
        frase += ', ' + ('igual ao mês anterior' if a == t['pct'] else f'{"melhor" if t["pct"] > a else "pior"} que no mês anterior ({pct_txt(a)})')
    frase += f', {"dentro" if t["situacao"] == "ok" else "abaixo"} da meta de {pct_txt(d["meta"])}.'
    frase += (f' Foram {plural(t["quedas"], "queda", "quedas")}, que somaram {dur_txt(t["fora_s"])} sem conexão.' if t['quedas']
              else ' Não houve nenhuma queda no mês.')
    paragrafos = [frase]
    if t['quedas']:
        partes = []
        principal, info = next(iter(d['causas'].items()))
        if principal != 'indefinida':
            locais = Counter(q['local'] for q in d['quedas'] if q['categoria'] == principal)
            onde = f' em {locais.most_common(1)[0][0]}' if len(locais) == 1 else ''
            texto = f'A causa mais frequente foi {info["rotulo"].lower()}{onde} ({plural(info["quedas"], "queda", "quedas")})'
            janela = _janela_concentrada([q for q in d['quedas'] if q['categoria'] == principal])
            if janela:
                texto += f', {janela[2]} delas entre {janela[0]}h e {janela[1]}h'
                if principal == 'starlink_obstrucao' and 14 <= janela[0] <= 17:
                    texto += ', o que indica sombra no fim da tarde'
            partes.append(texto + '.')
        prov = d['provedor']
        if prov:
            partes.append(f'{plural(len(prov), "queda foi atribuída", "quedas foram atribuídas")} ao provedor, com as medições guardadas para cobrança.')
        abaixo = [l['nome'] for l in d['locais'] if l['situacao'] in ('atencao', 'critico')]
        if abaixo and n_locais > 1:
            partes.append(f'{"Ficou" if len(abaixo) == 1 else "Ficaram"} abaixo da meta: {", ".join(abaixo)}.')
        if partes:
            paragrafos.append(' '.join(partes))
    return paragrafos


def recomendacoes(d):
    recs = []
    for l in d['locais']:
        sl = l.get('starlink') or {}
        if (sl.get('quedas_obstrucao') or 0) >= 3:
            recs.append(('alta', f'Verificar o campo de visão da antena Starlink em {l["nome"]}',
                         f'{sl["quedas_obstrucao"]} quedas por obstrução no mês. Recomendamos inspeção no local e, se for o caso, '
                         'poda ou reposicionamento da antena.'))
        if l['situacao'] == 'critico':
            recs.append(('alta', f'Investigar a conexão de {l["nome"]}',
                         f'O local ficou em {pct_txt(l["pct"])}, mais de 1 ponto abaixo da meta de {pct_txt(d["meta"])}.'))
        if (sl.get('desvio_medio_graus') or 0) > 2:
            recs.append(('media', f'Conferir o alinhamento da antena em {l["nome"]}',
                         f'Desvio médio de {num_txt(sl["desvio_medio_graus"])}° em relação à posição indicada pela Starlink.'))
    if d['provedor']:
        locais = sorted({q['local'] for q in d['provedor']})
        tempo = sum(q['duracao_s'] for q in d['provedor'])
        recs.append(('media', f'Pedir desconto ou abrir chamado com o provedor ({", ".join(locais)})',
                     f'{plural(len(d["provedor"]), "queda", "quedas")} com evidência de falha do provedor, {dur_txt(tempo)} no total. '
                     'A MD pode preparar o pedido com as medições anexadas.'))
    locais_lan = Counter(q['local'] for q in d['quedas'] if q['categoria'] == 'rede_local')
    for local, n in locais_lan.items():
        if n >= 2:
            recs.append(('media', f'Revisar a rede interna de {local}',
                         f'{n} quedas começaram dentro do local (roteador, cabos ou energia dos equipamentos).'))
    indef = [q for q in d['quedas'] if q['categoria'] == 'indefinida']
    if len(indef) >= 2:
        recs.append(('baixa', 'Avaliar falta de energia nos locais',
                     f'{len(indef)} quedas sem causa determinada pelas medições. Isso costuma acontecer quando falta energia '
                     'no roteador ou no sensor; um nobreak resolve e ajuda o diagnóstico.'))
    for l in d['locais']:
        q = l['qualidade']
        if (q.get('perda_pct') or 0) > 1 or (q.get('latencia_p95_ms') or 0) > 150:
            recs.append(('baixa', f'Acompanhar a qualidade da internet em {l["nome"]}',
                         f'Perda média de {num_txt(q.get("perda_pct"), 2)}% e resposta de {num_txt(q.get("latencia_p95_ms"), 0)} ms nos piores momentos.'))
        if l['inventario']['sem_identificacao'] >= 3:
            recs.append(('baixa', f'Identificar os equipamentos sem nome em {l["nome"]}',
                         f'{l["inventario"]["sem_identificacao"]} equipamentos sem marca nem nome. Na próxima visita, a equipe pode nomeá-los no diagrama.'))
    ordem = {'alta': 0, 'media': 1, 'baixa': 2}
    recs.sort(key=lambda r: ordem[r[0]])
    return [{'prioridade': p, 'titulo': t, 'texto': x} for p, t, x in recs]


METODO = [
    {'titulo': 'Disponibilidade', 'texto': 'Parte do tempo do mês em que cada local teve comunicação com a plataforma. Locais em manutenção programada não entram na conta, e o número é arredondado para baixo: qualquer queda impede os 100%.'},
    {'titulo': 'Meta', 'texto': 'A meta de disponibilidade do contrato. Dentro da meta a partir dela; abaixo da meta até 1 ponto percentual abaixo; crítico mais que isso.'},
    {'titulo': 'Queda', 'texto': 'Período em que o local deixou de se comunicar com a plataforma, registrado pelo NexoPulse. Instabilidades que não chegam a derrubar a conexão aparecem em Qualidade.'},
    {'titulo': 'Causa', 'texto': 'O sensor mede cada camada da conexão (rede interna, antena, provedor e DNS) e aponta onde o problema começou. Sem medição que sustente uma causa, a queda fica como "não determinada".'},
    {'titulo': 'Qualidade', 'texto': 'Tempo de resposta, oscilação e perda de dados até destinos na internet, medidos a cada poucos segundos; velocidade pelos testes feitos no mês.'},
    {'titulo': 'Rede local', 'texto': 'Equipamentos encontrados pelo sensor e o diagrama da rede como estavam no fechamento do mês. O relatório é emitido no dia 1 e guardado como foi emitido.'},
]


METODO_ATENDIMENTO = {'titulo': 'Atendimento', 'texto': 'Chamados que a empresa abriu no NexoDesk, sem os internos da MD. Os prazos são os do contrato de cada chamado, contados no horário dele e sem o tempo em que a MD esperava uma resposta do cliente.'}
SITUACAO_PRAZO = {'cumprido': ('ok', 'No prazo'), 'estourou': ('critico', 'Fora do prazo')}


def _atendimento(a):
    """Seção do atendimento da MD (NexoDesk); None se a empresa não tem o Desk (ou relatório anterior a ele)."""
    if a is None:
        return None

    def placar(p):
        if not p:
            return {'pct': '—', 'det': 'nenhum no período'}
        return {'pct': f"{p['pct']}%", 'det': f"{p['no_prazo']} de {p['total']} no prazo · média de {dur_txt(p['media_s'])}"}

    def linha(c):
        if c['status'] in ('resolvido', 'fechado'):
            classe, rotulo = SITUACAO_PRAZO.get(c['prazo'], ('sem', '—'))
        else:
            classe, rotulo = 'sem', c['status_rotulo']
        return {**c, 'aberto_txt': data_txt(c['criado_em']), 'prazo_classe': classe, 'prazo_rotulo': rotulo}

    av = a.get('avaliacao')
    return {
        **{k: a[k] for k in ('abertos', 'resolvidos', 'pendentes', 'cancelados', 'de_quedas', 'chamados_omitidos')},
        'resposta': placar(a['resposta']), 'solucao': placar(a['solucao']),
        'nota': {'valor': num_txt(av['media'], 1), 'det': plural(av['total'], 'avaliação', 'avaliações')} if av else None,
        'categorias': svg_barras_h([(c['rotulo'], c['chamados'], str(c['chamados'])) for c in a['por_categoria']], cor='#12B5E5'),
        'chamados': [linha(c) for c in a['chamados']],
        'pendentes_lista': [linha(c) for c in a['pendentes_lista']],
    }


# ------------------------------------------------------------------ contexto e PDF
def contexto(d, versao=None):
    t, meta = d['totais'], d['meta']
    anterior = d.get('anterior') or {}

    def variacao(atual, antes, menor_melhor, fmt):
        if atual is None or antes is None:
            return 'igual', 'sem mês anterior para comparar'
        if atual == antes:
            return 'igual', 'igual ao mês anterior'
        melhor = (atual < antes) == menor_melhor
        return ('melhor' if melhor else 'pior'), f'{"▼" if atual < antes else "▲"} {fmt(abs(atual - antes))} em relação ao mês anterior'

    fv = variacao(t['fora_s'], anterior.get('fora_s'), True, dur_txt)
    qv = variacao(t['quedas'], anterior.get('quedas'), True, lambda x: str(int(x)))
    causa_top = next(iter(d['causas'].items()), None)
    locais_ctx = []
    for l in d['locais']:
        q, v = l['qualidade'], l['velocidade']
        locais_ctx.append({
            **l, 'disp': pct_txt(l['pct']), 'fora': dur_txt(l['fora_s']) if l['fora_s'] else 'sem quedas',
            'faixa': l['situacao'] or 'sem', 'faixa_rotulo': ROTULO_SITUACAO.get(l['situacao'], 'Sem medição'),
            # Só vale avisar quando o local entrou de fato depois do começo do mês (não por minutos).
            'desde_txt': (data_txt(l['monitorado_desde'], hora=False)
                          if l['monitorado_desde'] and l['monitorado_desde'] - d['periodo']['inicio'] > 86400 else None),
            'q': {'lat': num_txt(q['latencia_ms'], 0, ' ms'), 'p95': num_txt(q['latencia_p95_ms'], 0, ' ms'),
                  'perda': num_txt(q['perda_pct'], 2, '%'), 'jitter': num_txt(q['jitter_ms'], 0, ' ms'), 'tem': bool(q['amostras']),
                  'velocidade': (f'{num_txt(v["download_mbps"], 0)} / {num_txt(v["upload_mbps"], 0)} Mbps' if v['testes'] else '—')},
            'diagrama_svg': mapa_rede.svg(l['diagrama'], largura_caixa=180, coluna=215, linha=50) if l['diagrama'] else '',
            'diagrama_max_mm': _largura_diagrama(l['diagrama']),
        })
    starlinks = [{'local': l['nome'], 'obstrucao': num_txt(l['starlink']['obstrucao_media_pct'], 1, '%'),
                  'quedas': l['starlink'].get('quedas_obstrucao', 0), 'desvio': num_txt(l['starlink']['desvio_medio_graus'], 1, '°')}
                 for l in d['locais'] if l.get('starlink')]
    prov = d['provedor']
    return {
        'base': RAIZ, 'versao': versao, 'parcial': d['periodo']['parcial'],
        'empresa': {'nome': d['empresa']['nome'], 'locais': locais_ctx},
        'periodo': {'rotulo': d['periodo']['rotulo'], 'inicio': data_txt(d['periodo']['inicio'], hora=False),
                    'fim': data_txt(d['periodo']['calculado_ate'] - 1, hora=False),
                    'gerado_em': datetime.fromtimestamp(d['gerado_em'], FUSO_BR).strftime('%d/%m/%Y às %H:%M')},
        'qualidade_desde': data_txt(d['qualidade_desde'], hora=False) if d.get('qualidade_desde') else None,
        'kpi': {'tempo_fora': dur_txt(t['fora_s']) if t['fora_s'] else '0 min', 'fora_var_classe': fv[0], 'fora_var': fv[1],
                'quedas': t['quedas'], 'quedas_var_classe': qv[0], 'quedas_var': qv[1],
                'causa_principal': causa_top[1]['rotulo'] if causa_top else 'Nenhuma queda',
                'causa_principal_qtd': f'{causa_top[1]["quedas"]} de {t["quedas"]} quedas' if causa_top else '',
                'disp_var_txt': (('Melhor' if t['pct'] > anterior['pct'] else 'Pior' if t['pct'] < anterior['pct'] else 'Igual')
                                 + f' que o mês anterior ({pct_txt(anterior["pct"])})') if anterior.get('pct') is not None and t['pct'] is not None
                                else 'Primeiro mês com comparação disponível a partir do próximo',
                'situacao': t['situacao'] or 'sem', 'situacao_rotulo': ROTULO_SITUACAO.get(t['situacao'], 'Sem medição'),
                'meta': pct_txt(meta)},
        'resumo': resumo(d),
        'graficos': {
            'fundo_capa': svg_fundo_capa(), 'medidor': svg_medidor(t['pct'], meta),
            'tendencia': svg_tendencia(d['tendencia'], meta), 'diario': svg_diario(d['por_dia'], meta),
            'causas': svg_barras_h([(c['rotulo'], c['fora_s'], f'{dur_txt(c["fora_s"])} · {plural(c["quedas"], "queda", "quedas")}')
                                    for c in d['causas'].values()], destaque='#E39B2D'),
            'horarios': svg_barras_h([(f'{b * 4:02d}h às {b * 4 + 4:02d}h', n, str(n)) for b, n in enumerate(d['horarios'])], cor='#12B5E5')
                        if t['quedas'] else '',
        },
        'maiores_quedas': [{'inicio': data_txt(q['inicio']), 'duracao': dur_txt(q['duracao_s']), 'local': q['local'],
                            'causa': nexoreport.CATEGORIAS[q['categoria']], 'confianca': CONFIANCA.get(q['confianca'], '—'),
                            'em_aberto': q['em_aberto']}
                           for q in sorted(d['quedas'], key=lambda x: -x['duracao_s'])[:5]],
        'starlinks': starlinks,
        'provedor': {
            'resumo': (f'{plural(len(prov), "queda atribuída", "quedas atribuídas")} ao provedor, somando {dur_txt(sum(q["duracao_s"] for q in prov))} sem conexão.'
                       if prov else 'Nenhuma queda foi atribuída ao provedor neste mês.'),
            'ocorrencias': [{'inicio': data_txt(q['inicio']), 'duracao': dur_txt(q['duracao_s']), 'local': q['local'],
                             'evidencia': q['evidencia'] or 'Falha a partir do primeiro trecho do provedor, com a rede interna normal.'} for q in prov],
        },
        'recomendacoes': recomendacoes(d),
        'atendimento': _atendimento(d.get('atendimento')),
        'metodo': METODO + ([METODO_ATENDIMENTO] if d.get('atendimento') is not None else []),
    }


def _largura_diagrama(arvore, mm_por_px=0.24):
    """Rede pequena não deve virar um desenho gigante: mesma escala de letra de uma rede grande."""
    if not arvore:
        return 0
    profundidade = max(n for _, n in mapa_rede.linhas(arvore)) if arvore else 0
    return round(min(265, (profundidade * 215 + 180 + 20) * mm_por_px), 1)


def gerar_pdf(dados, versao=None):
    """PDF em bytes. O WeasyPrint só é importado aqui: o resto da Central funciona sem ele."""
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    env = Environment(loader=FileSystemLoader(os.path.join(RAIZ, 'templates', 'report')), autoescape=True)
    html = env.get_template('mensal.html').render(**contexto(dados, versao))
    return HTML(string=html, base_url=RAIZ).write_pdf()
