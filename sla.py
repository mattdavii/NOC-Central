"""Prazo de atendimento (SLA) em horas de atendimento: horário comercial ou 24x7, com pausas.

Comercial = segunda a sexta, 8h às 18h (Brasília), fora os feriados nacionais. Tudo em epoch (segundos, UTC).
"""
from datetime import date, datetime, time as hora, timedelta
from functools import lru_cache

from hub import FUSO_BR

HORARIOS = {'comercial': 'Comercial (seg a sex, 8h às 18h)', '24x7': '24 horas, todos os dias'}
INICIO_DIA, FIM_DIA = 8, 18
PADRAO = {'horario': 'comercial',
          'prazos': {'critica': [1, 8], 'alta': [2, 16], 'media': [4, 24], 'baixa': [8, 40]}}   # horas: [1ª resposta, solução]
PERTO = 0.8   # "perto de vencer" a partir de 80% do prazo consumido
INFINITO = float('inf')


def _pascoa(ano):
    """Domingo de Páscoa (algoritmo de Meeus/Butcher)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


@lru_cache(maxsize=32)
def feriados_nacionais(ano):
    """Feriados nacionais (Lei 662/1949 e alterações, incluindo 20/11 desde 2024) e a Sexta-feira Santa."""
    fixos = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)]
    if ano >= 2024:
        fixos.append((11, 20))
    dias = {date(ano, m, d) for m, d in fixos}
    dias.add(_pascoa(ano) - timedelta(days=2))
    return frozenset(dias)


def dia_util(d):
    return d.weekday() < 5 and d not in feriados_nacionais(d.year)


def _janelas(inicio, horario):
    """Janelas de atendimento a partir de `inicio`, em ordem (gerador sem fim)."""
    if horario == '24x7':
        yield inicio, INFINITO
        return
    d = datetime.fromtimestamp(inicio, FUSO_BR).date()
    while True:
        if dia_util(d):
            a = datetime.combine(d, hora(INICIO_DIA), FUSO_BR).timestamp()
            b = datetime.combine(d, hora(FIM_DIA), FUSO_BR).timestamp()
            if b > inicio:
                yield max(a, inicio), b
        d += timedelta(days=1)


def _sem_pausas(a, b, pausas):
    """Partes de [a, b) fora das pausas (lista de [ini, fim], fim None = pausa em andamento)."""
    partes = [(a, b)]
    for p_ini, p_fim in pausas:
        p_fim = INFINITO if p_fim is None else p_fim
        novas = []
        for x, y in partes:
            if p_fim <= x or p_ini >= y:
                novas.append((x, y))
                continue
            if p_ini > x:
                novas.append((x, p_ini))
            if p_fim < y:
                novas.append((p_fim, y))
        partes = novas
    return partes


def consumido(inicio, ate, horario, pausas=()):
    """Segundos de atendimento entre `inicio` e `ate`, sem as pausas."""
    total = 0.0
    for a, b in _janelas(inicio, horario):
        if a >= ate:
            break
        for x, y in _sem_pausas(a, min(b, ate), pausas):
            total += y - x
    return total


def vencimento(inicio, segundos, horario, pausas=()):
    """Quando o prazo de `segundos` de atendimento termina; None se uma pausa em andamento o segura."""
    if any(f is None for _, f in pausas):
        return None
    falta = segundos
    for a, b in _janelas(inicio, horario):
        for x, y in _sem_pausas(a, b, pausas):
            if y - x >= falta:
                return x + falta
            falta -= y - x
    return None


def normalizar(config):
    """Valida a configuração do contrato: {'horario': ..., 'prazos': {prioridade: [resposta_h, solucao_h]}}."""
    if not isinstance(config, dict) or config.get('horario') not in HORARIOS:
        raise ValueError('Escolha o horário de atendimento.')
    prazos = {}
    for prio in PADRAO['prazos']:
        try:
            resp, sol = (float(str(v).replace(',', '.')) for v in (config.get('prazos') or {})[prio])
        except (KeyError, TypeError, ValueError):
            raise ValueError('Preencha os prazos de todas as prioridades, em horas.')
        if not (0 < resp <= 720 and 0 < sol <= 2000):
            raise ValueError('Os prazos precisam ser maiores que zero (até 720 h para a resposta e 2.000 h para a solução).')
        if resp > sol:
            raise ValueError('A primeira resposta não pode ter prazo maior que a solução.')
        prazos[prio] = [resp, sol]
    return {'horario': config['horario'], 'prazos': prazos}

