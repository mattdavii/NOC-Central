"""Gera o modelo do relatório mensal do NexoReport (PDF) com dados de demonstração.

Serve para aprovar o conteúdo e o visual antes da implementação. Os totais são calculados
a partir da lista de quedas abaixo, do mesmo jeito que a versão real vai calcular a partir
do banco (ver ESCOPO.md, seção 4).

Uso (na raiz do repositório):  python3 docs/nexoreport/modelo/gerar_modelo.py
Saída: docs/nexoreport/modelo/modelo-relatorio-mensal.pdf
"""
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, RAIZ)
import mapa_rede  # noqa: E402  (o mesmo desenho do painel e do relatório da Central)

from jinja2 import Environment, FileSystemLoader  # noqa: E402

MES_INICIO = datetime(2026, 10, 1)
DIAS = 31
MINUTOS_MES = DIAS * 24 * 60
MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']

LOCAIS = [
    {'nome': 'UFV Alto Paraná', 'provedor': 'Starlink'},
    {'nome': 'Escritório central', 'provedor': 'Fibra (provedor local)'},
    {'nome': 'Subestação', 'provedor': 'Rádio (provedor local)'},
]

CAUSAS = {'starlink': 'Obstrução da Starlink', 'provedor': 'Falha no provedor', 'local': 'Rede interna do local', 'indefinida': 'Não determinada'}
CONFIANCA = {'alta': 'Alta', 'media': 'Média', 'baixa': 'Baixa'}

# (dia, hora, minuto, duração em minutos, local, causa, confiança, evidência)
QUEDAS = [
    (3, 16, 42, 31, 'UFV Alto Paraná', 'starlink', 'alta', None),
    (7, 17, 5, 24, 'UFV Alto Paraná', 'starlink', 'alta', None),
    (9, 10, 18, 46, 'Escritório central', 'provedor', 'alta', 'Gateway respondendo normal; perda de 100% a partir do 1º salto do provedor.'),
    (12, 16, 55, 28, 'UFV Alto Paraná', 'starlink', 'alta', None),
    (15, 3, 12, 41, 'Subestação', 'local', 'media', None),
    (19, 20, 31, 22, 'UFV Alto Paraná', 'starlink', 'alta', None),
    (22, 14, 2, 26, 'Escritório central', 'provedor', 'alta', 'Gateway normal; latência no provedor acima de 800 ms e perda de 64% antes de cair.'),
    (26, 17, 20, 20, 'UFV Alto Paraná', 'starlink', 'alta', None),
    (29, 11, 47, 23, 'UFV Alto Paraná', 'indefinida', 'baixa', None),
]


def fmt_min(m):
    m = int(round(m))
    if m < 60:
        return f'{m} min'
    return f'{m // 60}h{m % 60:02d}'


def pct(fora, total):
    # Arredonda para baixo: com qualquer queda o número nunca chega a 100% (mesma regra do NexoHub).
    v = math.floor(100 * (1 - fora / total) * 10) / 10
    return 99.9 if fora > 0 and v >= 100 else v


def pct_txt(v):
    return f'{v:.1f}%'.replace('.', ',')


META = 99.7  # meta de disponibilidade do contrato desta empresa (configurada pela MD por empresa)


def faixa(v, meta=META):
    """Situação contra a meta do contrato: dentro, até 1 p.p. abaixo, ou crítico."""
    if v >= meta:
        return 'ok', 'Dentro da meta'
    return ('atencao', 'Abaixo da meta') if v >= meta - 1 else ('critico', 'Crítico')


