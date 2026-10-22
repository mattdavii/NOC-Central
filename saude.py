"""Saúde do servidor da NexoPulse Central.

Verificações feitas de dentro da Central (banco, filas, guardião, memória) e a
regra de alerta comum a ela e ao vigia (ops/vigia): um problema só vira alerta
depois de N verificações seguidas com falha, avisa uma vez e avisa de novo
quando normaliza.

Configuração e estado moram no banco (config_sistema e saude_estado), não na
máquina: quando o banco mudar de máquina, nada se perde.
"""
import os
import time

CONFIG_PADRAO = {
    'vigia_intervalo_min': '3',          # 2 a 5
    'falhas_para_alertar': '2',
    'banco_latencia_alerta_ms': '800',
    'banco_latencia_critico_ms': '3000',
    'memoria_alerta_pct': '85',
    'memoria_critico_pct': '95',
    'disco_alerta_pct': '85',
    'disco_critico_pct': '95',
    'backup_max_horas': '26',
    'central_url': os.environ.get('NEXO_CENTRAL_URL', 'https://nexopulse.up.railway.app'),
}
# Só estas chaves podem ser alteradas pela página (o resto é interno).
EDITAVEIS = {
    'vigia_intervalo_min': (2, 5), 'falhas_para_alertar': (1, 10),
    'banco_latencia_alerta_ms': (50, 60000), 'banco_latencia_critico_ms': (100, 120000),
    'memoria_alerta_pct': (50, 99), 'memoria_critico_pct': (50, 100),
    'disco_alerta_pct': (50, 99), 'disco_critico_pct': (50, 100), 'backup_max_horas': (1, 240),
}
INICIO_PROCESSO = time.time()


def _pg():
    return bool(os.environ.get('DATABASE_URL'))


def garantir_config(conn, telegram_token=None, telegram_chat=None):
    """Grava os padrões que faltam e o destino de alerta do vigia (bot master)."""
    agora = time.time()
    for chave, valor in CONFIG_PADRAO.items():
        conn.execute('INSERT INTO config_sistema (chave, valor, atualizado_em) VALUES (?, ?, ?) ON CONFLICT (chave) DO NOTHING',
                     (chave, valor, agora))
    # O vigia roda fora da Central e precisa avisar mesmo com ela fora do ar.
    for chave, valor in (('telegram_bot_token', telegram_token), ('telegram_chat_id', telegram_chat)):
        if valor:
            conn.execute('INSERT INTO config_sistema (chave, valor, atualizado_em) VALUES (?, ?, ?) '
                         'ON CONFLICT (chave) DO UPDATE SET valor=excluded.valor, atualizado_em=excluded.atualizado_em',
                         (chave, valor, agora))
    conn.commit()


def ler_config(conn):
    cfg = dict(CONFIG_PADRAO)
    for r in conn.execute("SELECT chave, valor FROM config_sistema WHERE chave NOT LIKE 'telegram_%'").fetchall():
        cfg[r['chave']] = r['valor']
    return cfg


def _n(cfg, chave):
    try:
        return float(cfg.get(chave, CONFIG_PADRAO.get(chave, 0)))
    except (TypeError, ValueError):
        return float(CONFIG_PADRAO.get(chave, 0))


def salvar_config(conn, mudancas):
    """Valida e grava as chaves editáveis. Devolve (ok, erro)."""
    limpas = {}
    for chave, valor in (mudancas or {}).items():
        if chave not in EDITAVEIS:
            return False, f'Configuração desconhecida: {chave}'
        try:
            numero = int(valor)
        except (TypeError, ValueError):
            return False, f'{chave} precisa ser um número inteiro.'
        minimo, maximo = EDITAVEIS[chave]
        if not minimo <= numero <= maximo:
            return False, f'{chave} deve ficar entre {minimo} e {maximo}.'
        limpas[chave] = str(numero)
    agora = time.time()
    for chave, valor in limpas.items():
        conn.execute('INSERT INTO config_sistema (chave, valor, atualizado_em) VALUES (?, ?, ?) '
                     'ON CONFLICT (chave) DO UPDATE SET valor=excluded.valor, atualizado_em=excluded.atualizado_em', (chave, valor, agora))
    conn.commit()
    return True, None


