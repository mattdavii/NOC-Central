"""NexoCampo na Central: acesso do app do técnico e as visitas de campo que ele envia.

O app entra com o usuário do NexoHub (só a equipe da MD) e recebe um token próprio do aparelho; no banco fica
só o hash. Cada visita chega com o JSON do levantamento e o PDF, ligada a uma empresa e, se quiser, a um local
(sensor) e a um chamado do NexoDesk. Reenviar a mesma visita (mesmo id) atualiza a que já estava lá.
"""
import hashlib
import json
import secrets
import time

import nexodesk

LIMITE_PDF = 20 * 1024 * 1024
LIMITE_JSON = 4 * 1024 * 1024
LIMITE_REQUISICAO = LIMITE_PDF + LIMITE_JSON + 1024 * 1024


class Recusa(Exception):
    def __init__(self, mensagem, status=400):
        super().__init__(mensagem)
        self.mensagem, self.status = mensagem, status


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def criar_token(conn, usuario_id, aparelho, agora=None):
    token = 'ncp_' + secrets.token_urlsafe(32)
    conn.execute('INSERT INTO campo_tokens (usuario_id, token_hash, aparelho, criado_em) VALUES (?, ?, ?, ?)',
                 (usuario_id, _hash(token), (aparelho or '')[:120], agora or time.time()))
    return token


def usuario_do_token(conn, token, agora=None):
    """Usuário (dict) dono de um token válido, ativo e da MD; None se não."""
    if not token or not token.startswith('ncp_'):
        return None
    row = conn.execute('SELECT t.id AS token_id, c.* FROM campo_tokens t JOIN clientes c ON c.id = t.usuario_id '
                       'WHERE t.token_hash = ? AND t.revogado_em IS NULL', (_hash(token),)).fetchone()
    if not row or not row['ativo'] or not nexodesk.eh_md(row):
        return None
    conn.execute('UPDATE campo_tokens SET usado_em = ? WHERE id = ?', (agora or time.time(), row['token_id']))
    usuario = dict(row)
    usuario.pop('senha', None)
    return usuario


def revogar(conn, token, agora=None):
    conn.execute('UPDATE campo_tokens SET revogado_em = ? WHERE token_hash = ? AND revogado_em IS NULL', (agora or time.time(), _hash(token)))


def locais(conn):
    """Empresas, os locais monitorados de cada uma e os chamados abertos (para ligar a visita)."""
    abertos = {}
    for r in conn.execute(f"SELECT id, empresa_id, titulo, status FROM chamados WHERE status IN ({', '.join('?' for _ in nexodesk.ABERTOS)}) "
                          'ORDER BY id DESC', nexodesk.ABERTOS).fetchall():
        abertos.setdefault(r['empresa_id'], []).append({'numero': r['id'], 'titulo': r['titulo'], 'status': nexodesk.STATUS.get(r['status'], r['status'])})
    return [{**e, 'locais': nexodesk.locais_da_empresa(conn, e['id']), 'chamados': abertos.get(e['id'], [])[:30]}
            for e in nexodesk.empresas_para_md(conn)]


def resumo_de(dados):
    """Linha curta para a lista da Central."""
    partes = []
    if dados.get('dispositivos'):
        partes.append(f"{len(dados['dispositivos'])} aparelhos")
    if dados.get('wifi_ao_redor'):
        partes.append(f"{len(dados['wifi_ao_redor'])} redes Wi-Fi")
    v = dados.get('velocidade') or {}
    if v.get('download_mbps') is not None:
        partes.append(f"↓ {v['download_mbps']:.0f} / ↑ {(v.get('upload_mbps') or 0):.0f} Mbps")
    if dados.get('ambientes'):
        partes.append(f"{len(dados['ambientes'])} ambientes")
    if dados.get('fotos'):
        partes.append(f"{len(dados['fotos'])} fotos")
    return ' · '.join(partes) or 'sem medições'


