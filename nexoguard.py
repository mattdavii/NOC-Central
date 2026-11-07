"""NexoGuard: achados de segurança da rede a partir do que o sensor já observa.

O sensor só envia o que viu (portas TCP que responderam ao aperto de mão e se o roteador anuncia UPnP);
as regras ficam aqui, na Central. Uma regra nova ou corrigida vale para todos os locais na próxima
varredura, sem atualizar sensor nenhum.

Cada achado é (local, equipamento, regra), com estado:
  - aberto: a condição está presente;
  - aceito: a MD (ou o cliente, numa fase futura) decidiu conviver com ele, com o motivo registrado;
  - resolvido: a condição sumiu numa varredura em que o equipamento estava na rede. Se voltar, reabre.

Equipamento fora da rede ou que ainda não teve as portas testadas não muda achado nenhum: ausência de
dado não é prova de correção. Título e recomendação vêm da regra na hora de mostrar (texto corrigido
vale também para achados antigos); no banco fica só a evidência daquela varredura.
"""
import json
import time

GRAVIDADES = ('critico', 'alto', 'medio', 'info')
ROTULO_GRAVIDADE = {'critico': 'Crítico', 'alto': 'Alto', 'medio': 'Médio', 'info': 'Informativo'}
ESTADOS = ('aberto', 'aceito', 'resolvido')
ROTULO_ESTADO = {'aberto': 'Aberto', 'aceito': 'Aceito como risco', 'resolvido': 'Resolvido'}
MAX_MOTIVO = 500


class Recusa(Exception):
    def __init__(self, mensagem, status=400):
        super().__init__(mensagem)
        self.mensagem, self.status = mensagem, status


def _porta(numero, nome):
    return lambda d, portas: f'A porta {numero} ({nome}) respondeu na varredura.' if numero in portas else None


def _http_sem_https(d, portas):
    # Só em equipamento de infraestrutura identificado: computador com servidor web não é gerência.
    if d.get('tipo_equipamento') and 80 in portas and not portas & {443, 8443}:
        return 'Responde em HTTP (porta 80) e não tem HTTPS (443 ou 8443).'
    return None


def _upnp(d, portas):
    return 'O equipamento se anuncia na rede como roteador UPnP (InternetGatewayDevice).' if d.get('upnp_igd') else None


REGRAS = [
    {'chave': 'telnet', 'gravidade': 'alto', 'teste': _porta(23, 'Telnet'),
     'titulo': 'Telnet aberto',
     'recomendacao': 'Desligar o Telnet e gerenciar por SSH ou pela interface web com HTTPS. No Telnet a senha '
                     'de gerência passa pela rede sem criptografia.'},
    {'chave': 'vnc', 'gravidade': 'alto', 'teste': _porta(5900, 'VNC'),
     'titulo': 'Acesso remoto VNC aberto',
     'recomendacao': 'Desligar o VNC ou exigir senha forte e liberar só para quem precisa. Prefira uma '
                     'ferramenta de acesso remoto com login individual.'},
    {'chave': 'ftp', 'gravidade': 'medio', 'teste': _porta(21, 'FTP'),
     'titulo': 'FTP aberto',
     'recomendacao': 'Desligar o FTP ou trocar por SFTP. No FTP usuário, senha e arquivos passam sem criptografia.'},
    {'chave': 'rdp', 'gravidade': 'medio', 'teste': _porta(3389, 'RDP'),
     'titulo': 'Área de Trabalho Remota (RDP) aberta',
     'recomendacao': 'Liberar o RDP só para os computadores que precisam, com NLA ligado e senhas fortes. '
                     'Nunca encaminhar essa porta para a internet.'},
    {'chave': 'upnp', 'gravidade': 'medio', 'teste': _upnp,
     'titulo': 'UPnP ligado no roteador',
     'recomendacao': 'Desligar o UPnP no roteador. Com ele ligado, qualquer aparelho da rede pode abrir portas '
                     'para a internet sem ninguém saber.'},
    {'chave': 'http_gerencia', 'gravidade': 'medio', 'teste': _http_sem_https,
     'titulo': 'Gerência só por HTTP',
     'recomendacao': 'Ativar HTTPS na gerência do equipamento e desligar o HTTP. Sem isso, a senha de acesso '
                     'passa pela rede sem criptografia.'},
    {'chave': 'mikrotik_api', 'gravidade': 'medio', 'teste': _porta(8728, 'API MikroTik'),
     'titulo': 'API do MikroTik sem criptografia',
     'recomendacao': 'Em IP > Services, desligar o serviço "api" ou usar só o "api-ssl" (8729), liberado apenas '
                     'para o IP de gerência.'},
    {'chave': 'xmeye', 'gravidade': 'medio', 'teste': _porta(34567, 'XMeye/iCSee'),
     'titulo': 'Protocolo XMeye/iCSee ativo',
     'recomendacao': 'Câmeras e gravadores XMeye/iCSee têm histórico de falhas de segurança. Manter o firmware '
                     'em dia, trocar a senha padrão e nunca expor o equipamento à internet.'},
    {'chave': 'winbox', 'gravidade': 'info', 'teste': _porta(8291, 'Winbox'),
     'titulo': 'Winbox aberto para a rede toda',
     'recomendacao': 'Em IP > Services, limitar o Winbox à faixa de gerência (campo "Available From").'},
    {'chave': 'tr069', 'gravidade': 'info', 'teste': _porta(7547, 'TR-069'),
     'titulo': 'Gerência do provedor (TR-069) responde na rede interna',
     'recomendacao': 'A porta de gerência remota do provedor não precisa responder para a rede interna. '
                     'Pedir ao provedor para restringir.'},
    {'chave': 'smb', 'gravidade': 'info', 'teste': _porta(445, 'SMB'),
     'titulo': 'Compartilhamento de arquivos (SMB) aberto',
     'recomendacao': 'Normal em computadores e servidores de arquivos. Confirmar que o SMBv1 está desligado '
                     'e que as pastas compartilhadas pedem senha.'},
]
REGRA = {r['chave']: r for r in REGRAS}
assert all(r['gravidade'] in GRAVIDADES for r in REGRAS)


