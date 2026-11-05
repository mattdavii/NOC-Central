# NexoReport — escopo da versão 1

Status: **aprovado para desenvolvimento** · 04/11/2026 (rev. 2: meta por contrato e capa nova)

Modelo visual: [`modelo/modelo-relatorio-mensal.pdf`](modelo/modelo-relatorio-mensal.pdf) (dados de demonstração;
gerado por `python3 docs/nexoreport/modelo/gerar_modelo.py`).

## 1. Objetivo

Entregar, todo mês, um **relatório da rede em PDF, pronto para a diretoria**, a cada empresa que contratou o
NexoReport. O relatório transforma o que o NexoPulse mede em respostas simples: a internet ficou no ar? quanto
tempo caiu? por quê? de quem foi a culpa? o que fazer agora?

Definido para a v1 (04/11/2026):
- **Conteúdo:** só a rede, com os dados do NexoPulse. Serviços da MD (visitas, manutenções) e dados de usina ficam para depois.
- **Entrega:** fica **no NexoHub** para ver e baixar, com o histórico dos meses anteriores. Sem envio automático
  e sem etapa de revisão pela MD nesta versão.

## 2. O que já existe (ponto de partida)

- **Vaga do aplicativo no NexoHub:** `report` em `acessos.APLICATIVOS`, com contrato por empresa e níveis de
  acesso; o perfil pronto "Diretoria" já dá leitura no Report. O atalho no lançador mostra "Em breve".
- **Dados do NexoPulse por site:**

| Dado | Onde está | Guardado por |
|---|---|---|
| Quedas (sem comunicação) e diagnósticos com causa, camada, confiança e evidências | `diagnosticos` | sem limite |
| Disponibilidade do mês e por dia | `hub._janelas_do_mes`, `disponibilidade_mes`, `disponibilidade_por_dia` | calculada na hora |
| Latência, oscilação e perda por alvo | `medicoes_rede` | **30 dias** (`DIAG_RETENCAO_DIAS`) |
| Leituras da Starlink (obstrução, alinhamento) | `starlink_medicoes` | **30 dias** |
| Traceroutes | `rotas_rede` | **30 dias** |
| Testes de velocidade | `historico_telemetria` | sem limite |
| Equipamentos da rede | `dispositivos` | só a foto atual |
| Diagrama da rede | `sensores.mapa_rede` + `mapa_rede.py` | atual |
| Eventos | `logs_ia` | sem limite |

- **Desenho do diagrama em SVG** (`mapa_rede.svg`) já usado no relatório da Central: entra no PDF sem retrabalho.

### O ponto que muda o plano: o mês precisa ser "fotografado" no fechamento

As medições de qualidade e da Starlink ficam só 30 dias, e o inventário é só a foto atual. Um relatório gerado
depois do fim do mês perderia o começo dele, e um relatório reaberto meses depois mostraria números diferentes.
Por isso a v1 **consolida o mês no dia 1** e guarda o resultado: o relatório é emitido uma vez e fica como foi
emitido.

## 3. O que entra na v1

### 3.1 Fechamento do mês (base de tudo)
- Tarefa agendada na Central, **dia 1 às 00:30 (Brasília)**, para cada empresa com o NexoReport contratado
  (e para a MD, que vê todas).
- Calcula e grava em `relatorios_mensais` (empresa, mês, versão, criado_em, `dados` em JSON, PDF em bytes):
  todos os números do relatório já prontos. O PDF é gerado a partir desse JSON.
- **Idempotente:** se a Central estiver fora no dia 1, a próxima execução fecha os meses pendentes.
- Mês de instalação: o local conta a partir da primeira medição (regra que já existe em `_janelas_do_mes`); o
  relatório informa "monitorado desde dd/mm".

### 3.2 Conteúdo do relatório (o do modelo)
1. **Capa com o resumo executivo:** medidor da disponibilidade contra a meta do contrato, tempo sem conexão,
   quedas e causa mais frequente, cada um comparado com o mês anterior; dois parágrafos "Em resumo" escritos por regras (sem IA); tendência dos
   últimos 6 meses.
2. **Disponibilidade por local:** tabela com provedor, %, tempo fora, quedas e situação **contra a meta do
   contrato da empresa** (ver 3.3) e gráfico dia a dia.
