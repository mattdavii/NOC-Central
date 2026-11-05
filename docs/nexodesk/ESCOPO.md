# NexoDesk — escopo da versão 1

Status: **proposta para aprovação** · 05/11/2026

Modelo das telas: [`modelo/`](modelo/) (dados de demonstração; `modelo/telas.html`, imagens geradas por
`python3 docs/nexodesk/modelo/gerar_telas.py`).

## 1. Objetivo

Um lugar só para **pedir suporte à MD e acompanhar o pedido até o fim**, dentro do NexoHub. Hoje os pedidos
chegam por WhatsApp e telefone, se perdem na conversa e não há como mostrar ao cliente que o prazo do contrato foi
cumprido. O NexoDesk registra cada pedido como um chamado, com responsável, conversa, anexos e o prazo do contrato
correndo, e dá à MD uma fila única para trabalhar.

Definido para a v1 (05/11/2026):
- **Quem abre:** qualquer usuário do NexoHub. Os usuários dos clientes abrem chamados para a MD; a equipe da MD abre
  chamados internos e também em nome de um cliente (quando o pedido chega por WhatsApp ou telefone).
- **SLA:** por contrato de cada empresa, já na v1.
- **Chamado automático por queda:** não. O NexoPulse continua avisando as quedas como hoje; abrir chamado é sempre
  uma decisão de alguém.

## 2. O que já existe (ponto de partida)

- **Usuários e empresas:** tabela `clientes`, com a empresa (`role='Cliente'`) e os usuários dela (`cliente_pai_id`);
  a MD é a empresa `empresa_master`. `acessos.empresa_de()` diz a empresa de qualquer usuário.
- **Aplicativos por contrato e níveis de acesso:** `acessos.APLICATIVOS`, `CONTRATO_PADRAO`, níveis leitura /
  operador / admin e perfis prontos. O NexoDesk entra como mais um aplicativo (`desk`).
- **Marca:** logo e símbolo do NexoDesk já existem em `docs/familia-nexo/logos/` ("Chamados e suporte").
- **Avisos:** fila de Telegram (`notifications.enqueue`) com o grupo da MD e o grupo de cada cliente.
- **Auditoria de acessos** (`auditoria_acessos`) e o **cadastro da empresa** em Acessos, onde já ficam os
  aplicativos contratados e a meta do NexoReport: o SLA entra no mesmo lugar.
- **Locais monitorados** (`sensores`): o chamado pode apontar o local, e a MD abre o NexoPulse dele com um clique.
- Não existe envio de arquivos nem e-mail na Central hoje: os dois são novidade (ver 3.6 e 4).

## 3. O que entra na v1

### 3.1 O chamado
| Campo | Regra |
|---|---|
| Número | Sequencial e curto (`#1042`), igual para cliente e MD |
| Empresa e solicitante | Preenchidos pelo login; a MD escolhe a empresa quando abre em nome de alguém |
| Canal | NexoHub, WhatsApp, telefone, presencial ou interno (os quatro últimos só a MD marca) |
| Categoria | Internet e rede · Segurança e câmeras · Energia, elétrica e solar · Sistemas e software · Acesso ao NexoHub · Outros |
| Local | Opcional: um dos locais monitorados da empresa |
| Urgência | O solicitante diz o impacto ("parou tudo", "atrapalha", "dúvida/pedido"); a MD confirma a **prioridade** |
| Prioridade | Crítica, Alta, Média, Baixa: é ela que define o prazo do SLA |
| Título, descrição, anexos | Anexos na abertura e em qualquer resposta |
| Responsável | Um usuário da MD |

### 3.2 Andamento
`Novo` → `Em atendimento` → `Aguardando cliente` ⇄ `Em atendimento` → `Resolvido` → `Fechado`; e `Cancelado`.
- **Aguardando cliente** pausa o relógio do SLA; volta sozinho para "Em atendimento" quando o cliente responde.
- **Resolvido:** a MD descreve a solução. O cliente confirma (e dá uma nota de 1 a 5, opcional) ou reabre.
  Sem resposta em **3 dias úteis**, o chamado fecha sozinho. Reabrir é possível por **7 dias** depois de fechado.
