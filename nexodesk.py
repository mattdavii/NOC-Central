"""NexoDesk: chamados e suporte da MD (regras, quem vê o quê e anexos).

Escopo: docs/nexodesk/ESCOPO.md. Horários em epoch (segundos, UTC); o número do chamado é o id da tabela.
"""
import html
import json
import os
import time
from datetime import datetime, timedelta

import acessos
import database
import sla
from hub import FUSO_BR

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
CANAIS = {'nexohub': 'NexoHub', 'whatsapp': 'WhatsApp', 'telefone': 'Telefone', 'presencial': 'Presencial', 'interno': 'Interno',
          'nexopulse': 'Detectado pelo NexoPulse'}
CANAIS_DA_MD = ('whatsapp', 'telefone', 'presencial', 'nexopulse')   # a MD registrando um pedido que chegou por fora

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


# ------------------------------------------------------------------ prazo do contrato (SLA)
PAUSAM = ('aguardando', 'resolvido', 'fechado')   # o relógio para enquanto a MD espera o cliente (ou depois de resolvido)
DIAS_SEMANA = ('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')


def sla_da_empresa(conn, empresa_id):
    """Configuração de SLA do contrato da empresa (a tabela padrão se não houver uma própria)."""
    row = conn.execute('SELECT sla_config FROM clientes WHERE id = ?', (empresa_id,)).fetchone()
    if row and row['sla_config']:
        try:
            return {**sla.normalizar(json.loads(row['sla_config'])), 'proprio': True}
        except (ValueError, TypeError):
            pass
    return {**json.loads(json.dumps(sla.PADRAO)), 'proprio': False}


def definir_sla(conn, empresa_id, config):
    """Grava o SLA do contrato (None volta para a tabela padrão). Chamados já abertos não mudam."""
    valor = None if config is None else json.dumps(sla.normalizar(config))
    conn.execute('UPDATE clientes SET sla_config = ? WHERE id = ?', (valor, empresa_id))


def quando_txt(ts, agora=None):
    """Data e hora em Brasília, curta: "hoje 13:30", "amanhã 10:00", "seg 09/11 10:00"."""
    agora = agora or time.time()
    d, hoje = datetime.fromtimestamp(ts, FUSO_BR), datetime.fromtimestamp(agora, FUSO_BR).date()
    hora = d.strftime('%H:%M')
    if d.date() == hoje:
        return f'hoje {hora}'
    if d.date() == hoje + timedelta(days=1):
        return f'amanhã {hora}'
    if d.date() == hoje - timedelta(days=1):
        return f'ontem {hora}'
    return f'{DIAS_SEMANA[d.weekday()]} {d.strftime("%d/%m")} {hora}'


