// Ações remotas da tela do sensor (só aparece para o Administrador Master).
(() => {
    const root = document.getElementById('acoes-remotas');
    if (!root) return;
    const mac = root.dataset.mac;
    const base = `/api/v2/sensor/${encodeURIComponent(mac)}`;
    const botoes = document.getElementById('acoes-botoes');
    const historico = document.getElementById('acoes-historico');
    const chave = document.getElementById('acoes-permitir');
    const chaveTexto = document.getElementById('acoes-permitir-texto');
    const auto = document.getElementById('auto-atualizar');
    const versaoInfo = document.getElementById('versao-info');
    const modal = document.getElementById('modal-senha');
    const form = document.getElementById('modal-senha-form');
    const campo = document.getElementById('modal-senha-input');
    const erro = document.getElementById('modal-senha-erro');
    const STATUS = {pendente: 'Na fila', enviada: 'Executando', concluida: 'Concluída', erro: 'Falhou'};
    const PERIGOSAS = new Set(['atualizar_sistema', 'reiniciar_maquina']);
    let nomes = {}, atualizando = null;

    const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const quando = iso => {
        if (!iso) return '';
        let txt = String(iso).trim().replace(' ', 'T');
        if (!/[zZ]|[+-]\d\d:?\d\d$/.test(txt)) txt += 'Z';
        const d = new Date(txt);
        return isNaN(d) ? '' : d.toLocaleString('pt-BR', {dateStyle: 'short', timeStyle: 'short'});
    };

    // Pede a senha numa janela própria e chama `executar(senha)`; a janela mostra o erro da Central.
    function pedirSenha(titulo, texto, executar) {
        document.getElementById('modal-senha-titulo').textContent = titulo;
        document.getElementById('modal-senha-texto').textContent = texto;
        erro.textContent = ''; campo.value = ''; modal.hidden = false; campo.focus();
        form.onsubmit = async ev => {
            ev.preventDefault();
            const ok = document.getElementById('modal-senha-ok'); ok.disabled = true;
            try {
                const msg = await executar(campo.value);
                if (msg) { erro.textContent = msg; return; }
                modal.hidden = true;
            } finally { ok.disabled = false; campo.value = ''; }
        };
    }
    document.getElementById('modal-senha-cancelar').onclick = () => { modal.hidden = true; carregar(); };
    modal.addEventListener('keydown', ev => { if (ev.key === 'Escape') { modal.hidden = true; carregar(); } });

    async function enviar(url, corpo) {
        const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corpo)});
        const dados = await r.json().catch(() => ({}));
        return r.ok ? null : (dados.error || 'Não foi possível concluir.');
    }

    function situacaoVersao(d) {
        const sensor = d.build_sensor || 'não informada (agente antigo)';
        let situacao;
        if (d.build_sensor && d.build_sensor === d.build_central) situacao = 'em dia com a Central.';
        else if (!d.guarda) situacao = 'reinstale o sensor (comando de instalação) para habilitar a atualização automática com volta segura.';
        else if (d.build_recusado && d.build_recusado === d.build_central) situacao = `a versão ${d.build_recusado} falhou neste sensor e foi desfeita; ele aguarda a próxima.`;
        else if (d.auto_atualizar) situacao = 'versão nova disponível; atualiza sozinho na madrugada (04h).';
        else situacao = 'versão nova disponível; use "Atualizar agente" ou ligue a atualização automática.';
        return `Versão do sensor: ${sensor} · Central: ${d.build_central} — ${situacao}`;
    }

    function desenhar(dados) {
        nomes = dados.acoes || {};
        chave.checked = !!dados.permitido;
        chaveTexto.textContent = dados.permitido ? 'Ligadas' : 'Desligadas';
        auto.checked = !!dados.auto_atualizar;
        versaoInfo.textContent = situacaoVersao(dados);
        botoes.innerHTML = Object.entries(nomes).map(([id, nome]) =>
            `<button class="action-btn ${PERIGOSAS.has(id) ? 'btn-danger' : 'btn-secondary'}" data-acao="${id}" ${dados.permitido ? '' : 'disabled'}>${esc(nome)}</button>`).join('');
        const itens = dados.historico || [];
        historico.innerHTML = itens.length ? itens.map(a => `
            <details class="acao-item status-${esc(a.status)}">
                <summary><span class="acao-nome">${esc(nomes[a.acao] || a.acao)}</span>
                    <span class="acao-meta">${esc(STATUS[a.status] || a.status)} · ${esc(a.solicitante || '')} · ${esc(quando(a.criado_em))}</span></summary>
                <pre>${esc(a.saida || (a.status === 'pendente' ? 'Aguardando o sensor buscar o comando…' : a.status === 'enviada' ? 'Executando no sensor…' : 'Sem saída.'))}</pre>
            </details>`).join('') : '<div class="hist-empty">Nenhuma ação executada.</div>';
        const emAndamento = itens.some(a => a.status === 'pendente' || a.status === 'enviada');
        clearTimeout(atualizando);
        if (emAndamento) atualizando = setTimeout(carregar, 4000);
    }

    async function carregar() {
        const r = await fetch(`${base}/acoes`);
        if (r.ok) desenhar(await r.json());
    }

    botoes.addEventListener('click', ev => {
        const botao = ev.target.closest('button[data-acao]');
        if (!botao) return;
        const acao = botao.dataset.acao;
        pedirSenha(nomes[acao], 'Confirme sua senha para executar esta ação no sensor.', async senha => {
            const msg = await enviar(`${base}/acoes`, {acao, senha});
            if (!msg) carregar();
            return msg;
        });
    });

    chave.addEventListener('change', () => {
        const permitir = chave.checked;
        chave.checked = !permitir;
        pedirSenha(permitir ? 'Ligar ações remotas' : 'Desligar ações remotas',
            permitir ? 'Com as ações ligadas, a Central pode atualizar e reiniciar esta máquina.' : 'Nenhuma ação remota será aceita para este sensor.',
            async senha => {
                const msg = await enviar(`${base}/permitir_acoes`, {permitir, senha});
                if (!msg) carregar();
                return msg;
            });
    });

    auto.addEventListener('change', () => {
        const ligar = auto.checked;
        auto.checked = !ligar;
        pedirSenha(ligar ? 'Ligar atualização automática' : 'Desligar atualização automática',
            ligar ? 'Na madrugada, o sensor baixa sozinho a versão nova da Central. Se ela não funcionar, volta para a anterior.'
                  : 'O sensor só será atualizado quando alguém usar "Atualizar agente".',
            async senha => {
                const msg = await enviar(`${base}/auto_atualizar`, {ligar, senha});
                if (!msg) carregar();
                return msg;
            });
    });

    carregar();
})();
