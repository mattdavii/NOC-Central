def test_get_with_json_content_type_and_no_body_is_accepted(client):
    # O agente envia Content-Type: application/json em todas as chamadas.
    response = client.get('/api/v2/ips_customizados/AA:BB:CC:DD:EE:FF',
                          headers={'Content-Type': 'application/json', 'X-NOC-Sensor-ID': 'AA:BB:CC:DD:EE:FF'})
    assert response.status_code == 200


def test_post_with_invalid_json_is_still_rejected(client):
    response = client.post('/api/v2/report_data', data='[]', content_type='application/json')
    assert response.status_code == 400