def svg_fundo_capa():
    """Prancheta técnica: grade que some para baixo, dois brilhos e um traço de sinal (violeta → ciano → verde)."""
    w, h, passo = 210, 297, 7
    p = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297" width="210mm" height="297mm">',
         '<defs><radialGradient id="g1" cx="0.15" cy="0.12" r="0.55"><stop offset="0" stop-color="#12B5E5" stop-opacity=".28"/><stop offset="1" stop-color="#12B5E5" stop-opacity="0"/></radialGradient>',
         '<radialGradient id="g2" cx="0.95" cy="0.55" r="0.5"><stop offset="0" stop-color="#7A3FE0" stop-opacity=".26"/><stop offset="1" stop-color="#7A3FE0" stop-opacity="0"/></radialGradient>',
         '<linearGradient id="sinal" x1="0" x2="1"><stop offset="0" stop-color="#7A3FE0"/><stop offset=".55" stop-color="#12B5E5"/><stop offset="1" stop-color="#0BB39A"/></linearGradient></defs>',
         f'<rect width="{w}" height="{h}" fill="url(#g1)"/><rect width="{w}" height="{h}" fill="url(#g2)"/>']
    for x in range(0, w + 1, passo):
        p.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="#A9BED3" stroke-opacity=".06" stroke-width=".15"/>')
    for y in range(0, h + 1, passo):
        op = max(0.0, 0.09 * (1 - y / 190))
        if op > 0:
            p.append(f'<line x1="0" y1="{y}" x2="{w}" y2="{y}" stroke="#A9BED3" stroke-opacity="{op:.3f}" stroke-width=".15"/>')
    # Traço de sinal no alto à direita: linha de base com um pulso, como num monitor.
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