def duracao_txt(segundos):
    segundos = max(0, int(segundos))
    h, m = divmod(segundos // 60, 60)
    if h and m:
        return f'{h} h {m:02d}'
    return f'{h} h' if h else f'{m} min'


def _pausas(c):
    try:
        lista = [list(p) for p in json.loads(c.get('pausas') or '[]')]
    except (TypeError, ValueError):
        lista = []
    if c.get('pausado_desde'):
        lista.append([c['pausado_desde'], None])
    return lista


def _relogio(c, meta_h, fim, agora, horario, pausas):
    meta_s = meta_h * 3600
    usado = sla.consumido(c['criado_em'], fim or agora, horario, pausas)
    r = {'meta_h': meta_h, 'meta_txt': duracao_txt(meta_s), 'usado_s': usado, 'pct': min(100, round(100 * usado / meta_s)) if meta_s else 100}
    if fim:
        r.update(estado='cumprido' if usado <= meta_s else 'estourou', em_txt=duracao_txt(usado), fim_txt=quando_txt(fim, agora))
        return r
    vence = sla.vencimento(c['criado_em'], meta_s, horario, pausas)
    falta = meta_s - usado
    r.update(vence_em=vence, vence_txt=quando_txt(vence, agora) if vence else None, falta_txt=duracao_txt(abs(falta)))
    if c.get('pausado_desde'):
        r['estado'] = 'pausado'
    elif falta <= 0:
        r['estado'] = 'estourado'
    elif usado >= sla.PERTO * meta_s:
        r['estado'] = 'perto'
    else:
        r['estado'] = 'ok'
    return r


def prazos(c, agora=None):
    """Os relógios do chamado (1ª resposta e solução) e o próximo a vencer; None sem SLA (ou cancelado)."""
    agora = agora or time.time()
    if c['status'] == 'cancelado':
        return None
    if c.get('interno') or not c.get('sla'):
        if not c.get('prazo_manual'):
            return None
        fim = c.get('resolvido_em') if c['status'] in ('resolvido', 'fechado') else None
        total = max(1.0, c['prazo_manual'] - c['criado_em'])
        usado = (fim or agora) - c['criado_em']
        r = {'manual': True, 'vence_em': c['prazo_manual'], 'vence_txt': quando_txt(c['prazo_manual'], agora),
             'pct': min(100, round(100 * usado / total)), 'falta_txt': duracao_txt(abs(c['prazo_manual'] - agora))}
        if fim:
            r['estado'] = 'cumprido' if fim <= c['prazo_manual'] else 'estourou'
        else:
            r['estado'] = 'estourado' if agora >= c['prazo_manual'] else 'perto' if usado >= sla.PERTO * total else 'ok'
        return {'manual': True, 'solucao': r, 'proximo': r if not fim else None, 'horario': None}
    cfg = json.loads(c['sla'])
    horario = cfg['horario']
    resp_h, sol_h = cfg['prazos'].get(c['prioridade'], sla.PADRAO['prazos'][c['prioridade']])
    pausas = _pausas(c)
    resposta = _relogio(c, resp_h, c.get('primeira_resposta_em'), agora, horario, pausas)
    fim_sol = c.get('resolvido_em') if c['status'] in ('resolvido', 'fechado') else None
    solucao = _relogio(c, sol_h, fim_sol, agora, horario, pausas)
    proximo = None
    if c['status'] in ABERTOS and not fim_sol:
        proximo = dict(solucao if c.get('primeira_resposta_em') else resposta, qual='solucao' if c.get('primeira_resposta_em') else 'resposta')
    return {'manual': False, 'horario': horario, 'horario_txt': sla.HORARIOS[horario], 'resposta': resposta,
            'solucao': solucao, 'proximo': proximo}


def _ajustar_pausa(conn, c, novo, agora):
    """Abre ou fecha a pausa do relógio quando o status entra ou sai de "esperando o cliente"."""
    antigo = c['status']
    pausas = [list(p) for p in json.loads(c.get('pausas') or '[]')]
    desde = c.get('pausado_desde')
    if novo in PAUSAM and antigo not in PAUSAM and not desde:
        desde = agora
    elif novo not in PAUSAM and desde:
        pausas.append([desde, agora])
        desde = None
    else:
        return
    conn.execute('UPDATE chamados SET pausas = ?, pausado_desde = ? WHERE id = ?', (json.dumps(pausas), desde, c['id']))
    c['pausas'], c['pausado_desde'] = json.dumps(pausas), desde


def verificar_prazos(conn, agora=None):
    """Mensagens para o Telegram da MD: cada relógio avisa uma vez ao chegar a 80% e uma vez ao estourar."""
    agora = agora or time.time()
    avisos = []
    rows = conn.execute(_CAMPOS + f" WHERE c.status IN ({', '.join('?' for _ in ABERTOS)})", ABERTOS).fetchall()
    for row in rows:
        c = _enfeitar(row)
        p = prazos(c, agora)
        if not p:
            continue
        ja = set(json.loads(c.get('sla_avisos') or '[]'))
        novos = []
        for qual, rotulo in (('resposta', '1ª resposta'), ('solucao', 'solução')):
            r = p.get(qual)
            if not r or r['estado'] not in ('perto', 'estourado'):
                continue
            if r['estado'] == 'estourado' and f'{qual}:estourado' not in ja:
                marco = 'estourado'
            elif r['estado'] == 'perto' and f'{qual}:perto' not in ja:
                marco = 'perto'
            else:
                continue
            chaves = {f'{qual}:perto', f'{qual}:estourado'} if marco == 'estourado' else {f'{qual}:perto'}
            ja |= chaves
            novos += sorted(chaves)
            if marco == 'perto':
                avisos.append(f"⏳ <b>Chamado #{c['numero']}</b>: {rotulo} perto de vencer (vence {r['vence_txt']})\n"
                              f"🏢 {html.escape(c['empresa'])} · {c['prioridade_rotulo']} · {html.escape(c['responsavel'] or 'sem responsável')}\n📝 {html.escape(c['titulo'])}")
            else:
                avisos.append(f"🚨 <b>Chamado #{c['numero']}</b>: {rotulo} com prazo estourado (meta {r['meta_txt']})\n"
                              f"🏢 {html.escape(c['empresa'])} · {c['prioridade_rotulo']} · {html.escape(c['responsavel'] or 'sem responsável')}\n📝 {html.escape(c['titulo'])}")
        if novos:
            conn.execute('UPDATE chamados SET sla_avisos = ? WHERE id = ?', (json.dumps(sorted(ja)), c['id']))
    return avisos


def no_prazo_do_mes(conn, agora=None):
    """% dos chamados resolvidos no mês (Brasília) dentro do prazo de solução; None se nenhum."""
    agora = agora or time.time()
    d = datetime.fromtimestamp(agora, FUSO_BR)
    inicio = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = conn.execute(_CAMPOS + ' WHERE c.resolvido_em >= ? AND c.interno = 0', (inicio,)).fetchall()
    relogios = [p['solucao'] for p in (prazos(dict(_enfeitar(r)), agora) for r in rows) if p and p.get('solucao')]
    finais = [r for r in relogios if r['estado'] in ('cumprido', 'estourou')]
    if not finais:
        return None
    return {'pct': round(100 * sum(1 for r in finais if r['estado'] == 'cumprido') / len(finais)), 'total': len(finais),
            'mes': ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro',
                    'novembro', 'dezembro')[d.month - 1]}



