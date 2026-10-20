"""Kit de marca da Família Nexo (assets/branding/nexo): o validador passa no kit e reprova kits quebrados."""
import importlib.util
import shutil
from pathlib import Path

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
