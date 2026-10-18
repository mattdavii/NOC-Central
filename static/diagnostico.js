// Diagnóstico de rede por camadas, card da Starlink e linha do tempo (dados do /diagnostico).
(() => {
    const root = document.getElementById('diag-rede');
    if (!root) return;
    const mac = root.dataset.mac;
    const url = `/api/v2/sensor/${encodeURIComponent(mac)}/diagnostico`;
    const $ = id => document.getElementById(id);
    const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const num = (v, casas = 1) => v == null ? '--' : Number(v).toFixed(casas).replace(/\.0+$/, '');
    const hora = ts => ts ? new Date(ts * 1000).toLocaleString('pt-BR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}) : '--';
    const duracao = s => s < 3600 ? `${Math.max(1, Math.round(s / 60))} min` : `${Math.floor(s / 3600)}h ${Math.round(s % 3600 / 60)}min`;
    const ESTADOS = {ok: ['ok', 'Normal'], alerta: ['warn', 'Alerta'], critico: ['crit', 'Crítico'], desconhecido: ['na', 'Sem leitura']};
    const CAMADAS = [
        ['lan', 'fa-ethernet', 'Rede local (LAN)', 'gateway'],
        ['starlink', 'fa-satellite-dish', 'Starlink', null],
        ['provedor', 'fa-tower-broadcast', 'Provedor', 'provedor'],
        ['dns', 'fa-globe', 'DNS', null],
    ];
    const NOMES_ALERTAS = {
        motors_stuck: 'Motores travados', mast_not_near_vertical: 'Mastro fora do prumo', thermal_throttle: 'Limitando por calor',
        thermal_shutdown: 'Desligada por calor', slow_ethernet_speeds: 'Ethernet lenta', unexpected_location: 'Local inesperado',
        install_pending: 'Atualização pendente', is_heating: 'Aquecendo (neve)', power_supply_thermal_throttle: 'Fonte quente',
    };

    function ultimaPorPapel(serie) {
        const ult = {};
        for (const m of serie || []) ult[m.papel] = m;
        return ult;
    }

    function camadas(d) {
        const c = d.camadas || {};
        const ult = ultimaPorPapel(d.serie);
        const externos = (d.serie || []).filter(m => m.papel === 'externo').slice(-2);
        $('diag-atualizado').textContent = d.atualizado_em ? `janela de ${hora(d.atualizado_em)}` : 'sem medições';
        $('diag-camadas').innerHTML = CAMADAS.filter(([k]) => k !== 'starlink' || c.starlink).map(([k, icone, nome, papel]) => {
            const [cls, rotulo] = ESTADOS[c[k]] || ESTADOS.desconhecido;
            const m = papel && ult[papel];
            let detalhe = m ? `${esc(m.alvo)} · ${num(m.latencia_ms)} ms · ${num(m.perda_pct)}% perda · jitter ${num(m.jitter_ms)} ms` : '';
            if (k === 'provedor' && !m && externos.length) detalhe = `1º salto não identificado · externos ${externos.map(e => `${num(e.perda_pct)}%`).join(' / ')} perda`;
            if (k === 'starlink' && d.starlink?.enlace) detalhe = `PoP ${num(d.starlink.enlace.latencia_ms)} ms · ${num(d.starlink.enlace.perda_pct)}% perda`;
            return `<li class="saude-item estado-${cls}"><i class="fa-solid ${icone}"></i><span class="saude-rotulo">${nome}</span>
                <span class="saude-valor">${rotulo}</span>${detalhe ? `<span class="saude-detalhe">${detalhe}</span>` : ''}</li>`;
        }).join('') || '<li class="hist-empty">Aguardando a primeira janela de medições do sensor…</li>';
        if (d.status === 'offline') $('diag-camadas').insertAdjacentHTML('afterbegin',
            '<li class="saude-item estado-crit"><i class="fa-solid fa-plug-circle-xmark"></i><span class="saude-rotulo">Comunicação</span><span class="saude-valor">Sem comunicação</span></li>');

        $('diag-ativos').innerHTML = (d.ativos || []).map(a => `<div class="diag-ativo grav-${esc(a.gravidade)}">
            <strong>${esc(a.causa)}</strong> <span class="iface-tag">confiança ${esc(a.confianca)}</span>
            <p>${esc(a.mensagem)}</p></div>`).join('');
        const origem = {manual: 'definido à mão', 'edge-antena': 'pela antena', 'edge-ip': 'pelo IP público', 'edge-manual': 'no sensor'}[d.provedor_origem] || '';
        $('diag-provedor').textContent = d.provedor || 'não identificado';
        $('diag-provedor-origem').textContent = origem ? `(${origem})` : '';
        const r = d.rota;
        $('diag-rota').innerHTML = r ? `<i class="fa-solid fa-route"></i> Rota ${esc(r.ferramenta || '')} de ${hora(r.coletado_em)} (${esc(r.motivo)}): ` +
            (r.pior_salto ? `<strong class="txt-warn">salto ${r.pior_salto.ttl} (${esc(r.pior_salto.ip || '?')}) piorou</strong> — ${esc(r.pior_salto.motivo)}, ` +
                `antes ${num(r.pior_salto.antes?.rtt_ms)} ms/${num(r.pior_salto.antes?.perda_pct)}%, agora ${num(r.pior_salto.depois?.rtt_ms)} ms/${num(r.pior_salto.depois?.perda_pct)}%`
                : `${(r.saltos || []).length} saltos, sem piora`) : '';
    }

    function starlink(sl) {
        const corpo = $('sl-corpo');
        $('sl-firmware').textContent = sl?.firmware ? `fw ${sl.firmware}` : '';
        if (!sl || !sl.detectada) {
            corpo.innerHTML = `<div class="hist-empty"><i class="fa-solid fa-satellite-dish"></i> Starlink não detectada${sl?.motivo ? ` — ${esc(sl.motivo)}` : ''}.</div>
                <p class="sl-dica">Com o roteador Starlink em modo bypass, a antena só responde se houver rota para 192.168.100.1 (veja o README do Edge).</p>`;
            return;
        }
        const o = sl.obstrucao || {}, a = sl.alinhamento || {}, e = sl.enlace || {};
        const nivel = (v, lim1, lim2) => v == null ? 'na' : v >= lim2 ? 'crit' : v >= lim1 ? 'warn' : 'ok';
        const alertas = (sl.alertas || []).map(x => `<span class="iface-tag warn">${esc(NOMES_ALERTAS[x] || x.replace(/_/g, ' '))}</span>`).join('') || '<span class="txt-muted">Nenhum alerta</span>';
        const quedas = (sl.quedas || []).slice(-5).reverse().map(q => `<li><span class="mono">${hora(q.inicio)}</span> ${esc(q.causa)} · ${num(q.duracao_s)} s</li>`).join('');
        corpo.innerHTML = `
            <div class="sl-metricas">
                <div class="sl-metrica estado-${nivel(o.percentual, 2, 6)}"><span>Obstrução</span><strong>${num(o.percentual, 2)}%</strong>
                    <small>${o.atual ? 'obstruída agora' : 'céu livre agora'}</small></div>
                <div class="sl-metrica estado-${nivel(a.desvio_graus, 8, 15)}"><span>Desvio de alinhamento</span><strong>${num(a.desvio_graus)}°</strong>
                    <small>az ${num(a.azimute)}° → ${num(a.azimute_desejado)}° · el ${num(a.elevacao)}° → ${num(a.elevacao_desejada)}° · tilt ${num(a.tilt)}°</small></div>
                <div class="sl-metrica estado-${nivel(e.perda_pct, 5, 15)}"><span>Até o PoP</span><strong>${num(e.latencia_ms)} ms</strong>
                    <small>${num(e.perda_pct)}% de perda</small></div>
            </div>
            <div class="sl-alertas">${alertas}</div>
            ${quedas ? `<div class="sl-quedas-titulo">Quedas da última hora</div><ul class="sl-quedas">${quedas}</ul>` : ''}
            ${sl.modo === 'mock' ? `<p class="sl-dica">Dados simulados (modo mock: ${esc(sl.cenario)}).</p>` : ''}`;
    }

    function timeline(itens) {
        $('diag-timeline').innerHTML = (itens || []).length ? itens.map(d => {
            const fim = d.fim ? `${hora(d.fim)} · durou ${duracao(d.fim - d.inicio)}` : 'em andamento';
            const ev = Object.entries(d.evidencias || {}).filter(([, v]) => v != null && !(Array.isArray(v) && !v.length))
                .map(([k, v]) => `<li><span>${esc(k.replace(/_/g, ' '))}</span><span class="mono">${esc(Array.isArray(v) ? v.join(', ') : v)}</span></li>`).join('');
            return `<li class="tl-item grav-${esc(d.gravidade)} ${d.estado === 'ativo' ? 'ativo' : ''}">
                <div class="tl-head"><strong>${esc(d.causa)}</strong><span class="iface-tag">${esc(d.camada)}</span></div>
                <div class="tl-quando">${hora(d.inicio)} → ${fim} · confiança ${esc(d.confianca)}</div>
                <p>${esc(d.mensagem)}</p>
                ${ev ? `<details><summary>Evidências</summary><ul class="tl-ev">${ev}</ul>${d.acao ? `<p class="tl-acao"><i class="fa-solid fa-wrench"></i> ${esc(d.acao)}</p>` : ''}</details>` : ''}
            </li>`;
        }).join('') : '<li class="hist-empty">Nenhum diagnóstico registrado.</li>';
    }

    async function carregar() {
        try {
            const r = await fetch(url);
            if (!r.ok) return;
            const d = await r.json();
            camadas(d); starlink(d.starlink); timeline([...(d.grupos || []).filter(g => g.estado === 'ativo'), ...(d.linha_do_tempo || [])]);
        } catch (e) { /* tenta de novo no próximo ciclo */ }
    }

    $('diag-provedor-editar').addEventListener('click', async () => {
        const atual = $('diag-provedor').textContent;
        const nome = window.prompt('Provedor deste site (deixe vazio para identificar automaticamente):', atual === 'não identificado' ? '' : atual);
        if (nome === null) return;
        await fetch(`/api/v2/sensor/${encodeURIComponent(mac)}/provedor`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({provedor: nome})});
        carregar();
    });

    carregar();
    setInterval(carregar, 30000);
})();