def atendimento_do_periodo(conn, empresa_id, inicio, fim, agora=None, limite_lista=15):
    """O atendimento da MD a uma empresa num período, para o NexoReport (fica gravado no fechamento do mês).

    Conta chamados do cliente (não os internos da MD). Os prazos são os do contrato gravado em cada chamado,
    contados no horário dele e sem o tempo em que a MD esperava o cliente.
    """
    agora = min(agora or time.time(), fim)
    rows = [_enfeitar(r) for r in conn.execute(
        _CAMPOS + ' WHERE c.empresa_id = ? AND c.interno = 0 AND (c.criado_em < ?) AND '
                  '(c.criado_em >= ? OR c.resolvido_em >= ? OR c.fechado_em >= ? OR c.status IN (?, ?, ?, ?)) ORDER BY c.id',
        (empresa_id, fim, inicio, inicio, inicio, *ABERTOS)).fetchall()]
    for c in rows:
        c['prazo'] = prazos(c, agora)

    def no_mes(ts):
        return ts is not None and inicio <= ts < fim

    abertos_mes = [c for c in rows if no_mes(c['criado_em'])]
    cancelados = [c for c in abertos_mes if c['status'] == 'cancelado']
    validos = [c for c in abertos_mes if c['status'] != 'cancelado']
    resolvidos = [c for c in rows if c['status'] in ('resolvido', 'fechado') and no_mes(c.get('resolvido_em'))]
    respondidos = [c for c in rows if no_mes(c.get('primeira_resposta_em')) and c['status'] != 'cancelado']

    def relogios(lista, qual):
        return [c['prazo'][qual] for c in lista
                if c['prazo'] and not c['prazo'].get('manual') and c['prazo'].get(qual, {}).get('estado') in ('cumprido', 'estourou')]

    def placar(rel):
        if not rel:
            return None
        ok = sum(1 for r in rel if r['estado'] == 'cumprido')
        return {'total': len(rel), 'no_prazo': ok, 'pct': round(100 * ok / len(rel)),
                'media_s': int(sum(r['usado_s'] for r in rel) / len(rel))}

    notas = [c['avaliacao'] for c in rows if c.get('avaliacao') and no_mes(c.get('fechado_em'))]
    # Em aberto no fim do período: ainda sem solução naquele momento (o fechamento roda logo depois do fim do mês).
    pendentes = [c for c in rows if c['status'] != 'cancelado'
                 and not (c['status'] in ('resolvido', 'fechado') and c.get('resolvido_em') and c['resolvido_em'] < fim)]
    por_categoria = {}
    for c in validos:
        item = por_categoria.setdefault(c['categoria'], {'rotulo': c['categoria_rotulo'], 'chamados': 0})
        item['chamados'] += 1
    por_prioridade = {k: sum(1 for c in validos if c['prioridade'] == k) for k in PRIORIDADES}

    def linha(c):
        sol = (c['prazo'] or {}).get('solucao') or {}
        return {'numero': c['numero'], 'criado_em': c['criado_em'], 'titulo': c['titulo'], 'local': c.get('local_nome'),
                'categoria': c['categoria_rotulo'], 'prioridade': c['prioridade'], 'prioridade_rotulo': c['prioridade_rotulo'],
                'status': c['status'], 'status_rotulo': c['status_rotulo'], 'canal': c['canal'],
                'prazo': sol.get('estado'), 'da_queda': bool(c.get('diagnostico_id')), 'avaliacao': c.get('avaliacao')}

    return {
        'abertos': len(validos), 'cancelados': len(cancelados), 'resolvidos': len(resolvidos),
        'pendentes': len(pendentes),
        'de_quedas': sum(1 for c in validos if c.get('diagnostico_id')),
        'resposta': placar(relogios(respondidos, 'resposta')),
        'solucao': placar(relogios(resolvidos, 'solucao')),
        'avaliacao': {'media': round(sum(notas) / len(notas), 1), 'total': len(notas),
                      'distribuicao': [notas.count(n) for n in range(1, 6)]} if notas else None,
        'por_categoria': sorted(por_categoria.values(), key=lambda x: -x['chamados']),
        'por_prioridade': por_prioridade,
        'chamados': [linha(c) for c in validos[-limite_lista:]],
        'chamados_omitidos': max(0, len(validos) - limite_lista),
        'pendentes_lista': [linha(c) for c in pendentes if not no_mes(c['criado_em'])][:limite_lista],
    }

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
    c['prazo'] = prazos(c)
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
    diagnostico_id = None
    if dados.get('diagnostico_id'):
        diag = diagnostico_da_empresa(conn, dados.get('diagnostico_id'), empresa_id)
        if not diag:
            raise Recusa('Essa queda não é de um local da empresa do chamado.')
        diagnostico_id, sensor_mac = diag['id'], sensor_mac or diag['sensor_mac']
    contrato = None
    if not interno:
        cfg = sla_da_empresa(conn, empresa_id)
        contrato = json.dumps({'horario': cfg['horario'], 'prazos': cfg['prazos']})   # o SLA fica gravado no chamado
    numero = _inserir_id(conn,
        'INSERT INTO chamados (empresa_id, interno, solicitante_id, solicitante, aberto_por_id, canal, categoria, sensor_mac, '
        'impacto, prioridade, titulo, status, criado_em, atualizado_em, sla, pausas, diagnostico_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (empresa_id, interno, solicitante_id, solicitante, autor['id'], canal, categoria, sensor_mac, impacto, prioridade,
         titulo, 'novo', agora, agora, contrato, '[]', diagnostico_id))
    _mensagem(conn, numero, autor, 'abertura', texto, arquivos, agora)
    return numero


