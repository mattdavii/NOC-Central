# Instalar um sensor NexoPulse Edge

O sensor é um serviço Linux. Numa máquina Windows ele roda dentro do WSL. Nos dois
casos ele **sobe sozinho quando a máquina liga** e **volta sozinho se cair**.

Central: `https://nexopulse.up.railway.app`

Versão para imprimir ou mandar ao cliente: [guia em PDF](guia-instalacao/NexoPulse-Edge-Guia-de-Instalacao.pdf)
(gerado de `guia-instalacao/guia.html`; veja o comando no topo daquele arquivo).

---

## Máquina com Linux

Debian/Ubuntu, Arch/CachyOS, Fedora ou openSUSE, com acesso à internet.

1. Abra um terminal como **usuário normal** (não root).
2. Rode:

   ```bash
   curl -fsSL https://nexopulse.up.railway.app/instalar.sh | bash
   ```

   Para escolher o nome que aparece na Central (o padrão é o nome da máquina):

   ```bash
   curl -fsSL https://nexopulse.up.railway.app/instalar.sh | NOC_SENSOR_NOME="Loja Centro" bash
   ```

3. Digite a senha do `sudo` quando pedir.
4. No fim aparece **"✅ NexoPulse Edge instalado, iniciado e configurado para subir
   com a máquina"**. Em até 1 minuto o sensor aparece na Central e o grupo
   **NexoPulse · Alertas** recebe "🔐 SENSOR VINCULADO".

Conferir: `sudo systemctl status nexopulse-edge --no-pager` (deve estar `active (running)`
e `enabled`).

---

## Máquina com Windows (via WSL)

Windows 11 versão 22H2 ou mais nova.

1. Abra o **PowerShell como Administrador** (botão direito no menu Iniciar → Terminal (Admin)).
2. Baixe e rode o instalador:

   ```powershell
   irm https://nexopulse.up.railway.app/instalar-windows.ps1 -OutFile $env:TEMP\nexopulse.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\nexopulse.ps1 -NomeSensor "Loja Centro"
   ```

3. **Primeira vez:** o script instala o Ubuntu e para. Abra o **Ubuntu** pelo menu
   Iniciar, crie o usuário e a senha do Linux, feche, e **rode o passo 2 de novo**
   (se o Windows pedir, reinicie antes).
4. Na segunda execução ele configura a rede espelhada e o systemd, instala o sensor
   dentro do Ubuntu (pede a senha do Linux), libera a porta do painel local (10000) nos
   firewalls do Windows e do Hyper-V (só para a rede local) e cria a tarefa
   **"NexoPulse Edge (WSL)"**, que liga o WSL sempre que o Windows inicia. No fim ele
   confere o serviço e o painel pelo IP da rede e mostra o endereço do painel.
5. **Reinicie o Windows uma vez** e rode a verificação (não muda nada, só confere):

   ```powershell
   irm https://nexopulse.up.railway.app/verificar-windows.ps1 -OutFile $env:TEMPerificar.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMPerificar.ps1
   ```

   Ela mostra OK / AVISO / FALHA para Windows, virtualização, WSL, rede espelhada,
   systemd, serviço, IP visto pelo sensor, painel pelo IP, firewalls e inicialização
   automática, com o que fazer em cada falha. `-Relatorio C:\verificacao.txt` grava o
   resultado num arquivo.

**Painel local:** `http://<ip do Windows>:10000`, com o usuário e a senha do painel local.

**Limites do Windows:** a máquina não pode suspender nem hibernar (o sensor para junto);
deixe a energia em "Nunca suspender". Com o Windows desligado ou suspenso, o sensor fica fora.

---

## Depois de instalar

- **Nome:** pode ser trocado na tela do sensor na Central.
- **Cliente:** vincule o sensor ao cliente na tela **Sensores**. Se o cliente tiver
  Chat ID cadastrado, o grupo dele passa a receber os alertas desse sensor.
- **Atualização:** automática, de madrugada (04h), sempre que o código do sensor
  muda na Central; se a versão nova não funcionar, o sensor volta sozinho para a
  anterior. Pode ser desligada na tela do sensor (veja `linux/README.md` → *Atualização*).
- **Ações remotas** (diagnóstico, atualizar e reiniciar o agente): ligue o
  interruptor no card **Ações remotas** da tela do sensor (só Administrador Master).
  "Atualizar sistema" e "Reiniciar máquina" exigem liberar o root nessa máquina:
  instale com `NEXOPULSE_ROOT=1` (no Windows, `-Root`) ou rode
  `bash ~/nexopulse-edge/linux/liberar-root.sh` depois (veja `linux/README.md` →
  *Ações que precisam de root*).

## Se algo der errado

| Sintoma | O que fazer |
|---|---|
| Sensor não aparece | `journalctl -u nexopulse-edge -n 50 --no-pager` e veja o erro |
| `401 Sensor não autenticado` no log | A chave mudou. Na Central, **Sensores → excluir** o sensor e reinicie o serviço: ele se vincula de novo |
| Não volta depois de reiniciar o PC (Windows) | Rode a verificação do Windows; se a tarefa só roda no logon, o sensor volta quando alguém entra no Windows |
| Painel não abre de outro computador (Windows) | Rode o instalador de novo (libera a porta nos firewalls) e confira a verificação |
| Sensor vê poucos equipamentos (Windows) | Verificação → "IP visto pelo sensor": se for 172.x, o WSL está em NAT |
| Reinstalar | Rode o mesmo comando de instalação: identidade e chave são preservadas |