def _check(chave, rotulo, estado, valor, detalhe=''):
    return {'chave': chave, 'origem': 'central', 'rotulo': rotulo, 'estado': estado, 'valor': valor, 'detalhe': detalhe}


def _nivel(valor, alerta, critico):
    return 'critico' if valor >= critico else 'alerta' if valor >= alerta else 'ok'


def _memoria():
    """Uso de memória do container (cgroup v2); fora de container, da máquina."""
    try:
        with open('/sys/fs/cgroup/memory.max') as f:
            limite = f.read().strip()
        with open('/sys/fs/cgroup/memory.current') as f:
            atual = int(f.read().strip())
        if limite != 'max':
            return 100.0 * atual / int(limite), atual
    except (OSError, ValueError):
        pass
    try:
        import psutil
        vm = psutil.virtual_memory()
        return vm.percent, vm.used
    except Exception:
        return None, None


def coletar(conn, contexto=None):
    """Verificações da Central. `contexto`: pool, idade do guardião, guardião ligado."""
    contexto = contexto or {}
    cfg = ler_config(conn)
    checks = []

    # Banco: latência de ida e volta (pela tailnet em produção)
    tempos = []
    for _ in range(3):
        t = time.perf_counter()
        conn.execute('SELECT 1').fetchone()
        tempos.append((time.perf_counter() - t) * 1000)
    ms = sum(tempos) / len(tempos)
    checks.append(_check('banco_latencia', 'Latência do banco',
                         _nivel(ms, _n(cfg, 'banco_latencia_alerta_ms'), _n(cfg, 'banco_latencia_critico_ms')),
                         f'{ms:.0f} ms', 'média de 3 consultas'))

    if _pg():
        presas = conn.execute("""SELECT COUNT(*) AS n FROM pg_stat_activity WHERE datname = current_database()
            AND state = 'idle in transaction' AND now() - state_change > interval '60 seconds'""").fetchone()['n']
        checks.append(_check('banco_sessoes_presas', 'Sessões presas no banco', 'alerta' if presas else 'ok', str(presas),
                             'transações abertas e paradas há mais de 1 min (seguram locks)' if presas else ''))
        tamanho = conn.execute('SELECT pg_database_size(current_database()) AS b').fetchone()['b']
        checks.append(_check('banco_tamanho', 'Tamanho do banco', 'ok', f'{tamanho / 2**30:.2f} GB'))

    pool = contexto.get('pool')
    if pool is not None and getattr(pool, 'maxconn', None):
        usadas = len(getattr(pool, '_used', {}) or {})
        pct = 100.0 * usadas / pool.maxconn
        checks.append(_check('banco_conexoes', 'Conexões do pool', _nivel(pct, 80, 100), f'{usadas} de {pool.maxconn}'))

    # Fila do Telegram
    agora = time.time()
    atrasadas = conn.execute("SELECT COUNT(*) AS n FROM telegram_outbox WHERE state IN ('pending', 'sending') AND next_at < ?",
                             (agora - 300,)).fetchone()['n']
    falhas = conn.execute("SELECT COUNT(*) AS n FROM telegram_outbox WHERE state = 'failed' AND next_at > ?",
                          (agora - 3600,)).fetchone()['n']
    estado = 'alerta' if atrasadas or falhas else 'ok'
    checks.append(_check('telegram_fila', 'Fila do Telegram', estado,
                         f'{atrasadas} atrasada(s) · {falhas} falha(s) na última hora',
                         'mensagens paradas há mais de 5 min ou recusadas pelo Telegram' if estado != 'ok' else ''))

    # Guardião (detecta sensores offline)
    if contexto.get('guardiao_ligado'):
        idade = contexto.get('guardiao_idade_s')
        limite = max(120, 6 * contexto.get('guardiao_intervalo_s', 15))
        if idade is None:
            checks.append(_check('guardiao', 'Guardião de quedas', 'na', 'ainda não rodou'))
        else:
            checks.append(_check('guardiao', 'Guardião de quedas', 'critico' if idade > limite else 'ok', f'rodou há {idade:.0f} s',
                                 'sem varredura, sensores caídos não geram alerta' if idade > limite else ''))

    pct, usado = _memoria()
    if pct is not None:
        checks.append(_check('memoria', 'Memória', _nivel(pct, _n(cfg, 'memoria_alerta_pct'), _n(cfg, 'memoria_critico_pct')),
                             f'{pct:.0f}% ({usado / 2**20:.0f} MB)'))

    # Vigia: o batimento dele fica no banco
    batimento = conn.execute("SELECT valor FROM config_sistema WHERE chave='vigia_ultimo_batimento'").fetchone()
    intervalo_s = 60 * _n(cfg, 'vigia_intervalo_min')
    if not batimento:
        checks.append(_check('vigia', 'Vigia externo', 'na', 'não instalado', 'instale com ops/vigia/install.sh na máquina do banco'))
    else:
        idade = agora - float(batimento['valor'])
        parado = idade > 3 * intervalo_s + 60
        checks.append(_check('vigia', 'Vigia externo', 'alerta' if parado else 'ok', f'último teste há {idade / 60:.0f} min',
                             'o vigia parou de rodar: sem ele, quedas da Central não geram alerta' if parado else ''))

    sensores = conn.execute("SELECT SUM(CASE WHEN status='online' THEN 1 ELSE 0 END) AS on_, "
                            "SUM(CASE WHEN status='offline' THEN 1 ELSE 0 END) AS off FROM sensores").fetchone()
    checks.append(_check('sensores', 'Sensores', 'ok', f"{sensores['on_'] or 0} online · {sensores['off'] or 0} offline"))
    return checks


