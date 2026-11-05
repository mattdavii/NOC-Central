<#
.SYNOPSIS
  Vigia do NexoPulse Edge no Windows: religa o sensor sozinho se ele parar, sem precisar reiniciar o PC.

.DESCRIPTION
  O sensor roda como serviço do Linux dentro do WSL. A tarefa "NexoPulse Edge (WSL)" liga o Ubuntu com o
  Windows e o mantém ligado, mas se o wsl.exe dela morrer (atualização do WSL, "wsl --shutdown", falha do
  WSL) o Agendador de Tarefas não a liga de novo: o "reiniciar se falhar" dele só vale quando a tarefa não
  consegue nem começar. O vigia roda a cada 2 minutos e, a cada vez:
    1. Se a tarefa que mantém o Ubuntu ligado não está rodando, liga de novo.
    2. Se o serviço nexopulse-edge não está ativo, reinicia o serviço.
    3. Se o serviço está ativo mas o painel não responde há 3 rodadas seguidas (travado), reinicia o serviço.
    4. Se o próprio WSL não responde há 3 rodadas seguidas, desliga o WSL e liga de novo.
  Só escreve no registro (C:\ProgramData\NexoPulse\vigia.log) quando age ou quando o estado muda.
  Para uma manutenção sem o vigia religar o sensor: crie o arquivo C:\ProgramData\NexoPulse\vigia.pausado
  (e apague depois).

  Instalar (PowerShell como Administrador, no PC do sensor):
    powershell -ExecutionPolicy Bypass -File vigia-nexopulse.ps1 -Instalar
  A instalação também tira a suspensão do PC na tomada (um sensor dormindo é um sensor desligado).
  Remover:  powershell -ExecutionPolicy Bypass -File vigia-nexopulse.ps1 -Remover
#>
param(
    [switch]$Instalar,
    [switch]$Remover,
    [string]$Distro = 'Ubuntu',
    [int]$PortaPainel = 10000,
    # Não mexe na suspensão do PC (padrão: na tomada, nunca suspender nem hibernar).
    [switch]$ManterSuspensao
)

$script:PASTA = Join-Path $env:ProgramData 'NexoPulse'
$script:NOME_TAREFA = 'NexoPulse Edge (WSL)'
$script:NOME_VIGIA = 'NexoPulse Vigia'
$script:ARQUIVO_ESTADO = Join-Path $script:PASTA 'vigia-estado.json'
$script:ARQUIVO_LOG = Join-Path $script:PASTA 'vigia.log'
$script:ARQUIVO_PAUSA = Join-Path $script:PASTA 'vigia.pausado'
$script:LIMITE_FALHAS = 3          # rodadas seguidas (2 min cada) antes de agir no painel ou no WSL
$script:LINHAS_LOG = 1000
$script:ESTE_ARQUIVO = $PSCommandPath   # guardado aqui: dentro de uma função carregada pelos testes, seria outro arquivo

# ------------------------------------------------------------------ decisão (sem efeitos; testada no CI)
function Get-DecisaoVigia {
    <# O que fazer nesta rodada. $Servico: saída do "systemctl is-active" ('' = o WSL não respondeu).
       $Painel: código HTTP do painel visto de dentro do Ubuntu (0 = não respondeu; -1 = não testado).
       $Estado: contadores da rodada anterior. Devolve a ação e os contadores novos. #>
    param([bool]$TarefaRodando, [string]$Servico, [int]$Painel, [hashtable]$Estado)
    $novo = @{ falhas_wsl = [int]$Estado.falhas_wsl; falhas_painel = [int]$Estado.falhas_painel }
    if (-not $TarefaRodando) {
        return @{ Acao = 'ligar-ubuntu'; Estado = $novo; Motivo = 'a tarefa que mantém o Ubuntu ligado não estava rodando' }
    }
    if (-not $Servico) {
        $novo.falhas_wsl++
        $novo.falhas_painel = 0
        if ($novo.falhas_wsl -ge $script:LIMITE_FALHAS) {
            $novo.falhas_wsl = 0
            return @{ Acao = 'reiniciar-wsl'; Estado = $novo; Motivo = "o WSL não respondeu em $($script:LIMITE_FALHAS) rodadas seguidas" }
        }
        return @{ Acao = 'esperar'; Estado = $novo; Motivo = "o WSL não respondeu ($($novo.falhas_wsl) de $($script:LIMITE_FALHAS))" }
    }
    $novo.falhas_wsl = 0
    # 'activating'/'reloading': o serviço está subindo (ou se atualizando); não atrapalha.
    if ($Servico -in @('activating', 'reloading', 'deactivating')) {
        $novo.falhas_painel = 0
        return @{ Acao = 'nada'; Estado = $novo; Motivo = "serviço $Servico" }
    }
    if ($Servico -ne 'active') {
        $novo.falhas_painel = 0
        return @{ Acao = 'reiniciar-servico'; Estado = $novo; Motivo = "serviço nexopulse-edge estava '$Servico'" }
    }
    if ($Painel -eq 200 -or $Painel -lt 0) {
        $novo.falhas_painel = 0
        return @{ Acao = 'nada'; Estado = $novo; Motivo = 'tudo de pé' }
    }
    $novo.falhas_painel++
    if ($novo.falhas_painel -ge $script:LIMITE_FALHAS) {
        $novo.falhas_painel = 0
        return @{ Acao = 'reiniciar-servico'; Estado = $novo; Motivo = "serviço ativo, mas o painel não respondeu em $($script:LIMITE_FALHAS) rodadas seguidas (código $Painel)" }
    }
    return @{ Acao = 'esperar'; Estado = $novo; Motivo = "painel não respondeu (código $Painel, $($novo.falhas_painel) de $($script:LIMITE_FALHAS))" }
}

