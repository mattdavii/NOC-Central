// Página "Saúde do servidor" (Administrador Master).
(() => {
    const $ = id => document.getElementById(id);
    const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const ESTADOS = {ok: ['ok', 'OK'], alerta: ['warn', 'Alerta'], critico: ['crit', 'Crítico'], na: ['na', '—']};
    const ROTULOS = {
        vigia_intervalo_min: ['Intervalo do vigia', 'min'], falhas_para_alertar: ['Falhas seguidas para alertar', ''],
        banco_latencia_alerta_ms: ['Latência do banco: alerta', 'ms'], banco_latencia_critico_ms: ['Latência do banco: crítico', 'ms'],
        memoria_alerta_pct: ['Memória: alerta', '%'], memoria_critico_pct: ['Memória: crítico', '%'],
        disco_alerta_pct: ['Disco: alerta', '%'], disco_critico_pct: ['Disco: crítico', '%'], backup_max_horas: ['Backup mais velho que', 'h'],
    };
    const ICONES = {
        banco_latencia: 'fa-gauge', banco_sessoes_presas: 'fa-lock', banco_tamanho: 'fa-database', banco_conexoes: 'fa-plug',
        telegram_fila: 'fa-paper-plane', guardiao: 'fa-shield-halved', memoria: 'fa-memory', vigia: 'fa-shield-dog', sensores: 'fa-satellite-dish',
        central: 'fa-globe', banco: 'fa-database', tailscale: 'fa-network-wired', tailscale_serve: 'fa-route', disco: 'fa-hard-drive', backup: 'fa-cloud-arrow-up',
    };
    const desde = ts => ts ? new Date(ts * 1000).toLocaleString('pt-BR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}) : '';

    function item(c) {
        const [cls, rotulo] = ESTADOS[c.estado] || ESTADOS.na;
        const extra = [c.detalhe, c.desde && c.estado !== 'ok' ? `desde ${desde(c.desde)}` : '', c.notificado ? 'alerta enviado' : '']
            .filter(Boolean).map(esc).join(' · ');
        return `<li class="saude-item estado-${cls}"><i class="fa-solid ${ICONES[String(c.chave).replace('vigia:', '')] || 'fa-circle-check'}"></i>
            <span class="saude-rotulo">${esc(c.rotulo || c.chave)}</span>
            <span class="saude-valor" title="${esc(rotulo)}">${esc(c.valor || rotulo)}</span>
            ${extra ? `<span class="saude-detalhe">${extra}</span>` : ''}</li>`;
    }

    function config(cfg, editaveis) {
        const form = $('saude-config');
        if (form.dataset.pronto) return;  // não sobrescreve o que está sendo editado
        form.dataset.pronto = '1';
        const ordem = [...Object.keys(ROTULOS).filter(k => k in editaveis), ...Object.keys(editaveis).filter(k => !(k in ROTULOS))];
        form.innerHTML = ordem.map(chave => [chave, editaveis[chave]]).map(([chave, [min, max]]) => {
            const [nome, unidade] = ROTULOS[chave] || [chave, ''];
            return `<label><span>${esc(nome)}</span><span class="saude-campo"><input type="number" name="${chave}" min="${min}" max="${max}" value="${esc(cfg[chave])}" required>${unidade ? `<small>${unidade}</small>` : ''}</span></label>`;
        }).join('') + '<button class="action-btn btn-primary" type="submit"><i class="fa-solid fa-floppy-disk"></i> Salvar</button>';
    }

    async function carregar() {
        try {
            const r = await fetch('/api/v2/saude');
            if (!r.ok) return;
            const d = await r.json();
            $('saude-central').innerHTML = d.central.map(item).join('');
            $('saude-vigia').innerHTML = d.vigia.length ? d.vigia.map(item).join('')
                : '<li class="hist-empty">O vigia ainda não reportou. Instale com <code>ops/vigia/install.sh</code> na máquina do banco.</li>';
            const ruins = [...d.central, ...d.vigia].filter(c => c.estado === 'alerta' || c.estado === 'critico');
            $('saude-resumo').textContent = ruins.length ? `${ruins.length} ponto(s) de atenção` : 'Tudo normal';
            $('saude-resumo').className = `iface-tag ${ruins.length ? 'warn' : ''}`;
            const p = d.processo || {};
            $('saude-processo').textContent = `v${d.versao}${p.versao_commit ? ` · ${p.versao_commit}` : ''} · no ar desde ${desde(p.iniciado_em)}`;
            config(d.config, d.editaveis);
        } catch (e) { /* tenta de novo no próximo ciclo */ }
    }

    $('saude-config').addEventListener('submit', async ev => {
        ev.preventDefault();
        const corpo = Object.fromEntries([...new FormData(ev.target)].map(([k, v]) => [k, Number(v)]));
        const r = await fetch('/api/v2/saude/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corpo)});
        const d = await r.json().catch(() => ({}));
        $('saude-config-msg').textContent = r.ok ? 'Configuração salva no banco. O vigia usa os valores novos no próximo teste.' : (d.error || 'Não foi possível salvar.');
    });

    carregar();
    setInterval(carregar, 30000);
})();