def portas_da_rede(valor):
    """Lista de portas que o sensor mandou -> JSON para o banco; None se não veio ou é inválida."""
    if not isinstance(valor, list) or len(valor) > 64:
        return None
    if not all(isinstance(p, int) and not isinstance(p, bool) and 0 < p < 65536 for p in valor):
        return None
    return json.dumps(sorted(set(valor)))


def _portas(d):
    try:
        lista = json.loads(d.get('portas_abertas') or 'null')
    except (TypeError, ValueError):
        return None
    return set(lista) if isinstance(lista, list) else None


def _chave(d):
    return d.get('mac') or f"ip:{d.get('ip')}"


def _nome(d):
    return d.get('nome_custom') or d.get('hostname') or d.get('modelo') or d.get('fabricante') or d.get('ip')


def avaliar(d):
    """[(regra, evidência)] para um equipamento (dict da tabela dispositivos); None se não dá para avaliar."""
    portas = _portas(d)
    if portas is None:
        return None
    achados = []
    for regra in REGRAS:
        evidencia = regra['teste'](d, portas)
        if evidencia:
            achados.append((regra['chave'], evidencia))
    return achados


def sincronizar(conn, sensor_mac, agora=None):
    """Atualiza os achados de um local a partir do inventário atual. Devolve os ids dos achados novos ou reabertos."""
    agora = agora or time.time()
    existentes = {(r['dispositivo'], r['regra']): dict(r) for r in conn.execute(
        'SELECT id, dispositivo, regra, estado FROM guard_achados WHERE sensor_mac = ?', (sensor_mac,)).fetchall()}
    novos = []
    for row in conn.execute('SELECT * FROM dispositivos WHERE sensor_mac = ?', (sensor_mac,)).fetchall():
        d = dict(row)
        if d.get('status') == 'offline':
            continue
        achados = avaliar(d)
        if achados is None:
            continue
        chave = _chave(d)
        presentes = set()
        for regra, evidencia in achados:
            presentes.add(regra)
            atual = existentes.get((chave, regra))
            comuns = (d.get('ip'), _nome(d), d.get('tipo_equipamento'), evidencia, agora)
            if atual is None:
                row = conn.execute(
                    'INSERT INTO guard_achados (sensor_mac, dispositivo, ip, nome, tipo, evidencia, visto_em, regra, '
                    "gravidade, estado, primeiro_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'aberto', ?) RETURNING id",
                    (sensor_mac, chave, *comuns, regra, REGRA[regra]['gravidade'], agora)).fetchone()
                novos.append(row['id'])
            elif atual['estado'] == 'resolvido':
                conn.execute("UPDATE guard_achados SET ip = ?, nome = ?, tipo = ?, evidencia = ?, visto_em = ?, "
                             "gravidade = ?, estado = 'aberto', resolvido_em = NULL, notificado = 0 WHERE id = ?",
                             (*comuns, REGRA[regra]['gravidade'], atual['id']))
                novos.append(atual['id'])
            else:
                conn.execute('UPDATE guard_achados SET ip = ?, nome = ?, tipo = ?, evidencia = ?, visto_em = ?, '
                             'gravidade = ? WHERE id = ?', (*comuns, REGRA[regra]['gravidade'], atual['id']))
        for (dispositivo, regra), atual in existentes.items():
            if dispositivo == chave and regra not in presentes and atual['estado'] != 'resolvido':
                conn.execute("UPDATE guard_achados SET estado = 'resolvido', resolvido_em = ? WHERE id = ?",
                             (agora, atual['id']))
    return novos


