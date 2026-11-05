from datetime import date, datetime

import pytest

import sla
from hub import FUSO_BR


def ts(a, m, d, h, mi=0):
    return datetime(a, m, d, h, mi, tzinfo=FUSO_BR).timestamp()


def test_national_holidays():
    f = sla.feriados_nacionais(2026)
    assert date(2026, 4, 3) in f                     # Sexta-feira Santa (Páscoa em 05/04/2026)
    assert date(2026, 11, 20) in f and date(2026, 11, 2) in f and date(2026, 11, 15) in f
    assert date(2026, 2, 17) not in f                # Carnaval é ponto facultativo
    assert date(2023, 11, 20) not in sla.feriados_nacionais(2023)


def test_business_hours_cross_the_weekend():
    # Sexta 06/11/2026 às 17h, 4 h de atendimento: 1 h na sexta e 3 h na segunda → segunda 11h.
    assert sla.vencimento(ts(2026, 11, 6, 17), 4 * 3600, 'comercial') == ts(2026, 11, 9, 11)
    # Aberto fora do horário (sábado) começa a contar na segunda às 8h.
    assert sla.vencimento(ts(2026, 11, 7, 10), 2 * 3600, 'comercial') == ts(2026, 11, 9, 10)


def test_holiday_does_not_count():
    # Quinta 19/11 às 17h + 4 h: sexta 20/11 é feriado → segunda 23/11 às 11h.
    assert sla.vencimento(ts(2026, 11, 19, 17), 4 * 3600, 'comercial') == ts(2026, 11, 23, 11)


def test_24x7_and_consumed_time():
    assert sla.vencimento(ts(2026, 11, 7, 22), 4 * 3600, '24x7') == ts(2026, 11, 8, 2)
    assert sla.consumido(ts(2026, 11, 6, 17), ts(2026, 11, 9, 9), 'comercial') == 2 * 3600


def test_pauses():
    pausas = [[ts(2026, 11, 9, 10), ts(2026, 11, 9, 14)]]
    assert sla.vencimento(ts(2026, 11, 9, 9), 4 * 3600, 'comercial', pausas) == ts(2026, 11, 9, 17)
    assert sla.consumido(ts(2026, 11, 9, 9), ts(2026, 11, 9, 15), 'comercial', pausas) == 2 * 3600
    assert sla.vencimento(ts(2026, 11, 9, 9), 4 * 3600, 'comercial', [[ts(2026, 11, 9, 10), None]]) is None


def test_contract_validation():
    ok = sla.normalizar({'horario': '24x7', 'prazos': {'critica': ['0,5', 4], 'alta': [2, 16], 'media': [4, 24], 'baixa': [8, 40]}})
    assert ok['prazos']['critica'] == [0.5, 4.0]
    for ruim in ({'horario': 'x', 'prazos': sla.PADRAO['prazos']},
                 {'horario': 'comercial', 'prazos': {'critica': [1, 8]}},
                 {'horario': 'comercial', 'prazos': {**sla.PADRAO['prazos'], 'alta': [20, 16]}},
                 {'horario': 'comercial', 'prazos': {**sla.PADRAO['prazos'], 'baixa': [0, 40]}}):
        with pytest.raises(ValueError):
            sla.normalizar(ruim)
