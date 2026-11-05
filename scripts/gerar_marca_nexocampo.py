#!/usr/bin/env python3
"""Marca do NexoCampo (app de campo), na mesma gramática do kit da Família Nexo.

O kit (assets/branding/nexo) não tem o NexoCampo. Este script monta a marca com as mesmas regras:
  - símbolo: o hexágono com os seis nós do kit, idêntico ao das outras marcas, e o miolo próprio
    (um marcador de local com o sinal de Wi-Fi: a rede, analisada no local);
  - nome: "Nexo" + "Campo" em Inter Display Bold (opsz 32, peso 700), em contornos, com o mesmo corpo,
    posição e cores do kit (Nexo branco/azul-marinho, Campo no gradiente);
  - descritor "Análise de rede em campo" em Inter Display Medium, como nas outras horizontais.

Gera em static/brand/nexocampo/ (SVG) e os recursos do app em android/app/src/main/res/ (ícone adaptativo
e logo em vetor). Os PNGs saem de scripts/gerar_marca_nexocampo.py --png (Playwright com Chromium).

A fonte não fica no repositório (o kit não distribui fonte): passe o arquivo variável da Inter com o eixo
opsz, por exemplo o inter-latin-opsz-normal.woff2 do pacote npm @fontsource-variable/inter:
    python scripts/gerar_marca_nexocampo.py --fonte caminho/inter-latin-opsz-normal.woff2
"""
import argparse
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SAIDA = RAIZ / 'static' / 'brand' / 'nexocampo'
RES = RAIZ / 'android' / 'app' / 'src' / 'main' / 'res'

NOME, SUFIXO, DESCRITOR = 'Nexo', 'Campo', 'Análise de rede em campo'
GRADIENTES = ('<defs><linearGradient id="nexoGrad" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#08ABD0"/>'
              '<stop offset="56%" stop-color="#05B7BE"/><stop offset="100%" stop-color="#04B89C"/></linearGradient>'
              '<linearGradient id="nexoGradText" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#08ABD0"/>'
              '<stop offset="100%" stop-color="#04B89C"/></linearGradient></defs>')
HEXAGONO = 'M80 12 L138 46 L138 114 L80 148 L22 114 L22 46 Z'
NOS = [(80, 12), (138, 46), (138, 114), (80, 148), (22, 114), (22, 46)]
# Cores dos nós, na ordem acima (iguais às do kit).
CORES_NOS = {'escuro': ['#06D2C9', '#9CEDE5', '#67E9D7', '#04B89C', '#B1E8FF', '#08ABD0'],
             'claro': ['#08ABD0', '#0B1B32', '#04B89C', '#04B89C', '#0B1B32', '#0B1B32']}
COR_NOME = {'escuro': '#FFFFFF', 'claro': '#0B1B32'}
COR_DESCRITOR = {'escuro': '#BDCCDC', 'claro': '#566982'}
COR_MIOLO = {'escuro': '#4DE6D9', 'claro': 'url(#nexoGrad)'}

# Miolo: marcador de local (cabeça centrada em 80,71, raio 25, ponta em 80,119) com o Wi-Fi dentro;
# ocupa a mesma altura dos miolos do kit (47 a 116 no NexoGuard).
MIOLO_PINO = 'M80 119 C71 107 55 94 55 71 A25 25 0 0 1 105 71 C105 94 89 107 80 119 Z'
MIOLO_ARCOS = 'M67.98 66.98 A17 17 0 0 1 92.02 66.98 M72.93 71.93 A10 10 0 0 1 87.07 71.93'
MIOLO_PONTO = (80, 79, 3.6)


