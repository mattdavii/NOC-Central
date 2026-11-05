"""Família Nexo: contratos (empresa → aplicativos) e níveis de acesso (usuário → aplicativo).

Acesso efetivo de um usuário a um aplicativo = o aplicativo está contratado pela empresa
∩ o usuário tem nível nele. Ausência de registro significa "padrão do perfil" (é o que
mantém quem já usa a Central com o mesmo acesso de antes); registro explícito é decisão
tomada — inclusive "nenhum" (sem acesso) e contrato desligado.
"""
import json

APLICATIVOS = {'pulse': 'NexoPulse', 'guard': 'NexoGuard', 'report': 'NexoReport', 'desk': 'NexoDesk'}
NIVEIS = ('leitura', 'operador', 'admin')  # do menor para o maior; 'nenhum' = sem acesso
NIVEL_NENHUM = 'nenhum'
ROLES_MASTER = ('Administrador Master', 'Operador Master')
ROLES_ADMIN_EMPRESA = ('Cliente', 'Administrador Cliente')

# Sem contrato registrado, a empresa tem o que a Central sempre ofereceu: a NexoPulse.
# O NexoDesk vem ligado: todo cliente precisa conseguir pedir suporte (o que muda por contrato é o SLA).
CONTRATO_PADRAO = {'pulse': True, 'guard': False, 'report': False, 'desk': True}
# Sem nível registrado, o usuário recebe o padrão do perfil em cada aplicativo contratado.
NIVEL_PADRAO_POR_PERFIL = {'Cliente': 'admin', 'Administrador Cliente': 'admin', 'Operador Cliente': 'operador'}
ROTULOS_NIVEL = {'nenhum': 'Sem acesso', 'leitura': 'Leitura', 'operador': 'Operador', 'admin': 'Administrador'}

# Atalhos da Central de Acessos: aplicativo fora da lista fica "Sem acesso".
PERFIS_PRONTOS = {
    'diretoria': {'nome': 'Diretoria', 'descricao': 'Acompanha a rede e os relatórios, sem agir.',
                  'niveis': {'pulse': 'leitura', 'report': 'leitura', 'desk': 'leitura'}},
    'tecnico': {'nome': 'Técnico', 'descricao': 'Opera o dia a dia da rede e da segurança.',
                'niveis': {'pulse': 'operador', 'guard': 'operador', 'desk': 'operador'}},
    'gestor_ti': {'nome': 'Gestor de TI', 'descricao': 'Administra todos os aplicativos contratados.',
                  'niveis': {'pulse': 'admin', 'guard': 'admin', 'report': 'admin', 'desk': 'admin'}},
}

# Rotas do próprio Hub (portal, login, administração de acessos, servidor): não pertencem
# a um aplicativo. Toda outra rota acessada com sessão de usuário é da NexoPulse.
ROTAS_DO_HUB = {
    'static', 'hub', 'login', 'logout', 'healthz', 'manifest', 'service_worker', 'privacidade',
    'pacote_agente', 'instalar_sh', 'instalar_windows', 'agent_versao', 'public_cron_trigger',
    'gerenciar_usuarios', 'criar_usuario', 'deletar_usuario', 'editar_usuario_info',
    'alterar_senha_user', 'toggle_user_status', 'testar_telegram',
    'pagina_saude', 'api_saude', 'api_saude_config', 'debug_db',
    'acessos_me', 'acessos_empresas', 'acessos_contrato', 'acessos_delegacao',
    'acessos_usuarios', 'acessos_nivel', 'acessos_perfil', 'acessos_auditoria',
    # Segurança de acesso (PR5): segunda etapa do login, Minha conta e controles da Central de Acessos.
    'login_2fa', 'conta', 'conta_senha', 'conta_2fa_iniciar', 'conta_2fa_confirmar', 'conta_2fa_desativar',
    'conta_2fa_codigos', 'conta_encerrar_sessoes', 'acessos_encerrar_sessoes', 'acessos_redefinir_2fa',
    'acessos_exigir_2fa', 'acessos_exigir_2fa_md',
}


EMPRESA_MASTER_NOME = 'MD Soluções'
EMPRESA_MASTER_USUARIO = 'md-solucoes'


def empresa_master_id(conn):
    """Id da Empresa Master (dona da plataforma) ou None."""
    row = conn.execute("SELECT id FROM clientes WHERE role='Cliente' AND empresa_master=1 ORDER BY id LIMIT 1").fetchone()
    return row['id'] if row else None


