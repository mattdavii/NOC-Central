#!/usr/bin/env python3
"""Valida o kit de marca da Família Nexo em assets/branding/nexo/.

Checagens essenciais (só biblioteca padrão, rodam no CI):
  - as nove marcas do manifesto, cada uma com os 15 arquivos esperados e nada sobrando;
  - SVG: XML válido, viewBox, sem script/foreignObject/eventos/referências externas;
  - SVG final com texto em contornos (sem <text>) e editável com <text> em Inter Display;
  - claro x escuro: mesmo viewBox e mesma geometria (só as cores mudam);
  - PNG: assinatura, dimensões exatas e RGBA de 8 bits;
  - paleta oficial presente na paleta.json.
Checagens de pixel (com Pillow, se instalado; use --pixels para exigir):
  - horizontais e símbolos com fundo transparente e conteúdo visível;
  - ícones de aplicativo com cantos transparentes e fundo azul-marinho.

Uso: python3 scripts/validar_marca_nexo.py [--pixels]
Sai com código 1 se algo falhar.
"""
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1] / 'assets' / 'branding' / 'nexo'
SVG_NS = '{http://www.w3.org/2000/svg}'
PALETA_OFICIAL = {'navy': '#0B1B32', 'cyan': '#08ABD0', 'green': '#04B89C', 'pale': '#F3F7FB'}
TEMAS = ('claro', 'escuro')
ICONES_PX = (192, 512, 1024, 2048)
ARQUIVOS_POR_MARCA = (
    [f'horizontal-{t}.svg' for t in TEMAS] + [f'horizontal-{t}-editavel.svg' for t in TEMAS]
    + [f'horizontal-{t}-2048.png' for t in TEMAS] + [f'simbolo-{t}.svg' for t in TEMAS]
    + [f'simbolo-{t}-1024.png' for t in TEMAS] + ['app-icon.svg'] + [f'app-icon-{px}.png' for px in ICONES_PX]
)
PROIBIDO_SVG = re.compile(r'<script|<foreignObject|\son[a-z]+\s*=|(?:xlink:)?href\s*=\s*"(?!#)|url\(\s*["\']?(?!#)|@import', re.I)
COR = re.compile(r'#[0-9A-Fa-f]{3,8}\b|url\(#[^)]+\)')


class Relatorio:
    def __init__(self):
        self.erros, self.ok = [], 0

    def checa(self, condicao, mensagem):
        if condicao:
            self.ok += 1
        else:
            self.erros.append(mensagem)
        return condicao


def ler_png(caminho):
    """(largura, altura, profundidade, tipo_cor) lidos do cabeçalho IHDR."""
    dados = caminho.read_bytes()[:33]
    if dados[:8] != b'\x89PNG\r\n\x1a\n' or dados[12:16] != b'IHDR':
        return None
    largura, altura, profundidade, tipo = struct.unpack('>IIBB', dados[16:26])
    return largura, altura, profundidade, tipo


def geometria(svg_texto):
    """Estrutura do SVG sem as cores: claro e escuro devem dar o mesmo resultado."""
    return COR.sub('C', svg_texto)


def validar_svg(rel, caminho, editavel):
    texto = caminho.read_text(encoding='utf-8')
    try:
        raiz = ET.fromstring(texto)
    except ET.ParseError as erro:
        rel.checa(False, f'{caminho}: XML inválido ({erro})')
        return None
    rel.checa(raiz.tag == SVG_NS + 'svg', f'{caminho}: raiz não é <svg>')
    rel.checa(bool(raiz.get('viewBox')), f'{caminho}: sem viewBox (não escala)')
    rel.checa(not PROIBIDO_SVG.search(texto), f'{caminho}: conteúdo ativo ou referência externa')
    tem_texto = raiz.find(f'.//{SVG_NS}text') is not None
    if editavel:
        rel.checa(tem_texto and 'Inter Display' in texto, f'{caminho}: editável sem <text> em Inter Display')
    else:
        rel.checa(not tem_texto, f'{caminho}: SVG final ainda tem <text> (deveria estar em contornos)')
    return texto