# Para onde a MD pode levar o chamado (o "fechado" vem da confirmação do cliente, na entrega 4).
STATUS_DA_MD = ('atendimento', 'aguardando', 'resolvido', 'cancelado')
TEXTO_DO_STATUS = {
    'atendimento': 'Em atendimento',
    'aguardando': 'A MD pediu uma informação: aguardando o cliente',
    'resolvido': 'Marcado como resolvido: aguardando a confirmação do cliente',
    'cancelado': 'Chamado cancelado',
    'fechado': 'Chamado fechado',
}
ORDEM_PRIORIDADE = {'critica': 0, 'alta': 1, 'media': 2, 'baixa': 3}
ORDEM_STATUS = {'novo': 0, 'atendimento': 1, 'aguardando': 2, 'resolvido': 3, 'fechado': 4, 'cancelado': 5}


def equipe_md(conn):
    rows = conn.execute(f"SELECT id, nome, usuario FROM clientes WHERE role IN ({', '.join('?' for _ in acessos.ROLES_MASTER)}) "
                        'AND COALESCE(ativo, 1) = 1', acessos.ROLES_MASTER).fetchall()
    return sorted(({'id': r['id'], 'nome': r['nome'] or r['usuario']} for r in rows), key=lambda u: u['nome'].lower())