def garantir_empresa_master(conn, hash_senha_inutil):
    """Cria (uma vez) a Empresa Master, vincula a equipe Master a ela e passa para ela os sensores
    alocados direto numa conta Master (o jeito antigo de marcar "é da MD"). Idempotente.

    A conta da empresa não é para login: recebe uma senha aleatória que ninguém conhece.
    Sensores sem empresa continuam sem empresa (são alocados depois, na tela Sensores)."""
    resumo = {'criada': False, 'masters': 0, 'sensores': 0}
    empresa = empresa_master_id(conn)
    if empresa is None:
        existente = conn.execute("SELECT id FROM clientes WHERE role='Cliente' AND (usuario=? OR lower(nome)=lower(?)) ORDER BY id LIMIT 1",
                                 (EMPRESA_MASTER_USUARIO, EMPRESA_MASTER_NOME)).fetchone()
        if existente:
            empresa = existente['id']
        else:
            usuario = EMPRESA_MASTER_USUARIO
            if conn.execute('SELECT 1 FROM clientes WHERE usuario=?', (usuario,)).fetchone():
                usuario = f'{EMPRESA_MASTER_USUARIO}-empresa'
            conn.execute("INSERT INTO clientes (nome, usuario, senha, role, ativo, empresa_master) VALUES (?, ?, ?, 'Cliente', 1, 1)",
                         (EMPRESA_MASTER_NOME, usuario, hash_senha_inutil))
            empresa = conn.execute('SELECT id FROM clientes WHERE usuario=?', (usuario,)).fetchone()['id']
            resumo['criada'] = True
            for app in APLICATIVOS:
                definir_contrato(conn, empresa, app, True)
        conn.execute('UPDATE clientes SET empresa_master=1, ativo=1 WHERE id=?', (empresa,))
    marcadores = ','.join('?' * len(ROLES_MASTER))
    resumo['masters'] = conn.execute(f'UPDATE clientes SET cliente_pai_id=? WHERE role IN ({marcadores}) AND cliente_pai_id IS NULL',
                                     (empresa, *ROLES_MASTER)).rowcount or 0
    resumo['sensores'] = conn.execute(f'UPDATE sensores SET cliente_id=? WHERE cliente_id IN (SELECT id FROM clientes WHERE role IN ({marcadores}))',
                                      (empresa, *ROLES_MASTER)).rowcount or 0
    resumo['id'] = empresa
    return resumo


def aplicativo_da_rota(endpoint):
    if not endpoint or endpoint in ROTAS_DO_HUB:
        return None
    if endpoint.startswith('report_'):
        return 'report'
    if endpoint.startswith('desk_'):
        return 'desk'
    return 'pulse'


def nivel_suficiente(nivel, minimo):
    return nivel in NIVEIS and NIVEIS.index(nivel) >= NIVEIS.index(minimo)


def _usuario(conn, usuario_id):
    row = conn.execute('SELECT id, usuario, nome, role, ativo, cliente_pai_id FROM clientes WHERE id=?', (usuario_id,)).fetchone()
    return dict(row) if row else None


def empresa_de(usuario):
    """Id da empresa (conta com perfil Cliente) a que o usuário pertence; None para a equipe MD."""
    if not usuario or usuario['role'] in ROLES_MASTER:
        return None
    return usuario['id'] if usuario['role'] == 'Cliente' else usuario['cliente_pai_id']


def contrato(conn, empresa_id):
    contratado = dict(CONTRATO_PADRAO)
    for row in conn.execute('SELECT aplicativo, ativo FROM empresa_aplicativos WHERE empresa_id=?', (empresa_id,)).fetchall():
        if row['aplicativo'] in APLICATIVOS:
            contratado[row['aplicativo']] = bool(row['ativo'])
    return contratado


def niveis_registrados(conn, usuario_id):
    return {row['aplicativo']: row['nivel'] for row in
            conn.execute('SELECT aplicativo, nivel FROM usuario_aplicativos WHERE usuario_id=?', (usuario_id,)).fetchall()}


def acesso_efetivo(conn, usuario_id):
    """{aplicativo: nível} com o que o usuário pode usar agora (só níveis reais, nunca 'nenhum')."""
    usuario = _usuario(conn, usuario_id)
    if not usuario or not usuario.get('ativo', 1):
        return {}
    if usuario['role'] in ROLES_MASTER:
        return {app: 'admin' for app in APLICATIVOS}
    empresa = empresa_de(usuario)
    if empresa is None:
        return {}
    contratado = contrato(conn, empresa)
    registrados = niveis_registrados(conn, usuario_id)
    padrao = NIVEL_PADRAO_POR_PERFIL.get(usuario['role'])
    efetivo = {}
    for app in APLICATIVOS:
        if not contratado.get(app):
            continue
        nivel = registrados.get(app, padrao)
        if nivel in NIVEIS:
            efetivo[app] = nivel
    return efetivo