def info_processo():
    return {
        'versao_commit': (os.environ.get('RAILWAY_GIT_COMMIT_SHA') or '')[:7] or None,
        'deploy': os.environ.get('RAILWAY_DEPLOYMENT_ID'),
        'ambiente': os.environ.get('RAILWAY_ENVIRONMENT_NAME'),
        'iniciado_em': INICIO_PROCESSO,
    }


def aplicar(conn, checks, cfg, enviar):
    """Grava o estado de cada verificação e decide os alertas.

    `enviar(texto)` manda para o grupo master. Devolve os textos enviados.
    """
    limite = max(1, int(_n(cfg, 'falhas_para_alertar')))
    agora = time.time()
    enviados = []
    anteriores = {r['chave']: dict(r) for r in conn.execute('SELECT * FROM saude_estado').fetchall()}
    for c in checks:
        ant = anteriores.get(c['chave']) or {}
        ruim = c['estado'] in ('alerta', 'critico')
        falhas = (ant.get('falhas') or 0) + 1 if ruim else 0
        desde = (ant.get('desde') or agora) if ruim else None
        notificado = ant.get('notificado') or 0
        if ruim and not notificado and falhas >= limite:
            icone = '🚨' if c['estado'] == 'critico' else '⚠️'
            texto = f"{icone} SAÚDE DO SERVIDOR\n\n{c['rotulo']}: {c['valor']}" + (f"\n{c['detalhe']}" if c['detalhe'] else '')
            enviar(texto)
            enviados.append(texto)
            notificado = 1
        elif not ruim and notificado:
            texto = f"✅ SERVIDOR NORMALIZADO\n\n{c['rotulo']}: {c['valor']}"
            enviar(texto)
            enviados.append(texto)
            notificado = 0
        conn.execute('''INSERT INTO saude_estado (chave, origem, rotulo, estado, valor, detalhe, desde, falhas, notificado, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (chave) DO UPDATE SET origem=excluded.origem, rotulo=excluded.rotulo,
            estado=excluded.estado, valor=excluded.valor, detalhe=excluded.detalhe, desde=excluded.desde, falhas=excluded.falhas,
            notificado=excluded.notificado, atualizado_em=excluded.atualizado_em''',
                     (c['chave'], c['origem'], c['rotulo'], c['estado'], c['valor'], c['detalhe'], desde, falhas, notificado, agora))
    conn.commit()
    return enviados


def estado_vigia(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM saude_estado WHERE origem='vigia' ORDER BY chave").fetchall()]