3. **Quedas e causas:** tempo sem conexão por causa, quedas por faixa de horário e as 5 maiores quedas.
4. **Qualidade:** resposta média, resposta nos piores momentos (p95), perda, oscilação e velocidade média por
   local; bloco da Starlink quando houver (obstrução média, quedas por obstrução, desvio de alinhamento).
5. **Provedor: evidências para cobrança:** quedas atribuídas ao provedor, com duração e o que as medições
   mostram, prontas para anexar ao chamado.
6. **Rede local:** totais do inventário e o diagrama de cada local (página em paisagem).
7. **Recomendações da MD**, geradas por regras a partir dos números, e **como os números são calculados**.

### 3.3 Regras de cálculo
- **Disponibilidade:** a mesma conta do NexoHub (arredonda para baixo; qualquer queda impede 100%; manutenção
  programada fora da conta). As funções de `hub.py` passam a aceitar um período fechado (`inicio`, `fim`), e o
  NexoHub e o NexoReport usam o mesmo código, para os números nunca divergirem.
- **Meta por contrato:** cada empresa tem a sua meta de disponibilidade (ex.: 99,7%), definida pela MD no
  cadastro da empresa, junto dos aplicativos contratados. Situação: **dentro da meta** a partir dela, **abaixo da
  meta** até 1 ponto percentual abaixo, **crítico** mais que isso. Sem meta cadastrada, vale 99,5% (a faixa do
  NexoHub). A meta usada fica gravada no relatório do mês: mudar o contrato não altera relatórios já emitidos.
- **Causa de uma queda:** o diagnóstico do mesmo local que cobre a janela da queda, escolhendo a camada mais baixa
  (rede interna < antena < provedor < DNS), que é a mesma lógica do diagnóstico ao vivo. Sem diagnóstico que a
  explique, a causa é **"não determinada"**, e o relatório não chuta.
- **Provedor:** só entra em "evidências" a queda com causa `provedor`/`queda_provedor` e confiança média ou alta.
- **Recomendações:** lista fechada de regras (por exemplo, ≥ 3 quedas por obstrução → verificar o campo de
  visão; quedas do provedor → pedir desconto; equipamentos sem nome → identificar). Sem texto livre na v1.

### 3.4 No NexoHub
- Atalho **NexoReport** ativo para quem tem o aplicativo; página `/report` com os meses disponíveis por empresa
  (o mais recente em destaque), botões **Ver** (abre o PDF no navegador) e **Baixar**.
- Campo **Meta de disponibilidade** no cadastro da empresa (só Master edita; registrado na auditoria).
- Nível **leitura** basta para ver e baixar. **Master** vê todas as empresas, pode **gerar de novo** um mês
  (vira uma nova versão; a anterior fica guardada) e pode gerar sob demanda o mês corrente, marcado como "parcial".
- Cada visualização e download fica na auditoria de acessos.

### 3.5 Técnica
- **PDF no servidor com WeasyPrint** (testado: a v70 gera o modelo em cerca de 2,5 s, com SVG, fontes próprias e
  página em paisagem). Precisa de `weasyprint` no `requirements.txt` e das bibliotecas `libpango-1.0-0` e
  `libpangoft2-1.0-0` no `Dockerfile`.
- Gráficos em SVG gerados em Python (sem JavaScript), no mesmo padrão do `mapa_rede.svg`.
- Fontes da marca (Bricolage Grotesque e IBM Plex Sans) embarcadas no PDF.
- Código novo em `nexoreport.py` (consolidação e regras) e `templates/report/` (template do PDF e a página do Hub).

### 3.6 Andamento
- **Entregas 1 e 2 no ar (04/11/2026):** meta por empresa no cadastro (Master), contas por período fechado em
  `hub.janelas_periodo`/`disponibilidade_periodo`, fechamento em `nexoreport.py` (tabela `relatorios_mensais`,
  verificação a cada 15 min, aviso no Telegram da MD se falhar) e retenção das medições ampliada para **40 dias**.
