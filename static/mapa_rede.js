/* Diagrama da rede do sensor: desenha a árvore (vis-network) e edita o mapa
   (em quem cada equipamento liga, o tipo e o meio, e equipamentos que o sensor não vê). */
(() => {
    const ICONES = {internet: '☁️', roteador: '🖧', switch: '🔀', conversor: '🔁', ap: '📶', radio: '📡', onu: '💡',
        starlink: '🛰️', modem: '📟', firewall: '🧱', camera: '📷', nvr: '📼', computador: '💻', servidor: '🗄️',
        inversor: '☀️', datalogger: '📈', clp: '⚙️', medidor: '⚡', impressora: '🖨️', telefone: '☎️', iot: '🔌',
        dispositivo: '💻', outro: '▫️', sensor: '🛡️'};
    const INFRA = new Set(['roteador', 'switch', 'conversor', 'ap', 'radio', 'onu', 'starlink', 'modem', 'firewall']);
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[c]);
    let estado = {mapa: {extras: [], nos: {}}, arvore: [], tipos: {}, meios: {}};
    let rascunho = null, seq = 0;
    // Na Central, o desenho é o vis-network da tela do sensor. No painel local (sem internet, sem CDN),
    // o desenho é o SVG que o próprio sensor gera; MAPA_REDE_URL e MAPA_REDE_SVG apontam para as rotas locais.
    const urlMapa = () => window.MAPA_REDE_URL || `/api/v2/sensor/${encodeURIComponent(mac_id)}/mapa_rede`;
    const comVis = () => typeof nodes !== 'undefined' && typeof edges !== 'undefined';

    function corDoNo(no) {
        if (no.tipo === 'internet') return {background: '#05A9DC', border: '#05A9DC'};
        if (no.tipo === 'sensor') return {background: '#05A88C', border: '#05A88C'};
        if (no.adicionado || INFRA.has(no.tipo)) {
            if (no.status === 'offline') return {background: STATUS_CANVAS.crit, border: STATUS_CANVAS.crit};
            return {background: '#9CC3E6', border: '#5B8DB8'};
        }
        const c = {online: STATUS_CANVAS.ok, sem_icmp: STATUS_CANVAS.warn, offline: STATUS_CANVAS.crit}[no.status] || STATUS_CANVAS.unknown;
        return {background: c, border: c};
    }

    const curto = t => (t || '').length > 26 ? t.slice(0, 25) + '…' : (t || '');
    const ZOOM_MINIMO = 0.75;   // abaixo disso o texto das caixas fica ilegível

    function folhas() {
        const pais = new Set(estado.arvore.map(n => n.pai).filter(Boolean));
        return estado.arvore.filter(n => !pais.has(n.id)).length;
    }

    // A área cresce com a rede (uma linha por equipamento na ponta), em vez de encolher o desenho.
    function enquadrar() {
        const area = document.getElementById('network-topology');
        if (area) area.style.height = Math.max(450, Math.min(1800, folhas() * 54 + 80)) + 'px';
        setTimeout(() => {
            for (const grafo of [network, networkModal]) {
                if (!grafo) continue;
                grafo.redraw(); grafo.fit({animation: false});
                const apertado = grafo.getScale() < ZOOM_MINIMO;
                if (grafo === network) { const dica = document.getElementById('mapa-rede-dica'); if (dica) dica.hidden = !apertado; }
                if (apertado) {
                    // Rede larga demais (celular): mantém o texto legível e começa pela Internet; arraste para ver o resto.
                    const raiz = grafo.getPositions(['internet']).internet || {x: 0, y: 0};
                    const largura = grafo.body.container.clientWidth;
                    const centro = grafo.getViewPosition();
                    grafo.moveTo({scale: ZOOM_MINIMO, position: {x: raiz.x + (largura / 2 - 40) / ZOOM_MINIMO, y: centro.y}, animation: false});
                }
            }
        }, 60);
    }

    async function desenharSvg() {
        const alvo = document.getElementById('mapa-rede-svg');
        if (!alvo) return;
        try {
            const r = await fetch((window.MAPA_REDE_SVG || '/api/diagrama.svg') + '?t=' + Date.now());
            alvo.innerHTML = r.ok ? await r.text() : '<p class="hist-empty">Não foi possível desenhar o diagrama.</p>';
        } catch (e) { alvo.innerHTML = '<p class="hist-empty">Não foi possível desenhar o diagrama.</p>'; }
    }

    function escreverInfo() {
        const info = document.getElementById('mapa-rede-info');
        if (!info) return;
        const informados = estado.arvore.filter(n => n.informado).length + (estado.mapa.extras || []).length;
        let texto = informados
            ? `Diagrama com ligações informadas pela equipe${estado.atualizado_por ? ` (última edição: ${estado.atualizado_por})` : ''}. Clique em "Editar diagrama" para ajustar.`
            : 'Ligações observadas: todos aparecem no gateway. Use "Editar diagrama" para dizer o que passa por switch, conversor, rádio…';
        if (estado.pendente) texto += ' A edição está salva no sensor e vai para a Central assim que houver conexão.';
        info.textContent = texto;
    }

    function desenhar() {
        if (!comVis()) { desenharSvg(); escreverInfo(); return; }
        nodes.clear(); edges.clear();
        const status = {online: 'ON', sem_icmp: 'SEM ICMP', offline: 'OFF'};
        for (const no of estado.arvore) {
            const st = status[no.status] ? ` [${status[no.status]}]` : '';
            const linha2 = [no.tipo !== 'dispositivo' && no.tipo !== 'internet' ? no.tipo_rotulo : null, no.sub].filter(Boolean).join(' · ');
            nodes.add({
                id: no.id, ip: no.sub, mac: no.mac, rawName: no.nome, tipo: no.tipo, status: no.status,
                label: `${ICONES[no.tipo] || '▫️'} ${curto(no.nome)}${st}${linha2 ? '\n' + linha2 : ''}`,
                title: [no.tipo_rotulo, no.sub, no.mac ? 'MAC: ' + no.mac : null, no.fabricante ? 'Marca: ' + no.fabricante : null,
                        no.meio_rotulo ? 'Ligação: ' + no.meio_rotulo : null, no.informado ? 'Ligação informada pela equipe' : null,
                        no.status === 'offline' && no.visto_em ? 'Fora da rede · visto por último em ' + new Date(no.visto_em).toLocaleString('pt-BR') : null].filter(Boolean).join('\n'),
                shape: 'box', color: corDoNo(no),
                borderWidth: no.adicionado ? 2 : 1, shapeProperties: {borderDashes: no.adicionado ? [5, 4] : false},
                font: {color: '#0B1728', face: 'JetBrains Mono', bold: no.tipo === 'internet' || INFRA.has(no.tipo)}
            });
            if (no.pai) {
                const cor = no.meio === 'fibra' ? '#E39B2D' : (no.pai === 'internet' ? '#05A9DC' : cssVar('--border'));
                edges.add({from: no.pai, to: no.id, width: INFRA.has(no.tipo) || no.adicionado ? 2.5 : 1.5,
                           dashes: no.meio === 'wifi' || no.meio === 'radio', color: {color: cor},
                           label: no.meio_rotulo || undefined, font: {size: 10, color: cssVar('--text-muted'), strokeWidth: 0, align: 'middle'}});
            }
        }
        enquadrar();
        escreverInfo();
    }

    async function carregar() {
        const r = await fetch(urlMapa());
        if (!r.ok) throw new Error('Falha ao carregar o diagrama');
        estado = await r.json();
        desenhar();
    }

    // ---------- Editor ----------
    const opcoes = (mapa, sel) => Object.entries(mapa).map(([v, t]) => `<option value="${esc(v)}" ${v === sel ? 'selected' : ''}>${esc(t)}</option>`).join('');

    function nosDoRascunho() {
        // Árvore atual + os equipamentos adicionados no rascunho (ainda não salvos).
        const base = estado.arvore.filter(n => !n.adicionado);
        const extras = rascunho.extras.map(e => ({id: e.id, nome: e.nome || 'Novo equipamento', sub: e.obs, tipo: rascunho.nos[e.id]?.tipo || 'outro', adicionado: true}));
        return base.concat(extras);
    }

    function descendentes(id) {
        const filhos = {};
        for (const n of nosDoRascunho()) {
            const pai = (rascunho.nos[n.id] && rascunho.nos[n.id].pai) || (estado.arvore.find(a => a.id === n.id) || {}).pai;
            (filhos[pai] = filhos[pai] || []).push(n.id);
        }
        const fora = new Set([id]), pilha = [id];
        while (pilha.length) for (const f of filhos[pilha.pop()] || []) if (!fora.has(f)) { fora.add(f); pilha.push(f); }
        return fora;
    }

    function paiAtual(n) {
        return (rascunho.nos[n.id] && rascunho.nos[n.id].pai) || (estado.arvore.find(a => a.id === n.id) || {}).pai || 'gateway';
    }

    function linhaEditor(n) {
        const proibidos = descendentes(n.id);
        const destinos = nosDoRascunho().filter(o => !proibidos.has(o.id) && o.id !== 'internet');
        const pai = paiAtual(n), cfg = rascunho.nos[n.id] || {};
        const tipoFixo = n.tipo === 'sensor' || n.tipo === 'internet';
        const tipo = cfg.tipo || n.tipo;
        const nome = n.adicionado
            ? `<input type="text" class="me-nome" maxlength="60" value="${esc(n.nome === 'Novo equipamento' ? '' : n.nome)}" placeholder="Ex.: Switch do rack" aria-label="Nome do equipamento">
               <input type="text" class="me-obs" maxlength="120" value="${esc(n.sub || '')}" placeholder="Observação (opcional): portas, local…" aria-label="Observação">`
            : `<strong>${esc(n.nome)}</strong><small>${esc([n.sub, n.mac].filter(Boolean).join(' · '))}</small>`;
        return `<div class="me-linha${n.adicionado ? ' me-adicionado' : ''}" data-id="${esc(n.id)}">
            <div class="me-nome-col">${nome}</div>
            <label><span>Tipo</span><select class="me-tipo" ${tipoFixo ? 'disabled' : ''}>${tipoFixo ? `<option>${esc(n.tipo_rotulo || 'Sensor NexoPulse')}</option>` : opcoes(estado.tipos, tipo)}</select></label>
            <label><span>Liga em</span><select class="me-pai">${n.id === 'gateway' ? `<option value="internet" ${pai === 'internet' ? 'selected' : ''}>Internet</option>` : ''}${destinos.map(o => `<option value="${esc(o.id)}" ${o.id === pai ? 'selected' : ''}>${esc(o.nome)}${o.sub && !o.adicionado ? ' (' + esc(o.sub) + ')' : ''}</option>`).join('')}</select></label>
            <label><span>Meio</span><select class="me-meio"><option value="">Não informado</option>${opcoes(estado.meios, cfg.meio || '')}</select></label>
            ${n.adicionado ? '<button type="button" class="me-remover" title="Remover equipamento" aria-label="Remover equipamento">✕</button>' : '<span></span>'}
        </div>`;
    }

    function ordenados() {
        // Infra e adicionados primeiro, depois o sensor, depois os demais por IP.
        const peso = n => n.id === 'gateway' ? 0 : n.adicionado || INFRA.has((rascunho.nos[n.id] || {}).tipo || n.tipo) ? 1 : n.tipo === 'sensor' ? 2 : 3;
        const ip = s => (s || '').split('.').map(x => x.padStart(3, '0')).join('.');
        return nosDoRascunho().filter(n => n.id !== 'internet').sort((a, b) => peso(a) - peso(b) || ip(a.sub).localeCompare(ip(b.sub)) || a.nome.localeCompare(b.nome));
    }

    function renderEditor(foco) {
        const lista = document.getElementById('mapa-editor-lista');
        lista.innerHTML = ordenados().map(linhaEditor).join('');
        if (foco) {
            const alvo = lista.querySelector(`[data-id="${CSS.escape(foco)}"]`);
            if (alvo) { alvo.classList.add('me-foco'); alvo.scrollIntoView({block: 'center'}); alvo.querySelector('.me-pai')?.focus(); }
        }
    }

    function lerFormulario() {
        for (const linha of document.querySelectorAll('#mapa-editor-lista .me-linha')) {
            const id = linha.dataset.id, cfg = rascunho.nos[id] = rascunho.nos[id] || {};
            const tipo = linha.querySelector('.me-tipo'), pai = linha.querySelector('.me-pai'), meio = linha.querySelector('.me-meio');
            if (tipo && !tipo.disabled) cfg.tipo = tipo.value;
            cfg.pai = pai.value; cfg.meio = meio.value || undefined;
            const extra = rascunho.extras.find(e => e.id === id);
            if (extra) { extra.nome = linha.querySelector('.me-nome').value.trim(); extra.obs = linha.querySelector('.me-obs').value.trim() || undefined; }
        }
    }

    function abrirEditor(foco) {
        rascunho = JSON.parse(JSON.stringify(estado.mapa || {extras: [], nos: {}}));
        rascunho.extras = rascunho.extras || []; rascunho.nos = rascunho.nos || {};
        seq = rascunho.extras.reduce((m, e) => Math.max(m, parseInt(e.id.slice(2), 36) || 0), 0);
        const novoTipo = document.getElementById('mapa-editor-novo-tipo');
        if (!novoTipo.options.length) novoTipo.innerHTML = opcoes(estado.tipos, 'switch');
        document.getElementById('mapa-editor-erro').textContent = '';
        const hist = document.getElementById('mapa-editor-historico');
        if (hist) hist.hidden = true;
        document.getElementById('modalMapaRede').style.display = 'block';
        renderEditor(foco);
    }

    function fecharEditor() { document.getElementById('modalMapaRede').style.display = 'none'; }

    async function salvar() {
        lerFormulario();
        const erro = document.getElementById('mapa-editor-erro');
        if (rascunho.extras.some(e => !e.nome)) { erro.textContent = 'Dê um nome a cada equipamento adicionado.'; return; }
        // Só guarda o que difere do observado: tipo igual ao detectado e ligação no gateway não precisam ir.
        const nos = {};
        for (const [id, cfg] of Object.entries(rascunho.nos)) {
            const obs = estado.arvore.find(a => a.id === id);
            const item = {};
            const paiPadrao = id === 'gateway' ? 'internet' : 'gateway';
            if (cfg.pai && (cfg.pai !== paiPadrao || (obs && obs.informado))) item.pai = cfg.pai;
            if (cfg.tipo && (!obs || cfg.tipo !== obs.tipo || (estado.mapa.nos[id] || {}).tipo)) item.tipo = cfg.tipo;
            if (cfg.meio) item.meio = cfg.meio;
            if (id.startsWith('m:') || Object.keys(item).length) nos[id] = item;
        }
        const botao = document.getElementById('mapa-editor-salvar');
        botao.disabled = true; erro.textContent = '';
        try {
            const r = await fetch(urlMapa(), {
                method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mapa: {extras: rascunho.extras, nos}})});
            const dados = await r.json();
            if (!r.ok) throw new Error(dados.error || 'Não foi possível salvar o diagrama.');
            estado = dados; desenhar(); fecharEditor();
        } catch (e) { erro.textContent = e.message; } finally { botao.disabled = false; }
    }

    // ---------- Versões anteriores (só na Central) ----------
    async function abrirHistorico() {
        const caixa = document.getElementById('mapa-editor-historico');
        if (!caixa) return;
        caixa.hidden = false;
        caixa.innerHTML = '<p class="hist-empty">Carregando…</p>';
        try {
            const r = await fetch(urlMapa() + '/historico');
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || 'Falha ao carregar as versões.');
            const data = t => { const x = new Date(t); return isNaN(x) ? (t || '—') : x.toLocaleString('pt-BR', {dateStyle: 'short', timeStyle: 'short'}); };
            caixa.innerHTML = d.versoes.length ? `<p class="mapa-editor-ajuda">Cada vez que o diagrama muda, a versão anterior fica guardada aqui (as 30 últimas), inclusive a edição do painel local que perdeu para uma mais recente.</p>` +
                d.versoes.map(v => `<div class="me-versao"><div><strong>${esc(data(v.em))} · ${esc(v.por || 'sem autor')}</strong>
                    <small>${esc(v.origem)} · ${esc(v.motivo || '')} · ${v.ligacoes} ligações, ${v.adicionados} adicionados</small></div>
                    <button type="button" class="action-btn btn-secondary" data-restaurar="${v.id}">Restaurar</button></div>`).join('')
                : '<p class="hist-empty">Nenhuma versão anterior guardada ainda.</p>';
        } catch (e) { caixa.innerHTML = `<p class="mapa-editor-erro">${esc(e.message)}</p>`; }
    }

    async function restaurar(id) {
        if (!confirm('Restaurar esta versão do diagrama? A versão atual fica guardada no histórico.')) return;
        const r = await fetch(`${urlMapa()}/historico/${id}/restaurar`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { document.getElementById('mapa-editor-erro').textContent = d.error || 'Não foi possível restaurar.'; return; }
        estado = d; desenhar(); fecharEditor();
    }

    document.addEventListener('DOMContentLoaded', () => {
        const modal = document.getElementById('modalMapaRede');
        if (!modal) return;
        const botaoHist = modal.querySelector('#mapa-editor-ver-historico');
        if (botaoHist) botaoHist.onclick = abrirHistorico;
        const caixaHist = modal.querySelector('#mapa-editor-historico');
        if (caixaHist) caixaHist.addEventListener('click', ev => { const b = ev.target.closest('[data-restaurar]'); if (b) restaurar(b.dataset.restaurar); });
        modal.querySelector('#mapa-editor-fechar').onclick = fecharEditor;
        modal.querySelector('#mapa-editor-cancelar').onclick = fecharEditor;
        modal.querySelector('#mapa-editor-salvar').onclick = salvar;
        modal.querySelector('#mapa-editor-adicionar').onclick = () => {
            lerFormulario();
            const id = 'm:' + (++seq).toString(36);
            rascunho.extras.push({id, nome: ''});
            rascunho.nos[id] = {tipo: document.getElementById('mapa-editor-novo-tipo').value, pai: 'gateway'};
            renderEditor(id);
            document.querySelector(`#mapa-editor-lista [data-id="${CSS.escape(id)}"] .me-nome`)?.focus();
        };
        modal.querySelector('#mapa-editor-lista').addEventListener('change', ev => {
            // Mudar tipo ou ligação muda quem pode ser destino das outras linhas: redesenha mantendo o foco.
            if (ev.target.matches('.me-pai, .me-tipo')) { const id = ev.target.closest('.me-linha').dataset.id; lerFormulario(); renderEditor(); document.querySelector(`#mapa-editor-lista [data-id="${CSS.escape(id)}"] .${ev.target.className}`)?.focus(); }
        });
        modal.querySelector('#mapa-editor-lista').addEventListener('click', ev => {
            const botao = ev.target.closest('.me-remover');
            if (!botao) return;
            lerFormulario();
            const id = botao.closest('.me-linha').dataset.id;
            // Quem ligava no equipamento removido passa a ligar onde ele ligava.
            const paiDoRemovido = (rascunho.nos[id] || {}).pai || 'gateway';
            rascunho.extras = rascunho.extras.filter(e => e.id !== id);
            delete rascunho.nos[id];
            for (const cfg of Object.values(rascunho.nos)) if (cfg.pai === id) cfg.pai = paiDoRemovido;
            renderEditor();
        });
    });

    window.MapaRede = {carregar, abrirEditor, enquadrar};
})();
