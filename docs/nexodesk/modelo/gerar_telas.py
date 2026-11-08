"""Gera as imagens do modelo de telas do NexoDesk a partir de telas.html.

Uso: python3 docs/nexodesk/modelo/gerar_telas.py [caminho-do-font-awesome/all.min.css]
Sem o caminho, o Font Awesome vem do CDN (precisa de internet).
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

AQUI = pathlib.Path(__file__).resolve().parent
FA_LOCAL = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None

with sync_playwright() as p:
    nav = p.chromium.launch()
    pag = nav.new_page(viewport={'width': 1500, 'height': 1000}, device_scale_factor=2)
    if FA_LOCAL:
        base = FA_LOCAL.parent.parent  # .../fontawesome-free
        def servir(rota):
            nome = rota.request.url.split('/font-awesome/6.4.0/')[1]
            rota.fulfill(path=str(base / nome))
        pag.route('**/cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/**', servir)
    pag.goto((AQUI / 'telas.html').as_uri())
    pag.wait_for_load_state('networkidle')
    pag.evaluate('document.fonts.ready')
    for tela in pag.query_selector_all('[data-tela]'):
        nome = tela.get_attribute('data-tela')
        tela.screenshot(path=str(AQUI / f'{nome}.png'))
        print(nome)
    nav.close()