def fila(conn, filtros=None):
    """Fila da MD: chamados filtrados, na ordem de atendimento, e os contadores dos abertos."""
    f = filtros or {}
    todos = [_enfeitar(r) for r in conn.execute(_CAMPOS).fetchall()]
    abertos = [c for c in todos if c['status'] in ABERTOS]
    proximos = [c['prazo']['proximo'] for c in abertos if c['prazo'] and c['prazo'].get('proximo')]
    contadores = {
        'perto': sum(1 for p in proximos if p['estado'] == 'perto'),
        'estourados': sum(1 for p in proximos if p['estado'] == 'estourado'),
        'novos': sum(1 for c in abertos if c['status'] == 'novo' and not c['responsavel_id']),
        'atendimento': sum(1 for c in abertos if c['status'] == 'atendimento'),
        'aguardando': sum(1 for c in abertos if c['status'] == 'aguardando'),
        'resolvidos': sum(1 for c in abertos if c['status'] == 'resolvido'),
        'meus': sum(1 for c in abertos if f.get('eu') and c['responsavel_id'] == f['eu'] and c['status'] != 'resolvido'),
        'abertos': len(abertos), 'empresas': len({c['empresa_id'] for c in abertos}),
    }
    status = f.get('status') or 'abertos'
    if status == 'abertos':
        lista = abertos
    elif status == 'todos':
        lista = todos
    else:
        lista = [c for c in todos if c['status'] == status]
    if f.get('empresa_id'):
        lista = [c for c in lista if c['empresa_id'] == f['empresa_id']]
    resp = f.get('responsavel')
    if resp == 'ninguem':
        lista = [c for c in lista if not c['responsavel_id']]
    elif resp:
        lista = [c for c in lista if str(c['responsavel_id']) == str(resp)]
    if f.get('prazo') in ('perto', 'estourado'):
        lista = [c for c in lista if c['prazo'] and (c['prazo'].get('proximo') or {}).get('estado') == f['prazo']]
    if f.get('categoria') in CATEGORIAS:
        lista = [c for c in lista if c['categoria'] == f['categoria']]
    busca = (f.get('busca') or '').strip().lower().lstrip('#')
    if busca:
        lista = [c for c in lista if busca == str(c['numero']) or any(busca in (c.get(k) or '').lower()
                 for k in ('titulo', 'solicitante', 'empresa', 'local_nome'))]
    # O prazo que vence primeiro no topo; pausados (esperando o cliente) e sem prazo depois, por prioridade.
    def ordem(c):
        p = (c['prazo'] or {}).get('proximo')
        correndo = bool(p and p.get('vence_em') and p['estado'] != 'pausado')
        return (c['status'] not in ABERTOS, not correndo, p['vence_em'] if correndo else 0,
                ORDEM_PRIORIDADE.get(c['prioridade'], 9), ORDEM_STATUS.get(c['status'], 9), c['criado_em'])
    lista.sort(key=ordem)
    return lista, contadores


def _mudar_status(conn, autor, chamado, novo, agora, texto=None):
    """Grava a troca de status com o evento no histórico; devolve o novo status."""
    if novo == chamado['status']:
        return novo
    campos = {'status': novo, 'atualizado_em': agora}
    if novo == 'resolvido':
        campos['resolvido_em'] = agora
    elif novo != 'fechado' and chamado.get('resolvido_em'):
        campos['resolvido_em'] = None
    if novo in ENCERRADOS:
        campos['fechado_em'] = agora
    elif chamado['status'] in ENCERRADOS:
        campos['fechado_em'] = None
    _ajustar_pausa(conn, chamado, novo, agora)
    if texto is None:
        texto = TEXTO_DO_STATUS[novo]
        if novo == 'atendimento' and chamado['status'] in ('aguardando', 'resolvido', 'cancelado', 'fechado'):
            texto = 'Reaberto pela MD: em atendimento'
    evento(conn, chamado['id'], autor, texto, agora)
    conn.execute(f"UPDATE chamados SET {', '.join(k + ' = ?' for k in campos)} WHERE id = ?", (*campos.values(), chamado['id']))
    chamado.update(campos)
    return novo