- **Entregas 3, 4 e 5 no ar (04/11/2026):** PDF em `nexoreport_pdf.py` + `templates/report/mensal.html`, gerado do
  JSON emitido (não guardamos o arquivo: o mesmo JSON gera sempre o mesmo PDF, e há cache em memória); página
  `/report` com Ver/Baixar, auditoria, prévia do mês corrente e "Emitir de novo" para a MD; card e atalho do NexoReport
  ativos no NexoHub; "Em resumo" e recomendações por regras.
- **MD vê todas as empresas (05/11/2026):** em `/report`, a MD escolhe qualquer empresa cliente; as sem o NexoReport no
  contrato aparecem marcadas e aceitam prévia e emissão manual. O fechamento do dia 1 e o acesso do cliente continuam
  dependendo do contrato.
- O fechamento automático começa em **novembro de 2026** (`PRIMEIRO_MES`): outubro já tinha perdido os dias 1 a 4
  pela retenção antiga de 30 dias, e um relatório incompleto não deve sair sozinho.
- O mês anterior só entra na comparação se tiver relatório emitido ou se as medições dele ainda existirem; a
  tendência de 6 meses vem só de relatórios emitidos.

## 4. Fora da v1
- Serviços da MD no relatório (visitas, manutenções, chamados) e dados de usina/O&M.
- Envio automático por e-mail ou Telegram e etapa de revisão/aprovação pela MD com parecer escrito.
- Relatórios por local separado, períodos personalizados e exportação em planilha (o levantamento já cobre o inventário).
- "Equipamentos novos/sumidos no mês": exige guardar o histórico do inventário (dá para começar a guardar já na v1
  e mostrar a partir do segundo mês).

## 5. Como vamos entregar (PRs pequenos, cada um com testes e CI)
1. **Período fechado nas contas do Hub e meta por empresa:** coluna da meta no cadastro da empresa; `_janelas_do_mes` e as funções de disponibilidade aceitam `inicio/fim`;
   testes provam que o mês corrente continua igual.
2. **Fechamento do mês:** tabela `relatorios_mensais`, consolidação em JSON e tarefa do dia 1. **Precisa estar no ar
   antes de 01/12** para o relatório de novembro sair completo (hoje há medições desde 05/10).
3. **PDF:** template a partir do modelo aprovado, WeasyPrint na imagem e geração a partir do JSON.
4. **Página no NexoHub:** `/report`, permissões, auditoria, gerar de novo (Master) e atalho ativo no lançador.
5. **Regras de texto:** "Em resumo", comparação com o mês anterior e recomendações.

## 6. Testes e critérios de aceite
- Os números do relatório batem com o NexoHub para o mesmo período (teste com o mesmo banco de exemplo).
- Mês com local instalado no meio conta só a partir da instalação; local em manutenção fica fora.
- Queda sem diagnóstico aparece como "não determinada"; nenhuma queda é atribuída ao provedor sem evidência.
- Empresa sem o NexoReport não vê a página (403) e não tem relatório gerado; usuário de outra empresa nunca baixa o
  relatório de outra.
- Gerar de novo cria nova versão sem apagar a anterior; a tarefa do dia 1 rodando duas vezes não duplica.
- O PDF abre, tem as 6 seções e cabe em A4 com o diagrama legível; tempo de geração abaixo de 10 s por empresa.

## 7. Riscos
- **Retenção de 30 dias:** se o fechamento falhar por mais de alguns dias, as medições de qualidade do começo do
  mês se perdem. Mitigação: o fechamento roda no dia 1 e de novo a cada hora enquanto houver mês pendente, e a
  falha vai para a tela de Saúde e para o Telegram da MD.
- **Imagem maior:** o WeasyPrint e as bibliotecas de texto aumentam a imagem da Central em algumas dezenas de MB.
- **Agente antigo sem medições:** o local entra com disponibilidade, mas sem a seção de qualidade, e o relatório
  diz isso em vez de mostrar zeros.

## 8. Decisões tomadas (04/11/2026)
- **D1. Situação:** pela **meta do contrato de cada empresa** (seção 3.3).
- **D2. Recomendações:** a lista de regras da seção 3.3 atende para começar; novas regras entram com o uso.
- **D3. Marca:** NexoReport na capa e a MD no rodapé; capa com visual técnico (grade de prancheta, traço de sinal
  nas cores da marca e medidor da meta), como no modelo.