function Get-LinhasLogAparadas([string[]]$Linhas, [int]$Maximo) {
    if ($Linhas.Count -le $Maximo) { return $Linhas }
    return $Linhas[($Linhas.Count - $Maximo)..($Linhas.Count - 1)]
}

# ------------------------------------------------------------------ efeitos
function Write-Registro([string]$Texto) {
    New-Item -ItemType Directory -Path $script:PASTA -Force | Out-Null
    $linha = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Texto"
    $atuais = @()
    if (Test-Path $script:ARQUIVO_LOG) { $atuais = @(Get-Content $script:ARQUIVO_LOG -Encoding UTF8) }
    Set-Content -Path $script:ARQUIVO_LOG -Value (Get-LinhasLogAparadas ($atuais + $linha) $script:LINHAS_LOG) -Encoding UTF8
}

function Read-EstadoVigia {
    $padrao = @{ falhas_wsl = 0; falhas_painel = 0; ultimo = '' }
    if (-not (Test-Path $script:ARQUIVO_ESTADO)) { return $padrao }
    try {
        $o = Get-Content $script:ARQUIVO_ESTADO -Raw | ConvertFrom-Json
        return @{ falhas_wsl = [int]$o.falhas_wsl; falhas_painel = [int]$o.falhas_painel; ultimo = "$($o.ultimo)" }
    } catch { return $padrao }
}

function Save-EstadoVigia([hashtable]$Estado) {
    New-Item -ItemType Directory -Path $script:PASTA -Force | Out-Null
    ($Estado | ConvertTo-Json -Compress) | Set-Content -Path $script:ARQUIVO_ESTADO -Encoding ASCII
}

function Invoke-WslComPrazo([string]$Argumentos, [int]$Segundos = 60, [string]$Entrada = $null) {
    <# Roda o wsl.exe e devolve a saída; '' se ele travar além do prazo (o vigia não pode ficar preso).
       $Entrada vai pela entrada padrão (scripts de shell sem brigar com as aspas do Windows). #>
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'wsl.exe'
    $psi.Arguments = $Argumentos
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = [bool]$Entrada
    try { $p = [System.Diagnostics.Process]::Start($psi) } catch { return '' }
    if ($Entrada) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($Entrada -replace "`r`n", "`n") + "`n")
        $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
        $p.StandardInput.Close()
    }
    $saida = $p.StandardOutput.ReadToEndAsync()
    $null = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($Segundos * 1000)) {
        try { $p.Kill() } catch { }
        return ''
    }
    return ($saida.Result -replace "`0", '').Trim()
}

function Get-TarefaRodando {
    $t = Get-ScheduledTask -TaskName $script:NOME_TAREFA -ErrorAction SilentlyContinue
    return [bool]($t -and $t.State -eq 'Running')
}

