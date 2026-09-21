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
- painel local: porta 10000.

O agente usa `ip neigh` no Linux, suporta sensores térmicos AMD/Intel expostos por `lm-sensors`/psutil e não depende de ícone de bandeja quando executado headless.

Para ações administrativas, o sensor tenta o wrapper `/usr/local/sbin/minipc-admin` quando ele existe. Caso contrário, usa apenas `sudo -n`, sem bloquear esperando senha.
