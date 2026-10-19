# NexoPulse Edge no Linux

O `nexopulse_edge.py` roda só em Linux, como serviço `systemd`, sem depender de sessão
gráfica. No Windows ele roda dentro do WSL (veja abaixo).

## Instalação (Linux)

Numa máquina com Linux (Debian/Ubuntu, Arch/CachyOS, Fedora ou openSUSE), como
usuário normal (o instalador pede `sudo` quando precisa):

```bash
curl -fsSL https://nexopulse.up.railway.app/instalar.sh | bash
```

Com um nome diferente do hostname:

```bash
curl -fsSL https://nexopulse.up.railway.app/instalar.sh | NOC_SENSOR_NOME="Loja Centro" bash
```

O instalador baixa o agente da Central para `~/nexopulse-edge`, instala as
dependências e registra o serviço `nexopulse-edge` no systemd com:

- **início automático no boot** (`systemctl enable`): a máquina liga, o sensor sobe;
- **`Restart=always` sem limite de tentativas**: se o agente cair, volta em 5 s.

Reinstalar pelo mesmo comando preserva a identidade (`sensor_id.txt`) e a chave
(`sensor_key.txt`) do sensor. Uma instalação antiga (serviço `noc-sensor`) é
migrada sozinha: o serviço velho é removido e `/etc/default/noc-sensor` vira
`/etc/default/nexopulse-edge`.

Na máquina fica só o necessário para o sensor rodar (agente, módulos e
instalador); documentação e instalador do Windows ficam no servidor. O coletor
Starlink só é instalado se a antena responder; para instalar depois:
`NOC_STARLINK=on bash ~/nexopulse-edge/linux/install.sh`.

## Diagnóstico de rede e Starlink

O Edge mede a rede em camadas (gateway, provedor, internet, DNS) e lê a antena
Starlink quando houver. Variáveis, modo mock, modo bypass da Starlink e as regras
de diagnóstico estão no [README principal](../README.md#diagnóstico-de-rede).

## Windows (via WSL)

No Windows 11 22H2 ou mais novo, baixe o instalador e rode num PowerShell
**como Administrador**:

```powershell
irm https://nexopulse.up.railway.app/instalar-windows.ps1 -OutFile $env:TEMP\nexopulse.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\nexopulse.ps1 -NomeSensor "Loja Centro"
```

O script instala o Ubuntu no WSL (na primeira vez pede para criar o usuário
Linux e rodar de novo), liga a **rede espelhada** (`networkingMode=mirrored`,
para o sensor enxergar a rede e o MAC reais), o systemd e roda o mesmo
instalador Linux lá dentro. Também cria a tarefa agendada **"NexoPulse Edge
(WSL)"**, que liga o WSL quando o Windows inicia — o systemd do WSL então sobe o
sensor. No WSL não há leitura de temperatura da CPU.

## Identidade e chave do sensor

- O sensor aparece na Central com o **hostname** (ou `NOC_SENSOR_NOME`); o nome
  dado na tela da Central nunca é sobrescrito.
- A identidade é o MAC da placa de rede, guardado em `sensor_id.txt`.
- No primeiro uso o agente gera uma chave em `sensor_key.txt` (permissão 600). A
  Central a vincula ao sensor no primeiro contato, avisa o grupo NexoPulse no
  Telegram e passa a exigi-la. Para trocar a chave de um sensor, use
  **Gerar chave** na tela Sensores e coloque o valor em `NOC_SENSOR_API_KEY` no
  `/etc/default/nexopulse-edge`.

## Ações remotas

Na tela do sensor, o **Administrador Master** pode ligar as ações remotas daquele
sensor e executar: coletar diagnóstico, atualizar o agente, reiniciar o agente,
atualizar o sistema e reiniciar a máquina. Cada uso pede a senha, fica em
Incidentes e avisa o grupo NexoPulse no Telegram. O agente só executa essa lista
fixa — a Central nunca manda comando livre.

### Ações que precisam de root

"Atualizar o sistema" e "Reiniciar a máquina" precisam de root. O agente roda
como usuário comum e só consegue fazê-las se você liberar, nesta máquina, o
ajudante `nexopulse-admin` (que aceita apenas `update-system`, `reboot`,
`restart-agent` e `start-service`). **Isso dá à Central o poder de atualizar e
reiniciar a máquina — libere só onde fizer sentido.** Na pasta do agente:

```bash
sudo install -o root -g root -m 0755 linux/nexopulse-admin /usr/local/sbin/nexopulse-admin
echo "$USER ALL=(root) NOPASSWD: /usr/local/sbin/nexopulse-admin" | sudo tee /etc/sudoers.d/nexopulse-edge
sudo chmod 0440 /etc/sudoers.d/nexopulse-edge && sudo visudo -c
```

Para desfazer: `sudo rm /etc/sudoers.d/nexopulse-edge /usr/local/sbin/nexopulse-admin`.

## Operação

```bash
sudo systemctl status nexopulse-edge --no-pager
journalctl -u nexopulse-edge -f
sudo systemctl restart nexopulse-edge
```

As opções de runtime ficam em `/etc/default/nexopulse-edge` (depois de editar,
reinicie o serviço). Os valores padrão são:

- telemetria: 5 s;
- watchdog: 15 s;
- varredura de topologia: 60 s;
- descoberta ativa: até 512 hosts por ciclo (`NOC_MAX_SCAN_HOSTS`);
- painel local: porta 10000.

O sensor identifica a interface da rota padrão, IP/CIDR, MAC e velocidade do link.
Em redes maiores que o limite de descoberta, mantém o CIDR real informado na
Central, mas restringe a varredura ativa ao /24 local para evitar tráfego excessivo.
Os valores RX/TX representam somente a interface do sensor e não o tráfego total da LAN.

O agente usa `ip neigh` no Linux, suporta sensores térmicos AMD/Intel expostos por `lm-sensors`/psutil e não depende de ícone de bandeja quando executado headless.

Fallback aproximado por IP é opcional: adicione `NOC_IP_GEOLOCATION=1` a `/etc/default/nexopulse-edge` e reinicie. Isso consulta o provedor ipwho.is a partir da máquina do sensor. Não substitui uma localização manual/do navegador. Sem essa opção, não há consulta externa de geolocalização.

## Atualização

Pela Central: na tela do sensor, **Atualizar agente** baixa o pacote da versão
atual e reinicia o serviço, preservando identidade, chave e fila local. Ou, na
máquina, rode de novo o comando de instalação.

## Credencial individual do sensor

Na Central, abra **Sensores**, clique em **Gerar chave** no sensor e copie o valor exibido uma única vez. Na máquina do sensor:

```bash
sudoedit /etc/default/nexopulse-edge
```

Adicione uma linha, sem aspas nem espaços ao redor de `=`:

```text
NOC_SENSOR_API_KEY=CHAVE_COPIADA_DA_CENTRAL
```

Salve e valide sem imprimir a chave nos logs:

```bash
sudo systemctl restart nexopulse-edge
sudo systemctl is-active nexopulse-edge
journalctl -u nexopulse-edge -n 80 --no-pager
```

Confirme na Central que o último contato continua atualizando. Repita para cada sensor (Linux ou WSL) antes de definir `NOC_SENSOR_AUTH_REQUIRED=1` no Railway. Gerar a chave não bloqueia agentes antigos enquanto essa variável permanecer `0`. Para remover a credencial, apague somente a linha `NOC_SENSOR_API_KEY` e reinicie o serviço.
