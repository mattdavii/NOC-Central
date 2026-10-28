# Rebranding NexoPulse

## Escopo

A identidade comercial da interface passou de NOC-Central para **NexoPulse**. A Central é apresentada como **NexoPulse Central** e o agente local como **NexoPulse Edge**.

Posicionamento: **Network Intelligence & Monitoring**  
Slogan: **Sua rede tem um pulso. Nós damos visibilidade a ele.**

## Identidade visual

Os ativos oficiais ficam em `static/brand/`:

- `nexopulse-logo-dark.png`: logomarca horizontal para fundos escuros;
- `nexopulse-logo-light.png`: logomarca horizontal para fundos claros;
- `nexopulse-symbol.png`: símbolo para espaços reduzidos;
- `static/icons/`: ícones locais do PWA;
- `static/favicon.png`: favicon da aplicação.

O manifesto usa o identificador estável `/`, o nome **NexoPulse Central** e
ícones próprios para instalação normal e maskable. O navegador consulta
`/manifest.json?v=nexopulse-2` e o service worker em `/sw.js?v=nexopulse-2`
sem cache, permitindo que instalações existentes recebam a nova identidade
sem criar um segundo aplicativo.

O agente também adota o nome do produto: o arquivo é `nexopulse_edge.py`
(Python não aceita hífen em nome de módulo), o serviço Linux é
`nexopulse-edge.service`, a configuração fica em `/etc/default/nexopulse-edge` e a
pasta de instalação é `~/nexopulse-edge`. O instalador migra sozinho uma
instalação antiga (`noc-sensor.service`, `/etc/default/noc-sensor`,
`agente_v2.py`), preservando identidade, chave e configuração. No Windows o
sensor roda dentro do WSL com o mesmo instalador.

A paleta e os componentes compartilhados são centralizados em `static/product.css`. O tema escuro é prioritário, e o tema claro pode ser selecionado pelo operador. A preferência fica somente no navegador por meio de `localStorage`.

| Token | Cor | Uso |
|---|---:|---|
| Azul Nexo | `#082E56` | marca e estrutura |
| Ciano Pulse | `#05A9DC` | ações e destaques |
| Verde Pulse | `#05A88C` | marca e destaques (não indica estado) |
| Azul Noturno | `#0B1728` | fundo escuro |
| Azul Superfície | `#12263D` | cards e painéis |
| Cinza Claro | `#E8F1FA` | texto no tema escuro |
| Cinza Secundário | `#94A3B8` | metadados |

As cores da marca ficam nos tokens `--brand-nexo`, `--brand-cyan`, `--brand-green`
e `--brand-gradient`, e nunca representam estado operacional. O rebranding não
muda o significado dos indicadores.

### Paleta operacional

Os estados usam tokens próprios em `static/product.css`. O Verde Pulse é
verde-azulado e não é usado como "OK".

| Estado | Token | Tema escuro | Tema claro |
|---|---|---:|---:|
| OK | `--status-ok` | `#22C55E` | `#166534` |
| Atenção | `--status-warn` | `#FBBF24` | `#92400E` |
| Crítico | `--status-crit` | `#F87171` | `#B91C1C` |
| Offline | `--status-offline` | `#8391A7` | `#475569` |
| Desconhecido | `--status-unknown` | `#94A3B8` | `#526077` |

"Desconhecido" sempre tem borda tracejada (`.status-unknown`, `pill-unknown`,
`badge-unknown`, `b-unknown`, `.tv-card.unknown`). Um estado sem leitura aparece
como "Sem medição", "SEM DADOS" ou "AGUARDANDO", nunca como saudável:

- sensor cadastrado que ainda não reportou: "SEM DADOS" no dashboard, Gestão do
  Edge, página do sensor e TV. A contagem de "Falhas Críticas" do dashboard não
  mudou; na TV ele não entra em "Online (Estáveis)";
- ping de gateway ou de alvo externo ausente: "Sem medição", sem contar como
  internet OK; sem nenhum alvo medido a WAN mostra "SEM DADOS";
- IPs customizados e watchdog de energia sem latência: "Sem medição";
- CPU, RAM e tráfego sem leitura: `--` em vez de 0.

Gráficos (Chart.js) e topologia (vis-network) desenham em canvas e leem as cores
dos tokens na carga da página; a troca de tema vale para eles na próxima
atualização.

### Acessibilidade

No tema claro, texto, links e cores de destaque passam de 4,5:1 (WCAG AA) sobre
todos os fundos; links usam o Azul Nexo. Ciano e Verde Pulse puros ficam em
ícones, faixas e gradientes. O teste
`test_status_palette_is_separate_from_brand_and_accessible` calcula o contraste a
partir do próprio CSS. O relatório usa cores fixas de fundo claro para impressão.

## Compatibilidade preservada

Os seguintes identificadores técnicos permanecem inalterados intencionalmente:

- repositório `NOC-Central`;
- domínio e rotas atuais;
- variáveis `NOC_*`;
- cabeçalhos `X-NOC-Sensor-*`;
- banco, IDs, credenciais e protocolos dos sensores;
- integração Railway e compatibilidade de agentes Linux/Windows antigos.

Não há migração de banco para esta alteração.

## Validação e rollback

Validar login, dashboard, detalhe do sensor, gestão de Edge, usuários, TV, relatório e PWA nos temas escuro e claro, em desktop e viewport móvel. Executar a suíte completa em SQLite e PostgreSQL antes de publicar.

O rollback consiste em reverter o commit de rebranding. Como não há migração nem alteração de contrato, não é necessário reverter banco, credenciais ou instalações dos agentes.

A revisão foi feita em fases, um commit por fase, cada uma revertível isoladamente
com `git revert`:

1. NexoPulse Edge no agente e atualização do PWA instalado;
2. fase (a): tokens de marca e de estado, contraste AA;
3. fase (b): login e layout base;
4. fase (c): dashboard e sensores;
5. fase (d): relatório, TV, usuários e documentação.

## Pendências conhecidas

- Serviços monitorados (`servicos_os`) nascem com status `'ONLINE'` por padrão no
  banco e aparecem verdes antes da primeira verificação do agente. Corrigir exige
  mudar o padrão e o agente; não foi feito nesta revisão.
- Se a consulta de eventos do relatório falhar, ele mostra "Nenhuma ocorrência
  registrada no período" em vez de um aviso de erro.
- Versões oficiais da logomarca para fundo escuro: na versão atual, "Pulse"
  termina em verde puro, e os nós azul-marinho do ícone PWA perdem contraste sobre
  fundo escuro.