def validar_estrutura(rel):
    manifesto = json.loads((RAIZ / 'manifesto.json').read_text(encoding='utf-8'))
    paleta = json.loads((RAIZ / 'paleta.json').read_text(encoding='utf-8'))
    for nome, cor in PALETA_OFICIAL.items():
        rel.checa(paleta['cores'].get(nome, '').upper() == cor, f'paleta.json: {nome} deveria ser {cor}')
    slugs = [m['slug'] for m in manifesto['logos']]
    rel.checa(len(slugs) == 9 and len(set(slugs)) == 9, f'manifesto.json: esperava 9 marcas distintas, achei {slugs}')
    pastas = sorted(p.name for p in (RAIZ / 'logos').iterdir() if p.is_dir())
    rel.checa(pastas == sorted(slugs), f'pastas em logos/ ({pastas}) diferem do manifesto ({sorted(slugs)})')
    rel.checa((RAIZ / 'referencias' / 'painel-visual-aprovado.png').is_file(), 'falta referencias/painel-visual-aprovado.png')

    for slug in slugs:
        pasta = RAIZ / 'logos' / slug
        presentes = sorted(p.name for p in pasta.iterdir()) if pasta.is_dir() else []
        faltando = sorted(set(ARQUIVOS_POR_MARCA) - set(presentes))
        sobrando = sorted(set(presentes) - set(ARQUIVOS_POR_MARCA))
        rel.checa(not faltando, f'{slug}: faltam {faltando}')
        rel.checa(not sobrando, f'{slug}: arquivos inesperados {sobrando}')
        if faltando:
            continue
        svgs = {}
        for nome in ARQUIVOS_POR_MARCA:
            if nome.endswith('.svg'):
                svgs[nome] = validar_svg(rel, pasta / nome, editavel='editavel' in nome)
        for base in ('horizontal-{}.svg', 'horizontal-{}-editavel.svg', 'simbolo-{}.svg'):
            claro, escuro = svgs[base.format('claro')], svgs[base.format('escuro')]
            if claro and escuro:
                rel.checa(geometria(claro) == geometria(escuro), f'{slug}: {base.format("*")} claro e escuro com geometria diferente')
        for nome in ARQUIVOS_POR_MARCA:
            if not nome.endswith('.png'):
                continue
            info = ler_png(pasta / nome)
            if not rel.checa(info is not None, f'{slug}/{nome}: PNG corrompido ou sem IHDR'):
                continue
            largura, altura, profundidade, tipo = info
            rel.checa((profundidade, tipo) == (8, 6), f'{slug}/{nome}: esperava RGBA 8 bits, achei profundidade {profundidade} tipo {tipo}')
            if nome.startswith('horizontal'):
                rel.checa(largura == 2048 and 300 < altura < 1024, f'{slug}/{nome}: esperava 2048 px de largura, achei {largura}x{altura}')
            elif nome.startswith('simbolo'):
                rel.checa((largura, altura) == (1024, 1024), f'{slug}/{nome}: esperava 1024x1024, achei {largura}x{altura}')
            else:
                px = int(nome.split('-')[-1].split('.')[0])
                rel.checa((largura, altura) == (px, px), f'{slug}/{nome}: esperava {px}x{px}, achei {largura}x{altura}')
    return slugs


def validar_pixels(rel, slugs):
    from PIL import Image  # noqa: PLC0415 (opcional)
    navy = tuple(int(PALETA_OFICIAL['navy'][i:i + 2], 16) for i in (1, 3, 5))
    for slug in slugs:
        pasta = RAIZ / 'logos' / slug
        for nome in [f'horizontal-{t}-2048.png' for t in TEMAS] + [f'simbolo-{t}-1024.png' for t in TEMAS]:
            img = Image.open(pasta / nome).convert('RGBA')
            alfa = img.getchannel('A')
            largura, altura = img.size
            cantos = [alfa.getpixel(p) for p in ((0, 0), (largura - 1, 0), (0, altura - 1), (largura - 1, altura - 1))]
            rel.checa(max(cantos) == 0, f'{slug}/{nome}: cantos não transparentes {cantos}')
            hist = alfa.histogram()
            transparentes = hist[0] / (largura * altura)
            rel.checa(0.3 < transparentes < 0.99, f'{slug}/{nome}: fundo transparente em {transparentes:.0%} (esperado entre 30% e 99%)')
            rel.checa(sum(hist[250:]) > 1000, f'{slug}/{nome}: quase nenhum pixel opaco (imagem vazia?)')
        for px in ICONES_PX:
            img = Image.open(pasta / f'app-icon-{px}.png').convert('RGBA')
            rel.checa(img.getpixel((0, 0))[3] == 0, f'{slug}/app-icon-{px}.png: canto deveria ser transparente (cantos arredondados)')
            fundo = img.getpixel((px // 2, int(px * 0.05)))
            rel.checa(fundo[3] == 255 and all(abs(a - b) <= 6 for a, b in zip(fundo[:3], navy)),
                      f'{slug}/app-icon-{px}.png: fundo {fundo} deveria ser o azul-marinho {PALETA_OFICIAL["navy"]}')


def main():
    exigir_pixels = '--pixels' in sys.argv
    rel = Relatorio()
    slugs = validar_estrutura(rel)
    try:
        import PIL  # noqa: F401
        validar_pixels(rel, slugs)
        pixels = 'feitas'
    except ImportError:
        pixels = 'puladas (Pillow ausente)'
        rel.checa(not exigir_pixels, 'checagens de pixel exigidas, mas o Pillow não está instalado')
    print(f'Kit Nexo: {len(slugs)} marcas · {rel.ok} checagens ok · checagens de pixel {pixels}')
    for erro in rel.erros:
        print('ERRO:', erro)
    return 1 if rel.erros else 0


if __name__ == '__main__':
    sys.exit(main())
