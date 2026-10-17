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

A paleta e os componentes compartilhados são centralizados em `static/product.css`. O tema escuro é prioritário, e o tema claro pode ser selecionado pelo operador. A preferência fica somente no navegador por meio de `localStorage`.

| Token | Cor | Uso |
|---|---:|---|
| Azul Nexo | `#082E56` | marca e estrutura |
| Ciano Pulse | `#05A9DC` | ações e destaques |
| Verde Pulse | `#05A88C` | conectividade e estados positivos |
| Azul Noturno | `#0B1728` | fundo escuro |
| Azul Superfície | `#12263D` | cards e painéis |
| Cinza Claro | `#E8F1FA` | texto no tema escuro |
| Cinza Secundário | `#94A3B8` | metadados |

As cores de atenção, criticidade e indisponibilidade continuam separadas da cor institucional. O rebranding não muda o significado dos indicadores.

## Compatibilidade preservada

Os seguintes identificadores técnicos permanecem inalterados intencionalmente:

- repositório `NOC-Central`;
- domínio e rotas atuais;
- variáveis `NOC_*`;
- cabeçalhos `X-NOC-Sensor-*`;
- serviço Linux `noc-sensor.service`;
- banco, IDs, credenciais e protocolos dos sensores;
- integração Railway e compatibilidade de agentes Linux/Windows antigos.

Não há migração de banco para esta alteração.

## Validação e rollback

Validar login, dashboard, detalhe do sensor, gestão de Edge, usuários, TV, relatório e PWA nos temas escuro e claro, em desktop e viewport móvel. Executar a suíte completa em SQLite e PostgreSQL antes de publicar.

O rollback consiste em reverter o commit de rebranding. Como não há migração nem alteração de contrato, não é necessário reverter banco, credenciais ou instalações dos agentes.
