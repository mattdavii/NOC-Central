<#
.SYNOPSIS
  Confere se o sensor NexoPulse Edge está pronto neste Windows (via WSL) e diz o que falta.

.DESCRIPTION
  Não muda nada na máquina: só lê e testa. Rode num PowerShell (de preferência como
  Administrador, para ler as regras de firewall) depois de instalar e depois de reiniciar:
    powershell -ExecutionPolicy Bypass -File verificar-nexopulse-windows.ps1
  Com -Relatorio, grava o resultado num arquivo para mandar ao suporte.
#>
param(
    [string]$Distro = 'Ubuntu',
    [int]$PortaPainel = 10000,
    [string]$Relatorio = ''
)

$script:resultados = @()

function Add-Item([string]$Item, [string]$Estado, [string]$Detalhe = '', [string]$Correcao = '') {
    $script:resultados += [pscustomobject]@{ Item = $Item; Estado = $Estado; Detalhe = $Detalhe; Correcao = $Correcao }
}

function Invoke-Nativo([scriptblock]$Comando) {
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Comando 2>$null } finally { $ErrorActionPreference = $anterior }
}

function Get-OpcaoWslConfig([string[]]$Linhas, [string]$Opcao) {
    # Valor de uma opção dentro da seção [wsl2] do .wslconfig (ou $null).
    $secao = ''
    foreach ($l in $Linhas) {
        if ($l -match '^\s*\[(.+?)\]\s*$') { $secao = $Matches[1].ToLower(); continue }
        if ($secao -eq 'wsl2' -and $l -match "^\s*$Opcao\s*=\s*(.+?)\s*$") { return $Matches[1] }
    }
    return $null
}