def _completar(a):
    regra = REGRA.get(a['regra'], {})
    a['titulo'] = regra.get('titulo', a['regra'])
    a['recomendacao'] = regra.get('recomendacao', '')
    a['rotulo_gravidade'] = ROTULO_GRAVIDADE.get(a['gravidade'], a['gravidade'])
    a['rotulo_estado'] = ROTULO_ESTADO.get(a['estado'], a['estado'])
    return a


def listar(conn, estado='aberto', empresa_id=None):
    filtros, params = ['g.estado = ?'], [estado]
    if empresa_id:
        filtros.append('s.cliente_id = ?')
        params.append(empresa_id)
    rows = conn.execute(
        'SELECT g.*, s.nome_local, s.cliente_id AS empresa_id, c.nome AS empresa, c.usuario AS empresa_usuario '
        'FROM guard_achados g LEFT JOIN sensores s ON s.mac_id = g.sensor_mac '
        f"LEFT JOIN clientes c ON c.id = s.cliente_id WHERE {' AND '.join(filtros)}", params).fetchall()
    achados = [_completar(dict(r)) for r in rows]
    achados.sort(key=lambda a: (GRAVIDADES.index(a['gravidade']) if a['gravidade'] in GRAVIDADES else 9,
                                (a['empresa'] or a['empresa_usuario'] or '~').lower(), (a['nome_local'] or '').lower(),
                                a['ip'] or ''))
    return achados


def resumo(conn, empresa_id=None):
    """Contagem por estado e, dos abertos, por gravidade."""
    filtro, params = ('', ()) if not empresa_id else (' WHERE s.cliente_id = ?', (empresa_id,))
    rows = conn.execute('SELECT g.estado, g.gravidade, COUNT(*) AS n FROM guard_achados g '
                        f'LEFT JOIN sensores s ON s.mac_id = g.sensor_mac{filtro} GROUP BY g.estado, g.gravidade',
                        params).fetchall()
    total = {e: 0 for e in ESTADOS}
    abertos = {g: 0 for g in GRAVIDADES}
    for r in rows:
        total[r['estado']] = total.get(r['estado'], 0) + r['n']
        if r['estado'] == 'aberto':
            abertos[r['gravidade']] = abertos.get(r['gravidade'], 0) + r['n']
    return {'estados': total, 'abertos': abertos}


def achado(conn, achado_id):
    row = conn.execute('SELECT g.*, s.nome_local, s.cliente_id AS empresa_id FROM guard_achados g '
                       'LEFT JOIN sensores s ON s.mac_id = g.sensor_mac WHERE g.id = ?', (achado_id,)).fetchone()
    return _completar(dict(row)) if row else None


def aceitar(conn, achado_id, autor, motivo, agora=None):
    a = achado(conn, achado_id)
    if not a:
        raise Recusa('Achado não encontrado.', 404)
    if a['estado'] != 'aberto':
        raise Recusa('Só um achado aberto pode ser aceito como risco.')
    motivo = (motivo or '').strip()
    if not motivo:
        raise Recusa('Diga por que o risco foi aceito: fica registrado no achado.')
    if len(motivo) > MAX_MOTIVO:
        raise Recusa(f'O motivo passa de {MAX_MOTIVO} caracteres.')
    conn.execute("UPDATE guard_achados SET estado = 'aceito', aceito_por = ?, aceito_em = ?, motivo = ? WHERE id = ?",
                 (autor, agora or time.time(), motivo, achado_id))
    return a


def reabrir(conn, achado_id):
    a = achado(conn, achado_id)
    if not a:
        raise Recusa('Achado não encontrado.', 404)
    if a['estado'] != 'aceito':
        raise Recusa('Só um achado aceito como risco pode ser reaberto.')
    conn.execute("UPDATE guard_achados SET estado = 'aberto', aceito_por = NULL, aceito_em = NULL, motivo = NULL "
                 'WHERE id = ?', (achado_id,))
    return a
