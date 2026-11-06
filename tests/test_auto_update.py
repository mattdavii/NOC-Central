import io
import os
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as central
import database
import nexopulse_edge as agent

MAC = 'AA:00:00:00:30:01'
SENHA = 'senha-de-teste-123'
RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _limpar():
    central._falhas_senha.clear()
    conn = database.get_db()
    conn.execute('DELETE FROM acoes_remotas WHERE sensor_mac=?', (MAC,))
    conn.commit(); conn.close()
    yield
    central._falhas_senha.clear()


def _sensor(**colunas):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local) VALUES (?, 'Rack')", (MAC,))
    for coluna, valor in colunas.items():
        conn.execute(f'UPDATE sensores SET {coluna}=? WHERE mac_id=?', (valor, MAC))
    conn.commit(); conn.close()


def _relatar(client, **extra):
    payload = {'mac_id': MAC, 'agent_build': 'antiga', 'auto_update_guard': True, 'build_recusado': ''}
    payload.update(extra)
    assert client.post('/api/v2/report_data', json=payload).status_code == 200


def _janela(monkeypatch, hora=4, minuto=59):
    monkeypatch.setattr(central, '_agora_brasil', lambda: datetime(2026, 10, 20, hora, minuto, tzinfo=central.FUSO_BRASIL))


def _q(sql, *args):
    conn = database.get_db()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _mensagens():
    return [r['message'] for r in _q('SELECT message FROM telegram_outbox')]


# ---------------------------------------------------------------- Central

def test_package_carries_agent_build_and_guard(client):
    nomes = {}
    with tarfile.open(fileobj=io.BytesIO(client.get('/agente/pacote.tar.gz').data), mode='r:gz') as tar:
        for membro in tar.getmembers():
            nomes[membro.name] = tar.extractfile(membro).read() if membro.isfile() else b''
    assert nomes['nexopulse-edge/VERSION'].decode().strip() == central.AGENT_BUILD
    assert 'nexopulse-edge/linux/nexopulse-guard.sh' in nomes
    assert central.AGENT_BUILD.startswith(central.CENTRAL_VERSION + '+')
    assert client.get('/api/v2/agent_versao').json['build'] == central.AGENT_BUILD


def test_outdated_sensor_is_updated_in_its_window_once(client, monkeypatch):
    _sensor()
    _janela(monkeypatch)
    _relatar(client)
    _relatar(client)
    acoes = _q('SELECT id, acao, solicitante FROM acoes_remotas WHERE sensor_mac=?', MAC)
    assert len(acoes) == 1 and acoes[0]['acao'] == 'atualizar_agente' and acoes[0]['solicitante'] == 'automático'
    # O primeiro relatório já leva o comando de volta; não sobra nada na fila.
    assert _q('SELECT comando FROM comandos_pendentes WHERE sensor_mac=?', MAC) == []
    assert any('ATUALIZAÇÃO AUTOMÁTICA' in m for m in _mensagens())


@pytest.mark.parametrize('caso', ['fora_da_janela', 'sem_guarda', 'desligada', 'em_dia', 'recusada'])
def test_no_automatic_update_when_not_allowed(client, monkeypatch, caso):
    _sensor(auto_atualizar=0 if caso == 'desligada' else 1)
    _janela(monkeypatch, hora=10 if caso == 'fora_da_janela' else 4)
    extra = {}
    if caso == 'sem_guarda':
        extra['auto_update_guard'] = False
    if caso == 'em_dia':
        extra['agent_build'] = central.AGENT_BUILD
    if caso == 'recusada':
        extra['build_recusado'] = central.AGENT_BUILD
    _relatar(client, **extra)
    assert _q('SELECT id FROM acoes_remotas WHERE sensor_mac=?', MAC) == []


def test_new_build_and_rollback_are_announced(client, monkeypatch):
    _sensor(agent_build='v1')
    _janela(monkeypatch, hora=12)
    _relatar(client, agent_build='v2')
    assert any('SENSOR ATUALIZADO' in m and 'v1' in m and 'v2' in m for m in _mensagens())
    _relatar(client, agent_build='v2', build_recusado='v3')
    assert any('ATUALIZAÇÃO DESFEITA' in m and 'v3' in m for m in _mensagens())
    linha = _q('SELECT agent_build, build_recusado, auto_update_guard FROM sensores WHERE mac_id=?', MAC)[0]
    assert linha == {'agent_build': 'v2', 'build_recusado': 'v3', 'auto_update_guard': 1}


