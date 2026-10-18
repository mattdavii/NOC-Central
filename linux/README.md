# NexoPulse Edge no Linux

O `agente_v2.py` roda só em Linux, como serviço `systemd`, sem depender de sessão
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
dependências e registra o serviço `noc-sensor` no systemd com:

- **início automático no boot** (`systemctl enable`): a máquina liga, o sensor sobe;
- **`Restart=always` sem limite de tentativas**: se o agente cair, volta em 5 s.

Reinstalar pelo mesmo comando preserva a identidade (`sensor_id.txt`) e a chave
(`sensor_key.txt`) do sensor.

## Diagnóstico de rede e Starlink

O Edge mede a rede em camadas (gateway, provedor, internet, DNS) e lê a antena
Starlink quando houver. Variáveis, modo mock, modo bypass da Starlink e as regras
de diagnóstico estão no [README principal](../README.md#diagnóstico-de-rede).

## Windows (via WSL)

No Windows 11 22H2 ou mais novo, baixe o instalador e rode num PowerShell
**como Administrador**:

```powershell
irm https://nexopulse.up.railway.app/instalar-windows.ps1 -OutFile instalar-nexopulse.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\instalar-nexopulse.ps1 -NomeSensor "Loja Centro"
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
  `/etc/default/noc-sensor`.

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
sudo systemctl status noc-sensor --no-pager
journalctl -u noc-sensor -f
sudo systemctl restart noc-sensor
```

As opções de runtime ficam em `/etc/default/noc-sensor`. Os valores padrão são:

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

Para ações administrativas, o sensor tenta o wrapper `/usr/local/sbin/minipc-admin` quando ele existe. Caso contrário, usa apenas `sudo -n`, sem bloquear esperando senha.

## Atualização para 2.2.0-rc1

Execute no Mini PC como o usuário que instalou o sensor. O caminho é obtido do próprio serviço:

```bash
cd "$(systemctl show noc-sensor.service -p WorkingDirectory --value)"
git status --short
```

Se houver alterações locais, preserve/revise-as antes de prosseguir. Não use reset --hard. Com a árvore limpa:

```bash
git branch "rollback/linux-$(date -u +%Y%m%dT%H%M%SZ)" HEAD
git fetch origin e5537ed24da09add6b3b541e03fe89c1577a1344
git switch --detach e5537ed24da09add6b3b541e03fe89c1577a1344
.venv/bin/python -m pip install -r requirements-agente.txt
.venv/bin/python -m py_compile agente_v2.py
sudo systemctl restart noc-sensor.service
sudo systemctl is-active noc-sensor.service
sudo systemctl status noc-sensor.service --no-pager
journalctl -u noc-sensor.service -n 80 --no-pager
```

O SHA acima é o commit imutável validado pelo CI que contém o hardening posterior ao RC original. A tag `v2.2.0-rc1` permanece imutável no primeiro RC e, sozinha, não contém essas correções. Depois que esse commit estiver em uso, `linux/update.sh` também aceitará tags ou um SHA completo nas próximas atualizações. Não reinstale o serviço nem apague bancos locais. `/etc/default/noc-sensor` é preservado.

Na Central, confira `Agente 2.2.0-rc1`, horário do último contato, IP/CIDR/interface, MAC, velocidade, RX/TX e dispositivos SEM ICMP. Acesse Configurações do sensor pelo celular no local e capture a localização; revise a precisão e salve. A Central deve ser acessada por HTTPS.

Fallback aproximado por IP é opcional: adicione `NOC_IP_GEOLOCATION=1` a `/etc/default/noc-sensor` e reinicie. Isso consulta o provedor ipwho.is a partir do Mini PC. Não substitui uma localização manual/do navegador. Sem essa opção, não há consulta externa de geolocalização.

## Credencial individual do Mini PC

Na Central, abra **Sensores**, clique em **Gerar chave** no Mini PC e copie o valor exibido uma única vez. No Mini PC:

```bash
sudoedit /etc/default/noc-sensor
```

Adicione uma linha, sem aspas nem espaços ao redor de `=`:

```text
NOC_SENSOR_API_KEY=CHAVE_COPIADA_DA_CENTRAL
```

Salve e valide sem imprimir a chave nos logs:

```bash
sudo systemctl restart noc-sensor.service
sudo systemctl is-active noc-sensor.service
journalctl -u noc-sensor.service -n 80 --no-pager
```

Confirme na Central que o último contato continua atualizando. Repita para cada sensor (Linux ou WSL) antes de definir `NOC_SENSOR_AUTH_REQUIRED=1` no Railway. Gerar a chave não bloqueia agentes antigos enquanto essa variável permanecer `0`.

Para rollback do Mini PC, selecione a branch `rollback/linux-...` criada acima, reinstale `requirements-agente.txt` e reinicie o mesmo serviço. Se a credencial precisar ser removida, apague somente a linha `NOC_SENSOR_API_KEY` de `/etc/default/noc-sensor`, reinicie o serviço e mantenha a Central em modo de compatibilidade até corrigir o sensor.
