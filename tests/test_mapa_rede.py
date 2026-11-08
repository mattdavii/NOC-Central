import io

import pytest

import database
import mapa_rede

MAC = 'AA:00:00:00:40:01'
CAM = '11:22:33:44:55:66'
INV = '11:22:33:44:55:77'


def _sensor(cliente_id=2):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id, ip_gateway, ip_sensor, ping_gateway) VALUES (?, 'UFV', ?, '192.168.0.1', '192.168.0.18', 2)",
                 (MAC, cliente_id))
    for ip, mac, nome in (('192.168.0.1', 'AA:BB:CC:00:00:01', 'Gateway'), ('192.168.0.25', CAM, 'Intelbras'), ('192.168.0.38', INV, 'WAGO')):
        conn.execute("INSERT INTO dispositivos (sensor_mac, ip, mac, fabricante, status) VALUES (?, ?, ?, ?, 'online')", (MAC, ip, mac, nome))
    conn.commit(); conn.close()


def _arvore(mapa):
    sensor = {'ip_gateway': '192.168.0.1', 'ip_sensor': '192.168.0.18', 'nome_local': 'UFV', 'ping_gateway': 3}
    disp = [{'ip': '192.168.0.25', 'mac': CAM, 'fabricante': 'Intelbras', 'status': 'online'},
            {'ip': '192.168.0.38', 'mac': INV, 'fabricante': 'WAGO', 'status': 'offline'}]
    return {n['id']: n for n in mapa_rede.montar(sensor, disp, mapa_rede.validar(mapa))}


def test_default_tree_hangs_everything_on_the_gateway():
    a = _arvore({})
    assert a['gateway']['pai'] == 'internet'
    assert a[f'd:{CAM}']['pai'] == 'gateway' and a['sensor']['pai'] == 'gateway'
    assert not any(n['informado'] for n in a.values())


def test_added_switch_and_fiber_converter_change_the_tree():
    a = _arvore({'extras': [{'id': 'm:1', 'nome': 'Conversor fibra'}, {'id': 'm:2', 'nome': 'Switch campo'}],
                 'nos': {'m:1': {'tipo': 'conversor', 'pai': 'gateway', 'meio': 'cabo'},
                         'm:2': {'tipo': 'switch', 'pai': 'm:1', 'meio': 'fibra'},
                         f'd:{CAM}': {'pai': 'm:2', 'tipo': 'camera'}}})
    assert a['m:2']['pai'] == 'm:1' and a['m:2']['meio_rotulo'] == 'Fibra óptica'
    assert a[f'd:{CAM}']['pai'] == 'm:2' and a[f'd:{CAM}']['tipo_rotulo'] == 'Câmera' and a[f'd:{CAM}']['informado']


def test_cycles_fall_back_to_the_gateway():
    a = _arvore({'extras': [{'id': 'm:1', 'nome': 'A'}, {'id': 'm:2', 'nome': 'B'}],
                 'nos': {'m:1': {'pai': 'm:2'}, 'm:2': {'pai': 'm:1'}}})
    # Sem ciclo: subindo de qualquer nó chega na Internet.
    for ident in a:
        vistos, atual = set(), ident
        while atual:
            assert atual not in vistos
            vistos.add(atual); atual = a[atual]['pai']


def test_link_to_missing_device_falls_back():
    a = _arvore({'nos': {f'd:{CAM}': {'pai': 'd:99:99:99:99:99:99'}}})
    assert a[f'd:{CAM}']['pai'] == 'gateway'


@pytest.mark.parametrize('mapa', [
    {'extras': [{'id': 'x1', 'nome': 'a'}]},
    {'extras': [{'id': 'm:1', 'nome': ''}]},
    {'extras': [{'id': 'm:1', 'nome': 'a' * 61}]},
    {'nos': {'gateway': {'tipo': 'nave-espacial'}}},
    {'nos': {'gateway': {'pai': 'gateway'}}},
    {'nos': {'<script>': {'pai': 'gateway'}}},
])
def test_validation_refuses_bad_input(mapa):
    with pytest.raises(ValueError):
        mapa_rede.validar(mapa)


