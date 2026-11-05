"""NexoDesk: chamados e suporte da MD (regras, quem vê o quê e anexos).

Escopo: docs/nexodesk/ESCOPO.md. Horários em epoch (segundos, UTC); o número do chamado é o id da tabela.
"""
import os
import time

import acessos
import database

CATEGORIAS = {
    'rede': ('Internet e rede', 'fa-wifi'),
    'seguranca': ('Segurança e câmeras', 'fa-video'),
    'energia': ('Energia, elétrica e solar', 'fa-solar-panel'),
    'sistemas': ('Sistemas e software', 'fa-code'),
    'acesso': ('Acesso ao NexoHub', 'fa-user-lock'),
    'outros': ('Outros', 'fa-ellipsis'),
}
# O solicitante diz o impacto; ele sugere a prioridade, que a MD confirma.
IMPACTOS = {
    'parou': {'rotulo': 'Parou tudo', 'descricao': 'Ninguém consegue trabalhar ou um local inteiro está fora.', 'prioridade': 'critica'},
    'atrapalha': {'rotulo': 'Atrapalha o trabalho', 'descricao': 'Funciona com falhas, lentidão ou só em parte.', 'prioridade': 'alta'},
    'duvida': {'rotulo': 'Dúvida ou pedido', 'descricao': 'Uma orientação, um ajuste ou algo novo.', 'prioridade': 'baixa'},
}
PRIORIDADES = {'critica': 'Crítica', 'alta': 'Alta', 'media': 'Média', 'baixa': 'Baixa'}
STATUS = {'novo': 'Novo', 'atendimento': 'Em atendimento', 'aguardando': 'Aguardando cliente',
          'resolvido': 'Resolvido', 'fechado': 'Fechado', 'cancelado': 'Cancelado'}
ABERTOS = ('novo', 'atendimento', 'aguardando', 'resolvido')   # "resolvido" ainda espera a confirmação do cliente
ENCERRADOS = ('fechado', 'cancelado')
CANAIS = {'nexohub': 'NexoHub', 'whatsapp': 'WhatsApp', 'telefone': 'Telefone', 'presencial': 'Presencial', 'interno': 'Interno'}
CANAIS_DA_MD = ('whatsapp', 'telefone', 'presencial')   # a MD registrando um pedido que chegou por fora

# Tipo do arquivo pela extensão (nunca pelo que o navegador mandou).
TIPOS_ANEXO = {
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp',
    'pdf': 'application/pdf', 'txt': 'text/plain', 'log': 'text/plain', 'csv': 'text/csv',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}
ABRE_NO_NAVEGADOR = {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'application/pdf'}
MAX_ARQUIVO = 10 * 1024 * 1024
MAX_ARQUIVOS = 5
MAX_TITULO = 140
MAX_TEXTO = 20000
TELEFONE_MD = os.environ.get('MD_TELEFONE_SUPORTE', '(83) 98127-0116')
# Teto do corpo da requisição nas rotas que recebem arquivos (o resto da Central fica em 1 MB).
LIMITE_REQUISICAO = MAX_ARQUIVOS * MAX_ARQUIVO + 1024 * 1024


class Recusa(Exception):
    """Pedido inválido ou não permitido; a mensagem vai para a tela como está."""

    def __init__(self, mensagem, status=400):
        super().__init__(mensagem)
        self.mensagem, self.status = mensagem, status


def eh_md(usuario):
    return bool(usuario) and usuario['role'] in acessos.ROLES_MASTER


def nome_de(usuario):
    return (usuario.get('nome') or usuario.get('usuario') or 'Usuário') if usuario else 'Usuário'


def nivel(conn, usuario):
    return 'admin' if eh_md(usuario) else acessos.nivel_app(conn, usuario['id'], 'desk')


def ve_toda_empresa(conn, usuario):
    """Operador e admin veem os chamados de toda a empresa; leitura, só os que abriu."""
    n = nivel(conn, usuario)
    return bool(n) and acessos.nivel_suficiente(n, 'operador')


def pode_ver(conn, usuario, chamado):
    if eh_md(usuario):
        return True
    if not chamado or chamado['interno'] or chamado['empresa_id'] != acessos.empresa_de(usuario):
        return False
    if not nivel(conn, usuario):
        return False
    return chamado['solicitante_id'] == usuario['id'] or ve_toda_empresa(conn, usuario)