def miolo_svg(tema):
    cor = COR_MIOLO[tema]
    tracado = f'stroke="{cor}" stroke-width="5.7" stroke-linecap="round" stroke-linejoin="round" fill="none"'
    x, y, r = MIOLO_PONTO
    return (f'<path d="{MIOLO_PINO}" {tracado}/><path d="{MIOLO_ARCOS}" {tracado}/>'
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="{cor}"/>')


def simbolo_svg(tema):
    nos = '\n'.join(f'<circle cx="{x}" cy="{y}" r="9.1" fill="{c}"/>' for (x, y), c in zip(NOS, CORES_NOS[tema]))
    return (f'<path d="{HEXAGONO}" fill="none" stroke="url(#nexoGrad)" stroke-width="7" stroke-linecap="round" '
            f'stroke-linejoin="round"/>\n{miolo_svg(tema)}\n{nos}')


class Fonte:
    def __init__(self, caminho):
        from fontTools.ttLib import TTFont
        from fontTools.varLib.instancer import instantiateVariableFont
        self.pesos = {p: instantiateVariableFont(TTFont(caminho), {'wght': p, 'opsz': 32}) for p in (500, 700)}

    def texto(self, s, x, y, corpo, peso):
        """Contornos de s em coordenadas absolutas (sem transform), um caminho por letra; devolve (caminhos, x final)."""
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.pens.transformPen import TransformPen
        fonte = self.pesos[peso]
        gs, cmap = fonte.getGlyphSet(), fonte.getBestCmap()
        k = corpo / fonte['head'].unitsPerEm
        caminhos = []
        for ch in s:
            glifo = gs[cmap[ord(ch)]]
            if ch != ' ':
                caneta = SVGPathPen(gs, ntos=lambda v: f'{v:.2f}'.rstrip('0').rstrip('.'))
                glifo.draw(TransformPen(caneta, (k, 0, 0, -k, x, y)))
                caminhos.append(caneta.getCommands())
            x += glifo.width * k
        return caminhos, x


def horizontal(fonte, tema, com_descritor):
    """Marca horizontal: símbolo do kit em translate(36 31) scale(1.3), nome em corpo 102."""
    if com_descritor:
        base, desloc = 144, 0
    else:
        base, desloc = 172.1, 0     # nome centralizado na altura do símbolo, como os derivados da Central
    nome, x = fonte.texto(NOME, 286, base + desloc, 102, 700)
    sufixo, x_fim = fonte.texto(SUFIXO, x, base + desloc, 102, 700)
    partes = [f'<g transform="translate(36 31) scale(1.3)">{simbolo_svg(tema)}</g>']
    partes += [f'<path d="{d}" fill="{COR_NOME[tema]}"/>' for d in nome]
    # O gradiente do kit vai de ponta a ponta do sufixo: um caminho só, com o gradiente no espaço dele.
    partes.append(f'<path d="{" ".join(sufixo)}" fill="url(#nexoGradText)"/>')
    if com_descritor:
        desc, _ = fonte.texto(DESCRITOR, 289, 199, 27, 500)
        partes += [f'<path d="{d}" fill="{COR_DESCRITOR[tema]}"/>' for d in desc]
        caixa = '0 0 1320 270'
        rotulo = f'NexoCampo — {DESCRITOR}'
    else:
        topo, base_simbolo, esquerda = 31 + (12 - 9.1) * 1.3, 31 + (148 + 9.1) * 1.3, 36 + (22 - 9.1) * 1.3
        margem = 8
        caixa = f'{esquerda - margem:.2f} {topo - margem:.2f} {x_fim + margem - (esquerda - margem):.2f} {base_simbolo - topo + 2 * margem:.2f}'
        rotulo = 'NexoCampo'
    w, h = caixa.split()[2:]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{float(w):.0f}" height="{float(h):.0f}" viewBox="{caixa}" '
            f'role="img" aria-label="{rotulo}">\n<title>{rotulo}</title>\n{GRADIENTES}\n' + '\n'.join(partes) + '\n</svg>\n')


def simbolo_arquivo(tema):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160" role="img" '
            f'aria-label="Símbolo NexoCampo">\n<title>Símbolo NexoCampo</title>\n{GRADIENTES}\n{simbolo_svg(tema)}\n</svg>\n')


def app_icon(maskable=False):
    if maskable:   # fundo de ponta a ponta; símbolo menor, dentro da zona segura
        fundo = '<rect width="256" height="256" fill="#0B1B32"/>'
        escala = 1.1
    else:          # quadrado arredondado do kit
        fundo = ('<rect x="3" y="3" width="250" height="250" rx="52" fill="#0B1B32"/>'
                 '<rect x="3" y="3" width="250" height="250" rx="52" fill="none" stroke="#234664" stroke-width="3"/>')
        escala = 1.24
    t = 128 - 80 * escala
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256" role="img" '
            f'aria-label="Ícone NexoCampo">\n<title>Ícone NexoCampo</title>\n{GRADIENTES}\n{fundo}'
            f'<g transform="translate({t:.2f} {t:.2f}) scale({escala})">{simbolo_svg("escuro")}</g>\n</svg>\n')


