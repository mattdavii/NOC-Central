#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Instala o sensor NexoPulse Edge num Windows, rodando como Linux dentro do WSL.

.DESCRIPTION
  1. Confere se o Windows suporta a rede espelhada do WSL (Windows 11 22H2+).
  2. Instala a distribuição (Ubuntu por padrão), se ainda não existir.
  3. Configura %UserProfile%\.wslconfig: networkingMode=mirrored (o sensor enxerga
     a rede e o MAC reais) e vmIdleTimeout=-1 (o WSL não desliga sozinho).
  4. Liga o systemd dentro da distribuição (o sensor roda como serviço systemd).
  5. Baixa o agente da Central e roda o instalador Linux dentro do WSL.
  6. Libera no firewall do Windows e no do Hyper-V (o que vale para o WSL espelhado)
     a porta do painel local (10000) e a descoberta MikroTik (UDP 5678), só para a rede local.
  7. Cria uma tarefa agendada que liga o WSL quando o Windows inicia.
  8. Confere se ficou tudo de pé: serviço, painel pelo IP da rede e inicialização automática.

  Rode num PowerShell como Administrador:
    Set-ExecutionPolicy -Scope Process Bypass
    .\instalar-nexopulse-wsl.ps1 -NomeSensor "Loja Centro"
  Com -Root, libera também "Atualizar sistema" e "Reiniciar máquina".
  Para conferir depois (ou depois de reiniciar): verificar-nexopulse-windows.ps1
#>
param(
    [string]$Distro = 'Ubuntu',
    [string]$Central = 'https://nexopulse.up.railway.app',
    [string]$NomeSensor = '',
    # Libera "Atualizar sistema" e "Reiniciar máquina" (a Central passa a poder fazer isso no Linux do WSL).
    [switch]$Root,
    [int]$PortaPainel = 10000
)

# Identificador do WSL no firewall do Hyper-V (o mesmo em todas as máquinas).
$script:WSL_VM_CREATOR_ID = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
$script:NOME_TAREFA = 'NexoPulse Edge (WSL)'

function Passo($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }

function Invoke-Nativo([scriptblock]$Comando) {
    <# Programa externo (wsl.exe) sem o modo 'Stop': no Windows PowerShell 5.1, qualquer texto
       no stderr de um programa externo vira erro e derrubaria o script. Devolve a saída. #>
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Comando 2>$null } finally { $ErrorActionPreference = $anterior }
}

function Test-ReinicioPendente {
    <# O Windows está esperando reiniciar para terminar de instalar um recurso (ex.: a plataforma de
       máquina virtual do WSL). Enquanto isso, o Ubuntu não abre e o instalador não tem como seguir. #>
    foreach ($chave in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
                       'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') {
        if (Test-Path $chave) { return $true }
    }
    return $false
}

function Get-MensagemDistroPendente([string]$Distro, [bool]$Reiniciar) {
    <# O que fazer quando a distribuição ainda não está pronta: com reinício pendente, reiniciar vem primeiro. #>
    if ($Reiniciar) {
        return @(
            'FALTA REINICIAR O WINDOWS.',
            "1. Reinicie o Windows agora (o WSL só funciona depois disso).",
            "2. Depois de reiniciar, abra o '$Distro' pelo menu Iniciar e crie o usuário e a senha do Linux.",
            "   Se o '$Distro' não aparecer no menu, rode no Terminal (Admin): wsl --install -d $Distro",
            '3. Rode este mesmo comando de instalação de novo.'
        )
    }
    return @(
        "$Distro instalado.",
        "1. Abra o '$Distro' pelo menu Iniciar e crie o usuário e a senha do Linux (anote a senha).",
        '2. Feche o Ubuntu e rode este mesmo comando de instalação de novo.'
    )
}

function Test-RedeEspelhadaSuportada([int]$Build) {
    # Rede espelhada exige Windows 11 22H2 (build 22621) ou mais novo.
    return $Build -ge 22621
}

function Get-WslConfigAtualizado([string[]]$Linhas) {
    <# Devolve o .wslconfig com networkingMode=mirrored e vmIdleTimeout=-1 na seção [wsl2],
       mantendo o resto do arquivo (outras seções e opções) como estava. #>
    $limpas = @($Linhas | Where-Object { $_ -notmatch '^\s*(networkingMode|vmIdleTimeout)\s*=' })
    if (-not ($limpas -match '^\s*\[wsl2\]\s*$')) { $limpas += '[wsl2]' }
    $saida = @()
    foreach ($l in $limpas) {
        $saida += $l
        if ($l -match '^\s*\[wsl2\]\s*$') { $saida += 'networkingMode=mirrored'; $saida += 'vmIdleTimeout=-1' }
    }
    return $saida
}

