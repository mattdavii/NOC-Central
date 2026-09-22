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
