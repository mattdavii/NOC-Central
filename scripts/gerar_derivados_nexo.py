#!/usr/bin/env python3
"""Gera, a partir do kit (assets/branding/nexo/logos/<slug>), os derivados que a Central usa.

Marcas configuradas em MARCAS (hoje NexoPulse e NexoHub). Para cada uma:
Masters SVG (assets/branding/nexo/derivados/<slug>/):
  - horizontal-sem-descritor-{claro,escuro}.svg: a horizontal do kit sem o descritor, com o nome
    centralizado no símbolo e o viewBox recortado (para barra, login e relatório);
  - favicon.svg: versão simplificada para 16-48 px (hexágono e pulso grossos, sem os nós);
  - app-icon-maskable.svg: ícone do kit com fundo de ponta a ponta (Android recorta a forma).
Exportações (static/brand/<slug>/): logos SVG servidos pela Central, símbolos do kit, ícones PWA
192/512 (kit), maskable 192/512, apple-touch-icon 180 e favicon 16/32/48 + favicon.ico.

Requer Playwright com Chrome (mede o texto e rasteriza): python scripts/gerar_derivados_nexo.py [slug ...]
"""
import json
import re
import shutil
import struct
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parents[1]
BRANDING = RAIZ / 'assets' / 'branding' / 'nexo'
CHROME = '/usr/bin/google-chrome'
COR_DESCRITOR = {'claro': '#566982', 'escuro': '#BDCCDC'}
# Símbolo no kit: translate(36 31) scale(1.3) sobre viewBox 160; nós com raio 9.1.
SIMBOLO_TOPO, SIMBOLO_BASE, SIMBOLO_ESQ = 31 + (12 - 9.1) * 1.3, 31 + (148 + 9.1) * 1.3, 36 + (22 - 9.1) * 1.3
MARGEM = 8


def sem_descritor(svg, tema, medir, descritor):
    """Remove os glifos do descritor (cor própria), centraliza o nome no símbolo e recorta o viewBox."""
    linhas = svg.splitlines()
    linhas = [l for l in linhas if f'fill="{COR_DESCRITOR[tema]}"' not in l]
    nome = [i for i, l in enumerate(linhas) if 'translate(' in l and 'scale(0.049805' in l]
    assert nome, 'glifos do nome não encontrados'
    texto = '\n'.join(linhas)
    texto = texto.replace(f' — {descritor}', '')
    # Centraliza a altura das maiúsculas (topo do "N" até a linha de base) no centro do símbolo.
    caixa = medir(texto, 'nome')
    centro_simbolo = (SIMBOLO_TOPO + SIMBOLO_BASE) / 2
    deslocamento = round(centro_simbolo - (caixa['topo_maiuscula'] + caixa['base']) / 2, 2)
    linhas = texto.splitlines()
    primeiro, ultimo = min(nome), max(nome)
    linhas[primeiro] = f'<g transform="translate(0 {deslocamento})">' + linhas[primeiro]
    linhas[ultimo] = linhas[ultimo] + '</g>'
    texto = '\n'.join(linhas)
    direita = caixa['direita']
    x0, y0 = SIMBOLO_ESQ - MARGEM, SIMBOLO_TOPO - MARGEM
    largura, altura = direita + MARGEM - x0, SIMBOLO_BASE + MARGEM - y0
    texto = re.sub(r'width="1320" height="270" viewBox="0 0 1320 270"',
                   f'width="{largura:.0f}" height="{altura:.0f}" viewBox="{x0:.2f} {y0:.2f} {largura:.2f} {altura:.2f}"', texto, count=1)
    return texto


def maskable(app_icon, nome):
    """Fundo azul-marinho de ponta a ponta e símbolo reduzido para sobrar folga na zona segura (raio 40%).

    No kit (escala 1.24) os nós chegam a 37% do lado a partir do centro, rente ao limite; com 1.1 ficam em 33%."""
    texto = re.sub(r'<rect x="3" y="3" width="250" height="250" rx="52" fill="#0B1B32"/>', '<rect width="256" height="256" fill="#0B1B32"/>', app_icon)
    texto = re.sub(r'<rect x="3" y="3" width="250" height="250" rx="52" fill="none" stroke="#234664" stroke-width="3"/>', '', texto)
    assert 'translate(29 29) scale(1.24)' in texto
    texto = texto.replace('translate(29 29) scale(1.24)', 'translate(40 40) scale(1.1)')  # centro (80, 80) * 1.1 + 40 = 128
    assert '<rect width="256" height="256" fill="#0B1B32"/>' in texto and 'rx="52"' not in texto
    return re.sub(r'Ícone PWA nexo[a-z]+', f'Ícone PWA {nome} (maskable)', texto)