function Set-RegrasFirewall([int]$Porta) {
    <# Painel local (TCP) e descoberta MikroTik (UDP 5678, broadcast que chega sem pedido).
       No modo espelhado, o que entra no WSL passa pelo firewall do Hyper-V; o firewall do
       Windows vale para o próprio Windows. Libera nos dois, só para a rede local. #>
    $regras = @(
        @{ Nome = 'NexoPulse-Edge-Painel'; Exibir = "NexoPulse Edge - painel local (TCP $Porta)"; Protocolo = 'TCP'; Porta = $Porta },
        @{ Nome = 'NexoPulse-Edge-MNDP';   Exibir = 'NexoPulse Edge - descoberta MikroTik (UDP 5678)'; Protocolo = 'UDP'; Porta = 5678 }
    )
    $resultado = @()
    foreach ($r in $regras) {
        Remove-NetFirewallRule -Name $r.Nome -ErrorAction SilentlyContinue
        New-NetFirewallRule -Name $r.Nome -DisplayName $r.Exibir -Direction Inbound -Action Allow `
            -Protocol $r.Protocolo -LocalPort $r.Porta -RemoteAddress LocalSubnet -Profile Any | Out-Null
        $hyperv = $false
        if (Get-Command New-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
            # Tira a regra antiga (de uma instalação anterior) antes de criar; se ainda assim já existir
            # (o Remove por nome nem sempre acha a regra do Hyper-V), a regra que está lá serve.
            Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq $r.Nome } |
                Remove-NetFirewallHyperVRule -ErrorAction SilentlyContinue
            try {
                New-NetFirewallHyperVRule -Name $r.Nome -DisplayName $r.Exibir -Direction Inbound -Action Allow `
                    -VMCreatorId $script:WSL_VM_CREATOR_ID -Protocol $r.Protocolo -LocalPorts $r.Porta -ErrorAction Stop | Out-Null
            } catch {
                if (-not (Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq $r.Nome })) { throw }
            }
            $hyperv = $true
        }
        $resultado += [pscustomobject]@{ Regra = $r.Nome; Windows = $true; HyperV = $hyperv }
    }
    return $resultado
}

function Get-IpRedeLocal {
    # IPv4 da interface que tem gateway padrão (a rede do site), ignorando a virtual do WSL.
    $cfg = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
        Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
    if ($cfg) { return ($cfg.IPv4Address | Select-Object -First 1).IPAddress }
    return $null
}

function Register-TarefaInicializacao([string]$Distro, [string]$LogonType = 'S4U') {
    $acao = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument "-d $Distro --exec /bin/true"
    $gatilhos = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME))
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType $LogonType -RunLevel Limited
    $config = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $script:NOME_TAREFA -Action $acao -Trigger $gatilhos -Principal $principal -Settings $config -Force | Out-Null
}

function Test-TarefaInicializacao([string]$Distro) {
    <# Roda a tarefa agora e vê se terminou bem. Algumas versões do WSL não sobem pela tarefa
       "sem usuário logado" (S4U); nesse caso a tarefa é refeita para rodar no logon. #>
    Start-ScheduledTask -TaskName $script:NOME_TAREFA
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        $info = Get-ScheduledTaskInfo -TaskName $script:NOME_TAREFA
        if ((Get-ScheduledTask -TaskName $script:NOME_TAREFA).State -ne 'Running' -and $info.LastRunTime -gt (Get-Date).AddMinutes(-2)) { break }
    }
    $codigo = (Get-ScheduledTaskInfo -TaskName $script:NOME_TAREFA).LastTaskResult
    if ($codigo -eq 0) { return 'boot' }
    Write-Host "  A tarefa sem usuário logado falhou (código $codigo). Refazendo para rodar no logon do Windows." -ForegroundColor Yellow
    Register-TarefaInicializacao -Distro $Distro -LogonType Interactive
    return 'logon'
}

