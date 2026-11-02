"""Família Nexo: contratos (empresa → aplicativos) e níveis de acesso (usuário → aplicativo).

Acesso efetivo de um usuário a um aplicativo = o aplicativo está contratado pela empresa
∩ o usuário tem nível nele. Ausência de registro significa "padrão do perfil" (é o que
mantém quem já usa a Central com o mesmo acesso de antes); registro explícito é decisão
tomada — inclusive "nenhum" (sem acesso) e contrato desligado.
"""
import json

APLICATIVOS = {'pulse': 'NexoPulse', 'guard': 'NexoGuard', 'report': 'NexoReport'}
NIVEIS = ('leitura', 'operador', 'admin')  # do menor para o maior; 'nenhum' = sem acesso
NIVEL_NENHUM = 'nenhum'
ROLES_MASTER = ('Administrador Master', 'Operador Master')
ROLES_ADMIN_EMPRESA = ('Cliente', 'Administrador Cliente')

# Sem contrato registrado, a empresa tem o que a Central sempre ofereceu: a NexoPulse.
CONTRATO_PADRAO = {'pulse': True, 'guard': False, 'report': False}
# Sem nível registrado, o usuário recebe o padrão do perfil em cada aplicativo contratado.
NIVEL_PADRAO_POR_PERFIL = {'Cliente': 'admin', 'Administrador Cliente': 'admin', 'Operador Cliente': 'operador'}

# Rotas do próprio Hub (portal, login, administração de acessos, servidor): não pertencem
# a um aplicativo. Toda outra rota acessada com sessão de usuário é da NexoPulse.
ROTAS_DO_HUB = {
    'static', 'login', 'logout', 'healthz', 'manifest', 'service_worker', 'privacidade',
    'pacote_agente', 'instalar_sh', 'instalar_windows', 'agent_versao', 'public_cron_trigger',
    'gerenciar_usuarios', 'criar_usuario', 'deletar_usuario', 'editar_usuario_info',
    'alterar_senha_user', 'toggle_user_status', 'testar_telegram',
    'pagina_saude', 'api_saude', 'api_saude_config', 'debug_db',
    'acessos_me', 'acessos_empresas', 'acessos_contrato', 'acessos_delegacao',
    'acessos_usuarios', 'acessos_nivel', 'acessos_auditoria',
}


def aplicativo_da_rota(endpoint):
    if not endpoint or endpoint in ROTAS_DO_HUB:
        return None
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