def atualizar(conn, autor, chamado, mudancas, agora=None):
    """A MD muda responsável, prioridade, categoria ou status; cada mudança entra no histórico."""
    if not eh_md(autor):
        raise Recusa('Só a MD muda os dados do chamado.', 403)
    if chamado['status'] in ENCERRADOS and mudancas.get('status') != 'atendimento':
        raise Recusa('Este chamado está encerrado.')
    agora = agora or time.time()
    passo = 0.000001
    if 'responsavel_id' in mudancas:
        novo = mudancas['responsavel_id'] or None
        if novo is not None:
            try:
                novo = int(novo)
            except (TypeError, ValueError):
                raise Recusa('Responsável inválido.')
            pessoa = next((u for u in equipe_md(conn) if u['id'] == novo), None)
            if not pessoa:
                raise Recusa('O responsável precisa ser da equipe da MD.')
        if novo != chamado['responsavel_id']:
            texto = (f"{nome_de(autor)} assumiu o chamado" if novo == autor['id']
                     else f"Responsável: {pessoa['nome']}" if novo else 'Chamado sem responsável')
            evento(conn, chamado['id'], autor, texto, agora)
            conn.execute('UPDATE chamados SET responsavel_id = ?, atualizado_em = ? WHERE id = ?', (novo, agora, chamado['id']))
            chamado['responsavel_id'] = novo
            if novo and chamado['status'] == 'novo':
                _mudar_status(conn, autor, chamado, 'atendimento', agora + passo)
    for campo, opcoes, rotulo in (('prioridade', PRIORIDADES, 'Prioridade'), ('categoria', {k: v[0] for k, v in CATEGORIAS.items()}, 'Categoria')):
        if campo in mudancas and mudancas[campo] != chamado[campo]:
            if mudancas[campo] not in opcoes:
                raise Recusa(f'{rotulo} inválida.')
            agora += passo
            evento(conn, chamado['id'], autor, f'{rotulo}: {opcoes.get(chamado[campo], chamado[campo])} → {opcoes[mudancas[campo]]}', agora)
            conn.execute(f'UPDATE chamados SET {campo} = ?, atualizado_em = ? WHERE id = ?', (mudancas[campo], agora, chamado['id']))
            chamado[campo] = mudancas[campo]
    if 'prazo_manual' in mudancas:
        if not chamado['interno']:
            raise Recusa('Chamado de cliente segue o prazo do contrato.')
        valor = mudancas['prazo_manual']
        if valor:
            try:
                prazo = datetime.strptime(str(valor)[:16], '%Y-%m-%dT%H:%M').replace(tzinfo=FUSO_BR).timestamp()
            except ValueError:
                raise Recusa('Data do prazo inválida.')
            texto = f'Prazo: {quando_txt(prazo, agora)}'
        else:
            prazo, texto = None, 'Prazo removido'
        agora += passo
        evento(conn, chamado['id'], autor, texto, agora)
        conn.execute('UPDATE chamados SET prazo_manual = ?, atualizado_em = ? WHERE id = ?', (prazo, agora, chamado['id']))
        chamado['prazo_manual'] = prazo
    if mudancas.get('status'):
        novo = mudancas['status']
        if novo not in STATUS_DA_MD:
            raise Recusa('Status inválido.')
        if novo in ('aguardando', 'resolvido'):
            raise Recusa('Escreva a mensagem para o cliente junto (pedido de informação ou a solução).')
        _mudar_status(conn, autor, chamado, novo, agora + 2 * passo)
    return chamado


def responder(conn, autor, chamado, texto, arquivos=(), nota=False, depois=None, agora=None):
    """Resposta (o cliente vê) ou nota interna (só a MD). A MD pode, junto da resposta, pedir uma informação
    (depois='aguardando') ou dar a solução (depois='resolvido'). Devolve o novo status."""
    agora = agora or time.time()
    md = eh_md(autor)
    if nota and not md:
        raise Recusa('Só a MD escreve notas internas.', 403)
    if chamado['status'] in ENCERRADOS and (md or nota or not pode_reabrir(chamado, agora)):
        if md and chamado['status'] == 'fechado':
            raise Recusa('Este chamado está fechado. Use "Reabrir" para voltar a atendê-lo.')
        raise Recusa(f'Este chamado está encerrado. Abra um novo chamado se precisar.' if chamado['status'] == 'cancelado'
                     else f'Este chamado fechou há mais de {REABRIR_DIAS} dias. Abra um novo chamado se precisar.')
    if depois and (not md or depois not in ('aguardando', 'resolvido')):
        raise Recusa('Ação inválida.')
    if depois and nota:
        raise Recusa('Pedido de informação e solução vão para o cliente: use "Responder ao cliente".')
    texto = (texto or '').strip()
    arquivos = validar_arquivos(arquivos)
    if not texto and not arquivos:
        raise Recusa('Escreva a mensagem ou anexe um arquivo.')
    if depois and not texto:
        raise Recusa('Escreva a pergunta para o cliente.' if depois == 'aguardando' else 'Descreva a solução para o cliente.')
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
        elif not md and status == 'fechado':
            status = 'atendimento'
            evento(conn, chamado['id'], autor, f'Reaberto por {nome_de(autor)}: em atendimento de novo', agora + 0.000002)
    if md and not nota and not chamado.get('primeira_resposta_em'):
        conn.execute('UPDATE chamados SET primeira_resposta_em = ? WHERE id = ?', (agora, chamado['id']))
        chamado['primeira_resposta_em'] = agora
    if status != chamado['status']:
        _ajustar_pausa(conn, chamado, status, agora)
    reaberto = chamado['status'] in ('resolvido', 'fechado') and status == 'atendimento'
    conn.execute('UPDATE chamados SET status = ?, responsavel_id = ?, atualizado_em = ?' +
                 (', resolvido_em = NULL, fechado_em = NULL' if reaberto else '') + ' WHERE id = ?',
                 (status, responsavel, agora, chamado['id']))
    chamado.update(status=status, responsavel_id=responsavel)
    if depois:
        status = _mudar_status(conn, autor, chamado, depois, agora + 0.000003)
    return status


