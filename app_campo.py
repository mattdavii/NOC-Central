"""NexoCampo: a Central entrega a versão mais nova do app de campo (Android) para ele se atualizar.

Cada APK é publicado pelo GitHub Actions como uma versão (tag nexocampo-v0.1.N) do repositório, que é
privado. A Central lê essas versões com um token só de leitura (GITHUB_TOKEN_APP) e repassa ao app:
  GET /app/nexocampo/versao  -> número da versão mais nova (o app compara com a dele)
  GET /app/nexocampo.apk     -> o arquivo, guardado em cache no disco da Central
Sem o token, as duas rotas respondem 503 e o app segue funcionando sem o aviso de atualização.
"""
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request

REPO = os.environ.get('GITHUB_REPO_APP', 'mattdavii/NOC-Central')
API = 'https://api.github.com'
PREFIXO_TAG = 'nexocampo-v'
CACHE_S = 300
PASTA = os.path.join(tempfile.gettempdir(), 'nexocampo')

_trava = threading.Lock()
_cache = {'quando': 0.0, 'versao': None}


class SemConfiguracao(Exception):
    pass


def _token():
    token = (os.environ.get('GITHUB_TOKEN_APP') or '').strip()
    if not token:
        raise SemConfiguracao('GITHUB_TOKEN_APP não configurado')
    return token


def _pedir(url, aceitar='application/vnd.github+json', tempo=20):
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {_token()}', 'Accept': aceitar,
                                               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'NexoPulse-Central'})
    return urllib.request.urlopen(req, timeout=tempo)


def versao_da_tag(tag):
    """'nexocampo-v0.1.12' -> (0, 1, 12); None se não for do app."""
    m = re.fullmatch(re.escape(PREFIXO_TAG) + r'(\d+)\.(\d+)\.(\d+)', tag or '')
    return tuple(int(x) for x in m.groups()) if m else None


def escolher(versoes):
    """A versão mais nova com um .apk, a partir da lista de versões do GitHub."""
    melhor = None
    for v in versoes:
        numero = versao_da_tag(v.get('tag_name'))
        apk = next((a for a in v.get('assets') or [] if a.get('name', '').endswith('.apk')), None)
        if not numero or not apk or v.get('draft'):
            continue
        if melhor is None or numero > melhor['numero']:
            melhor = {'numero': numero, 'versao': '.'.join(map(str, numero)), 'versao_codigo': numero[2],
                      'asset_id': apk['id'], 'tamanho': apk.get('size'), 'publicado_em': v.get('published_at'),
                      'notas': (v.get('body') or '').strip()[:2000]}
    return melhor


def ultima_versao(forcar=False):
    with _trava:
        if not forcar and _cache['versao'] and time.time() - _cache['quando'] < CACHE_S:
            return _cache['versao']
    with _pedir(f'{API}/repos/{REPO}/releases?per_page=30') as r:
        versao = escolher(json.loads(r.read().decode()))
    with _trava:
        _cache.update(quando=time.time(), versao=versao)
    return versao


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def arquivo_apk(versao):
    """Caminho do APK no disco da Central (baixa do GitHub na primeira vez)."""
    os.makedirs(PASTA, exist_ok=True)
    destino = os.path.join(PASTA, f"nexocampo-{versao['versao']}.apk")
    if os.path.exists(destino) and (not versao.get('tamanho') or os.path.getsize(destino) == versao['tamanho']):
        return destino
    url = f"{API}/repos/{REPO}/releases/assets/{versao['asset_id']}"
    # O GitHub responde com um redirecionamento para o armazenamento dele: segue sem o token.
    abridor = urllib.request.build_opener(_SemRedirecionar)
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {_token()}', 'Accept': 'application/octet-stream',
                                               'User-Agent': 'NexoPulse-Central'})
    try:
        resposta = abridor.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        resposta = urllib.request.urlopen(urllib.request.Request(e.headers['Location'], headers={'User-Agent': 'NexoPulse-Central'}),
                                          timeout=120)
    temporario = destino + '.parcial'
    with resposta, open(temporario, 'wb') as saida:
        while True:
            bloco = resposta.read(256 * 1024)
            if not bloco:
                break
            saida.write(bloco)
    os.replace(temporario, destino)
    for antigo in os.listdir(PASTA):   # só guarda a versão atual
        if antigo.endswith('.apk') and os.path.join(PASTA, antigo) != destino:
            try:
                os.remove(os.path.join(PASTA, antigo))
            except OSError:
                pass
    return destino
