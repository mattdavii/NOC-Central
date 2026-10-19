# NexoPulse

Plataforma de monitoramento de rede em dois componentes:

- **NexoPulse Edge** (`agente_v2.py` e módulos): agente Linux que roda na rede do
  site (no Windows, dentro do WSL). Instalação em [`linux/README.md`](linux/README.md)
  e [`docs/INSTALAR-SENSOR.md`](docs/INSTALAR-SENSOR.md).
- **NexoPulse Central** (`app.py`): servidor Flask na nuvem (Railway) com o painel,
  os alertas no Telegram e o motor de diagnóstico.

O Edge manda o estado a cada 5 s (`POST /api/v2/report_data`, que também é o
heartbeat) e as medições de diagnóstico em lotes (`POST /api/v2/medicoes`), sempre
com a chave do próprio sensor (`X-NOC-Sensor-Key`).

## Saúde do servidor

A página **Servidor** (só Administrador Master) mostra a saúde da Central (latência
do banco, sessões presas, pool, fila do Telegram, guardião, memória) e o que o
**vigia** viu na máquina do banco (Central no ar, banco, Tailscale, disco, backup).
Alertas vão para o grupo NexoPulse depois de falhas seguidas e quando normaliza.
Configuração e estado ficam no banco. Detalhes e instalação do vigia em
[`ops/vigia/README.md`](ops/vigia/README.md).

---

## Diagnóstico de rede

O objetivo é dizer **em que camada está o problema** de um site, e não só que
"caiu". O Edge mede e a Central conclui:

```
LAN (gateway) → Starlink (antena) → Provedor (1º salto) → Internet (1.1.1.1 / 8.8.8.8) → DNS
```

### O que o Edge mede

| Módulo | O quê | Quando |
|---|---|---|
| `netdiag.py` | Ping para o gateway, o 1º salto do provedor e os alvos externos: latência média, jitter e perda | janelas de 60 s, contínuas |
| `netdiag.py` | DNS separado do ICMP: resolvedor do sistema e consulta UDP direta a 1.1.1.1 e 8.8.8.8 (tempo e falha) | 3 rodadas por janela |
| `netdiag.py` | Rota (`mtr`, `traceroute` ou `tracepath`, o que existir), comparada com a última rota boa para apontar **qual salto piorou** | a cada 15 min e logo que uma janela degrada |
| `starlink.py` | Obstrução, alinhamento (atual × desejado, tilt e desvio em graus), alertas nativos, latência e perda até o PoP, quedas da última hora com a causa, firmware | a cada 30 s (5 min sem antena) |

O 1º salto do provedor é descoberto pela rota: o primeiro endereço depois do
gateway que responde a ping (muitos roteadores de borda não respondem; nesse
caso o próximo é testado).

### Configuração do Edge

As variáveis ficam em `/etc/default/noc-sensor` (depois de editar:
`sudo systemctl restart noc-sensor`). Todas são opcionais.

| Variável | Padrão | Para quê |
|---|---|---|
| `NOC_DIAG` | `1` | `0` desliga os testes de rede |
| `NOC_DIAG_JANELA` | `60` | Duração da janela de medição (s) |
| `NOC_DIAG_PING_INTERVALO` | `2` | Intervalo entre pings de cada alvo (s, mínimo 1) |
| `NOC_DIAG_EXTERNOS` | `1.1.1.1,8.8.8.8` | Alvos externos de ICMP |
| `NOC_DIAG_DNS_NOMES` | `cloudflare.com,google.com` | Nomes resolvidos no teste de DNS |
| `NOC_DIAG_DNS_SERVIDORES` | `1.1.1.1,8.8.8.8` | Servidores públicos consultados direto (além do resolvedor do sistema) |
| `NOC_DIAG_DNS_TIMEOUT` | `2` | Tempo limite por consulta DNS (s) |
| `NOC_DIAG_TRACE_ALVO` | `8.8.8.8` | Destino da rota |
| `NOC_DIAG_TRACE_INTERVALO` | `900` | Rota periódica (s) |
| `NOC_DIAG_TRACE_MIN` | `120` | Intervalo mínimo entre rotas disparadas por degradação (s) |
| `NOC_DIAG_REDESCOBRIR_SALTO` | `1800` | Redescobre o 1º salto do provedor (s) |
| `NOC_DIAG_GATILHO_PERDA` / `_LATENCIA` / `_JITTER` | `5` % / `150` ms / `30` ms | Quando a janela conta como degradada e dispara a rota |
| `NOC_PROVEDOR` | — | Nome fixo do provedor deste site |
| `NOC_PROVEDOR_LOOKUP` | `1` | Descobre o provedor pelo IP público (ipwho.is, a cada 6 h). `0` desliga |
| `NOC_STARLINK` | `auto` | `auto`, `off` ou `mock` |
| `NOC_STARLINK_ALVO` | `192.168.100.1:9200` | Endereço gRPC da antena |
| `NOC_STARLINK_INTERVALO` | `30` | Leitura da antena (s) |
| `NOC_STARLINK_MOCK` | `normal` | Cenário do mock: `normal`, `obstruida`, `desalinhada`, `degradada` |
| `NOC_FILA_MAX_ITENS` / `NOC_FILA_MAX_DIAS` | `20000` / `7` | Tamanho máximo do buffer local |
| `NOC_LINK_SECUNDARIO`, `NOC_CENTRAL_URL_SECUNDARIA`, `NOC_INTERFACE_SECUNDARIA` | desligado | Preparado para link reserva (ex.: 4G), veja abaixo |