def nivel_app(conn, usuario_id, app):
    return acesso_efetivo(conn, usuario_id).get(app)


def delegacao_ativa(conn, empresa_id):
    row = conn.execute('SELECT delegacao_acessos FROM clientes WHERE id=?', (empresa_id,)).fetchone()
    return bool(row and (row['delegacao_acessos'] is None or row['delegacao_acessos']))


def pode_administrar_empresa(conn, autor):
    """O autor pode gerenciar usuários e níveis da própria empresa (MD sempre pode)."""
    if autor['role'] in ROLES_MASTER:
        return True
    if autor['role'] not in ROLES_ADMIN_EMPRESA:
        return False
    empresa = empresa_de(autor)
    return empresa is not None and delegacao_ativa(conn, empresa)


def validar_mudanca_nivel(conn, autor_id, alvo_id, app, nivel):
    """Mensagem de erro (texto) se o autor não pode dar esse nível ao alvo; None se pode."""
    if app not in APLICATIVOS:
        return 'Aplicativo desconhecido.'
    if nivel not in NIVEIS and nivel != NIVEL_NENHUM:
        return 'Nível inválido.'
    autor, alvo = _usuario(conn, autor_id), _usuario(conn, alvo_id)
    if not autor or not alvo:
        return 'Usuário não encontrado.'
    if alvo['role'] in ROLES_MASTER:
        return 'A equipe MD tem acesso total e não recebe nível por aplicativo.'
    empresa = empresa_de(alvo)
    if empresa is None:
        return 'Usuário sem empresa.'
    if nivel != NIVEL_NENHUM and not contrato(conn, empresa).get(app):
        return f'A empresa não contratou o {APLICATIVOS[app]}.'
    if autor['role'] in ROLES_MASTER:
        return None
    if not pode_administrar_empresa(conn, autor) or empresa_de(autor) != empresa:
        return 'Sem permissão para gerenciar acessos desta empresa.'
    if alvo['id'] == autor['id']:
        return 'Não é possível alterar o próprio acesso.'
    meu = nivel_app(conn, autor['id'], app)
    if nivel != NIVEL_NENHUM and not (meu and nivel_suficiente(meu, nivel)):
        return 'Não é possível conceder um nível acima do seu.'
    return None


def mudancas_do_perfil(conn, usuario_id, perfil):
    """{aplicativo: nível} que o perfil pronto aplica, só nos aplicativos contratados pela empresa."""
    usuario = _usuario(conn, usuario_id)
    empresa = empresa_de(usuario)
    if perfil not in PERFIS_PRONTOS or empresa is None:
        return {}
    contratado = contrato(conn, empresa)
    niveis = PERFIS_PRONTOS[perfil]['niveis']
    return {app: niveis.get(app, NIVEL_NENHUM) for app in APLICATIVOS if contratado.get(app)}


def definir_nivel(conn, usuario_id, app, nivel):
    conn.execute('INSERT INTO usuario_aplicativos (usuario_id, aplicativo, nivel) VALUES (?, ?, ?) '
                 'ON CONFLICT (usuario_id, aplicativo) DO UPDATE SET nivel=excluded.nivel, atualizado_em=CURRENT_TIMESTAMP',
                 (usuario_id, app, nivel))


def definir_contrato(conn, empresa_id, app, ativo):
    conn.execute('INSERT INTO empresa_aplicativos (empresa_id, aplicativo, ativo) VALUES (?, ?, ?) '
                 'ON CONFLICT (empresa_id, aplicativo) DO UPDATE SET ativo=excluded.ativo, atualizado_em=CURRENT_TIMESTAMP',
                 (empresa_id, app, 1 if ativo else 0))


def remover_registros_do_usuario(conn, usuario_id):
    conn.execute('DELETE FROM usuario_aplicativos WHERE usuario_id=?', (usuario_id,))
    conn.execute('DELETE FROM empresa_aplicativos WHERE empresa_id=?', (usuario_id,))


def registrar(conn, autor_id, autor, empresa_id, acao, alvo=None, detalhes=None):
    """Auditoria de acessos: quem fez o quê, em qual empresa e quando."""
    conn.execute('INSERT INTO auditoria_acessos (autor_id, autor, empresa_id, acao, alvo, detalhes) VALUES (?, ?, ?, ?, ?, ?)',
                 (autor_id, autor, empresa_id, acao, alvo,
                  json.dumps(detalhes, ensure_ascii=False) if isinstance(detalhes, (dict, list)) else detalhes))