# ------------------------------------------------------------------ Android (VectorDrawable)
def _circulo_d(x, y, r):
    return f'M{x - r},{y} a{r},{r} 0 1,0 {2 * r},0 a{r},{r} 0 1,0 {-2 * r},0'


def _gradiente(x1, y1, x2, y2, paradas):
    stops = ''.join(f'<item android:offset="{o}" android:color="#FF{c[1:]}"/>' for o, c in paradas)
    return (f'<aapt:attr name="android:{{attr}}"><gradient android:type="linear" android:startX="{x1}" android:startY="{y1}" '
            f'android:endX="{x2}" android:endY="{y2}">{stops}</gradient></aapt:attr>')


GRAD_SIMBOLO = _gradiente(22, 12, 138, 148, [(0, '#08ABD0'), (0.56, '#05B7BE'), (1, '#04B89C')])


def simbolo_vector(cor_unica=None):
    """Caminhos do símbolo (coordenadas 0..160) para um <group>. cor_unica: ícone monocromático."""
    def traco(d, largura, cor=None, gradiente=False):
        if cor_unica:
            return f'<path android:pathData="{d}" android:strokeColor="{cor_unica}" android:strokeWidth="{largura}" android:strokeLineCap="round" android:strokeLineJoin="round"/>'
        if gradiente:
            return (f'<path android:pathData="{d}" android:strokeWidth="{largura}" android:strokeLineCap="round" '
                    f'android:strokeLineJoin="round">{GRAD_SIMBOLO.replace("{attr}", "strokeColor")}</path>')
        return f'<path android:pathData="{d}" android:strokeColor="{cor}" android:strokeWidth="{largura}" android:strokeLineCap="round" android:strokeLineJoin="round"/>'
    x, y, r = MIOLO_PONTO
    partes = [traco(HEXAGONO, 7, gradiente=True), traco(MIOLO_PINO, 5.7, '#4DE6D9'), traco(MIOLO_ARCOS, 5.7, '#4DE6D9'),
              f'<path android:pathData="{_circulo_d(x, y, r)}" android:fillColor="{cor_unica or "#4DE6D9"}"/>']
    for (cx, cy), cor in zip(NOS, CORES_NOS['escuro']):
        partes.append(f'<path android:pathData="{_circulo_d(cx, cy, 9.1)}" android:fillColor="{cor_unica or cor}"/>')
    return '\n        '.join(partes)


def android_icone():
    # Ícone adaptativo: 108 dp, área visível de 72 dp e zona segura de 66 dp; o símbolo cabe num raio de 31 dp.
    escala, t = 0.40, 54 - 80 * 0.40
    cabeca = ('<vector xmlns:android="http://schemas.android.com/apk/res/android" xmlns:aapt="http://schemas.android.com/aapt"\n'
              '    android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">\n')
    grupo = f'    <group android:translateX="{t}" android:translateY="{t}" android:scaleX="{escala}" android:scaleY="{escala}">\n        '
    frente = cabeca + grupo + simbolo_vector() + '\n    </group>\n</vector>\n'
    mono = cabeca + grupo + simbolo_vector('#FFFFFFFF') + '\n    </group>\n</vector>\n'
    return frente, mono