function Start-Ubuntu {
    $t = Get-ScheduledTask -TaskName $script:NOME_TAREFA -ErrorAction SilentlyContinue
    if ($t) {
        if ($t.State -eq 'Disabled') { Enable-ScheduledTask -TaskName $script:NOME_TAREFA | Out-Null }
        Start-ScheduledTask -TaskName $script:NOME_TAREFA
    } else {
        # Sem a tarefa (instalação antiga ou apagada): mantém o Ubuntu ligado por conta própria.
        Start-Process -FilePath 'conhost.exe' -ArgumentList "--headless wsl.exe -d $Distro --exec /bin/sleep infinity" -WindowStyle Hidden
    }
}

function Get-CodigoPainel {
    # De dentro do Ubuntu, pelos IPs da máquina (na rede espelhada o 127.0.0.1 pode não chegar ao painel).
    $sh = @'
c=0
for ip in $(hostname -I) 127.0.0.1; do
  case "$ip" in *:*) continue ;; esac
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$ip:PORTA/login")
  [ "$c" = 200 ] && break
done
echo "$c"
'@ -replace 'PORTA', $PortaPainel
    $saida = Invoke-WslComPrazo "-d $Distro --exec sh -s" 40 $sh
    $codigo = 0
    if ([int]::TryParse(($saida -replace '\D', ''), [ref]$codigo)) { return $codigo }
    return 0
}


function Invoke-Vigia {
    if (Test-Path $script:ARQUIVO_PAUSA) { return }
    $estado = Read-EstadoVigia
    $rodando = Get-TarefaRodando
    $servico = ''
    $painel = -1
    if ($rodando) {
        $servico = Invoke-WslComPrazo "-d $Distro -- systemctl is-active nexopulse-edge" 60
        if ($servico -eq 'active') { $painel = Get-CodigoPainel }
    }
    $d = Get-DecisaoVigia -TarefaRodando $rodando -Servico $servico -Painel $painel -Estado $estado
    switch ($d.Acao) {
        'ligar-ubuntu' {
            Write-Registro "AÇÃO ligar o Ubuntu: $($d.Motivo)"
            Start-Ubuntu
            Start-Sleep -Seconds 20
            $s = Invoke-WslComPrazo "-d $Distro -- systemctl is-active nexopulse-edge" 90
            if ($s -ne 'active' -and $s -ne 'activating') {
                Invoke-WslComPrazo "-d $Distro -u root -- systemctl restart nexopulse-edge" 90 | Out-Null
                $s = Invoke-WslComPrazo "-d $Distro -- systemctl is-active nexopulse-edge" 60
            }
            Write-Registro "  resultado: serviço '$s'"
        }
        'reiniciar-servico' {
            Write-Registro "AÇÃO reiniciar o serviço: $($d.Motivo)"
            Invoke-WslComPrazo "-d $Distro -u root -- systemctl restart nexopulse-edge" 90 | Out-Null
            Write-Registro "  resultado: serviço '$(Invoke-WslComPrazo "-d $Distro -- systemctl is-active nexopulse-edge" 60)'"
        }
        'reiniciar-wsl' {
            Write-Registro "AÇÃO reiniciar o WSL: $($d.Motivo)"
            Invoke-WslComPrazo '--shutdown' 120 | Out-Null
            Start-Sleep -Seconds 5
            Stop-ScheduledTask -TaskName $script:NOME_TAREFA -ErrorAction SilentlyContinue
            Start-Ubuntu
            Start-Sleep -Seconds 30
            Write-Registro "  resultado: serviço '$(Invoke-WslComPrazo "-d $Distro -- systemctl is-active nexopulse-edge" 90)'"
        }
        default {
            # Só registra quando o estado muda (o registro não enche com "tudo certo" a cada 2 minutos).
            if ($d.Motivo -ne $estado.ultimo) { Write-Registro "estado: $($d.Motivo)" }
        }
    }
    $d.Estado.ultimo = if ($d.Acao -in @('nada', 'esperar')) { $d.Motivo } else { "depois de $($d.Acao)" }
    Save-EstadoVigia $d.Estado
}

