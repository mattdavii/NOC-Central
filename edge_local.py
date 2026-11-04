"""Painel local do NexoPulse Edge: a mesma cara da tela do sensor na Central, servida pelo próprio
sensor na porta local (10000), para quem está na rede do site, inclusive quando a internet caiu.

Aqui ficam o registro de eventos local (SQLite, com retenção configurável), o levantamento de rede
montado a partir da última varredura, as rotas do painel e o acesso por usuário e senha.

Acesso: http://<ip do sensor>:10000 com o usuário e a senha do painel local, iguais em todos os
sensores. A Central define a credencial (EDGE_USUARIO / EDGE_SENHA) e manda só o hash a cada
contato; o sensor guarda e continua aceitando o login sem internet. Para instalar sem Central,
NOC_EDGE_USUARIO / NOC_EDGE_SENHA no sensor valem até a Central mandar a dela.
"""
import csv
import hmac
import io
import ipaddress
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from flask import Blueprint, Response, jsonify, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import levantamento
except ImportError:
    levantamento = None
try:
    import oui
except ImportError:
    oui = None

RETENCAO_DIAS = max(1, int(os.environ.get("NOC_LOG_DIAS", "90")))
MAX_EVENTOS = max(1000, int(os.environ.get("NOC_LOG_MAX", "100000")))
LIMPEZA_S = 3600
GRAVIDADES = ('Crítica', 'Alerta', 'Aviso', 'OK')
PERIODOS = ('ultimos', 'dia', 'periodo')