- **Conversa:** respostas visíveis ao cliente e **notas internas** só da MD, na mesma linha do tempo, junto do
  histórico de cada mudança (status, prioridade, responsável), com autor e hora.

### 3.3 SLA por contrato
- No cadastro da empresa (Acessos, só Master edita, com auditoria): **horário de atendimento** e a **tabela de
  prazos por prioridade** (primeira resposta e solução).
- **Horário:** "comercial" (seg a sex, 8h às 18h, Brasília, sem feriados nacionais) ou "24x7". Feriados estaduais
  e municipais ficam para depois.
- **Tabela padrão** (vale para quem não tiver contrato próprio; a MD ajusta empresa a empresa):

| Prioridade | 1ª resposta | Solução | Exemplo |
|---|---|---|---|
| Crítica | 1 h | 8 h | Unidade inteira sem internet, câmeras fora |
| Alta | 2 h | 16 h | Um setor parado, lentidão forte |
| Média | 4 h | 24 h | Problema com contorno |
| Baixa | 8 h | 40 h | Dúvida, pedido, melhoria |

  Horas contadas no horário do contrato (com o comercial, 8 h = 1 dia útil).
- **Primeira resposta** = a primeira resposta pública da MD (nota interna não conta). **Solução** = até "Resolvido".
- O relógio pausa em "Aguardando cliente". Mudar a prioridade recalcula os prazos a partir da abertura.
- **Situação do prazo:** no prazo · **perto de vencer** (80% consumido) · **estourado**. O SLA usado fica gravado
  no chamado: mudar o contrato não muda chamados já abertos (a mesma regra da meta do NexoReport).
- **Chamado interno da MD:** sem SLA de contrato; a MD pode marcar um prazo manual.

### 3.4 Quem vê o quê (nível no aplicativo `desk`)
| Quem | Vê | Pode |
|---|---|---|
| Usuário do cliente, nível **leitura** | Os chamados que abriu | Abrir, responder, anexar, confirmar a solução |
| Nível **operador** | Todos os chamados da empresa | O mesmo, em qualquer chamado da empresa |
| Nível **admin** (e a conta da empresa) | Todos os da empresa + o painel de SLA da empresa | Também cancelar e reabrir |
| **MD** (Operador Master) | Todos os chamados de todas as empresas e os internos | Atender, notas internas, prioridade, responsável, abrir em nome do cliente |
| **MD** (Administrador Master) | Idem | Também o SLA de cada contrato |

- **Contrato:** o NexoDesk fica **ligado por padrão para todas as empresas** (`CONTRATO_PADRAO['desk'] = True`):
  todo cliente precisa conseguir pedir suporte. O que muda de um contrato para outro é o SLA. A MD pode desligar
  para uma empresa específica.
- Usuário de uma empresa nunca vê chamado de outra, nem as notas internas.

### 3.5 Telas
1. **Meus chamados (cliente):** lista com número, título, status, prioridade e prazo; filtros "abertos / todos" e
   busca; botão **Abrir chamado**.
2. **Abrir chamado:** formulário curto (categoria, local, impacto, título, descrição, anexos), com a frase de
   quanto tempo a MD tem para responder pelo contrato.
3. **Chamado:** conversa no centro; ao lado, status, prioridade, responsável, local (com atalho para o NexoPulse) e
   os dois relógios do SLA.
4. **Fila da MD:** todos os chamados abertos, ordenados pelo prazo que vence primeiro; contadores (novos, perto de
   vencer, estourados, aguardando cliente); filtros por empresa, responsável, status e categoria; "Abrir em nome de".
5. **SLA no cadastro da empresa:** horário de atendimento e a tabela de prazos.
6. **NexoHub:** o card do NexoDesk mostra os chamados abertos e o próximo prazo; o atalho do lançador fica ativo.

### 3.6 Avisos e anexos
- **No NexoHub:** contador de chamados com novidade no card e no menu.
- **Telegram da MD:** chamado novo, resposta do cliente, perto de vencer e estourado.
- **Telegram do cliente** (se a empresa tiver o grupo configurado): resposta da MD, pedido de informação,
  resolvido. Nada de nota interna.