def test_toggle_requires_master_password_and_shows_versions(client, login):
    _sensor()
    conn = database.get_db()
    conn.execute('UPDATE clientes SET senha=? WHERE id=1', (generate_password_hash(SENHA),))
    conn.commit(); conn.close()
    master = login()
    assert master.post(f'/api/v2/sensor/{MAC}/auto_atualizar', json={'ligar': False, 'senha': 'errada'}).status_code == 401
    assert master.post(f'/api/v2/sensor/{MAC}/auto_atualizar', json={'ligar': False, 'senha': SENHA}).json['auto_atualizar'] is False
    dados = master.get(f'/api/v2/sensor/{MAC}/acoes').json
    assert dados['auto_atualizar'] is False and dados['build_central'] == central.AGENT_BUILD
    assert login(2, 'Cliente').post(f'/api/v2/sensor/{MAC}/auto_atualizar', json={'ligar': True, 'senha': SENHA}).status_code == 403


# ---------------------------------------------------------------- Sensor

def _pacote(arquivos):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for nome, conteudo in arquivos.items():
            info = tarfile.TarInfo(f'nexopulse-edge/{nome}')
            info.size = len(conteudo)
            tar.addfile(info, io.BytesIO(conteudo))
    return buffer.getvalue()


def test_package_is_applied_with_backup_and_can_be_restored(tmp_path):
    (tmp_path / 'nexopulse_edge.py').write_text('VELHO')
    (tmp_path / 'sensor_key.txt').write_text('minha-chave')
    dados = _pacote({'VERSION': b'v2\n', 'nexopulse_edge.py': b'NOVO', 'sensor_key.txt': b'chave-do-pacote'})
    assert agent._aplicar_pacote(dados, str(tmp_path)) == 'v2'
    assert (tmp_path / 'nexopulse_edge.py').read_text() == 'NOVO'
    assert (tmp_path / 'sensor_key.txt').read_text() == 'minha-chave'
    assert (tmp_path / '.atualizacao/anterior/nexopulse_edge.py').read_text() == 'VELHO'
    agent._restaurar_anterior(str(tmp_path))
    assert (tmp_path / 'nexopulse_edge.py').read_text() == 'VELHO'


def test_first_contact_confirms_pending_update_only_in_new_version(tmp_path, monkeypatch):
    estado = tmp_path / '.atualizacao'
    estado.mkdir()
    (estado / 'pendente').write_text('v2\nv1\n')
    (estado / 'tentativas').write_text('1')
    monkeypatch.setattr(agent, 'PASTA_ATUALIZACAO', str(estado))
    monkeypatch.setattr(agent, 'log_local_event', lambda *a, **k: None)
    # O processo antigo (v1), que ainda roda entre instalar e reiniciar, não confirma.
    monkeypatch.setattr(agent, 'BUILD_AGENTE', 'v1')
    agent._confirmar_atualizacao()
    assert (estado / 'pendente').exists()
    monkeypatch.setattr(agent, 'BUILD_AGENTE', 'v2')
    agent._confirmar_atualizacao()
    assert not (estado / 'pendente').exists() and not (estado / 'tentativas').exists()


def _rodar_guarda(raiz):
    return subprocess.run(['bash', str(raiz / 'linux/nexopulse-guard.sh')], capture_output=True, text=True, check=True).stdout


def test_guard_restores_previous_version_after_failed_starts(tmp_path):
    (tmp_path / 'linux').mkdir()
    shutil.copy(RAIZ / 'linux/nexopulse-guard.sh', tmp_path / 'linux/nexopulse-guard.sh')
    assert _rodar_guarda(tmp_path) == ''  # sem atualização pendente, não faz nada
    (tmp_path / 'nexopulse_edge.py').write_text('NOVO')
    anterior = tmp_path / '.atualizacao/anterior'
    anterior.mkdir(parents=True)
    (anterior / 'nexopulse_edge.py').write_text('VELHO')
    (tmp_path / '.atualizacao/pendente').write_text('v2\nv1\n')
    _rodar_guarda(tmp_path); _rodar_guarda(tmp_path)
    assert (tmp_path / 'nexopulse_edge.py').read_text() == 'NOVO'
    assert 'restaurada' in _rodar_guarda(tmp_path)
    assert (tmp_path / 'nexopulse_edge.py').read_text() == 'VELHO'
    assert (tmp_path / '.atualizacao/recusado').read_text().strip() == 'v2'
    assert not (tmp_path / '.atualizacao/pendente').exists()
