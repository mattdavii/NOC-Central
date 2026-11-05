"""NexoCampo: a Central escolhe a versão mais nova do app e a entrega (com o GitHub simulado)."""
import io
import json

import app_campo


VERSOES = [
    {'tag_name': 'nexocampo-v0.1.3', 'draft': False, 'published_at': '2026-11-05T19:20:00Z', 'body': 'a',
     'assets': [{'id': 3, 'name': 'nexocampo-0.1.3.apk', 'size': 10}]},
    {'tag_name': 'nexocampo-v0.1.12', 'draft': False, 'published_at': '2026-11-06T10:00:00Z', 'body': 'b',
     'assets': [{'id': 12, 'name': 'nexocampo-0.1.12.apk', 'size': 12}]},
    {'tag_name': 'nexocampo-v0.1.20', 'draft': True, 'assets': [{'id': 20, 'name': 'x.apk', 'size': 1}]},   # rascunho
    {'tag_name': 'v2.2.0-rc1', 'draft': False, 'assets': [{'id': 99, 'name': 'central.apk', 'size': 1}]},     # não é do app
    {'tag_name': 'nexocampo-v0.1.15', 'draft': False, 'assets': [{'id': 15, 'name': 'notas.txt', 'size': 1}]},  # sem APK
]


def test_escolhe_a_versao_numerica_mais_nova_com_apk():
    v = app_campo.escolher(VERSOES)
    assert (v['versao'], v['versao_codigo'], v['asset_id']) == ('0.1.12', 12, 12)   # 12 > 3 (não é ordem de texto)
    assert app_campo.escolher([]) is None
    assert app_campo.versao_da_tag('nexocampo-v1.2.3') == (1, 2, 3) and app_campo.versao_da_tag('outra') is None


def test_sem_token_responde_503(client, monkeypatch):
    monkeypatch.delenv('GITHUB_TOKEN_APP', raising=False)
    app_campo._cache.update(quando=0, versao=None)
    assert client.get('/app/nexocampo/versao').status_code == 503
    assert client.get('/app/nexocampo.apk').status_code == 503


def test_versao_e_download(client, monkeypatch, tmp_path):
    monkeypatch.setenv('GITHUB_TOKEN_APP', 'teste')
    monkeypatch.setattr(app_campo, 'PASTA', str(tmp_path))
    app_campo._cache.update(quando=0, versao=None)
    pedidos = []

    class Resposta(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def falso(req, timeout=None):
        pedidos.append(req.full_url)
        return Resposta(json.dumps(VERSOES).encode())
    monkeypatch.setattr(app_campo.urllib.request, 'urlopen', falso)

    class Abridor:
        def open(self, req, timeout=None):
            assert req.get_header('Authorization') == 'Bearer teste'
            return Resposta(b'APK-BYTES-12')
    monkeypatch.setattr(app_campo.urllib.request, 'build_opener', lambda *a: Abridor())

    r = client.get('/app/nexocampo/versao')
    assert r.status_code == 200 and r.json['versao_codigo'] == 12 and r.json['url'].endswith('/app/nexocampo.apk')
    apk = client.get('/app/nexocampo.apk')
    assert apk.status_code == 200 and apk.data == b'APK-BYTES-12'
    assert apk.mimetype == 'application/vnd.android.package-archive'
    client.get('/app/nexocampo/versao')
    assert len(pedidos) == 1   # a lista de versões fica 5 minutos em cache
