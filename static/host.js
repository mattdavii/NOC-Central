// Métricas estendidas do host (host_info enviado pelo agente a cada ~30 s).
(() => {
    const ed = document.getElementById('location-editor');
    if (!ed || !document.getElementById('host')) return;
    const mac = ed.dataset.mac;
    const $ = id => document.getElementById(id);
    const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    let modoProcs = 'cpu', ultimo = null;

    const duracao = s => {
        if (s == null) return '--';
        const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
        return d ? `${d}d ${h}h` : h ? `${h}h ${m}min` : `${m} min`;
    };
    const nivel = p => p >= 90 ? 'crit' : p >= 75 ? 'warn' : 'ok';
    const barra = (p, cls = '') => `<div class="progress-bg"><div class="progress-fill ${cls} nivel-${nivel(p)}" style="width:${Math.min(p, 100)}%"></div></div>`;
    const mbps = v => v == null ? '--' : v >= 100 ? `${Math.round(v)} Mb/s` : `${v.toFixed(v < 10 ? 2 : 1)} Mb/s`;

    function host(info) {
        const sis = info.sistema || {};
        $('host-uptime').textContent = sis.uptime_s != null ? `Ligado há ${duracao(sis.uptime_s)}` : '--';
        $('host-load').textContent = sis.load ? `Carga ${sis.load.map(n => n.toFixed(2)).join(' · ')}` : '--';
        $('host-boot').textContent = sis.boot_time ? `Último boot: ${new Date(sis.boot_time * 1000).toLocaleString('pt-BR', {dateStyle: 'short', timeStyle: 'short'})}` : '';
        $('host-nucleos').textContent = sis.nucleos ? `· ${sis.nucleos} núcleos` : '';
        $('host-cores').innerHTML = (sis.cpu_por_nucleo || []).map((p, i) =>
            `<span class="core nivel-${nivel(p)}" title="Núcleo ${i}: ${p}%"><i style="height:${Math.max(p, 3)}%"></i></span>`).join('');
        const sw = info.swap;
        if (sw && sw.total_mb) {
            $('swap-text').textContent = `${sw.percentual}% · ${(sw.usado_mb / 1024).toFixed(1)} de ${(sw.total_mb / 1024).toFixed(1)} GB`;
            $('swap-fill').style.width = Math.min(sw.percentual, 100) + '%';
            $('swap-fill').className = `progress-fill nivel-${nivel(sw.percentual)}`;
        } else { $('swap-text').textContent = 'sem swap'; $('swap-fill').style.width = '0'; }
        const discos = info.discos || [];
        if (discos.length) {
            const maior = Math.max(...discos.map(d => d.percentual));
            $('disk-text').textContent = discos.length > 1 ? `${discos.length} partições · maior ${maior}%` : `${maior}%`;
            $('host-discos').innerHTML = discos.map(d => `
                <div class="disco"><div class="disco-head"><span class="mono">${esc(d.ponto)}</span>
                <span>${d.usado_gb} / ${d.total_gb} GB · <strong>${d.percentual}%</strong></span></div>${barra(d.percentual)}</div>`).join('');
        }
    }

    function item(icone, rotulo, valor, estado = 'ok', detalhe = '') {
        return `<li class="saude-item estado-${estado}"><i class="fa-solid ${icone}"></i><span class="saude-rotulo">${esc(rotulo)}</span>
            <span class="saude-valor">${valor}</span>${detalhe ? `<span class="saude-detalhe">${detalhe}</span>` : ''}</li>`;
    }

    function saude(info) {
        const itens = [];
        const falhas = info.servicos_falha;
        if (falhas) itens.push(item('fa-gears', 'Serviços', falhas.length ? `${falhas.length} com falha` : 'Todos ativos',
            falhas.length ? 'crit' : 'ok', falhas.map(esc).join(', ')));
        const upd = info.atualizacoes_pendentes;
        itens.push(item('fa-box-open', 'Atualizações', upd == null ? 'Sem informação' : upd ? `${upd} pendentes` : 'Em dia',
            upd == null ? 'na' : upd > 20 ? 'warn' : 'ok'));
        if (info.reinicio_necessario != null) itens.push(item('fa-rotate', 'Reinício', info.reinicio_necessario ? 'Necessário' : 'Não necessário',
            info.reinicio_necessario ? 'warn' : 'ok', info.reinicio_necessario ? 'Kernel ou bibliotecas atualizados' : ''));
        const dns = info.dns;
        if (dns) itens.push(item('fa-globe', 'DNS', dns.ok ? `Resolvendo · ${dns.ms} ms` : 'Falhando', dns.ok ? 'ok' : 'crit'));
        if (info.hora_sincronizada != null) itens.push(item('fa-clock-rotate-left', 'Relógio', info.hora_sincronizada ? 'Sincronizado (NTP)' : 'Fora de sincronia',
            info.hora_sincronizada ? 'ok' : 'warn'));
        const sm = info.smart;
        if (sm) {
            if (sm.disponivel) {
                const ruins = sm.discos.filter(d => !d.ok);
                itens.push(item('fa-hard-drive', 'SMART', ruins.length ? `${ruins.length} disco(s) reprovados` : `${sm.discos.length} disco(s) saudáveis`,
                    ruins.length ? 'crit' : 'ok', sm.discos.map(d => `${esc(d.modelo || d.dispositivo)}${d.temperatura != null ? ` · ${d.temperatura} °C` : ''}`).join('<br>')));
            } else itens.push(item('fa-hard-drive', 'SMART', 'Indisponível', 'na', esc(sm.motivo)));
        }
        const b = info.bateria;
        if (b) itens.push(item(b.na_tomada ? 'fa-plug' : 'fa-battery-half', 'Bateria', `${b.percentual}%${b.na_tomada ? ' · na tomada' : ''}`,
            !b.na_tomada && b.percentual < 20 ? 'crit' : 'ok', !b.na_tomada && b.restante_min ? `~${duracao(b.restante_min * 60)} restantes` : ''));
        if (info.ventoinhas) itens.push(item('fa-fan', 'Ventoinhas', info.ventoinhas.map(f => `${esc(f.nome)}: ${f.rpm} rpm`).join(' · ')));
        $('saude-lista').innerHTML = itens.join('');
    }

    function ifaces(info) {
        const lista = info.interfaces || [];
        $('host-ifaces').innerHTML = lista.length ? lista.map(i => {
            const problemas = (i.erros || 0) + (i.descartes || 0);
            const wifi = i.wifi_dbm != null ? `<span class="iface-tag ${i.wifi_dbm <= -75 ? 'warn' : ''}"><i class="fa-solid fa-wifi"></i> ${i.wifi_dbm} dBm</span>` : '';
            return `<div class="iface ${i.ativa ? '' : 'inativa'}">
                <div class="iface-head"><span class="mono">${esc(i.nome)}</span>${wifi}
                    <span class="iface-tag">${i.ativa ? (i.velocidade_mbps ? `${i.velocidade_mbps} Mb/s` : 'ativa') : 'desligada'}</span></div>
                <div class="iface-taxas"><span><i class="fa-solid fa-arrow-down"></i> ${mbps(i.rx_mbps)}</span><span><i class="fa-solid fa-arrow-up"></i> ${mbps(i.tx_mbps)}</span>
                    <span class="${problemas ? 'txt-warn' : 'txt-muted'}">${problemas ? `${i.erros} erros · ${i.descartes} descartes` : 'sem erros'}</span></div></div>`;
        }).join('') : '<div class="hist-empty">Nenhuma interface física encontrada.</div>';
    }

    function procs(info) {
        const lista = ((info.processos || {})[modoProcs]) || [];
        $('host-procs').innerHTML = lista.length ? lista.map(p =>
            `<tr><td>${esc(p.nome)}</td><td class="mono">${p.pid}</td><td>${p.cpu}%</td><td>${p.memoria}%</td></tr>`).join('')
            : '<tr><td colspan="4" class="hist-empty">Sem dados.</td></tr>';
    }

    document.getElementById('procs-modo').addEventListener('click', ev => {
        const b = ev.target.closest('button[data-modo]');
        if (!b) return;
        modoProcs = b.dataset.modo;
        document.querySelectorAll('#procs-modo button').forEach(x => x.classList.toggle('ativo', x === b));
        if (ultimo) procs(ultimo);
    });

    async function carregar() {
        try {
            const r = await fetch(`/api/v2/sensor_data/${encodeURIComponent(mac)}`);
            if (!r.ok) return;
            const dados = await r.json();
            if (!dados.host_info) return;
            ultimo = JSON.parse(dados.host_info);
            host(ultimo); saude(ultimo); ifaces(ultimo); procs(ultimo);
        } catch (e) { /* tenta de novo no próximo ciclo */ }
    }
    carregar();
    setInterval(carregar, 15000);
})();