def test_svg_escapes_names_and_draws_dashed_wireless():
    a = mapa_rede.montar({'ip_gateway': '10.0.0.1', 'ip_sensor': '10.0.0.2', 'nome_local': 'S'},
                         [{'ip': '10.0.0.5', 'mac': CAM, 'nome_custom': '<b>x</b>', 'status': 'online'}],
                         mapa_rede.validar({'nos': {f'd:{CAM}': {'meio': 'wifi'}}}))
    desenho = mapa_rede.svg(a)
    assert desenho.startswith('<svg') and '&lt;b&gt;x&lt;/b&gt;' in desenho and 'stroke-dasharray="6 4"' in desenho


# ---------- rotas ----------

def test_put_and_get_roundtrip_with_tenant_rules(client, login):
    _sensor(cliente_id=2)
    cli = login(2, 'Cliente')
    mapa = {'extras': [{'id': 'm:1', 'nome': 'Switch rack'}], 'nos': {'m:1': {'tipo': 'switch'}, f'd:{CAM}': {'pai': 'm:1', 'meio': 'cabo'}}}
    r = cli.put(f'/api/v2/sensor/{MAC}/mapa_rede', json={'mapa': mapa})
    assert r.status_code == 200, r.json
    nos = {n['id']: n for n in r.json['arvore']}
    assert nos[f'd:{CAM}']['pai'] == 'm:1' and r.json['atualizado_por'] == 'user2'
    assert cli.get(f'/api/v2/sensor/{MAC}/mapa_rede').json['mapa']['extras'][0]['nome'] == 'Switch rack'
    assert login(3, 'Cliente').get(f'/api/v2/sensor/{MAC}/mapa_rede').status_code == 403
    assert login(3, 'Cliente').put(f'/api/v2/sensor/{MAC}/mapa_rede', json={'mapa': mapa}).status_code == 403


def test_put_rejects_invalid_map(client, login):
    _sensor()
    r = login().put(f'/api/v2/sensor/{MAC}/mapa_rede', json={'mapa': {'nos': {'gateway': {'tipo': 'x'}}}})
    assert r.status_code == 400 and 'desconhecido' in r.json['error']


def test_report_and_svg_and_survey_include_the_diagram(client, login):
    _sensor()
    m = login()
    m.put(f'/api/v2/sensor/{MAC}/mapa_rede', json={'mapa': {'extras': [{'id': 'm:1', 'nome': 'Switch rack'}],
                                                         'nos': {'m:1': {'tipo': 'switch'}, f'd:{CAM}': {'pai': 'm:1'}}}})
    rel = m.get(f'/relatorio/{MAC}').get_data(as_text=True)
    assert 'Diagrama da rede' in rel and 'Switch rack' in rel and 'completadas pela equipe' in rel
    svg = m.get(f'/api/v2/sensor/{MAC}/diagrama.svg')
    assert svg.status_code == 200 and svg.mimetype == 'image/svg+xml' and 'Switch rack' in svg.get_data(as_text=True)
    js = m.get(f'/api/v2/levantamento/{MAC}/json').json
    assert any(l['equipamento'] == 'Intelbras' and l['liga_em'] == 'Switch rack' for l in js['diagrama'])
    from openpyxl import load_workbook
    livro = load_workbook(io.BytesIO(m.get(f'/api/v2/levantamento/{MAC}/xlsx').data))
    assert 'Diagrama' in livro.sheetnames
    # Com nomes ocultos, o diagrama (que mostra nomes) não vai junto.
    assert 'diagrama' not in m.get(f'/api/v2/levantamento/{MAC}/json?ocultar_nomes=1').json