# ------------------------------------------------------------------ registro de eventos
class Eventos:
    """Eventos do sensor no SQLite local. Os horários ficam em UTC (CURRENT_TIMESTAMP) e são
    mostrados no fuso da máquina."""

    def __init__(self, caminho='sensor_local.db'):
        self.caminho = caminho
        self._trava = threading.Lock()
        self._ultima_limpeza = 0.0

    def _conectar(self):
        conn = sqlite3.connect(self.caminho, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def iniciar(self):
        with self._conectar() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS logs_locais (id INTEGER PRIMARY KEY, tipo TEXT, detalhes TEXT, '
                         'gravidade TEXT, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_logs_locais_data ON logs_locais (data_hora)')

    def registrar(self, tipo, detalhes, gravidade='Alerta'):
        try:
            with self._trava, self._conectar() as conn:
                conn.execute('INSERT INTO logs_locais (tipo, detalhes, gravidade) VALUES (?, ?, ?)',
                             (str(tipo)[:120], str(detalhes)[:2000], gravidade if gravidade in GRAVIDADES else 'Alerta'))
                if time.time() - self._ultima_limpeza > LIMPEZA_S:
                    self._limpar(conn)
                    self._ultima_limpeza = time.time()
        except sqlite3.Error:
            pass  # o registro local nunca pode derrubar o agente

    @staticmethod
    def _limpar(conn):
        conn.execute("DELETE FROM logs_locais WHERE data_hora < datetime('now', ?)", (f'-{RETENCAO_DIAS} days',))
        conn.execute('DELETE FROM logs_locais WHERE id <= (SELECT id FROM logs_locais ORDER BY id DESC LIMIT 1 OFFSET ?)',
                     (MAX_EVENTOS,))

    def _filtro(self, de=None, ate=None, gravidade=None, busca=None):
        where, args = [], []
        if de:
            where.append("date(data_hora, 'localtime') >= ?"); args.append(de)
        if ate:
            where.append("date(data_hora, 'localtime') <= ?"); args.append(ate)
        if gravidade in GRAVIDADES:
            where.append('gravidade = ?'); args.append(gravidade)
        if busca:
            where.append('(tipo LIKE ? OR detalhes LIKE ?)'); args += [f'%{busca[:80]}%'] * 2
        return (' WHERE ' + ' AND '.join(where)) if where else '', args

    def listar(self, de=None, ate=None, gravidade=None, busca=None, limite=100, deslocamento=0):
        clausula, args = self._filtro(de, ate, gravidade, busca)
        with self._conectar() as conn:
            total = conn.execute(f'SELECT COUNT(*) FROM logs_locais{clausula}', args).fetchone()[0]
            linhas = conn.execute(
                f"SELECT id, tipo, detalhes, gravidade, datetime(data_hora, 'localtime') AS data_hora FROM logs_locais"
                f"{clausula} ORDER BY id DESC LIMIT ? OFFSET ?", args + [limite, deslocamento]).fetchall()
        return total, [dict(l) for l in linhas]

    def resumo(self, de=None, ate=None):
        clausula, args = self._filtro(de, ate)
        with self._conectar() as conn:
            por_gravidade = {l[0]: l[1] for l in conn.execute(
                f'SELECT gravidade, COUNT(*) FROM logs_locais{clausula} GROUP BY gravidade', args)}
            por_tipo = [(l[0], l[1]) for l in conn.execute(
                f'SELECT tipo, COUNT(*) AS n FROM logs_locais{clausula} GROUP BY tipo ORDER BY n DESC LIMIT 10', args)]
            primeiro = conn.execute(f"SELECT MIN(datetime(data_hora, 'localtime')) FROM logs_locais").fetchone()[0]
        return {'por_gravidade': por_gravidade, 'por_tipo': por_tipo, 'primeiro_registro': primeiro}


def periodo_da_requisicao(args):
    """'ultimos' (padrão: últimos 7 dias), 'dia' (data) ou 'periodo' (de/ate). Datas AAAA-MM-DD."""
    def data(valor):
        try:
            return datetime.strptime(valor, '%Y-%m-%d').date().isoformat()
        except (TypeError, ValueError):
            return None
    tipo = args.get('periodo') if args.get('periodo') in PERIODOS else 'ultimos'
    hoje = datetime.now().date()
    if tipo == 'dia' and data(args.get('data')):
        d = data(args.get('data'))
        return d, d, f"Dia {datetime.fromisoformat(d).strftime('%d/%m/%Y')}"
    if tipo == 'periodo' and (data(args.get('de')) or data(args.get('ate'))):
        de, ate = data(args.get('de')), data(args.get('ate'))
        if de and ate and de > ate:
            de, ate = ate, de
        texto = ' a '.join(datetime.fromisoformat(x).strftime('%d/%m/%Y') if x else '…' for x in (de, ate))
        return de, ate, f'Período {texto}'
    try:
        dias = max(1, min(RETENCAO_DIAS, int(args.get('dias', 7))))
    except ValueError:
        dias = 7
    return (hoje - timedelta(days=dias - 1)).isoformat(), hoje.isoformat(), f'Últimos {dias} dias'


# ------------------------------------------------------------------ configuração local
MAX_REDES_EXTRAS = 8


def validar_rede(texto):
    """Rede extra aceita: IPv4 privada, de /20 (4.096 endereços) a /32. None se não servir."""
    try:
        rede = ipaddress.ip_network(str(texto).strip(), strict=False)
    except ValueError:
        return None
    if rede.version != 4 or not rede.is_private or rede.is_loopback or rede.is_link_local or rede.prefixlen < 20:
        return None
    return rede


def ler_config(caminho, chave, padrao=None):
    try:
        with sqlite3.connect(caminho, timeout=5) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS config_local (chave TEXT PRIMARY KEY, valor TEXT)')
            linha = conn.execute('SELECT valor FROM config_local WHERE chave = ?', (chave,)).fetchone()
        return json.loads(linha[0]) if linha else padrao
    except (sqlite3.Error, ValueError):
        return padrao


def gravar_config(caminho, chave, valor):
    with sqlite3.connect(caminho, timeout=5) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS config_local (chave TEXT PRIMARY KEY, valor TEXT)')
        conn.execute('INSERT OR REPLACE INTO config_local (chave, valor) VALUES (?, ?)', (chave, json.dumps(valor)))


# ------------------------------------------------------------------ acesso por usuário e senha
SESSAO_HORAS = 12
MAX_FALHAS, BLOQUEIO_S = 5, 300          # 5 senhas erradas do mesmo IP: 5 minutos de espera
ROTAS_PUBLICAS = {'static', 'painel_local.login', 'painel_local.service_worker'}


class AcessoLocal:
    """Credencial do painel local e controle de tentativas. O hash vem da Central (ou do ambiente,
    na instalação sem Central) e fica no sensor_local.db, para o login funcionar sem internet."""

    def __init__(self, caminho='sensor_local.db'):
        self.caminho = caminho
        self._falhas = {}                    # ip -> [quantidade, bloqueado_ate]
        self._trava = threading.Lock()

    def chave_sessao(self):
        """Chave do cookie de sessão: gerada uma vez por sensor e guardada (sessões sobrevivem ao restart)."""
        chave = ler_config(self.caminho, 'chave_sessao')
        if not chave:
            chave = secrets.token_hex(32)
            gravar_config(self.caminho, 'chave_sessao', chave)
        return chave

    def credencial(self):
        """(usuario, hash) em uso: o da Central, se já chegou; senão o do ambiente do sensor."""
        da_central = ler_config(self.caminho, 'painel_credencial')
        if da_central and da_central.get('usuario') and da_central.get('senha_hash'):
            return da_central['usuario'], da_central['senha_hash'], 'central'
        senha = os.environ.get('NOC_EDGE_SENHA', '')
        if senha:
            usuario = os.environ.get('NOC_EDGE_USUARIO', 'nexopulse').strip() or 'nexopulse'
            local = ler_config(self.caminho, 'painel_credencial_ambiente') or {}
            if local.get('usuario') != usuario or not check_password_hash(local.get('senha_hash', ''), senha):
                local = {'usuario': usuario, 'senha_hash': generate_password_hash(senha, method='pbkdf2:sha256')}
                gravar_config(self.caminho, 'painel_credencial_ambiente', local)
            return local['usuario'], local['senha_hash'], 'ambiente'
        return None, None, None

    def receber_da_central(self, dados):
        """Guarda a credencial enviada pela Central; devolve True se mudou."""
        if not isinstance(dados, dict):
            return False
        usuario, senha_hash = str(dados.get('usuario') or '')[:64], str(dados.get('senha_hash') or '')[:300]
        if not usuario or not senha_hash.startswith(('pbkdf2:', 'scrypt:')):
            return False
        atual = ler_config(self.caminho, 'painel_credencial') or {}
        if atual.get('usuario') == usuario and atual.get('senha_hash') == senha_hash:
            return False
        gravar_config(self.caminho, 'painel_credencial', {'usuario': usuario, 'senha_hash': senha_hash})
        # Credencial nova derruba as sessões abertas com a anterior.
        gravar_config(self.caminho, 'credencial_versao', (ler_config(self.caminho, 'credencial_versao', 0) or 0) + 1)
        return True

    def versao(self):
        return ler_config(self.caminho, 'credencial_versao', 0) or 0

    def bloqueado(self, ip):
        with self._trava:
            ate = self._falhas.get(ip, (0, 0))[1]
            return int(ate - time.time()) if ate > time.time() else 0

    def conferir(self, ip, usuario, senha):
        usuario_ok, senha_hash, _ = self.credencial()
        valido = bool(usuario_ok and senha_hash) and hmac.compare_digest(str(usuario), usuario_ok) \
            and check_password_hash(senha_hash, str(senha))
        with self._trava:
            if valido:
                self._falhas.pop(ip, None)
            else:
                qtd = self._falhas.get(ip, (0, 0))[0] + 1
                # Na 5ª erro: bloqueia por BLOQUEIO_S e zera a contagem para depois do bloqueio.
                self._falhas[ip] = (0, time.time() + BLOQUEIO_S) if qtd >= MAX_FALHAS else (qtd, 0)
        return valido


def exigir_login(acesso):
    """before_request do app do Edge: tudo exige sessão, menos a tela de login e o visual (CSS, logos)."""
    if request.endpoint in ROTAS_PUBLICAS:
        return None
    if session.get('ok') and session.get('versao') == acesso.versao() and \
            time.time() - session.get('desde', 0) < SESSAO_HORAS * 3600:
        # Escrita só da própria página: outro site aberto no navegador não manda comando ao sensor.
        if request.method not in ('GET', 'HEAD') and not _mesma_origem():
            return jsonify(erro='Origem não permitida.'), 403
        return None
    session.clear()
    if request.path.startswith('/api/'):
        return jsonify(erro='Sessão expirada. Entre de novo no painel local.'), 401
    return redirect('/login?proximo=' + (request.full_path.rstrip('?') if request.path != '/' else '/'))


def _mesma_origem():
    origem = request.headers.get('Origin') or request.headers.get('Referer') or ''
    if not origem:
        return True   # navegador antigo/ferramenta: o cookie SameSite=Strict já barra o envio entre sites
    from urllib.parse import urlparse
    return urlparse(origem).netloc == request.host


# ------------------------------------------------------------------ levantamento local
def linhas_dispositivos(topologia, nomes_salvos, visto_em):
    """Converte a última varredura do Edge para o formato de linhas que levantamento.montar usa."""
    linhas = []
    for d in topologia:
        mac = d.get('mac')
        registro, fonte = (oui.fabricante(mac) if oui else (None, None))
        if not registro and d.get('fabricante') not in (None, '', 'Desconhecido'):
            registro, fonte = d['fabricante'], 'sensor'
        linhas.append({
            'ip': d.get('ip'), 'mac': mac,
            'fabricante': oui.marca(registro) if (oui and fonte in ('ieee', 'sensor')) else (registro or 'Desconhecido'),
            'fabricante_registro': registro if fonte in ('ieee', 'sensor') else None,
            'fabricante_fonte': fonte,
            'nome_custom': nomes_salvos.get(mac),
            'hostname': d.get('hostname'), 'hostname_fonte': d.get('hostname_fonte'),
            'status': d.get('status'), 'latencia': d.get('latencia'),
            'neighbor_state': d.get('neighbor_state'), 'visto_em': visto_em,
            'modelo': d.get('modelo'), 'tipo_equipamento': d.get('tipo_equipamento'), 'cftv': d.get('cftv'),
            'comunicacao': d.get('comunicacao'), 'firmware': d.get('firmware'), 'rede_origem': d.get('rede'),
        })
    return linhas


def eventos_para_csv(eventos):
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=';', lineterminator='\r\n')
    escritor.writerow(['Data/hora', 'Gravidade', 'Evento', 'Detalhes'])
    for e in eventos:
        escritor.writerow([("'" + str(v) if str(v)[:1] in ('=', '+', '-', '@') else v)
                           for v in (e['data_hora'], e['gravidade'], e['tipo'], e['detalhes'])])
    return ('﻿' + saida.getvalue()).encode('utf-8')


