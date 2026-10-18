# NexoPulse Edge no Linux

O `agente_v2.py` roda só em Linux, como serviço `systemd`, sem depender de sessão
gráfica. No Windows ele roda dentro do WSL (veja abaixo).

## Instalação

Na raiz do repositório:

```bash
bash linux/install.sh
```

Funciona em distros com `apt` (Debian/Ubuntu), `pacman` (Arch/CachyOS), `dnf`
(Fedora) e `zypper` (openSUSE).

O sensor aparece na Central com o **hostname** da máquina. Para outro nome,
defina `NOC_SENSOR_NOME` em `/etc/default/noc-sensor` antes do primeiro contato
(depois disso, renomeie pela tela do sensor na Central; o nome dado lá nunca é
sobrescrito).

A identidade do sensor é o MAC da placa de rede, guardado em `sensor_id.txt`
na pasta do agente: reinícios e trocas de interface não criam sensores novos.

## Windows (via WSL)

Em um PowerShell **como Administrador**, no Windows 11 22H2 ou mais novo:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\windows\instalar-nexopulse-wsl.ps1 -NomeSensor "Loja Centro"
```

O script instala o Ubuntu no WSL (na primeira vez pede para criar o usuário Linux
e rodar de novo), liga a **rede espelhada** (`networkingMode=mirrored`, para o
sensor enxergar a rede e o MAC reais), o systemd, roda `linux/install.sh` lá
dentro e cria a tarefa "NexoPulse Edge (WSL)" que liga o WSL quando o Windows
inicia. No WSL não há leitura de temperatura da CPU (o `lm-sensors` não enxerga
o hardware do Windows).

O instalador cria um ambiente virtual, instala as dependências necessárias e registra o NexoPulse Edge no serviço compatível `noc-sensor.service`.

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