# ------------------------------------------------------------------ leitura
_CAMPOS = ('SELECT c.*, e.nome AS empresa_nome, e.usuario AS empresa_usuario, s.nome_local AS local_nome, '
           'r.nome AS responsavel_nome, r.usuario AS responsavel_usuario FROM chamados c '
           'LEFT JOIN clientes e ON e.id = c.empresa_id LEFT JOIN sensores s ON s.mac_id = c.sensor_mac '
           'LEFT JOIN clientes r ON r.id = c.responsavel_id')


def _enfeitar(row):
    c = dict(row)
    c['numero'] = c['id']
    c['empresa'] = c.pop('empresa_nome') or c.pop('empresa_usuario', None) or f"#{c['empresa_id']}"
    c.pop('empresa_usuario', None)
    c['responsavel'] = c.pop('responsavel_nome') or c.pop('responsavel_usuario', None)
    c.pop('responsavel_usuario', None)
    c['categoria_rotulo'], c['categoria_icone'] = CATEGORIAS.get(c['categoria'], (c['categoria'], 'fa-ellipsis'))
    c['prioridade_rotulo'] = PRIORIDADES.get(c['prioridade'], c['prioridade'])
    c['status_rotulo'] = STATUS.get(c['status'], c['status'])
    c['canal_rotulo'] = CANAIS.get(c['canal'], c['canal'])
    c['aberto'] = c['status'] in ABERTOS
    return c


def chamado(conn, numero):
    row = conn.execute(_CAMPOS + ' WHERE c.id = ?', (numero,)).fetchone()
    return _enfeitar(row) if row else None


def listar(conn, usuario, abertos=True, empresa_id=None):
    """Os chamados que o usuário pode ver, os atualizados por último primeiro."""
    filtros, params = [], []
    if eh_md(usuario):
        if empresa_id:
            filtros.append('c.empresa_id = ?'); params.append(empresa_id)
    else:
        filtros += ['c.empresa_id = ?', 'c.interno = 0']; params.append(acessos.empresa_de(usuario))
        if not ve_toda_empresa(conn, usuario):
            filtros.append('c.solicitante_id = ?'); params.append(usuario['id'])
    if abertos:
        filtros.append(f"c.status IN ({', '.join('?' for _ in ABERTOS)})"); params += ABERTOS
    sql = _CAMPOS + (' WHERE ' + ' AND '.join(filtros) if filtros else '') + ' ORDER BY c.atualizado_em DESC, c.id DESC'
    return [_enfeitar(r) for r in conn.execute(sql, params).fetchall()]


def contar_abertos(conn, usuario):
    chamados = listar(conn, usuario, abertos=True)
    return {'abertos': len(chamados),
            'com_voce': sum(1 for c in chamados if c['status'] in ('aguardando', 'resolvido')),
            'recentes': chamados[:3]}


def mensagens(conn, chamado_id, md):
    """Linha do tempo do chamado; quem não é da MD nunca recebe as notas internas."""
    tipos = ('abertura', 'resposta', 'nota', 'evento') if md else ('abertura', 'resposta', 'evento')
    rows = conn.execute(f"SELECT * FROM chamado_mensagens WHERE chamado_id = ? AND tipo IN ({', '.join('?' for _ in tipos)}) "
                        'ORDER BY criado_em, id', (chamado_id, *tipos)).fetchall()
    anexos = {}
    for a in conn.execute('SELECT id, mensagem_id, nome, tipo_mime, tamanho FROM chamado_anexos WHERE chamado_id = ? ORDER BY id',
                          (chamado_id,)).fetchall():
        anexos.setdefault(a['mensagem_id'], []).append(dict(a))
    saida = []
    for r in rows:
        m = dict(r)
        m['anexos'] = anexos.get(m['id'], [])
        saida.append(m)
    return saida


def anexo(conn, anexo_id):
    row = conn.execute('SELECT * FROM chamado_anexos WHERE id = ?', (anexo_id,)).fetchone()
    if not row:
        return None
    a = dict(row)
    a['conteudo'] = bytes(a['conteudo'])   # Postgres devolve memoryview
    return a


