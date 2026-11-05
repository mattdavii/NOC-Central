"""NexoCampo na Central: o app entra com o usuário da MD, lista as empresas e envia a visita (com PDF e chamado)."""
import io
import json

from werkzeug.security import generate_password_hash

import database
import nexodesk

EMPRESA, OUTRA = 2, 3


def _preparar():
    conn = database.get_db()
    for t in ('campo_tokens', 'visitas_campo', 'chamado_anexos', 'chamado_mensagens', 'chamados'):
        conn.execute(f'DELETE FROM {t}')
    conn.execute('UPDATE clientes SET senha = ? WHERE id IN (1, 2)', (generate_password_hash('senha-certa'),))
    conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id) VALUES ('AA:00:00:00:80:01', 'Usina', ?)", (EMPRESA,))
    conn.commit(); conn.close()


def _visita(**extra):
    dados = {'formato': 'nexocampo.levantamento-rede', 'versao_formato': 2, 'id': 'uuid-1', 'local': 'Usina', 'tecnico': 'Vicente',
             'quando_ms': 1_762_000_000_000, 'dispositivos': [{'ip': '192.168.0.1'}, {'ip': '192.168.0.20'}],
             'wifi_ao_redor': [{}], 'velocidade': {'download_mbps': 187.2, 'upload_mbps': 94.0}}
    dados.update(extra)
    return dados


def _entrar(client, usuario='user1', senha='senha-certa'):
    return client.post('/api/v2/campo/entrar', json={'usuario': usuario, 'senha': senha, 'aparelho': 'Pixel 8'})


def test_so_a_equipe_da_md_entra_e_o_token_vale_ate_sair(client):
    _preparar()
    assert _entrar(client, senha='errada').status_code == 401
    assert _entrar(client, usuario='user2').status_code == 403          # cliente não usa o app de campo
    r = _entrar(client)
    assert r.status_code == 200 and r.json['token'].startswith('ncp_')
    token = r.json['token']
    conn = database.get_db()
    assert token not in json.dumps([dict(x) for x in conn.execute('SELECT * FROM campo_tokens').fetchall()])   # só o hash
    conn.close()
    assert client.get('/api/v2/campo/locais').status_code == 401
    locais = client.get('/api/v2/campo/locais', headers={'Authorization': f'Bearer {token}'})
    empresa = [e for e in locais.json['empresas'] if e['id'] == EMPRESA][0]
    assert empresa['locais'][0]['mac_id'] == 'AA:00:00:00:80:01'
    client.post('/api/v2/campo/sair', headers={'Authorization': f'Bearer {token}'})
    assert client.get('/api/v2/campo/locais', headers={'Authorization': f'Bearer {token}'}).status_code == 401


def test_visita_chega_atualiza_no_reenvio_e_vira_nota_no_chamado(client, login):
    _preparar()
    token = _entrar(client).json['token']
    cab = {'Authorization': f'Bearer {token}'}
    conn = database.get_db()
    md = dict(conn.execute('SELECT * FROM clientes WHERE id = 1').fetchone())
    numero = nexodesk.abrir(conn, md, {'empresa_id': EMPRESA, 'canal': 'whatsapp', 'solicitante': 'Carla', 'categoria': 'rede',
                                       'impacto': 'atrapalha', 'titulo': 'Wi-Fi fraco', 'descricao': '.'})
    conn.commit(); conn.close()

    def enviar(dados, **form):
        corpo = {'dados': json.dumps(dados), 'empresa_id': str(EMPRESA), **form,
                 'pdf': (io.BytesIO(b'%PDF-1.4 visita'), 'visita.pdf')}
        return client.post('/api/v2/campo/visitas', data=corpo, headers=cab, content_type='multipart/form-data')

    assert enviar(_visita(), sensor_mac='AA:00:00:00:99:99').status_code == 400        # local de outra empresa
    assert enviar({'id': 'x'}).status_code == 400                                       # não é um levantamento
    r = enviar(_visita(), sensor_mac='AA:00:00:00:80:01', chamado=str(numero))
    assert r.status_code == 201 and r.json['chamado'] == numero
    visita_id = r.json['numero']
    r2 = enviar(_visita(local='Usina Norte'), chamado=str(numero))
    assert r2.status_code == 200 and r2.json['numero'] == visita_id and r2.json['chamado'] is None   # mesma visita; não repete a nota

    conn = database.get_db()
    notas = conn.execute("SELECT texto FROM chamado_mensagens WHERE chamado_id = ? AND tipo = 'nota'", (numero,)).fetchall()
    assert len(notas) == 1 and f'Visita de campo #{visita_id}' in notas[0]['texto'] and '2 aparelhos' in notas[0]['texto']
    assert conn.execute('SELECT COUNT(*) AS n FROM chamado_anexos WHERE chamado_id = ?', (numero,)).fetchone()['n'] == 1
    conn.close()

    login()
    pagina = client.get('/campo').get_data(as_text=True)
    assert 'Usina Norte' in pagina and f'/campo/{visita_id}.pdf' in pagina and '2 aparelhos' in pagina
    assert client.get(f'/campo/{visita_id}.pdf').data.startswith(b'%PDF')
    assert json.loads(client.get(f'/campo/{visita_id}.json').data)['local'] == 'Usina Norte'
    login(EMPRESA, 'Cliente')
    assert client.get('/campo').status_code == 403
    assert client.get(f'/campo/{visita_id}.pdf').status_code == 403