# ------------------------------------------------------------------ rotas
def criar_blueprint(eventos, estado):
    """estado: objeto com os callables do agente
         dados()        -> dict dados_sensores (última coleta)
         mac()          -> id do sensor
         nome()         -> nome do sensor
         versao()       -> versão do agente
         host_info()    -> última leitura de host_metrics (ou None)
         nomes_salvos() -> {mac: apelido}
         visto_em()     -> horário (UTC, ISO) da última varredura
    """
    bp = Blueprint('painel_local', __name__)

    @bp.route('/login', methods=['GET', 'POST'])
    def login():
        proximo = request.values.get('proximo') or '/'
        if not proximo.startswith('/') or proximo.startswith('//'):
            proximo = '/'
        acesso = estado.acesso   # lido a cada pedido (o agente pode trocar o objeto)
        usuario_cfg, _, _ = acesso.credencial()
        erro, ip = None, request.remote_addr or '?'
        if request.method == 'POST':
            espera = acesso.bloqueado(ip)
            if espera:
                erro = f'Muitas tentativas erradas. Tente de novo em {espera // 60 + 1} min.'
            elif acesso.conferir(ip, request.form.get('usuario', ''), request.form.get('senha', '')):
                session.clear()
                session.update(ok=True, desde=time.time(), versao=acesso.versao())
                session.permanent = True
                eventos.registrar('Acesso local', f'Entrada no painel local a partir de {ip}.', 'OK')
                return redirect(proximo)
            else:
                erro = 'Usuário ou senha incorretos.'
                eventos.registrar('Acesso local', f'Tentativa de entrada com senha errada a partir de {ip}.', 'Aviso')
        pagina = render_template('edge/login.html', sensor=contexto_sensor(), erro=erro, proximo=proximo,
                                 sem_credencial=usuario_cfg is None, usuario=request.form.get('usuario', '')[:64])
        return pagina, (401 if erro else 200)

    @bp.route('/sair', methods=['POST'])
    def sair():
        session.clear()
        return redirect('/login')

    def contexto_sensor():
        d = estado.dados()
        return {
            'mac_id': estado.mac(), 'nome_local': estado.nome(), 'agent_version': estado.versao(),
            'ip_sensor': d.get('meu_ip'), 'ip_gateway': d.get('gateway_ip'), 'interface_nome': d.get('interface'),
            'interface_mac': d.get('mac_interface'), 'rede_cidr': d.get('rede_cidr'), 'provedor': None,
            'cliente_nome': None, 'last_seen': datetime.now().isoformat(timespec='seconds'),
        }

    def montar_levantamento(ocultar_nomes=False):
        d = estado.dados()
        linhas = linhas_dispositivos(d.get('topologia') or [], estado.nomes_salvos(), estado.visto_em())
        return levantamento.montar(contexto_sensor(), linhas, ocultar_nomes=ocultar_nomes,
                                   gerado_por=f'NexoPulse Edge {estado.versao()} (painel local)')

    def baixar(conteudo, tipo, nome):
        resposta = Response(conteudo, mimetype=tipo)
        resposta.headers['Content-Disposition'] = f'attachment; filename="{nome}"'
        resposta.headers['Cache-Control'] = 'no-store'
        return resposta

    @bp.route('/')
    def painel():
        return render_template('edge/painel.html', sensor=contexto_sensor(), retencao_dias=RETENCAO_DIAS,
                               xlsx=_tem_openpyxl())

    @bp.route('/sw.js')
    def service_worker():
        # brand.js registra o app da Central; aqui não há o que guardar offline, então o worker é vazio.
        return Response('// NexoPulse Edge: sem cache offline\n', mimetype='application/javascript',
                        headers={'Cache-Control': 'no-store'})

    @bp.route('/api/v2/sensor_data/<mac_id>')
    def sensor_data(mac_id):
        # Mesmo formato que a Central devolve, para static/host.js funcionar sem mudanças.
        import json
        info = estado.host_info()
        return jsonify({'host_info': json.dumps(info) if info else None})

    @bp.route('/api/eventos')
    def api_eventos():
        try:
            limite = max(1, min(500, int(request.args.get('limite', 100))))
            deslocamento = max(0, int(request.args.get('deslocamento', 0)))
        except ValueError:
            return jsonify(erro='limite e deslocamento precisam ser números.'), 400
        total, linhas = eventos.listar(request.args.get('de') or None, request.args.get('ate') or None,
                                       request.args.get('gravidade') or None, request.args.get('busca') or None,
                                       limite, deslocamento)
        return jsonify(total=total, eventos=linhas, retencao_dias=RETENCAO_DIAS)

    @bp.route('/api/eventos.csv')
    def api_eventos_csv():
        de, ate, _ = periodo_da_requisicao(request.args)
        _, linhas = eventos.listar(de, ate, request.args.get('gravidade') or None, request.args.get('busca') or None,
                                   limite=MAX_EVENTOS)
        return baixar(eventos_para_csv(linhas), 'text/csv; charset=utf-8',
                      f"eventos-{_slug(estado.nome())}-{de or 'inicio'}-a-{ate or 'hoje'}.csv")

    @bp.route('/api/levantamento.<formato>')
    def api_levantamento(formato):
        if levantamento is None:
            return jsonify(erro='Módulo de levantamento ausente neste sensor; atualize o agente.'), 501
        dados = montar_levantamento(request.args.get('ocultar_nomes') == '1')
        if formato == 'json':
            import json
            return baixar(json.dumps(dados, ensure_ascii=False, indent=2), 'application/json',
                          levantamento.nome_arquivo(dados, 'json'))
        if formato == 'csv':
            return baixar(levantamento.para_csv(dados), 'text/csv; charset=utf-8', levantamento.nome_arquivo(dados, 'csv'))
        if formato == 'xlsx':
            if not _tem_openpyxl():
                return jsonify(erro='Exportação XLSX indisponível: instale o openpyxl no sensor ou use CSV.'), 501
            return baixar(levantamento.para_xlsx(dados),
                          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                          levantamento.nome_arquivo(dados, 'xlsx'))
        return jsonify(erro='Formato inválido: use json, csv ou xlsx.'), 400

    @bp.route('/api/config/redes_extras', methods=['GET', 'POST'])
    def config_redes_extras():
        caminho = eventos.caminho
        if request.method == 'GET':
            return jsonify(redes=ler_config(caminho, 'redes_extras', []), maximo=MAX_REDES_EXTRAS,
                           varredura=estado.dados().get('varredura'))
        itens = (request.json or {}).get('redes')
        if isinstance(itens, str):
            itens = [p for p in itens.replace(';', ',').replace('\n', ',').split(',')]
        if not isinstance(itens, list):
            return jsonify(erro='Envie as redes como lista, ex.: ["192.168.10.0/24"].'), 400
        redes, invalidas = [], []
        for item in (str(i).strip() for i in itens):
            if not item:
                continue
            rede = validar_rede(item)
            if rede is None:
                invalidas.append(item)
            elif str(rede) not in redes:
                redes.append(str(rede))
        if invalidas:
            return jsonify(erro='Rede inválida (use faixa privada de /20 a /32, ex.: 192.168.10.0/24): '
                                + ', '.join(invalidas)), 400
        if len(redes) > MAX_REDES_EXTRAS:
            return jsonify(erro=f'No máximo {MAX_REDES_EXTRAS} redes adicionais.'), 400
        gravar_config(caminho, 'redes_extras', redes)
        eventos.registrar('Configuração', 'Redes adicionais da varredura: ' + (', '.join(redes) or 'nenhuma'), 'OK')
        return jsonify(redes=redes)

    @bp.route('/api/dispositivos')
    def api_dispositivos():
        if levantamento is None:
            return jsonify(dispositivos=[], resumo={})
        dados = montar_levantamento()
        return jsonify(dispositivos=dados['dispositivos'], resumo=dados['resumo'])

    @bp.route('/relatorio')
    def relatorio():
        de, ate, periodo_str = periodo_da_requisicao(request.args)
        _, linhas = eventos.listar(de, ate, limite=2000)
        resumo = eventos.resumo(de, ate)
        inventario = montar_levantamento() if levantamento else {'dispositivos': [], 'resumo': {}}
        d = estado.dados()
        pings = d.get('pings') or {}
        return render_template('edge/relatorio.html', sensor=contexto_sensor(), logs=linhas, resumo=resumo,
                               inventario=inventario, periodo_str=periodo_str, de=de, ate=ate,
                               data_emissao=datetime.now().strftime('%d/%m/%Y às %H:%M'),
                               wan_ok=bool(pings.get('Google') or pings.get('Cloudflare')),
                               ping_gateway=d.get('ping_gateway'), truncado=len(linhas) >= 2000)

    return bp


def _tem_openpyxl():
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        return False


def _slug(texto):
    return ''.join(c if c.isalnum() or c in '-_' else '-' for c in (texto or 'sensor')).strip('-')[:40] or 'sensor'