def locais_da_empresa(conn, empresa_id):
    return [dict(r) for r in conn.execute('SELECT mac_id, nome_local FROM sensores WHERE cliente_id = ? ORDER BY nome_local',
                                          (empresa_id,)).fetchall()]


def empresas_para_md(conn):
    """Empresas em que a MD pode abrir chamado; a da MD (chamado interno) vem primeiro."""
    rows = conn.execute("SELECT id, nome, usuario, empresa_master FROM clientes WHERE role = 'Cliente' AND COALESCE(ativo, 1) = 1").fetchall()
    empresas = [{'id': r['id'], 'nome': r['nome'] or r['usuario'], 'master': bool(r['empresa_master'])} for r in rows]
    return sorted(empresas, key=lambda e: (not e['master'], e['nome'].lower()))


# ------------------------------------------------------------------ escrita
def validar_arquivos(arquivos):
    """[(nome, bytes)] → [(nome limpo, tipo, bytes)]; recusa tipo não aceito, arquivo grande demais ou arquivos demais."""
    arquivos = [(n, b) for n, b in arquivos if n]
    if len(arquivos) > MAX_ARQUIVOS:
        raise Recusa(f'Envie no máximo {MAX_ARQUIVOS} arquivos por mensagem.')
    saida = []
    for nome, conteudo in arquivos:
        nome = os.path.basename(nome.replace('\\', '/')).strip()[:120] or 'arquivo'
        ext = nome.rsplit('.', 1)[-1].lower() if '.' in nome else ''
        if ext not in TIPOS_ANEXO:
            raise Recusa(f'O arquivo "{nome}" não é de um tipo aceito. Envie imagens, PDF, planilhas, Word ou texto.')
        if len(conteudo) > MAX_ARQUIVO:
            raise Recusa(f'O arquivo "{nome}" passa de {MAX_ARQUIVO // (1024 * 1024)} MB.')
        if not conteudo:
            raise Recusa(f'O arquivo "{nome}" está vazio.')
        saida.append((nome, TIPOS_ANEXO[ext], conteudo))
    return saida


def _inserir_id(conn, sql, params):
    return conn.execute(sql + ' RETURNING id', params).fetchone()['id']