def svg_medidor(valor, meta=META):
    """Semicírculo de escala técnica: zonas pela meta do contrato, marca da meta e o ponteiro no valor do mês."""
    import math as m
    mini = math.floor(min(meta - 1.5, valor - 0.3) * 2) / 2  # escala justa: a zona da meta fica visível
    cx, cy, r = 120, 112, 92

    def ponto(v, raio):
        ang = m.pi * (1 - (min(max(v, mini), 100) - mini) / (100 - mini))
        return cx + raio * m.cos(ang), cy - raio * m.sin(ang)

    def arco(v1, v2, cor, larg=12):
        x1, y1 = ponto(v1, r)
        x2, y2 = ponto(v2, r)
        return f'<path d="M{x1:.1f} {y1:.1f} A{r} {r} 0 0 1 {x2:.1f} {y2:.1f}" fill="none" stroke="{cor}" stroke-width="{larg}"/>'

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 140" font-family="Plex">']
    p.append(arco(mini, 100, 'rgba(169,190,211,.14)', 14))
    p.append(arco(mini, meta - 1, '#D64545'))
    p.append(arco(meta - 1, meta, '#E39B2D'))
    p.append(arco(meta, 100, '#0BB39A'))
    # Escala: um traço a cada 0,5 p.p., com número nos inteiros.
    v = mini
    while v <= 100.001:
        x1, y1 = ponto(v, r - 11)
        x2, y2 = ponto(v, r - (17 if abs(v - round(v)) < 1e-6 else 14))
        p.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#A9BED3" stroke-opacity=".7" stroke-width=".8"/>')
        if abs(v - round(v)) < 1e-6 or v <= mini + 1e-6:
            tx, ty = ponto(v, r - 25)
            if v <= mini + 1e-6 or v >= 100 - 1e-6:  # nas pontas, o número vai embaixo do arco
                tx, ty = ponto(v, r)
                ty += 14
            p.append(f'<text x="{tx:.1f}" y="{ty + 3:.1f}" font-size="7.5" text-anchor="middle" fill="#A9BED3">{int(round(v)) if abs(v - round(v)) < 1e-6 else pct_txt(v)}</text>')
        v += 0.5
    # Marca da meta
    mx1, my1 = ponto(meta, r - 9)
    mx2, my2 = ponto(meta, r + 11)
    lx, ly = ponto(meta, r + 19)
    p.append(f'<line x1="{mx1:.1f}" y1="{my1:.1f}" x2="{mx2:.1f}" y2="{my2:.1f}" stroke="#fff" stroke-width="1.6"/>')
    p.append(f'<text x="{lx:.1f}" y="{ly + 2:.1f}" font-size="7.5" font-weight="600" text-anchor="middle" fill="#fff">meta {pct_txt(meta)}</text>')
    # Marcador do valor do mês sobre o arco, e o número grande no centro.
    vx, vy = ponto(valor, r)
    p.append(f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="9" fill="#fff" fill-opacity=".18"/>')
    p.append(f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="5.5" fill="#061529" stroke="#fff" stroke-width="2.2"/>')
    p.append(f'<text x="{cx}" y="{cy - 6}" font-size="34" font-weight="800" font-family="Bricolage" text-anchor="middle" fill="#fff">{pct_txt(valor)}</text>')
    p.append('</svg>')
    return ''.join(p)


def svg_tendencia(valores):
    w, h, base = 300, 120, 98.0
    passo = w / len(valores)
    partes = [f'<svg viewBox="0 0 {w} {h + 22}" xmlns="http://www.w3.org/2000/svg" font-family="Plex">']
    ym = h - (META - base) / (100 - base) * h
    partes.append(f'<line x1="0" x2="{w}" y1="{ym:.1f}" y2="{ym:.1f}" stroke="#fff" stroke-opacity=".55" stroke-width=".8" stroke-dasharray="3 3"/>')
    for i, (rotulo, v) in enumerate(valores):
        altura = max(4, (v - base) / (100 - base) * h)
        x = i * passo + 8
        ultimo = i == len(valores) - 1
        partes.append(f'<rect x="{x:.1f}" y="{h - altura:.1f}" width="{passo - 16:.1f}" height="{altura:.1f}" rx="3" fill="{"#12B5E5" if ultimo else ("rgba(169,190,211,.45)" if v >= META else "rgba(227,155,45,.6)")}"/>')
        partes.append(f'<text x="{x + (passo - 16) / 2:.1f}" y="{h - altura - 4:.1f}" font-size="9" text-anchor="middle" fill="{"#fff" if ultimo else "#A9BED3"}">{pct_txt(v)}</text>')
        partes.append(f'<text x="{x + (passo - 16) / 2:.1f}" y="{h + 14}" font-size="9" text-anchor="middle" fill="#A9BED3">{rotulo}</text>')
    partes.append('</svg>')
    return ''.join(partes)


def svg_diario(por_dia):
    w, h, base = 520, 120, 96.0
    passo = w / len(por_dia)
    cores = {'ok': '#0BA98F', 'atencao': '#E39B2D', 'critico': '#D64545'}
    partes = [f'<svg viewBox="-28 -8 {w + 30} {h + 28}" xmlns="http://www.w3.org/2000/svg" font-family="Plex">']
    for ref in (96, 98, 100):
        y = h - (ref - base) / (100 - base) * h
        partes.append(f'<line x1="0" x2="{w}" y1="{y:.1f}" y2="{y:.1f}" stroke="#D5E0EA" stroke-width=".8"/>')
        partes.append(f'<text x="-4" y="{y + 3:.1f}" font-size="8" text-anchor="end" fill="#6B7F95">{ref}%</text>')
    for i, v in enumerate(por_dia):
        altura = max(2, (min(v, 100) - base) / (100 - base) * h)
        partes.append(f'<rect x="{i * passo + 1.5:.1f}" y="{h - altura:.1f}" width="{passo - 3:.1f}" height="{altura:.1f}" rx="1.5" fill="{cores[faixa(v)[0]]}"/>')
        if (i + 1) % 5 == 0 or i == 0:
            partes.append(f'<text x="{i * passo + passo / 2:.1f}" y="{h + 13}" font-size="8" text-anchor="middle" fill="#6B7F95">{i + 1}</text>')
    partes.append('</svg>')
    return ''.join(partes)


def svg_barras_h(itens, cor='#0B3460', destaque=None):
    w, linha = 380, 28
    maximo = max(v for _, v, _ in itens) or 1
    partes = [f'<svg viewBox="0 0 {w} {len(itens) * linha}" xmlns="http://www.w3.org/2000/svg" font-family="Plex">']
    for i, (rotulo, valor, texto) in enumerate(itens):
        y = i * linha
        largura = max(3, valor / maximo * 120)
        partes.append(f'<text x="0" y="{y + 16}" font-size="11" fill="#142B45">{rotulo}</text>')
        partes.append(f'<rect x="132" y="{y + 6}" width="{largura:.1f}" height="13" rx="3" fill="{destaque if destaque and i == 0 else cor}"/>')
        partes.append(f'<text x="{132 + largura + 6:.1f}" y="{y + 16}" font-size="11" font-weight="600" fill="#142B45">{texto}</text>')
    partes.append('</svg>')
    return ''.join(partes)


def montar():
    fora_local, quedas_local = defaultdict(float), Counter()
    fora_dia = defaultdict(float)
    fora_causa, quedas_causa = defaultdict(float), Counter()
    horarios = Counter()
    for dia, hh, mm, dur, local, causa, conf, _ in QUEDAS:
        fora_local[local] += dur
        quedas_local[local] += 1
        fora_dia[dia] += dur
        fora_causa[causa] += dur
        quedas_causa[causa] += 1
        horarios[hh // 4] += 1

    total_fora = sum(fora_local.values())
    disp = pct(total_fora, MINUTOS_MES * len(LOCAIS))
    disp_anterior, fora_anterior, quedas_anteriores = 99.2, 352, 14
    causa_top, n_top = quedas_causa.most_common(1)[0]

    locais = []
    for l in LOCAIS:
        v = pct(fora_local[l['nome']], MINUTOS_MES)
        f = faixa(v)
        locais.append({**l, 'disp': pct_txt(v), 'fora': fmt_min(fora_local[l['nome']]) if fora_local[l['nome']] else 'sem quedas',
                       'quedas': quedas_local[l['nome']], 'faixa': f[0], 'faixa_rotulo': f[1]})

    por_dia = [pct(fora_dia[d], 24 * 60 * len(LOCAIS)) for d in range(1, DIAS + 1)]
    causas = sorted(fora_causa.items(), key=lambda x: -x[1])
    maiores = sorted(QUEDAS, key=lambda q: -q[3])[:5]
    data = lambda d, h, m: (MES_INICIO + timedelta(days=d - 1, hours=h, minutes=m)).strftime('%d/%m %H:%M')

    provedor = [q for q in QUEDAS if q[5] == 'provedor']
    starlink_quedas = [q for q in QUEDAS if q[5] == 'starlink']
    tarde = sum(1 for q in starlink_quedas if 15 <= q[1] < 18)

    # Árvore de demonstração no formato do mapa_rede (o mesmo desenho do painel).
    sensor = {'ip_gateway': '192.168.0.1', 'ip_sensor': '192.168.0.18', 'nome_local': 'Sensor NexoPulse', 'ping_gateway': 2, 'status': 'ONLINE', 'provedor': 'Starlink'}
    disp_demo = [('192.168.0.25', '10:00:00:00:00:25', 'Inversor WAGO 1', 'inversor'), ('192.168.0.38', '10:00:00:00:00:38', 'Inversor WAGO 2', 'inversor'),
                 ('192.168.0.27', '10:00:00:00:00:27', 'Câmera portaria', 'camera'), ('192.168.0.47', '10:00:00:00:00:47', 'Câmera inversores', 'camera'),
                 ('192.168.0.26', '10:00:00:00:00:26', 'Câmera perímetro', 'camera'), ('192.168.0.2', '10:00:00:00:00:02', 'Datalogger', 'datalogger'),
                 ('192.168.0.201', '10:00:00:00:02:01', 'Notebook O&M', 'computador')]
    dispositivos = [{'ip': ip, 'mac': mac, 'nome_custom': nome, 'status': 'online'} for ip, mac, nome, _ in disp_demo]
    mapa = mapa_rede.validar({
        'extras': [{'id': 'm:1', 'nome': 'Antena Starlink', 'obs': 'Mastro da cabine'}, {'id': 'm:2', 'nome': 'Conversor de fibra'},
                   {'id': 'm:3', 'nome': 'Switch da cabine'}, {'id': 'm:4', 'nome': 'Switch de CFTV'}],
        'nos': {'m:1': {'tipo': 'starlink', 'pai': 'internet'}, 'gateway': {'pai': 'm:1', 'meio': 'cabo'},
                'm:2': {'tipo': 'conversor', 'pai': 'gateway', 'meio': 'cabo'}, 'm:3': {'tipo': 'switch', 'pai': 'm:2', 'meio': 'fibra'},
                'm:4': {'tipo': 'switch', 'pai': 'gateway', 'meio': 'cabo'}, 'sensor': {'pai': 'm:3'},
                **{f'd:{mac}': {'tipo': tipo, 'pai': 'm:4' if tipo == 'camera' else 'm:3'} for _, mac, _, tipo in disp_demo}}})
    diagrama = mapa_rede.svg(mapa_rede.montar(sensor, dispositivos, mapa), largura_caixa=180, coluna=215, linha=50)

    var = lambda atual, anterior, menor_melhor, fmt: (
        ('igual', 'igual ao mês anterior') if atual == anterior else
        (('melhor' if (atual < anterior) == menor_melhor else 'pior'),
         f'{"▼" if atual < anterior else "▲"} {fmt(abs(atual - anterior))} em relação a setembro'))
    dv = var(disp, disp_anterior, False, lambda d: f'{d:.1f} p.p.'.replace('.', ','))
    fv = var(total_fora, fora_anterior, True, fmt_min)
    qv = var(len(QUEDAS), quedas_anteriores, True, lambda d: f'{int(d)}')

    return {
        'base': RAIZ,
        'empresa': {'nome': 'Empresa de demonstração', 'locais': locais},
        'periodo': {'rotulo': f'{MESES[MES_INICIO.month - 1].capitalize()} de {MES_INICIO.year}', 'inicio': '01/10/2026', 'fim': '31/10/2026',
                    'gerado_em': '01/11/2026 às 00:30 · modelo com dados de demonstração'},
        'kpi': {'disponibilidade': pct_txt(disp), 'disp_var_classe': dv[0], 'disp_var': dv[1],
                'disp_var_txt': f'{"Melhor" if disp > disp_anterior else "Pior"} que setembro ({pct_txt(disp_anterior)})',
                'situacao': faixa(disp)[0], 'situacao_rotulo': faixa(disp)[1], 'meta': pct_txt(META),
                'tempo_fora': fmt_min(total_fora), 'fora_var_classe': fv[0], 'fora_var': fv[1],
                'quedas': len(QUEDAS), 'quedas_var_classe': qv[0], 'quedas_var': qv[1],
                'causa_principal': CAUSAS[causa_top], 'causa_principal_qtd': f'{n_top} de {len(QUEDAS)} quedas'},
        'resumo': [
            f'Em outubro, a rede dos seus {len(LOCAIS)} locais ficou disponível {pct_txt(disp)} do tempo, melhor que em setembro ({pct_txt(disp_anterior)}). '
            f'Foram {len(QUEDAS)} quedas, que somaram {fmt_min(total_fora)} sem conexão.',
            f'A causa mais frequente foi a obstrução da antena Starlink da UFV Alto Paraná ({len(starlink_quedas)} quedas), {tarde} delas entre 15h e 18h, '
            'o que indica sombra no fim da tarde. As 2 quedas do Escritório central foram do provedor e estão documentadas para cobrança.',
        ],
        'graficos': {
            'fundo_capa': svg_fundo_capa(),
            'medidor': svg_medidor(disp),
            'tendencia': svg_tendencia([('mai', 98.7), ('jun', 99.1), ('jul', 98.4), ('ago', 99.0), ('set', disp_anterior), ('out', disp)]),
            'diario': svg_diario(por_dia),
            'causas': svg_barras_h([(CAUSAS[c], m, f'{fmt_min(m)} · {quedas_causa[c]} {"queda" if quedas_causa[c] == 1 else "quedas"}') for c, m in causas], destaque='#E39B2D'),
            'horarios': svg_barras_h([(f'{b * 4:02d}h às {b * 4 + 4:02d}h', horarios[b], str(horarios[b])) for b in range(6)], cor='#12B5E5'),
            'diagrama': diagrama,
        },
        'maiores_quedas': [{'inicio': data(d, h, m), 'duracao': fmt_min(dur), 'local': loc, 'causa': CAUSAS[c], 'confianca': CONFIANCA[cf]}
                           for d, h, m, dur, loc, c, cf, _ in maiores],
        'qualidade': [
            {'local': 'UFV Alto Paraná', 'lat': '38 ms', 'p95': '92 ms', 'perda': '0,8%', 'jitter': '9 ms', 'velocidade': '182 / 21 Mbps'},
            {'local': 'Escritório central', 'lat': '12 ms', 'p95': '31 ms', 'perda': '0,2%', 'jitter': '3 ms', 'velocidade': '296 / 148 Mbps'},
            {'local': 'Subestação', 'lat': '24 ms', 'p95': '67 ms', 'perda': '0,5%', 'jitter': '7 ms', 'velocidade': '48 / 19 Mbps'},
        ],
        'starlink': {'local': 'UFV Alto Paraná', 'obstrucao': '4,2%', 'quedas': len(starlink_quedas), 'desvio': '0,4°',
                     'leitura': f'{tarde} das {len(starlink_quedas)} quedas por obstrução aconteceram entre 15h e 18h. O alinhamento está correto (desvio de 0,4°): '
                                'a causa provável é algo no campo de visão a oeste, como árvore ou estrutura que faz sombra no fim da tarde.'},
        'provedor': {
            'resumo': f'{len(provedor)} quedas atribuídas ao provedor, somando {fmt_min(sum(q[3] for q in provedor))} sem conexão no Escritório central.',
            'texto': 'Nos dois casos, a rede interna e o roteador responderam normalmente durante toda a queda, e a falha começou já no primeiro trecho do provedor. '
                     'As medições completas ficam guardadas e podem ser anexadas ao chamado ou à solicitação de desconto.',
            'ocorrencias': [{'inicio': data(d, h, m), 'duracao': fmt_min(dur), 'local': loc, 'evidencia': ev} for d, h, m, dur, loc, c, _, ev in provedor],
        },
        'inventario': {'local': 'UFV Alto Paraná',
                       'numeros': [{'rotulo': 'Equipamentos encontrados', 'valor': 31}, {'rotulo': 'Câmeras (CFTV)', 'valor': 9},
                                   {'rotulo': 'Inversores e dataloggers', 'valor': 7}, {'rotulo': 'Sem identificação', 'valor': 4}],
                       'nota_diagrama': 'Ligações informadas pela equipe da MD no local e completadas com o que o sensor observa. Equipamentos que o sensor não enxerga (switch não gerenciável, conversor) foram acrescentados pela equipe.'},
        'recomendacoes': [
            {'prioridade': 'alta', 'titulo': 'Verificar o campo de visão da antena Starlink na UFV Alto Paraná',
             'texto': 'As quedas por obstrução se concentram no fim da tarde. Recomendamos inspeção no local e, se for o caso, poda ou reposicionamento da antena no mastro.'},
            {'prioridade': 'media', 'titulo': 'Abrir solicitação de desconto com o provedor do Escritório central',
             'texto': 'As duas quedas do mês têm evidência de falha do provedor. A MD pode preparar o pedido com as medições anexadas.'},
            {'prioridade': 'baixa', 'titulo': 'Identificar os 4 equipamentos sem nome na rede da UFV',
             'texto': 'Equipamentos não identificados dificultam o diagnóstico. Na próxima visita, a equipe pode nomeá-los no diagrama.'},
        ],
        'metodo': [
            {'titulo': 'Disponibilidade', 'texto': 'Parte do tempo do mês em que cada local teve comunicação com a plataforma. Períodos em manutenção programada não entram na conta, e o número é arredondado para baixo: qualquer queda impede os 100%.'},
            {'titulo': 'Queda', 'texto': 'Período em que o sensor do local deixou de ter internet, confirmado pelas medições. Instabilidades curtas que não chegam a derrubar a conexão aparecem em Qualidade.'},
            {'titulo': 'Causa', 'texto': 'O sensor mede cada camada da conexão (rede interna, antena, provedor, internet e DNS) e aponta onde o problema começou. A confiança diz o quanto as medições sustentam a conclusão.'},
            {'titulo': 'Qualidade', 'texto': 'Tempo de resposta, oscilação e perda de dados medidos a cada poucos segundos; velocidade pelos testes feitos no mês.'},
            {'titulo': 'Rede local', 'texto': 'Equipamentos encontrados pelo sensor na última varredura do mês, com marca identificada pelo prefixo do MAC. O diagrama combina o que o sensor vê com o que a equipe informou.'},
            {'titulo': 'Fonte dos dados', 'texto': 'Sensores NexoPulse instalados nos locais. O relatório é gerado no fechamento do mês e guardado como foi emitido.'},
        ],
    }


def main():
    from weasyprint import HTML
    pasta = os.path.dirname(os.path.abspath(__file__))
    env = Environment(loader=FileSystemLoader(pasta), autoescape=True)
    html = env.get_template('relatorio_mensal.html').render(**montar())
    destino = os.path.join(pasta, 'modelo-relatorio-mensal.pdf')
    HTML(string=html, base_url=RAIZ).write_pdf(destino)
    print(destino)


if __name__ == '__main__':
    main()
