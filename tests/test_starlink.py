import types

import pytest
import starlink


def test_desvio_angular():
    assert starlink.desvio_angular(10, 60, 10, 60) == 0
    assert starlink.desvio_angular(0, 0, 90, 0) == 90
    assert starlink.desvio_angular(10, None, 10, 60) is None


@pytest.mark.parametrize('cenario', sorted(starlink.CENARIOS_MOCK))
def test_mock_has_full_shape(cenario):
    dados = starlink._coletar_mock(cenario)
    assert dados['detectada'] and dados['modo'] == 'mock' and dados['firmware']
    assert {'percentual', 'atual'} <= set(dados['obstrucao'])
    assert {'desvio_graus', 'tilt'} <= set(dados['alinhamento'])
    assert {'latencia_ms', 'perda_pct'} <= set(dados['enlace'])


def test_mock_scenarios_differ():
    assert starlink._coletar_mock('obstruida')['obstrucao']['percentual'] == 6.0
    assert starlink._coletar_mock('desalinhada')['alinhamento']['desvio_graus'] > 10
    assert 'mast_not_near_vertical' in starlink._coletar_mock('desalinhada')['alertas']
    assert starlink._coletar_mock('normal')['alertas'] == []


def test_missing_library_marks_not_detected(monkeypatch):
    monkeypatch.setattr(starlink, 'starlink_grpc', None)
    monkeypatch.setattr(starlink, 'MODO', 'auto')
    dados = starlink.coletar()
    assert dados['detectada'] is False and 'não instalada' in dados['motivo']
    assert starlink.proximo_intervalo(dados) == starlink.INTERVALO_SEM_ANTENA


def test_off_mode_returns_none(monkeypatch):
    monkeypatch.setattr(starlink, 'MODO', 'off')
    assert starlink.coletar() is None


class _Contexto:
    def __init__(self, target=None):
        self.fechado = False

    def close(self):
        self.fechado = True


def _fake_grpc(status, alertas, falhar=False, bruto=None, historico=None):
    def status_data(context=None):
        if falhar:
            raise RuntimeError('sem rota')
        return status, {}, alertas
    return types.SimpleNamespace(ChannelContext=_Contexto, status_data=status_data,
                                 get_status=lambda context=None: bruto,
                                 get_history=lambda context=None: historico)


def test_real_collector_tolerates_missing_fields(monkeypatch):
    # Firmware que não manda alinhamento, obstrução nem histórico.
    fake = _fake_grpc({'software_version': 'fw-1', 'state': 'CONNECTED', 'fraction_obstructed': float('nan')},
                      {'alert_motors_stuck': False, 'alert_novo_alerta': True})
    monkeypatch.setattr(starlink, 'starlink_grpc', fake)
    monkeypatch.setattr(starlink, '_contexto', None)
    monkeypatch.setattr(starlink, 'MODO', 'auto')
    dados = starlink.coletar()
    assert dados['detectada'] and dados['firmware'] == 'fw-1'
    assert dados['obstrucao']['percentual'] is None
    assert dados['alinhamento']['desvio_graus'] is None
    assert dados['alertas'] == ['novo_alerta']  # alertas de firmware novo entram sem mudar o código
    assert dados['quedas'] == []


def test_real_collector_reads_alignment_and_outages(monkeypatch):
    stats = types.SimpleNamespace(boresight_azimuth_deg=20.0, boresight_elevation_deg=60.0,
                                  desired_boresight_azimuth_deg=20.0, desired_boresight_elevation_deg=70.0, tilt_angle_deg=3.5)
    agora = __import__('time').time()
    gps_ns = int((agora - 300 - 315964800 + 18) * 1e9)
    queda = types.SimpleNamespace(start_timestamp_ns=gps_ns, duration_ns=int(7e9), cause=1)
    fake = _fake_grpc({'software_version': 'fw-2', 'fraction_obstructed': 0.05, 'currently_obstructed': True,
                       'pop_ping_drop_rate': 0.1, 'pop_ping_latency_ms': 48.2}, {},
                      bruto=types.SimpleNamespace(alignment_stats=stats),
                      historico=types.SimpleNamespace(outages=[queda]))
    monkeypatch.setattr(starlink, 'starlink_grpc', fake)
    monkeypatch.setattr(starlink, '_contexto', None)
    monkeypatch.setattr(starlink, 'MODO', 'auto')
    dados = starlink.coletar()
    assert dados['obstrucao'] == {'fracao': 0.05, 'percentual': 5.0, 'atual': True, 'duracao_media_s': None, 'intervalo_medio_s': None}
    assert dados['alinhamento']['desvio_graus'] == 10.0 and dados['alinhamento']['tilt'] == 3.5
    assert dados['enlace'] == {'latencia_ms': 48.2, 'perda_pct': 10.0}
    assert len(dados['quedas']) == 1 and abs(dados['quedas'][0]['inicio'] - (agora - 300)) < 2
    assert dados['quedas'][0]['duracao_s'] == 7.0


def test_unreachable_dish_is_not_detected(monkeypatch):
    monkeypatch.setattr(starlink, 'starlink_grpc', _fake_grpc({}, {}, falhar=True))
    monkeypatch.setattr(starlink, '_contexto', None)
    monkeypatch.setattr(starlink, 'MODO', 'auto')
    dados = starlink.coletar()
    assert dados['detectada'] is False and 'não respondeu' in dados['motivo']