### Starlink

O coletor usa a API gRPC **local e não oficial** da antena, pelo módulo
`starlink_grpc` do projeto [starlink-grpc-tools](https://github.com/sparky8512/starlink-grpc-tools)
(pacote `starlink-grpc-core`, em `requirements-starlink.txt`). O instalador tenta
instalar essa dependência; se falhar, o Edge funciona normalmente e mostra
"Starlink não detectada".

- Campos que o firmware não mandar ficam vazios, sem erro; alertas novos aparecem
  sozinhos (a lista é lida de forma genérica). O firmware vai junto em toda leitura.
- **Sem antena física:** `NOC_STARLINK=mock` com `NOC_STARLINK_MOCK=obstruida`
  (ou outro cenário). Para ver uma leitura no terminal:
  `NOC_STARLINK=mock NOC_STARLINK_MOCK=desalinhada .venv/bin/python starlink.py`.
- **Modo bypass:** com o roteador Starlink em bypass, o roteador do site recebe o
  IP público e a antena (192.168.100.1) fica fora da rede local. O Edge só a
  alcança se houver rota até ela. Em geral basta uma rota estática no roteador do
  site para `192.168.100.1/32` pela interface WAN (ligada à antena). Para testar a
  partir da máquina do sensor: `ping 192.168.100.1`. No Linux do próprio sensor,
  se ele estiver na mesma rede física da antena:
  `sudo ip route add 192.168.100.1/32 dev <interface>`.

### Regras da Central (`diagnostico.py`)

As camadas são avaliadas de baixo para cima. A camada mais baixa com problema
explica as de cima, que ficam como "sem leitura" em vez de gerar alertas falsos.

| Regra | Diagnóstico | Ação sugerida |
|---|---|---|
| Sem gateway, ou perda/latência alta até o gateway | **Problema na rede local/equipamento do site** | Cabo, switch, Wi-Fi e roteador do site |
| Obstrução acima do limiar, ou obstruída agora | **Starlink obstruída** (crítica a partir de 3× o limiar ou 3 quedas por obstrução na hora) | Árvores ou estruturas no campo de visão (app Starlink → Verificar obstruções) |
| Perda ou latência alta até o PoP, ou antena em estado anormal, sem obstrução | **Starlink com degradação** | Acompanhar no app; persistindo, chamado na Starlink |
| Desvio de alinhamento acima do limiar, ou alerta de mastro/motores | **Starlink desalinhada** | Fixação e prumo do mastro |
| Outros alertas nativos (calor, Ethernet lenta, atualização pendente…) | **Alerta da antena Starlink** | Específica de cada alerta |
| Gateway OK e 1º salto do provedor com perda/latência/jitter alto | **Instabilidade no provedor** | Chamado no provedor, com as medições |
| Gateway OK, 1º salto OK (ou não identificado) e todos os externos ruins | **Instabilidade no provedor** (confiança menor) | Chamado no provedor, com a rota |
| ICMP externo OK e DNS do sistema falhando | **Falha de DNS** (separa "DNS do site" de "porta 53 bloqueada") | Trocar o DNS do roteador/DHCP ou reiniciar o roteador |
| Sem heartbeat além de `SITE_SEM_COMUNICACAO_S` | **Site sem comunicação** | Energia, link e sensor no local |
| N+ sites do mesmo provedor com problema de provedor, Starlink degradada ou sem comunicação dentro da janela de correlação | **Possível queda do provedor X**: um alerta só, os sites ficam agrupados nele | Acionar o provedor |

Cada diagnóstico traz site, camada, causa provável, **confiança** (alta, média ou
baixa, conforme quantas evidências concordam), horário de início, **evidências**
(as métricas que dispararam) e ação sugerida. A Starlink explica a degradação
antes do provedor: com a antena obstruída, a perda no 1º salto não vira alerta de
provedor.

Para não alarmar à toa, o diagnóstico só notifica depois de
`DIAG_CONFIRMAR_JANELAS` janelas seguidas (uma a mais para os de provedor, para dar
tempo de a correlação juntar os sites) e só é resolvido após
`DIAG_RESOLVER_JANELAS` janelas normais. O Telegram recebe o texto para o técnico
de campo, por exemplo: *"Starlink do site X com 6% de obstrução desde 14h00 —
verificar árvores ou estruturas no campo de visão da antena"*.

O provedor de cada site vem do Edge (Starlink detectada, ou o nome pelo IP
público) e pode ser definido à mão na tela do sensor, o que prevalece.

#### Limiares (variáveis da Central)

| Variável | Padrão | | Variável | Padrão |
|---|---|---|---|---|
| `DIAG_LAN_PERDA_PCT` | 5 | | `DIAG_SL_OBSTRUCAO_PCT` | 2 |
| `DIAG_LAN_LATENCIA_MS` | 100 | | `DIAG_SL_PERDA_POP_PCT` | 5 |
| `DIAG_PROV_PERDA_PCT` | 5 | | `DIAG_SL_LATENCIA_POP_MS` | 120 |
| `DIAG_PROV_LATENCIA_MS` | 100 | | `DIAG_SL_DESVIO_GRAUS` | 8 |
| `DIAG_PROV_JITTER_MS` | 40 | | `DIAG_DNS_FALHA_PCT` | 50 |
| `DIAG_EXT_PERDA_PCT` | 5 | | `DIAG_DNS_LATENCIA_MS` | 800 |
| `DIAG_EXT_LATENCIA_MS` | 200 | | `DIAG_CORRELACAO_MIN_SITES` | 3 |
| `DIAG_CONFIRMAR_JANELAS` | 2 | | `DIAG_CORRELACAO_JANELA_S` | 600 |
| `DIAG_RESOLVER_JANELAS` | 2 | | `DIAG_STARLINK_VALIDADE_S` | 300 |
| `SITE_SEM_COMUNICACAO_S` | 60 | | `DIAG_RETENCAO_DIAS` | 30 |

### Quando o link cai

- O Edge guarda as medições no SQLite local (`sensor_local.db`, tabela
  `fila_envio`) e as envia em lotes quando a conexão volta, **com o horário
  original**. A Central coloca esses dados na linha do tempo no momento certo
  (por exemplo, mostra que a queda das 14h foi obstrução da Starlink), mas não
  manda Telegram de coisa antiga.
- A Central abre **Site sem comunicação** quando o heartbeat some por mais de
  `SITE_SEM_COMUNICACAO_S` e o resolve, com a duração, quando o sensor volta.
- **Link secundário (preparado, desligado):** o envio já percorre uma lista de
  rotas (`fila_envio.rotas_envio`). Com `NOC_LINK_SECUNDARIO=1` e
  `NOC_CENTRAL_URL_SECUNDARIA`, a secundária é tentada quando a principal falha.
  Sair de fato pelo 4G (`NOC_INTERFACE_SECUNDARIA`) ainda exige rota de política
  no sistema operacional; o agente não faz bind de interface.

### No painel

- **Tela do sensor:** card *Diagnóstico de rede* (status por camada com as últimas
  medições, diagnósticos ativos, provedor e a última rota com o salto que piorou),
  card *Starlink* (obstrução, desvio de alinhamento, PoP, alertas, quedas,
  firmware) e *Linha do tempo* com evidências e ação de cada diagnóstico.
- **Sensores:** coluna *Rede* com o estado de cada camada (L, S, P, D).
- API: `GET /api/v2/sensor/<mac>/diagnostico`, `GET /api/v2/diagnosticos`,
  `POST /api/v2/sensor/<mac>/provedor`.

### Testes

```bash
python -m pytest -q
```

`tests/test_diagnostico.py` cobre os cenários do motor (Starlink obstruída,
desalinhada e degradada, provedor instável, DNS falhando, LAN com problema,
correlação entre sites e dados vindos do buffer); `tests/test_resiliencia.py`,
o buffer e o site sem comunicação; `tests/test_starlink.py` e
`tests/test_netdiag.py`, os coletores do Edge.