def receber(conn, autor, form, pdf, agora=None):
    """Grava (ou atualiza) a visita. Devolve (id, novo, chamado ligado agora ou None)."""
    agora = agora or time.time()
    try:
        dados = json.loads(form.get('dados') or '')
    except ValueError:
        raise Recusa('Os dados da visita vieram ilegíveis.')
    if not isinstance(dados, dict) or dados.get('formato') != 'nexocampo.levantamento-rede' or not dados.get('id'):
        raise Recusa('Isto não é um levantamento do NexoCampo.')
    if len(form.get('dados') or '') > LIMITE_JSON:
        raise Recusa('Os dados da visita passam do tamanho aceito.')
    if pdf is not None and len(pdf) > LIMITE_PDF:
        raise Recusa(f'O PDF passa de {LIMITE_PDF // (1024 * 1024)} MB.')
    try:
        empresa_id = int(form.get('empresa_id') or 0)
    except ValueError:
        empresa_id = 0
    if empresa_id not in {e['id'] for e in nexodesk.empresas_para_md(conn)}:
        raise Recusa('Escolha a empresa da visita.')
    mac = (form.get('sensor_mac') or '').strip() or None
    if mac and mac not in {l['mac_id'] for l in nexodesk.locais_da_empresa(conn, empresa_id)}:
        raise Recusa('Esse local não é da empresa escolhida.')
    chamado = None
    if form.get('chamado'):
        try:
            chamado = nexodesk.chamado(conn, int(form.get('chamado')))
        except ValueError:
            chamado = None
        if not chamado or chamado['empresa_id'] != empresa_id:
            raise Recusa('Esse chamado não é da empresa escolhida.')
    uuid = str(dados['id'])[:64]
    texto = json.dumps(dados, ensure_ascii=False)
    quando = (dados.get('quando_ms') or 0) / 1000 or agora
    campos = (empresa_id, mac, chamado['id'] if chamado else None, autor['id'], (dados.get('local') or '')[:200],
              (dados.get('tecnico') or '')[:120], quando, agora, resumo_de(dados), texto, nexodesk.database_binary(pdf) if pdf else None)
    existente = conn.execute('SELECT id, chamado_id FROM visitas_campo WHERE uuid = ?', (uuid,)).fetchone()
    if existente:
        conn.execute('UPDATE visitas_campo SET empresa_id=?, sensor_mac=?, chamado_id=?, usuario_id=?, local=?, tecnico=?, quando=?, '
                     'recebida_em=?, resumo=?, dados=?, pdf=COALESCE(?, pdf) WHERE id=?', campos + (existente['id'],))
        visita_id, novo, chamado_antes = existente['id'], False, existente['chamado_id']
    else:
        visita_id = conn.execute('INSERT INTO visitas_campo (empresa_id, sensor_mac, chamado_id, usuario_id, local, tecnico, quando, '
                                 'recebida_em, resumo, dados, pdf, uuid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id',
                                 campos + (uuid,)).fetchone()['id']
        novo, chamado_antes = True, None
    ligado = None
    # Anexa ao chamado como nota interna da MD (uma vez por chamado; o cliente não vê o levantamento técnico).
    if chamado and chamado['id'] != chamado_antes and chamado['status'] not in nexodesk.ENCERRADOS:
        nome_pdf = f"visita-campo-{visita_id}.pdf"
        texto_nota = (f"Visita de campo #{visita_id} registrada pelo NexoCampo"
                      f"{' em ' + dados['local'] if dados.get('local') else ''}"
                      f"{' (técnico: ' + dados['tecnico'] + ')' if dados.get('tecnico') else ''}: {resumo_de(dados)}.")
        cabe = pdf and len(pdf) <= nexodesk.MAX_ARQUIVO
        if pdf and not cabe:
            texto_nota += ' O PDF é grande demais para anexar aqui: baixe em Visitas de campo, na Central.'
        nexodesk.responder(conn, autor, chamado, texto_nota, arquivos=[(nome_pdf, pdf)] if cabe else [], nota=True, agora=agora)
        ligado = chamado['id']
    return visita_id, novo, ligado


def listar(conn, limite=200):
    rows = conn.execute('SELECT v.id, v.uuid, v.empresa_id, v.sensor_mac, v.chamado_id, v.local, v.tecnico, v.quando, v.recebida_em, '
                        'v.resumo, e.nome AS empresa, e.usuario AS empresa_usuario, s.nome_local, u.nome AS autor, u.usuario AS autor_usuario, '
                        'CASE WHEN v.pdf IS NULL THEN 0 ELSE 1 END AS tem_pdf '
                        'FROM visitas_campo v LEFT JOIN clientes e ON e.id = v.empresa_id LEFT JOIN sensores s ON s.mac_id = v.sensor_mac '
                        'LEFT JOIN clientes u ON u.id = v.usuario_id ORDER BY v.quando DESC LIMIT ?', (limite,)).fetchall()
    return [dict(r) for r in rows]


def visita(conn, visita_id):
    row = conn.execute('SELECT * FROM visitas_campo WHERE id = ?', (visita_id,)).fetchone()
    return dict(row) if row else None