# ------------------------------------------------------------------ instalação
function Install-Vigia {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { Write-Host 'Rode como Administrador (PowerShell > Executar como administrador).' -ForegroundColor Red; exit 1 }

    New-Item -ItemType Directory -Path $script:PASTA -Force | Out-Null
    # A tarefa roda como o usuário (não como Administrador): ele precisa escrever o registro e o estado.
    icacls.exe $script:PASTA /grant "$env:USERDOMAIN\$($env:USERNAME):(OI)(CI)M" /T /Q | Out-Null
    $destino = Join-Path $script:PASTA 'vigia-nexopulse.ps1'
    if ($script:ESTE_ARQUIVO -and ((Resolve-Path $script:ESTE_ARQUIVO).Path -ne $destino)) { Copy-Item $script:ESTE_ARQUIVO $destino -Force }

    # Roda do mesmo jeito que a tarefa que mantém o Ubuntu ligado: sem ninguém logado (S4U) quando ela
    # consegue; senão, no logon do usuário (algumas versões do WSL não sobem sem usuário logado).
    $mantem = Get-ScheduledTask -TaskName $script:NOME_TAREFA -ErrorAction SilentlyContinue
    if (-not $mantem) {
        Write-Host "A tarefa '$($script:NOME_TAREFA)' não existe: o vigia vai manter o Ubuntu ligado por conta própria." -ForegroundColor Yellow
        Write-Host 'O ideal é rodar o instalador do NexoPulse de novo (ele cria essa tarefa).' -ForegroundColor Yellow
    }
    $tipo = if ($mantem -and $mantem.Principal.LogonType -eq 'S4U') { 'S4U' } else { 'Interactive' }
    $argumentos = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$destino`" -Distro $Distro -PortaPainel $PortaPainel"
    $acao = if ($tipo -eq 'Interactive') {
        New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless powershell.exe $argumentos"   # sem janela piscando
    } else {
        New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argumentos
    }
    $repetir = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 2)
    $gatilhos = @($repetir, (New-ScheduledTaskTrigger -AtStartup))
    if ($tipo -eq 'Interactive') { $gatilhos += New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME }
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType $tipo -RunLevel Limited
    $config = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 8) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $script:NOME_VIGIA -Action $acao -Trigger $gatilhos -Principal $principal -Settings $config `
        -Description 'Religa o sensor NexoPulse Edge (WSL) se ele parar. Registro em C:\ProgramData\NexoPulse\vigia.log' -Force | Out-Null

    if (-not $ManterSuspensao) {
        # Na tomada, o PC não suspende nem hiberna (na bateria, segue como estava).
        powercfg.exe /change standby-timeout-ac 0 | Out-Null
        powercfg.exe /change hibernate-timeout-ac 0 | Out-Null
        Write-Host '  Suspensão na tomada: desligada (o PC não dorme mais sozinho).'
    }

    Remove-Item $script:ARQUIVO_ESTADO -ErrorAction SilentlyContinue
    Write-Registro "vigia instalado (roda a cada 2 minutos; $(if ($tipo -eq 'S4U') { 'mesmo sem ninguém logado' } else { 'com o usuário logado' }))"
    Start-ScheduledTask -TaskName $script:NOME_VIGIA
    Write-Host '  Conferindo o sensor agora (até 2 minutos)...'
    $fim = (Get-Date).AddMinutes(2)
    do { Start-Sleep -Seconds 5 } while ((Get-ScheduledTask -TaskName $script:NOME_VIGIA).State -eq 'Running' -and (Get-Date) -lt $fim)
    $servico = Invoke-WslComPrazo "-d $Distro -- systemctl is-active nexopulse-edge" 60
    Write-Host ''
    Write-Host ("  Vigia ............ instalado, a cada 2 minutos " + $(if ($tipo -eq 'S4U') { '(mesmo sem ninguém logado)' } else { '(com o usuário logado)' })) -ForegroundColor Green
    Write-Host ("  Sensor agora ..... " + $(if ($servico -eq 'active') { 'ligado' } else { "'$servico' (o vigia tenta de novo em 2 minutos)" })) -ForegroundColor $(if ($servico -eq 'active') { 'Green' } else { 'Yellow' })
    Write-Host "  Registro ......... $($script:ARQUIVO_LOG)"
    if (Test-Path $script:ARQUIVO_LOG) { Get-Content $script:ARQUIVO_LOG -Tail 5 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
}

function Uninstall-Vigia {
    Unregister-ScheduledTask -TaskName $script:NOME_VIGIA -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $script:ARQUIVO_ESTADO -ErrorAction SilentlyContinue
    Write-Host 'Vigia removido. O sensor continua ligando com o Windows, mas não volta sozinho se parar.'
}

# Carregado com ". .\vigia-nexopulse.ps1" (testes): só define as funções.
if ($MyInvocation.InvocationName -ne '.') {
    if ($Instalar) { Install-Vigia }
    elseif ($Remover) { Uninstall-Vigia }
    else { Invoke-Vigia }
}