function Test-Painel([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 -MaximumRedirection 0 -ErrorAction Stop
        return [int]$r.StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

function Invoke-Verificacao {
    # 1. Windows
    $so = Get-CimInstance Win32_OperatingSystem
    $build = [int]$so.BuildNumber
    if ($build -ge 22621) { Add-Item 'Windows' 'OK' "$($so.Caption) (build $build)" }
    else { Add-Item 'Windows' 'FALHA' "build $build" 'Rede espelhada do WSL exige Windows 11 22H2 (build 22621) ou mais novo.' }

    # 2. Virtualização
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $hyper = (Get-CimInstance Win32_ComputerSystem).HypervisorPresent
    if ($hyper -or $cpu.VirtualizationFirmwareEnabled) { Add-Item 'Virtualização' 'OK' $(if ($hyper) { 'hipervisor ativo' } else { 'habilitada na BIOS' }) }
    else { Add-Item 'Virtualização' 'FALHA' 'desligada' 'Ligue a virtualização (Intel VT-x / AMD-V) na BIOS/UEFI.' }

    # 3. WSL
    $versao = (Invoke-Nativo { wsl.exe --version }) -replace "`0", '' | Select-Object -First 1
    if ($versao -match '(\d+\.\d+\.\d+)') {
        $v = [version]$Matches[1]
        if ($v -ge [version]'2.0.0') { Add-Item 'WSL' 'OK' "versão $v" }
        else { Add-Item 'WSL' 'AVISO' "versão $v" 'Rode "wsl --update": a rede espelhada pede o WSL 2.0 ou mais novo.' }
    } else { Add-Item 'WSL' 'FALHA' 'não encontrado ou versão antiga (sem "wsl --version")' 'Rode "wsl --install" ou "wsl --update".' }

    $distros = (Invoke-Nativo { wsl.exe --list --verbose }) -replace "`0", '' | Where-Object { $_.Trim() } | Select-Object -Skip 1
    $linha = $distros | Where-Object { ($_ -replace '^\s*\*?\s*', '') -match "^$([regex]::Escape($Distro))\s" } | Select-Object -First 1
    if (-not $linha) { Add-Item "Distribuição $Distro" 'FALHA' 'não instalada' 'Rode o instalador do NexoPulse (ele instala o Ubuntu).' }
    elseif ($linha -match '\s1\s*$') { Add-Item "Distribuição $Distro" 'FALHA' 'rodando como WSL 1' "Rode: wsl --set-version $Distro 2" }
    else { Add-Item "Distribuição $Distro" 'OK' (($linha -replace '\s+', ' ').Trim()) }

    # 4. .wslconfig
    $arquivo = Join-Path $env:USERPROFILE '.wslconfig'
    $linhas = if (Test-Path $arquivo) { @(Get-Content $arquivo) } else { @() }
    $modo = Get-OpcaoWslConfig $linhas 'networkingMode'
    if ($modo -eq 'mirrored') { Add-Item 'Rede espelhada' 'OK' 'networkingMode=mirrored' }
    else { Add-Item 'Rede espelhada' 'FALHA' "networkingMode=$(if ($modo) { $modo } else { 'não definido (NAT)' })" 'Rode o instalador de novo ou ponha networkingMode=mirrored em [wsl2] no .wslconfig e rode "wsl --shutdown".' }
    $ocioso = Get-OpcaoWslConfig $linhas 'vmIdleTimeout'
    if ($ocioso -eq '-1') { Add-Item 'WSL sempre ligado' 'OK' 'vmIdleTimeout=-1' }
    else { Add-Item 'WSL sempre ligado' 'AVISO' "vmIdleTimeout=$ocioso" 'Sem vmIdleTimeout=-1 o WSL pode desligar sozinho quando ninguém usa o terminal.' }

    if (-not $linha) { return }

    # 5. Dentro do Linux
    $systemd = (Invoke-Nativo { wsl.exe -d $Distro -- sh -c 'ps -p 1 -o comm=' } | Out-String).Trim()
    if ($systemd -eq 'systemd') { Add-Item 'systemd no WSL' 'OK' }
    else { Add-Item 'systemd no WSL' 'FALHA' "PID 1 = $systemd" 'Ponha [boot] systemd=true em /etc/wsl.conf e rode "wsl --shutdown".' }

    $servico = (Invoke-Nativo { wsl.exe -d $Distro -- systemctl is-active nexopulse-edge } | Out-String).Trim()
    if ($servico -eq 'active') { Add-Item 'Serviço nexopulse-edge' 'OK' 'ativo' }
    else { Add-Item 'Serviço nexopulse-edge' 'FALHA' $servico "Veja: wsl -d $Distro -- journalctl -u nexopulse-edge -n 50 --no-pager" }

    $ipLinux = (Invoke-Nativo { wsl.exe -d $Distro -- sh -c "ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p'" } | Out-String).Trim()
    if ($ipLinux -match '^172\.(1[6-9]|2\d|3[01])\.') {
        Add-Item 'IP visto pelo sensor' 'FALHA' "$ipLinux (rede virtual do WSL)" 'O WSL está em NAT: o sensor não enxerga a rede do site. Confira a rede espelhada e rode "wsl --shutdown".'
    } elseif ($ipLinux) { Add-Item 'IP visto pelo sensor' 'OK' $ipLinux }

    $vizinhos = (Invoke-Nativo { wsl.exe -d $Distro -- sh -c 'ip neigh show | grep -c lladdr' } | Out-String).Trim()
    if ([int]("0$vizinhos") -gt 1) { Add-Item 'Equipamentos visíveis (ARP)' 'OK' "$vizinhos na tabela de vizinhos" }
    else { Add-Item 'Equipamentos visíveis (ARP)' 'AVISO' "$vizinhos na tabela de vizinhos" 'Poucos vizinhos: normal logo após ligar; se continuar, confira a rede espelhada e o cabo/Wi-Fi.' }

    # 6. Painel local
    $cfg = Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
    $ip = if ($cfg) { ($cfg.IPv4Address | Select-Object -First 1).IPAddress } else { $null }
    $local = Test-Painel "http://127.0.0.1:$PortaPainel/login"
    if ($local -eq 200) { Add-Item 'Painel neste PC' 'OK' "http://127.0.0.1:$PortaPainel" }
    else { Add-Item 'Painel neste PC' 'FALHA' "resposta $local" 'O serviço não está respondendo na porta do painel.' }
    if ($ip) {
        $rede = Test-Painel "http://${ip}:$PortaPainel/login"
        if ($rede -eq 200) { Add-Item 'Painel pelo IP da rede' 'OK' "http://${ip}:$PortaPainel" }
        else { Add-Item 'Painel pelo IP da rede' 'FALHA' "http://${ip}:$PortaPainel respondeu $rede" 'Rode o instalador de novo (ele libera a porta nos firewalls do Windows e do Hyper-V).' }
        $perfil = (Get-NetConnectionProfile -InterfaceIndex $cfg.InterfaceIndex -ErrorAction SilentlyContinue).NetworkCategory
        if ($perfil) { Add-Item 'Perfil da rede do Windows' $(if ($perfil -eq 'Public') { 'AVISO' } else { 'OK' }) "$perfil" $(if ($perfil -eq 'Public') { 'Rede marcada como Pública: as regras do NexoPulse valem, mas outros programas podem bloquear descoberta.' } else { '' }) }
    }

    # 7. Firewall
    $regra = Get-NetFirewallRule -Name 'NexoPulse-Edge-Painel' -ErrorAction SilentlyContinue
    if ($regra -and $regra.Enabled -eq 'True') { Add-Item 'Firewall do Windows' 'OK' 'porta do painel liberada para a rede local' }
    else { Add-Item 'Firewall do Windows' 'AVISO' 'regra do NexoPulse não encontrada' 'Rode o instalador de novo, como Administrador.' }
    if (Get-Command Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
        $hv = Get-NetFirewallHyperVRule -Name 'NexoPulse-Edge-Painel' -ErrorAction SilentlyContinue
        if ($hv) { Add-Item 'Firewall do Hyper-V (WSL)' 'OK' 'porta do painel liberada' }
        else { Add-Item 'Firewall do Hyper-V (WSL)' 'AVISO' 'regra do NexoPulse não encontrada' 'Sem ela, outros computadores da rede podem não abrir o painel. Rode o instalador de novo.' }
    }

    # 8. Inicialização automática
    $tarefa = Get-ScheduledTask -TaskName 'NexoPulse Edge (WSL)' -ErrorAction SilentlyContinue
    if (-not $tarefa) { Add-Item 'Liga sozinho com o Windows' 'FALHA' 'tarefa agendada não existe' 'Rode o instalador de novo.' }
    else {
        $info = Get-ScheduledTaskInfo -TaskName 'NexoPulse Edge (WSL)'
        $quando = if ($tarefa.Principal.LogonType -eq 'Interactive') { 'só depois que alguém entra no Windows' } else { 'ao ligar o Windows' }
        $mantem = ($tarefa.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -match 'sleep infinity'
        if (-not $mantem) {
            # Tarefa de instalações antigas: só acordava o Ubuntu, que desligava ~20 s depois.
            Add-Item 'Liga sozinho com o Windows' 'FALHA' 'tarefa antiga: liga o Ubuntu e ele desliga sozinho segundos depois' 'Rode o instalador de novo (ele refaz a tarefa para manter o Ubuntu ligado).'
        } elseif ($tarefa.State -eq 'Running') { Add-Item 'Liga sozinho com o Windows' 'OK' "$quando; mantendo o Ubuntu ligado desde $($info.LastRunTime)" }
        else { Add-Item 'Liga sozinho com o Windows' 'FALHA' "a tarefa não está rodando (último código $($info.LastTaskResult)); o Ubuntu desliga sozinho" "Rode: Start-ScheduledTask -TaskName 'NexoPulse Edge (WSL)'  (se não ficar rodando, rode o instalador de novo)" }
        $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
        if ($info.LastRunTime -lt $boot) { Add-Item 'Subiu depois do último boot' 'AVISO' "boot em $boot; tarefa rodou antes" 'A tarefa não rodou desde que o Windows ligou: o sensor pode só voltar quando alguém entrar.' }
    }
}

function Show-Resultado {
    $cores = @{ OK = 'Green'; AVISO = 'Yellow'; FALHA = 'Red' }
    Write-Host "`nNexoPulse Edge no Windows - verificação ($(Get-Date -Format 'dd/MM/yyyy HH:mm'))`n" -ForegroundColor Cyan
    foreach ($r in $script:resultados) {
        Write-Host ("[{0,-5}] {1}" -f $r.Estado, $r.Item) -ForegroundColor $cores[$r.Estado] -NoNewline
        if ($r.Detalhe) { Write-Host " - $($r.Detalhe)" } else { Write-Host '' }
        if ($r.Correcao) { Write-Host "        -> $($r.Correcao)" -ForegroundColor DarkGray }
    }
    $falhas = @($script:resultados | Where-Object Estado -eq 'FALHA').Count
    $avisos = @($script:resultados | Where-Object Estado -eq 'AVISO').Count
    Write-Host ''
    if ($falhas) { Write-Host "$falhas problema(s) impedem o sensor de funcionar direito." -ForegroundColor Red }
    elseif ($avisos) { Write-Host "Funcionando, com $avisos ponto(s) de atenção." -ForegroundColor Yellow }
    else { Write-Host 'Tudo certo.' -ForegroundColor Green }
    if ($Relatorio) {
        $script:resultados | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Set-Content -Path $Relatorio -Encoding UTF8
        Write-Host "Relatório salvo em $Relatorio"
    }
    return $falhas
}

# Carregado com ". .\verificar-nexopulse-windows.ps1" (testes): só define as funções.
if ($MyInvocation.InvocationName -ne '.') {
    Invoke-Verificacao
    $falhas = Show-Resultado
    exit $(if ($falhas) { 1 } else { 0 })
}