def _mensagem(conn, chamado_id, autor, tipo, texto, arquivos=(), agora=None):
    agora = agora or time.time()
    lado = 'md' if eh_md(autor) else 'cliente'
    msg_id = _inserir_id(conn, 'INSERT INTO chamado_mensagens (chamado_id, autor_id, autor, lado, tipo, texto, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (chamado_id, autor['id'] if autor else None, nome_de(autor), lado, tipo, texto, agora))
    for nome, tipo_mime, conteudo in arquivos:
        conn.execute('INSERT INTO chamado_anexos (chamado_id, mensagem_id, nome, tipo_mime, tamanho, conteudo, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?)',
                     (chamado_id, msg_id, nome, tipo_mime, len(conteudo), database_binary(conteudo), agora))
    return msg_id


def database_binary(conteudo):
    """bytes no formato que o driver do banco grava como BLOB/BYTEA."""
    if database.DATABASE_URL:
        import psycopg2
        return psycopg2.Binary(conteudo)
    return conteudo


def evento(conn, chamado_id, autor, texto, agora=None):
    return _mensagem(conn, chamado_id, autor, 'evento', texto, agora=agora)


def _texto(valor, rotulo, maximo):
    valor = (valor or '').strip()
    if not valor:
        raise Recusa(f'Preencha {rotulo}.')
    if len(valor) > maximo:
        raise Recusa(f'{rotulo[0].upper() + rotulo[1:]} passa de {maximo} caracteres.')
    return valor


def abrir(conn, autor, dados, arquivos=(), agora=None):
    """Abre o chamado e devolve o número. A MD abre em nome de uma empresa (ou interno, na empresa da MD)."""
    agora = agora or time.time()
    categoria, impacto = dados.get('categoria'), dados.get('impacto')
    if categoria not in CATEGORIAS:
        raise Recusa('Escolha sobre o que é o chamado.')
    if impacto not in IMPACTOS:
        raise Recusa('Diga qual é o impacto agora.')
    titulo = _texto(dados.get('titulo'), 'o título', MAX_TITULO)
    texto = _texto(dados.get('descricao'), 'o que está acontecendo', MAX_TEXTO)
    arquivos = validar_arquivos(arquivos)
    prioridade = IMPACTOS[impacto]['prioridade']
    if eh_md(autor):
        try:
            empresa_id = int(dados.get('empresa_id') or 0)
        except (TypeError, ValueError):
            empresa_id = 0
        empresa = conn.execute("SELECT id, empresa_master FROM clientes WHERE id = ? AND role = 'Cliente'", (empresa_id,)).fetchone()
        if not empresa:
            raise Recusa('Escolha a empresa do chamado.')
        interno = 1 if empresa['empresa_master'] else 0
        canal = 'interno' if interno else dados.get('canal')
        if not interno and canal not in CANAIS_DA_MD + ('nexohub',):
            raise Recusa('Diga por onde o pedido chegou.')
        solicitante_id = None
        solicitante = (dados.get('solicitante') or '').strip()[:120] or (nome_de(autor) if interno else None)
        if not solicitante:
            raise Recusa('Diga quem pediu (nome do solicitante no cliente).')
        if dados.get('prioridade') in PRIORIDADES:
            prioridade = dados['prioridade']
    else:
        empresa_id = acessos.empresa_de(autor)
        if not empresa_id or not nivel(conn, autor):
            raise Recusa('O NexoDesk não está liberado para você.', 403)
        interno, canal, solicitante_id, solicitante = 0, 'nexohub', autor['id'], nome_de(autor)
    sensor_mac = (dados.get('sensor_mac') or '').strip() or None
    if sensor_mac and not conn.execute('SELECT 1 FROM sensores WHERE mac_id = ? AND cliente_id = ?', (sensor_mac, empresa_id)).fetchone():
        raise Recusa('Esse local não é da empresa do chamado.')
    numero = _inserir_id(conn,
        'INSERT INTO chamados (empresa_id, interno, solicitante_id, solicitante, aberto_por_id, canal, categoria, sensor_mac, '
        'impacto, prioridade, titulo, status, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (empresa_id, interno, solicitante_id, solicitante, autor['id'], canal, categoria, sensor_mac, impacto, prioridade,
         titulo, 'novo', agora, agora))
    _mensagem(conn, numero, autor, 'abertura', texto, arquivos, agora)
    return numero


def responder(conn, autor, chamado, texto, arquivos=(), nota=False, agora=None):
    """Resposta (o cliente vê) ou nota interna (só a MD). Devolve o novo status."""
    agora = agora or time.time()
    md = eh_md(autor)
    if nota and not md:
        raise Recusa('Só a MD escreve notas internas.', 403)
    if chamado['status'] in ENCERRADOS:
        raise Recusa('Este chamado está encerrado. Abra um novo chamado se precisar.')
    texto = (texto or '').strip()
    arquivos = validar_arquivos(arquivos)
    if not texto and not arquivos:
        raise Recusa('Escreva a mensagem ou anexe um arquivo.')
    if len(texto) > MAX_TEXTO:
        raise Recusa(f'A mensagem passa de {MAX_TEXTO} caracteres.')
    _mensagem(conn, chamado['id'], autor, 'nota' if nota else 'resposta', texto, arquivos, agora + 0.000001)
    status, responsavel = chamado['status'], chamado['responsavel_id']
    if not nota:
        if md and responsavel is None:
            responsavel = autor['id']
            evento(conn, chamado['id'], autor, f'{nome_de(autor)} assumiu o chamado', agora)
        if md and status == 'novo':
            status = 'atendimento'
        elif not md and status == 'aguardando':
            status = 'atendimento'
            evento(conn, chamado['id'], autor, 'O cliente respondeu: em atendimento de novo', agora + 0.000002)
        elif not md and status == 'resolvido':
            status = 'atendimento'
            evento(conn, chamado['id'], autor, 'O cliente disse que ainda não resolveu: em atendimento de novo', agora + 0.000002)
    conn.execute('UPDATE chamados SET status = ?, responsavel_id = ?, atualizado_em = ? WHERE id = ?',
                 (status, responsavel, agora, chamado['id']))
    return status