FAVICON_BASE = '''<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32" role="img" aria-label="{nome}">
<title>{nome}</title>
<defs><linearGradient id="nexoFavGrad" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#08ABD0"/><stop offset="100%" stop-color="#04B89C"/></linearGradient></defs>
<rect width="32" height="32" rx="7" fill="#0B1B32"/>
<path d="M16 4.6 L25.9 10.3 V21.7 L16 27.4 L6.1 21.7 V10.3 Z" fill="none" stroke="url(#nexoFavGrad)" stroke-width="2.6" stroke-linejoin="round"/>
{interno}
</svg>
'''
# Miolo do favicon simplificado: o ícone de cada marca reduzido ao que se lê em 16 px (sem os nós do hexágono).
MARCAS = {
    'nexopulse': {'interno': '<path d="M9.2 16.6 H11.6 L13.1 19.6 L15.6 11.2 L18.1 20.2 L20.1 14.6 L21.4 16.6 H22.8" fill="none" stroke="#4DE6D9" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'},
    'nexohub': {'interno': '<circle cx="16" cy="16" r="5" fill="none" stroke="#4DE6D9" stroke-width="2.2"/>\n<circle cx="16" cy="16" r="1.7" fill="#4DE6D9"/>'},
}


def favicon(nome, interno):
    return FAVICON_BASE.format(nome=nome, interno=interno)


def ico_com_pngs(pngs):
    """Arquivo .ico com entradas PNG (aceito por todos os navegadores atuais)."""
    cabecalho = struct.pack('<HHH', 0, 1, len(pngs))
    deslocamento = 6 + 16 * len(pngs)
    entradas, dados = b'', b''
    for tamanho, conteudo in pngs:
        lado = 0 if tamanho >= 256 else tamanho
        entradas += struct.pack('<BBBBHHII', lado, lado, 0, 0, 1, 32, len(conteudo), deslocamento + len(dados))
        dados += conteudo
    return cabecalho + entradas + dados


def gerar(pg, medir, rasterizar, slug):
    manifesto = {m['slug']: m for m in json.loads((BRANDING / 'manifesto.json').read_text(encoding='utf-8'))['logos']}[slug]
    kit, masters, static = BRANDING / 'logos' / slug, BRANDING / 'derivados' / slug, RAIZ / 'static' / 'brand' / slug
    masters.mkdir(parents=True, exist_ok=True)
    static.mkdir(parents=True, exist_ok=True)
    for tema in ('claro', 'escuro'):
        svg = sem_descritor((kit / f'horizontal-{tema}.svg').read_text(encoding='utf-8'), tema, medir, manifesto['descricao'])
        (masters / f'horizontal-sem-descritor-{tema}.svg').write_text(svg, encoding='utf-8')
        shutil.copyfile(masters / f'horizontal-sem-descritor-{tema}.svg', static / f'logo-{tema}.svg')
        shutil.copyfile(kit / f'simbolo-{tema}.svg', static / f'simbolo-{tema}.svg')
    masc = maskable((kit / 'app-icon.svg').read_text(encoding='utf-8'), manifesto['nome'])
    (masters / 'app-icon-maskable.svg').write_text(masc, encoding='utf-8')
    fav = favicon(manifesto['nome'], MARCAS[slug]['interno'])
    (masters / 'favicon.svg').write_text(fav, encoding='utf-8')
    shutil.copyfile(masters / 'favicon.svg', static / 'favicon.svg')
    for px in (192, 512):
        shutil.copyfile(kit / f'app-icon-{px}.png', static / f'icon-{px}.png')
        rasterizar(masc, static / f'maskable-{px}.png', px)
    rasterizar(masc, static / 'apple-touch-icon.png', 180)
    pngs = []
    for px in (16, 32, 48):
        rasterizar(fav, static / f'favicon-{px}.png', px)
        pngs.append((px, (static / f'favicon-{px}.png').read_bytes()))
    (static / 'favicon.ico').write_bytes(ico_com_pngs(pngs))
    print(f'{slug}: derivados em {masters.relative_to(RAIZ)} e {static.relative_to(RAIZ)}')


def main():
    slugs = sys.argv[1:] or list(MARCAS)
    desconhecidas = set(slugs) - set(MARCAS)
    if desconhecidas:
        sys.exit(f'sem configuração de favicon para: {sorted(desconhecidas)} (adicione em MARCAS)')
    with sync_playwright() as p:
        nav = p.chromium.launch(executable_path=CHROME)
        pg = nav.new_page()

        def medir(svg, _):
            pg.set_content(f'<html><body style="margin:0">{svg}</body></html>')
            return pg.evaluate('''() => {
                const glifos = [...document.querySelectorAll('path[transform*="scale(0.049805"]')];
                const caixas = glifos.map(g => g.getBoundingClientRect());
                const svg = document.querySelector('svg').getBoundingClientRect();
                const n = glifos[0].getBoundingClientRect();   // primeira letra ("N"): topo das maiúsculas
                const base = parseFloat(glifos[0].getAttribute('transform').match(/translate\\([^ ]+ ([^)]+)\\)/)[1]);
                return { topo_maiuscula: n.top - svg.top, base, direita: Math.max(...caixas.map(c => c.right)) - svg.left };
            }''')

        def rasterizar(svg, destino, lado):
            pg.set_viewport_size({'width': lado, 'height': lado})
            corpo = re.sub(r'<svg ', f'<svg style="display:block;width:{lado}px;height:{lado}px" ', svg, count=1)
            pg.set_content(f'<html><body style="margin:0;background:transparent">{corpo}</body></html>')
            pg.screenshot(path=str(destino), omit_background=True)

        for slug in slugs:
            gerar(pg, medir, rasterizar, slug)
        nav.close()


if __name__ == '__main__':
    sys.exit(main())