def android_logo(fonte):
    """Logo horizontal (escuro, sem descritor) para o topo do app: contornos absolutos, sem transform de texto."""
    nome, x = fonte.texto(NOME, 286, 172.1, 102, 700)
    sufixo, x_fim = fonte.texto(SUFIXO, x, 172.1, 102, 700)
    esq, topo = 36 + 12.9 * 1.3 - 8, 31 + 2.9 * 1.3 - 8
    largura, altura = x_fim + 8 - esq, (157.1 - 2.9) * 1.3 + 16
    grad = _gradiente(x, 0, x_fim, 0, [(0, '#08ABD0'), (1, '#04B89C')]).replace('{attr}', 'fillColor')
    partes = [f'<group android:translateX="{36 - esq:.2f}" android:translateY="{31 - topo:.2f}" android:scaleX="1.3" android:scaleY="1.3">\n        '
              + simbolo_vector() + '\n    </group>']
    partes.append(f'<group android:translateX="{-esq:.2f}" android:translateY="{-topo:.2f}">')
    partes += [f'    <path android:pathData="{d}" android:fillColor="#FFFFFFFF"/>' for d in nome]
    partes.append(f'    <path android:pathData="{" ".join(sufixo)}">{grad}</path>')
    partes.append('</group>')
    return ('<vector xmlns:android="http://schemas.android.com/apk/res/android" xmlns:aapt="http://schemas.android.com/aapt"\n'
            f'    android:width="{largura / 4:.1f}dp" android:height="{altura / 4:.1f}dp" '
            f'android:viewportWidth="{largura:.2f}" android:viewportHeight="{altura:.2f}">\n    '
            + '\n    '.join(partes) + '\n</vector>\n')


def gerar(fonte_caminho):
    fonte = Fonte(fonte_caminho)
    SAIDA.mkdir(parents=True, exist_ok=True)
    for tema in ('claro', 'escuro'):
        (SAIDA / f'simbolo-{tema}.svg').write_text(simbolo_arquivo(tema), encoding='utf-8')
        (SAIDA / f'horizontal-{tema}.svg').write_text(horizontal(fonte, tema, True), encoding='utf-8')
        (SAIDA / f'logo-{tema}.svg').write_text(horizontal(fonte, tema, False), encoding='utf-8')
    (SAIDA / 'app-icon.svg').write_text(app_icon(), encoding='utf-8')
    (SAIDA / 'app-icon-maskable.svg').write_text(app_icon(maskable=True), encoding='utf-8')
    frente, mono = android_icone()
    (RES / 'drawable' / 'ic_frente.xml').write_text(frente, encoding='utf-8')
    (RES / 'drawable' / 'ic_monocromatico.xml').write_text(mono, encoding='utf-8')
    (RES / 'drawable' / 'logo_nexocampo.xml').write_text(android_logo(fonte), encoding='utf-8')
    print('SVGs em', SAIDA.relative_to(RAIZ), 'e vetores do app em', RES.relative_to(RAIZ))


def gerar_png():
    """PNGs a partir dos SVGs (Chromium do Playwright)."""
    from playwright.sync_api import sync_playwright
    alvos = [('app-icon.svg', 'app-icon-192.png', 192), ('app-icon.svg', 'app-icon-512.png', 512),
             ('app-icon-maskable.svg', 'maskable-512.png', 512), ('horizontal-escuro.svg', 'horizontal-escuro-2048.png', 2048),
             ('horizontal-claro.svg', 'horizontal-claro-2048.png', 2048)]
    with sync_playwright() as p:
        nav = p.chromium.launch()
        for origem, destino, largura in alvos:
            svg = (SAIDA / origem).read_text(encoding='utf-8')
            m = re.search(r'viewBox="([\d.\s-]+)"', svg)
            _, _, w, h = (float(v) for v in m.group(1).split())
            altura = round(largura * h / w)
            pagina = nav.new_page(viewport={'width': largura, 'height': altura})
            pagina.set_content(f'<html><body style="margin:0;background:transparent">'
                               f'<img src="data:image/svg+xml;base64,{__import__("base64").b64encode(svg.encode()).decode()}" '
                               f'width="{largura}" height="{altura}" style="display:block"></body></html>')
            pagina.screenshot(path=str(SAIDA / destino), omit_background=True)
            pagina.close()
        nav.close()
    print('PNGs em', SAIDA.relative_to(RAIZ))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--fonte', help='Inter variável com opsz (woff2/ttf)')
    ap.add_argument('--png', action='store_true', help='só exportar os PNGs a partir dos SVGs')
    a = ap.parse_args()
    if a.png:
        gerar_png()
    else:
        if not a.fonte:
            ap.error('passe --fonte (veja o cabeçalho do script)')
        gerar(a.fonte)