- **Anexos:** imagens, PDF, planilhas e texto, até **10 MB por arquivo** e 5 por mensagem, guardados no banco
  (entram no backup que já existe). Só baixa quem pode ver o chamado; cada download vai para a auditoria.

### 3.7 Técnica
- Tabelas novas: `chamados`, `chamado_mensagens` (resposta, nota interna ou evento), `chamado_anexos`,
  `sla_contratos` (por empresa). Código em `nexodesk.py` (regras, relógio do SLA) e `templates/desk/`.
- Relógio do SLA calculado sobre as janelas do horário de atendimento, descontando as pausas; uma tarefa a cada
  5 minutos marca "perto de vencer" e "estourado" e dispara os avisos (uma vez por chamado e por marco).
- Rotas `desk_*` mapeadas para o aplicativo `desk` em `acessos.aplicativo_da_rota`, como o NexoReport.

## 4. Fora da v1
- Chamado aberto sozinho por queda do NexoPulse (decidido: não); a MD pode, no máximo, abrir manualmente a partir
  de uma queda numa versão futura, com um botão.
- E-mail (abrir chamado respondendo e-mail e avisos por e-mail): os usuários não têm e-mail cadastrado hoje.
- Chamados internos de cada cliente (o cliente usar o NexoDesk como helpdesk da própria equipe).
- Base de conhecimento, respostas prontas, pesquisa de satisfação além da nota de 1 a 5.
- Feriados estaduais/municipais e calendário de atendimento por local.
- SLA e chamados no NexoReport (seção "Atendimento da MD no mês"): entra depois que houver um mês de dados.

## 5. Como vamos entregar (PRs pequenos, cada um com testes e CI)
1. **Base:** aplicativo `desk`, tabelas, abrir / listar / ver / responder, anexos, permissões e auditoria.
2. **Atendimento da MD:** fila, responsável, prioridade, notas internas, canal, abrir em nome do cliente, internos.
3. **SLA:** tabela por contrato no cadastro da empresa, horário comercial com feriados nacionais, pausas, situação
   do prazo e alertas no Telegram da MD.
4. **Fechamento e NexoHub:** resolvido → confirmação, nota e fechamento automático; reabrir; card e contador no Hub;
   Telegram do cliente.

## 6. Testes e critérios de aceite
- Usuário de uma empresa nunca vê, responde ou baixa anexo de chamado de outra; nota interna nunca chega ao cliente
  (página, API e Telegram).
- Leitura vê só os próprios chamados; operador e admin veem os da empresa.
- SLA no horário comercial: chamado aberto sexta às 17h com 4 h de resposta vence segunda às 11h; feriado nacional
  não conta; "Aguardando cliente" não conta.
- Mudar o SLA do contrato não altera chamados já abertos; mudar a prioridade recalcula a partir da abertura.
- Aviso de "perto de vencer" e "estourado" sai uma vez só por chamado.
- Fechamento automático depois de 3 dias úteis em "Resolvido"; reabrir só até 7 dias.
- Anexo acima de 10 MB ou de tipo não aceito é recusado com mensagem clara.

## 7. Riscos
- **Banco maior com anexos:** 10 MB por arquivo somam rápido. Mitigação: limite por arquivo e por mensagem, e um
  aviso na tela de Saúde quando os anexos passarem de 1 GB (aí migramos para um volume ou bucket).
- **Hábito do WhatsApp:** o cliente vai continuar mandando mensagem. Mitigação: a MD abre em nome dele em poucos
  segundos, e o Telegram do cliente mostra o número do chamado para ele acompanhar.

## 8. Decisões tomadas (05/11/2026)
- **D1. Quem abre:** qualquer usuário (clientes para a MD, e a MD internamente ou em nome do cliente).
- **D2. SLA:** por contrato, já na v1.
- **D3. Quedas:** não abrem chamado sozinhas.

Propostas desta revisão, para confirmar: NexoDesk ligado por padrão para todas as empresas (3.4), tabela padrão de
prazos (3.3) e fechamento automático em 3 dias úteis (3.2).
