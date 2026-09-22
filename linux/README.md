# NOC Sensor no Linux

O `agente_v2.py` pode rodar como serviço `systemd`, sem depender de sessão gráfica.

## Instalação

Na raiz do repositório:

```bash
bash linux/install.sh
```

O instalador cria um ambiente virtual, instala as dependências necessárias e registra o serviço `noc-sensor.service`.

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
git fetch origin tag v2.2.0-rc1
git switch --detach v2.2.0-rc1
.venv/bin/python -m pip install -r requirements-agente.txt
.venv/bin/python -m py_compile agente_v2.py
sudo systemctl restart noc-sensor.service
sudo systemctl is-active noc-sensor.service
sudo systemctl status noc-sensor.service --no-pager
journalctl -u noc-sensor.service -n 80 --no-pager
```

A tag deve estar publicada antes desses comandos. As próximas atualizações podem usar `bash linux/update.sh v2.2.0-rc1`, que também preserva uma branch de rollback. Não reinstale o serviço nem apague bancos locais. `/etc/default/noc-sensor` é preservado.

Na Central, confira `Agente 2.2.0-rc1`, horário do último contato, IP/CIDR/interface, MAC, velocidade, RX/TX e dispositivos SEM ICMP. Acesse Configurações do sensor pelo celular no local e capture a localização; revise a precisão e salve. A Central deve ser acessada por HTTPS.

Fallback aproximado por IP é opcional: adicione `NOC_IP_GEOLOCATION=1` a `/etc/default/noc-sensor` e reinicie. Isso consulta o provedor ipwho.is a partir do Mini PC. Não substitui uma localização manual/do navegador. Sem essa opção, não há consulta externa de geolocalização.

Não ative `NOC_SENSOR_API_KEY` na Central antes de atualizar todos os agentes Windows. Para rollback do Mini PC, selecione a branch `rollback/linux-...` criada acima, reinstale `requirements-agente.txt` e reinicie o mesmo serviço.
