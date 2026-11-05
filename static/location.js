(() => {
    const root = document.getElementById('location-editor');
    if (!root) return;
    const lat = document.getElementById('edit-lat'), lon = document.getElementById('edit-lon');
    const status = document.getElementById('location-status');
    let source = root.dataset.source || null, accuracy = root.dataset.accuracy ? Number(root.dataset.accuracy) : null;
    let changed = false, marker, map;
    const SOURCES = {browser: 'navegador', manual: 'manual', ip: 'internet (aproximada)', starlink: 'GPS da antena Starlink', windows: 'localização do Windows do sensor'};
    const fonte = s => SOURCES[s] || s || 'não informada';
    const precisao = p => p != null ? ` · precisão ~${Math.round(p)} m` : '';
    function quando(iso) {
        // Postgres manda "2026-10-18 12:21:02.32+00:00", SQLite sem fuso (UTC).
        if (!iso) return '';
        let txt = iso.trim().replace(' ', 'T');
        if (!/[zZ]|[+-]\d\d:?\d\d$/.test(txt)) txt += 'Z';
        const d = new Date(txt);
        return isNaN(d) ? '' : ` · atualizada em ${d.toLocaleString('pt-BR', {dateStyle: 'short', timeStyle: 'short'})}`;
    }
    status.textContent = `Fonte: ${fonte(source)}${precisao(accuracy)}${quando(root.dataset.updated)}`;
    const valid = () => lat.value !== '' && lon.value !== '' && Number.isFinite(Number(lat.value)) && Number.isFinite(Number(lon.value)) && Math.abs(Number(lat.value)) <= 90 && Math.abs(Number(lon.value)) <= 180;
    function showMarker() {
        if (!map || !valid()) return;
        const point = [Number(lat.value), Number(lon.value)];
        if (!marker) marker = L.marker(point).addTo(map); else marker.setLatLng(point);
        map.setView(point, Math.max(map.getZoom(), 13));
    }
    function setLocation(a, b, from, precision) {
        lat.value = Number(a).toFixed(6); lon.value = Number(b).toFixed(6);
        source = from; accuracy = precision; changed = true;
        status.textContent = `Fonte: ${fonte(from)}${precisao(precision)} · não salva ainda`;
        showMarker();
    }
    if (typeof L !== 'undefined') {
        map = L.map('location-map').setView(valid() ? [Number(lat.value),Number(lon.value)] : [-14.235,-51.925], valid() ? 13 : 3);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {attribution:'© OpenStreetMap', maxZoom:19}).addTo(map);
        map.on('click', e => setLocation(e.latlng.lat,e.latlng.lng,'manual',null));
        showMarker();
    } else document.getElementById('location-map').textContent = 'Mapa indisponível. Você pode informar coordenadas abaixo.';
    [lat,lon].forEach(input => input.addEventListener('input', () => {source='manual';accuracy=null;changed=true;status.textContent='Coordenadas manuais. Salve para aplicar.';}));
    document.getElementById('capture-location').onclick = () => {
        if (!window.isSecureContext || !navigator.geolocation) {status.textContent='Captura requer HTTPS e um navegador com geolocalização. Use coordenadas ou mapa.';return;}
        const button = document.getElementById('capture-location'); button.disabled = true;
        status.textContent='Aguardando permissão e localização do navegador…';
        navigator.geolocation.getCurrentPosition(position => {
            setLocation(position.coords.latitude,position.coords.longitude,'browser',position.coords.accuracy); button.disabled=false;
        }, error => { status.textContent=({1:'Permissão negada. Autorize a localização no navegador ou use o mapa.',2:'Localização indisponível. Tente com o celular no local.',3:'Tempo de captura esgotado. Tente novamente ou use coordenadas.'})[error.code] || 'Falha na captura.';button.disabled=false; }, {enableHighAccuracy:true, timeout:20000, maximumAge:0});
    };
    const botaoSensor = document.getElementById('capture-sensor');
    if (botaoSensor) botaoSensor.onclick = async () => {
        const base = `/api/v2/sensor/${encodeURIComponent(root.dataset.mac)}/capturar_local`;
        botaoSensor.disabled = true;
        status.textContent = 'Pedindo a posição ao sensor…';
        try {
            const r = await fetch(base, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
            const pedido = await r.json();
            if (!r.ok) throw new Error(pedido.error || 'Não foi possível pedir a localização ao sensor.');
            const limite = Date.now() + (pedido.prazo_s || 120) * 1000;
            while (Date.now() < limite) {
                await new Promise(ok => setTimeout(ok, 2500));
                const res = await (await fetch(`${base}/${pedido.id}`)).json();
                if (res.status === 'aguardando') {
                    status.textContent = res.entregue ? 'O sensor está buscando a posição (Starlink, Windows ou internet)…' : 'Aguardando o sensor buscar o pedido…';
                    continue;
                }
                if (res.status === 'ok') {
                    setLocation(res.latitude, res.longitude, res.location_source, res.accuracy_m);
                    let nota = res.location_source === 'ip' ? ` Aproximada pela internet${res.referencia ? ` (${res.referencia})` : ''}: pode errar por dezenas de km. Confira no mapa.` : '';
                    if (res.tentativas?.length && res.location_source !== 'starlink') nota += ` Antes: ${res.tentativas.join(' ')}`;
                    status.textContent += '.' + nota;
                } else {
                    status.textContent = 'O sensor não conseguiu a posição. ' + (res.tentativas || []).join(' ');
                }
                return;
            }
            status.textContent = 'O sensor não respondeu em 2 minutos. Ele está online? Tente de novo ou use o mapa.';
        } catch (error) {
            status.textContent = error.message;
        } finally {
            botaoSensor.disabled = false;
        }
    };
    document.getElementById('clear-location').onclick = () => {lat.value='';lon.value='';source=null;accuracy=null;changed=true;if(marker){map.removeLayer(marker);marker=null;}status.textContent='Localização removida. Salve para aplicar.';};
    window.salvarConfig = async () => {
        if ((lat.value !== '' || lon.value !== '') && !valid()) {status.textContent='Informe latitude entre -90 e 90 e longitude entre -180 e 180.';return;}
        const data = {mac_id:root.dataset.mac,nome:document.getElementById('edit-nome').value};
        if(changed) Object.assign(data,{latitude:lat.value===''?null:Number(lat.value),longitude:lon.value===''?null:Number(lon.value),location_source:source,accuracy_m:accuracy});
        const button = document.getElementById('save-location');button.disabled=true;
        try {const response=await fetch('/api/v2/configurar_sensor',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw new Error(result.error || 'Não foi possível salvar.');changed=false;status.textContent=`Salvo. Fonte: ${fonte(source)}${precisao(accuracy)}${quando(new Date().toISOString())}`;}catch(error){status.textContent=error.message;}finally{button.disabled=false;}
    };
})();
