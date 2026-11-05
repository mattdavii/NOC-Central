// NexoDesk: horários, escolha de arquivos e envio dos formulários (abrir chamado e responder).
(function () {
    'use strict';

    function quando(ts) {
        const d = new Date(Number(ts) * 1000);
        const seg = (Date.now() - d.getTime()) / 1000;
        if (seg < 60) return 'agora';
        if (seg < 3600) return `há ${Math.floor(seg / 60)} min`;
        const hoje = new Date();
        const hora = d.toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit'});
        if (d.toDateString() === hoje.toDateString()) return `hoje ${hora}`;
        const ontem = new Date(hoje); ontem.setDate(hoje.getDate() - 1);
        if (d.toDateString() === ontem.toDateString()) return `ontem ${hora}`;
        return d.toLocaleDateString('pt-BR', {day: '2-digit', month: '2-digit'}) + ' ' + hora;
    }

    function horarios(raiz) {
        (raiz || document).querySelectorAll('[data-ts]').forEach(el => {
            el.textContent = quando(el.dataset.ts);
            el.title = new Date(Number(el.dataset.ts) * 1000).toLocaleString('pt-BR');
        });
    }

    function tamanho(bytes) {
        return bytes >= 1048576 ? (bytes / 1048576).toFixed(1).replace('.', ',') + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';
    }

    // Área de arquivos: guarda a escolha (somando várias escolhas e arrastes) e mostra a lista com "remover".
    function areaDeArquivos(area) {
        const input = area.querySelector('input[type=file]');
        const lista = area.querySelector('.escolhidos');
        const max = Number(area.dataset.max || 5);
        const maxBytes = Number(area.dataset.maxMb || 10) * 1048576;
        let arquivos = [];
        const aviso = area.closest('form').querySelector('.msg-form');

        function mostrar() {
            lista.innerHTML = '';
            arquivos.forEach((f, i) => {
                const item = document.createElement('span');
                item.className = 'escolhido';
                item.innerHTML = '<i class="fa-solid fa-paperclip" aria-hidden="true"></i>';
                item.append(` ${f.name} · ${tamanho(f.size)}`);
                const tirar = document.createElement('button');
                tirar.type = 'button';
                tirar.setAttribute('aria-label', `Remover ${f.name}`);
                tirar.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
                tirar.addEventListener('click', () => { arquivos.splice(i, 1); mostrar(); });
                item.append(tirar);
                lista.append(item);
            });
        }

        function somar(novos) {
            for (const f of novos) {
                if (arquivos.length >= max) { aviso.className = 'msg-form erro'; aviso.textContent = `Envie no máximo ${max} arquivos.`; break; }
                if (f.size > maxBytes) { aviso.className = 'msg-form erro'; aviso.textContent = `"${f.name}" passa de ${area.dataset.maxMb} MB.`; continue; }
                arquivos.push(f);
            }
            mostrar();
        }

        input.addEventListener('change', () => { somar(input.files); input.value = ''; });
        ['dragenter', 'dragover'].forEach(ev => area.addEventListener(ev, e => { e.preventDefault(); area.classList.add('arrastando'); }));
        ['dragleave', 'drop'].forEach(ev => area.addEventListener(ev, e => { e.preventDefault(); area.classList.remove('arrastando'); }));
        area.addEventListener('drop', e => somar(e.dataTransfer.files));
        return {lista: () => arquivos, limpar: () => { arquivos = []; mostrar(); }};
    }

    // Formulário enviado por fetch (multipart). data-destino: para onde ir depois ("{numero}" vira o número do chamado).
    function formulario(form) {
        const area = form.querySelector('.soltar');
        const arquivos = area ? areaDeArquivos(area) : null;
        const aviso = form.querySelector('.msg-form');
        form.addEventListener('submit', async e => {
            e.preventDefault();
            const botao = form.querySelector('button[type=submit]');
            const dados = new FormData(form);
            dados.delete('arquivos');
            if (arquivos) arquivos.lista().forEach(f => dados.append('arquivos', f, f.name));
            botao.disabled = true;
            aviso.className = 'msg-form'; aviso.textContent = arquivos && arquivos.lista().length ? 'Enviando os arquivos…' : 'Enviando…';
            try {
                const r = await fetch(form.action, {method: 'POST', body: dados});
                let d = {};
                try { d = await r.json(); } catch (_) { /* resposta sem JSON */ }
                if (!r.ok) throw new Error(d.error || 'Não foi possível enviar. Tente de novo.');
                aviso.className = 'msg-form ok'; aviso.textContent = form.dataset.ok || 'Enviado.';
                const destino = (form.dataset.destino || '').replace('{numero}', d.numero || '');
                if (destino) location.href = destino; else location.reload();
            } catch (erro) {
                aviso.className = 'msg-form erro'; aviso.textContent = erro.message;
                botao.disabled = false;
            }
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        horarios();
        document.querySelectorAll('form[data-desk]').forEach(formulario);
    });
    window.NexoDesk = {horarios};
})();