function Wait-Painel([string]$Url, [int]$Segundos = 90) {
    <# Tenta o painel até ele responder 200 ou acabar o prazo; devolve o último código (0 = nada respondeu). #>
    $fim = (Get-Date).AddSeconds($Segundos)
    do {
        $codigo = Test-Painel $Url
        if ($codigo -eq 200) { return $codigo }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $fim)
    return $codigo
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

function Invoke-Instalacao {
    $ErrorActionPreference = 'Stop'

    # 1. Versão do Windows
    $build = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
    if (-not (Test-RedeEspelhadaSuportada $build)) {
        Write-Host "Este Windows (build $build) não suporta a rede espelhada do WSL." -ForegroundColor Red
        Write-Host "Sem ela o sensor enxergaria só a rede virtual do WSL. Atualize para o Windows 11 22H2+."
        exit 1
    }

    # 2. Distribuição Linux.
    Passo "Verificando o WSL e a distribuição $Distro"
    $instaladas = (Invoke-Nativo { wsl.exe --list --quiet }) -replace "`0", '' | Where-Object { $_.Trim() }
    if (-not ($instaladas -contains $Distro)) {
        wsl.exe --install -d $Distro
        Write-Host ''
        foreach ($linha in (Get-MensagemDistroPendente -Distro $Distro -Reiniciar (Test-ReinicioPendente))) {
            Write-Host $linha -ForegroundColor Yellow
        }
        exit 0
    }
    Invoke-Nativo { wsl.exe --update } | Out-Null   # rede espelhada estável pede o WSL da Store (2.x)

    # 3. .wslconfig: rede espelhada e sem desligamento por ociosidade.
    Passo 'Configurando a rede espelhada do WSL'
    $wslconfig = Join-Path $env:USERPROFILE '.wslconfig'
    $linhas = @()
    if (Test-Path $wslconfig) {
        Copy-Item $wslconfig "$wslconfig.bak-$(Get-Date -Format yyyyMMddHHmmss)"
        $linhas = @(Get-Content $wslconfig)
    }
    Set-Content -Path $wslconfig -Value @(Get-WslConfigAtualizado $linhas) -Encoding ASCII

    # 4. systemd dentro da distribuição.
    Passo 'Ativando o systemd no WSL'
    Invoke-Nativo { wsl.exe -d $Distro -u root -- sh -c "grep -q '^systemd=true' /etc/wsl.conf 2>/dev/null || printf '\n[boot]\nsystemd=true\n' >> /etc/wsl.conf" } | Out-Null
    Invoke-Nativo { wsl.exe --shutdown } | Out-Null
    Start-Sleep -Seconds 3

    # 5. Instalador Linux da própria Central (pede a senha do usuário Linux para o sudo).
    Passo 'Instalando o NexoPulse Edge dentro do WSL'
    $nome = $NomeSensor -replace "'", ''
    $raiz = if ($Root) { 1 } else { 0 }
    $ErrorActionPreference = 'Continue'   # o instalador Linux escreve progresso no stderr
    wsl.exe -d $Distro -- bash -lc "command -v curl >/dev/null || (sudo apt-get update && sudo apt-get install -y curl); curl -fsSL '$Central/instalar.sh' | NOC_SENSOR_NOME='$nome' NEXOPULSE_ROOT=$raiz bash"
    if ($LASTEXITCODE -ne 0) { Write-Host 'O instalador Linux terminou com erro; veja as mensagens acima.' -ForegroundColor Red; exit 1 }
    $ErrorActionPreference = 'Stop'

    # 6. Firewall
    Passo "Liberando o painel local (porta $PortaPainel) na rede do site"
    Set-RegrasFirewall -Porta $PortaPainel | Format-Table -AutoSize | Out-String | Write-Host

    # 7. Inicialização automática
    Passo 'Criando a inicialização automática'
    Register-TarefaInicializacao -Distro $Distro
    $modo = Test-TarefaInicializacao -Distro $Distro

    # 8. Conferência final
    Passo 'Conferindo a instalação'
    $ativo = $false
    for ($i = 0; $i -lt 45 -and -not $ativo; $i++) {
        $estado = (Invoke-Nativo { wsl.exe -d $Distro -- systemctl is-active nexopulse-edge } | Out-String).Trim()
        $ativo = $estado -eq 'active'
        if (-not $ativo) { Start-Sleep -Seconds 2 }
    }
    $ip = Get-IpRedeLocal
    # O serviço fica "active" assim que o processo nasce; o painel abre alguns segundos depois.
    $local = Wait-Painel "http://127.0.0.1:$PortaPainel/login"
    $rede = if ($ip) { Wait-Painel "http://${ip}:$PortaPainel/login" -Segundos 20 } else { 0 }
    $ok = { param($c) if ($c) { 'OK' } else { 'FALHOU' } }
    Write-Host ("  Serviço nexopulse-edge ........ " + (& $ok $ativo))
    Write-Host ("  Painel neste PC (127.0.0.1) .... " + (& $ok ($local -eq 200)))
    Write-Host ("  Painel pelo IP da rede ($ip) .. " + (& $ok ($rede -eq 200)))
    Write-Host ("  Liga sozinho ................... " + $(if ($modo -eq 'boot') { 'ao ligar o Windows' } else { 'ao entrar no Windows (com usuário logado)' }))

    Passo 'Pronto'
    Write-Host 'O sensor deve aparecer na Central em alguns segundos, com o nome deste computador.'
    if ($ip) { Write-Host "Painel local: http://${ip}:$PortaPainel  (usuário e senha do painel local)" -ForegroundColor Green }
    if (-not $ativo -or $local -ne 200 -or $rede -ne 200) {
        Write-Host 'Algo não ficou de pé. Rode verificar-nexopulse-windows.ps1 para ver o motivo.' -ForegroundColor Yellow
    }
    Write-Host 'Depois de reiniciar o Windows, rode verificar-nexopulse-windows.ps1 para confirmar que voltou sozinho.'
}

# Carregado com ". .\instalar-nexopulse-wsl.ps1" (testes): só define as funções.
if ($MyInvocation.InvocationName -ne '.') { Invoke-Instalacao }
