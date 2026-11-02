"""Kit de marca da Família Nexo (assets/branding/nexo): o validador passa no kit e reprova kits quebrados."""
import importlib.util
import shutil
from pathlib import Path

import pytest

RAIZ_REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('validar_marca_nexo', RAIZ_REPO / 'scripts' / 'validar_marca_nexo.py')
validador = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validador)


def _validar(raiz, monkeypatch):
    monkeypatch.setattr(validador, 'RAIZ', raiz)
    rel = validador.Relatorio()
    validador.validar_estrutura(rel)
    return rel


def test_kit_oficial_passa(monkeypatch):
    rel = _validar(RAIZ_REPO / 'assets' / 'branding' / 'nexo', monkeypatch)
    assert rel.erros == []
    assert rel.ok >= 520  # 9 marcas x (SVGs, PNGs, claro x escuro) + manifesto e paleta


def test_validador_reprova_kit_quebrado(tmp_path, monkeypatch):
    kit = tmp_path / 'nexo'
    shutil.copytree(RAIZ_REPO / 'assets' / 'branding' / 'nexo', kit, ignore=shutil.ignore_patterns('*.png'))
    # Dos PNGs basta o cabeçalho (é o que as checagens essenciais leem); deixa o teste leve.
    for png in (RAIZ_REPO / 'assets' / 'branding' / 'nexo' / 'logos').rglob('*.png'):
        destino = kit / 'logos' / png.parent.name / png.name
        destino.write_bytes(png.read_bytes()[:64])
    (kit / 'logos' / 'nexoweb' / 'simbolo-escuro.svg').unlink()
    guard = kit / 'logos' / 'nexoguard' / 'horizontal-escuro.svg'
    guard.write_text(guard.read_text(encoding='utf-8').replace('</svg>', '<script>alert(1)</script></svg>'), encoding='utf-8')
    pulse = kit / 'logos' / 'nexopulse' / 'simbolo-claro.svg'
    pulse.write_text(pulse.read_text(encoding='utf-8').replace('r="9.1"', 'r="12"', 1), encoding='utf-8')
    (kit / 'logos' / 'nexohub' / 'app-icon-512.png').write_bytes(b'nao e png')
    rel = _validar(kit, monkeypatch)
    texto = '\n'.join(rel.erros)
    assert "nexoweb: faltam ['simbolo-escuro.svg']" in texto
    assert 'nexoguard/horizontal-escuro.svg: conteúdo ativo' in texto
    assert 'nexopulse: simbolo-*.svg claro e escuro com geometria diferente' in texto
    assert 'nexohub/app-icon-512.png: PNG corrompido' in texto


# ===== Derivados usados pela Central (scripts/gerar_derivados_nexo.py) =====


@pytest.mark.parametrize('slug,nome,descritor', [('nexopulse', 'NexoPulse', 'Monitoramento'), ('nexohub', 'NexoHub', 'Portal único')])
def test_horizontal_sem_descritor(slug, nome, descritor):
    static, derivados = RAIZ_REPO / 'static' / 'brand' / slug, RAIZ_REPO / 'assets' / 'branding' / 'nexo' / 'derivados' / slug
    for tema, cor in (('claro', '#566982'), ('escuro', '#BDCCDC')):
        svg = (static / f'logo-{tema}.svg').read_text(encoding='utf-8')
        assert svg == (derivados / f'horizontal-sem-descritor-{tema}.svg').read_text(encoding='utf-8')
        assert cor not in svg and descritor not in svg  # sem o descritor
        assert f'<title>{nome}</title>' in svg and 'viewBox="44.77 ' in svg
        assert not validador.PROIBIDO_SVG.search(svg)
        # O nome continua em contornos, igual ao kit.
        kit = (validador.RAIZ / 'logos' / slug / f'horizontal-{tema}.svg').read_text(encoding='utf-8')
        glifos_nome = [l for l in kit.splitlines() if 'translate(' in l and ' 144.000)' in l]
        assert glifos_nome and all(g.split(' transform=')[0] in svg for g in glifos_nome)


@pytest.mark.parametrize('slug', ['nexopulse', 'nexohub'])
def test_icones_e_favicon(slug):
    STATIC = RAIZ_REPO / 'static' / 'brand' / slug
    DERIVADOS = RAIZ_REPO / 'assets' / 'branding' / 'nexo' / 'derivados' / slug
    # tipo 6 = RGBA (cantos transparentes); tipo 2 = RGB, totalmente opaco (maskable e iOS não podem ter transparência)
    for nome, lado, tipo in (('icon-192.png', 192, 6), ('icon-512.png', 512, 6), ('maskable-192.png', 192, 2), ('maskable-512.png', 512, 2),
                             ('apple-touch-icon.png', 180, 2), ('favicon-16.png', 16, 6), ('favicon-32.png', 32, 6), ('favicon-48.png', 48, 6)):
        assert validador.ler_png(STATIC / nome) == (lado, lado, 8, tipo), nome
    assert (STATIC / 'icon-512.png').read_bytes() == (validador.RAIZ / 'logos' / slug / 'app-icon-512.png').read_bytes()
    maskable = (DERIVADOS / 'app-icon-maskable.svg').read_text(encoding='utf-8')
    assert '<rect width="256" height="256" fill="#0B1B32"/>' in maskable and 'rx=' not in maskable  # fundo de ponta a ponta
    ico = (STATIC / 'favicon.ico').read_bytes()
    assert ico[:4] == b'\x00\x00\x01\x00' and int.from_bytes(ico[4:6], 'little') == 3
    assert ico.count(b'\x89PNG') == 3