# ------------------------------------------------------------------ fechamento (entrega 4)
DIAS_PARA_FECHAR = 3     # dias úteis em "resolvido" sem resposta do cliente
REABRIR_DIAS = 7         # o cliente reabre um chamado fechado até 7 dias depois
SISTEMA = {'id': None, 'nome': 'NexoDesk', 'usuario': 'nexodesk', 'role': 'Administrador Master'}


def prazo_fechamento(c):
    """Quando o chamado resolvido fecha sozinho: no mesmo horário, 3 dias úteis depois (sem fins de semana e feriados)."""
    if c['status'] != 'resolvido' or not c.get('resolvido_em'):
        return None
    d = datetime.fromtimestamp(c['resolvido_em'], FUSO_BR)
    contados = 0
    while contados < DIAS_PARA_FECHAR:
        d += timedelta(days=1)
        if sla.dia_util(d.date()):
            contados += 1
    return d.timestamp()


def pode_reabrir(c, agora=None):
    agora = agora or time.time()
    return c['status'] == 'fechado' and bool(c.get('fechado_em')) and agora - c['fechado_em'] <= REABRIR_DIAS * 86400


def confirmar(conn, autor, c, nota=None, comentario=None, agora=None):
    """O cliente confirma a solução (com nota de 1 a 5, opcional) e o chamado fecha; a MD pode fechar sem nota."""
    agora = agora or time.time()
    if c['status'] != 'resolvido':
        raise Recusa('Só um chamado resolvido pode ser fechado.')
    md = eh_md(autor)
    if md and nota:
        raise Recusa('A nota é do cliente.')
    if nota not in (None, ''):
        try:
            nota = int(nota)
        except (TypeError, ValueError):
            raise Recusa('A nota vai de 1 a 5.')
        if not 1 <= nota <= 5:
            raise Recusa('A nota vai de 1 a 5.')
    else:
        nota = None
    comentario = (comentario or '').strip()[:1000] or None
    texto = (f'Fechado pela MD ({nome_de(autor)})' if md else
             f'{nome_de(autor)} confirmou a solução' + (f' · nota {nota}/5' if nota else ''))
    _mudar_status(conn, autor, c, 'fechado', agora, texto)
    if not md:
        conn.execute('UPDATE chamados SET avaliacao = ?, avaliacao_comentario = ? WHERE id = ?', (nota, comentario, c['id']))
        c.update(avaliacao=nota, avaliacao_comentario=comentario)
    return c


def cancelar_pelo_cliente(conn, autor, c, agora=None):
    """O admin da empresa cancela um chamado que ainda não foi resolvido."""
    if eh_md(autor) or nivel(conn, autor) != 'admin':
        raise Recusa('Só o administrador da empresa cancela chamados.', 403)
    if c['status'] not in ('novo', 'atendimento', 'aguardando'):
        raise Recusa('Este chamado não pode ser cancelado agora.')
    agora = agora or time.time()
    _mudar_status(conn, autor, c, 'cancelado', agora, f'Cancelado por {nome_de(autor)}')
    return c


def fechar_automaticos(conn, agora=None):
    """Fecha os chamados resolvidos há 3 dias úteis sem resposta do cliente; devolve os fechados."""
    agora = agora or time.time()
    fechados = []
    for row in conn.execute(_CAMPOS + " WHERE c.status = 'resolvido'").fetchall():
        c = _enfeitar(row)
        prazo = prazo_fechamento(c)
        if prazo and agora >= prazo:
            _mudar_status(conn, SISTEMA, c, 'fechado', agora,
                          f'Fechado automaticamente: {DIAS_PARA_FECHAR} dias úteis sem resposta depois de resolvido')
            fechados.append(c)
    return fechados


def aviso_cliente(c, o_que, texto=None):
    """Mensagem para o grupo de Telegram do cliente (nunca de chamado interno nem de nota interna)."""
    if c.get('interno'):
        return None
    titulos = {'resposta': '💬 A MD respondeu', 'aguardando': '❓ A MD precisa de uma informação',
               'resolvido': '✅ A MD marcou como resolvido', 'fechado': '🔒 Chamado fechado'}
    trecho = ''
    if texto:
        trecho = texto.strip().replace('\n', ' ')
        trecho = '\n' + html.escape(trecho[:280] + ('…' if len(trecho) > 280 else ''))
    fim = {'aguardando': '\nResponda no NexoHub para o atendimento continuar.',
           'resolvido': '\nConfirme no NexoHub se resolveu. Sem resposta, ele fecha sozinho em 3 dias úteis.'}.get(o_que, '')
    return f"{titulos[o_que]}: <b>chamado #{c['numero']}</b>\n📝 {html.escape(c['titulo'])}{trecho}{fim}"


# ------------------------------------------------------------------ chamado a partir de uma queda do NexoPulse
NOMES_EVIDENCIAS = {'perda_pct': 'perda', 'latencia_ms': 'latência (ms)', 'jitter_ms': 'oscilação (ms)'}


def diagnostico_da_empresa(conn, diagnostico_id, empresa_id=None):
    """A queda/diagnóstico com o local dela; None se não existir ou (com empresa) se o local for de outra empresa."""
    try:
        diagnostico_id = int(diagnostico_id)
    except (TypeError, ValueError):
        return None
    row = conn.execute('SELECT d.*, s.nome_local, s.cliente_id FROM diagnosticos d JOIN sensores s ON s.mac_id = d.sensor_mac '
                       'WHERE d.id = ?', (diagnostico_id,)).fetchone()
    if not row or (empresa_id is not None and row['cliente_id'] != empresa_id):
        return None
    return dict(row)


def chamados_das_quedas(conn, ids):
    """{id do diagnóstico: chamado mais recente ligado a ele} para mostrar o vínculo na linha do tempo."""
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    rows = conn.execute(f"SELECT id, diagnostico_id, status FROM chamados WHERE diagnostico_id IN ({', '.join('?' for _ in ids)}) "
                        'ORDER BY id', ids).fetchall()
    return {r['diagnostico_id']: {'numero': r['id'], 'status': r['status'], 'status_rotulo': STATUS.get(r['status'], r['status']),
                                  'aberto': r['status'] in ABERTOS} for r in rows}


def rascunho_da_queda(diag, agora=None):
    """Campos do formulário já preenchidos com o que o NexoPulse viu (a pessoa revisa antes de abrir)."""
    agora = agora or time.time()
    local = diag.get('nome_local') or diag['sensor_mac']
    em_andamento = diag.get('estado') == 'ativo' or not diag.get('fim')
    queda = diag.get('chave') == 'sem_comunicacao'
    linhas = [f"Detectado pelo NexoPulse em {local}.",
              f"Início: {quando_txt(diag['inicio'], agora)}" + ('' if em_andamento else
                                                                f" · fim: {quando_txt(diag['fim'], agora)} · duração: {duracao_txt(diag['fim'] - diag['inicio'])}"),
              f"Situação: {'em andamento' if em_andamento else 'normalizado'}."]
    if diag.get('causa'):
        linhas.append(f"Diagnóstico: {diag['causa']}" + (f" (confiança {diag['confianca']})" if diag.get('confianca') else ''))
    if diag.get('mensagem'):
        linhas.append(diag['mensagem'])
    try:
        evidencias = json.loads(diag.get('evidencias') or '{}') if isinstance(diag.get('evidencias'), str) else (diag.get('evidencias') or {})
    except ValueError:
        evidencias = {}
    ev = [f"- {NOMES_EVIDENCIAS.get(k, k.replace('_', ' '))}: {', '.join(map(str, v)) if isinstance(v, list) else v}"
          for k, v in evidencias.items() if v not in (None, '', [])]
    if ev:
        linhas += ['', 'Evidências:'] + ev[:12]
    if diag.get('acao'):
        linhas += ['', f"Ação sugerida pelo diagnóstico: {diag['acao']}"]
    titulo = (f"Sem conexão em {local}" if queda else f"{diag.get('causa') or 'Problema na rede'} em {local}")[:MAX_TITULO]
    return {'categoria': 'rede', 'impacto': 'parou' if queda and em_andamento else 'atrapalha', 'titulo': titulo,
            'descricao': '\n'.join(linhas)[:MAX_TEXTO], 'sensor_mac': diag['sensor_mac'], 'diagnostico_id': diag['id'],
            'canal': 'nexopulse', 'solicitante': 'Monitoramento NexoPulse'}
