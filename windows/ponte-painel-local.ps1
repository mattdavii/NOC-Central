<#
  Ponte do painel local para o próprio Windows onde o sensor roda (NexoPulse Edge no WSL).

  Na rede espelhada do WSL, em algumas versões do Windows 11 o próprio PC não alcança o Ubuntu
  (nem por 127.0.0.1 nem pelo IP da rede), embora os outros aparelhos da rede alcancem. Esta ponte
  escuta em http://127.0.0.1:10010 no Windows (a 10080 os navegadores bloqueiam: ERR_UNSAFE_PORT) e leva cada conexão até o painel pelo próprio
  wsl.exe (entrada e saída padrão), sem depender da rede entre Windows e WSL.

  Uso:
    powershell -ExecutionPolicy Bypass -File ponte-painel-local.ps1 -Instalar   # instala e liga
    powershell -ExecutionPolicy Bypass -File ponte-painel-local.ps1             # roda a ponte (a tarefa usa)
#>
param(
    [switch]$Instalar,
    [string]$Distro = 'Ubuntu',
    [int]$PortaPainel = 10000,
    [int]$PortaLocal = 10010
)

$script:PASTA = Join-Path $env:ProgramData 'NexoPulse'
$script:NOME_TAREFA_PONTE = 'NexoPulse Painel local (neste PC)'
$script:AJUDANTE_LINUX = '/usr/local/bin/nexopulse-ponte'

# Ajudante que roda dentro do Ubuntu: liga a entrada/saída padrão ao painel. De dentro do Ubuntu o
# 127.0.0.1 pode não funcionar na rede espelhada, então tenta primeiro os IPs da própria máquina.
$script:AJUDANTE_PY = @'
#!/usr/bin/env python3
# NEXOPULSE-PONTE-INICIO
import os, socket, subprocess, sys, threading

porta = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
try:
    ips = [ip for ip in subprocess.run(['hostname', '-I'], capture_output=True, text=True).stdout.split() if ':' not in ip]
except OSError:
    ips = []
conexao = None
for ip in ips + ['127.0.0.1']:
    try:
        conexao = socket.create_connection((ip, porta), timeout=3)
        break
    except OSError:
        continue
if conexao is None:
    sys.exit(1)
conexao.settimeout(None)


def subir():
    while True:
        dados = os.read(0, 65536)
        if not dados:
            break
        conexao.sendall(dados)
    try:
        conexao.shutdown(socket.SHUT_WR)
    except OSError:
        pass


threading.Thread(target=subir, daemon=True).start()
while True:
    dados = conexao.recv(65536)
    if not dados:
        break
    while dados:
        escritos = os.write(1, dados)
        dados = dados[escritos:]
# NEXOPULSE-PONTE-FIM
'@

function Install-Ponte {
    New-Item -ItemType Directory -Force -Path $script:PASTA | Out-Null
    $destino = Join-Path $script:PASTA 'ponte-painel-local.ps1'
    if ($PSCommandPath -and ($PSCommandPath -ne $destino)) { Copy-Item -Path $PSCommandPath -Destination $destino -Force }

    # Grava o ajudante dentro do Ubuntu (como root) pela entrada padrão do wsl.exe.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'wsl.exe'
    $psi.Arguments = "-d $Distro -u root -- sh -c 'cat > $script:AJUDANTE_LINUX && chmod 755 $script:AJUDANTE_LINUX'"
    $psi.UseShellExecute = $false; $psi.RedirectStandardInput = $true; $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($script:AJUDANTE_PY -replace "`r`n", "`n") + "`n")
    $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $p.StandardInput.Close(); $p.WaitForExit()

    # Tarefa no logon do usuário, sem janela (conhost --headless), que volta se cair.
    $argumentos = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$destino`" -Distro $Distro -PortaPainel $PortaPainel -PortaLocal $PortaLocal"
    $acao = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument $argumentos
    $gatilho = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    $config = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $script:NOME_TAREFA_PONTE -Action $acao -Trigger $gatilho -Principal $principal -Settings $config -Force | Out-Null
    Stop-ScheduledTask -TaskName $script:NOME_TAREFA_PONTE -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $script:NOME_TAREFA_PONTE

    # Atalho na área de trabalho de todos os usuários.
    $atalho = Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Painel NexoPulse.url'
    Set-Content -Path $atalho -Value @('[InternetShortcut]', "URL=http://127.0.0.1:$PortaLocal/") -Encoding ASCII

    Start-Sleep -Seconds 5
    $codigo = 0
    try { $codigo = [int](Invoke-WebRequest -Uri "http://127.0.0.1:$PortaLocal/login" -UseBasicParsing -TimeoutSec 15).StatusCode } catch { }
    if ($codigo -eq 200) {
        Write-Host "Pronto: abra http://127.0.0.1:$PortaLocal neste PC (atalho 'Painel NexoPulse' na área de trabalho)." -ForegroundColor Green
    } else {
        Write-Host "A ponte foi instalada, mas o painel ainda não respondeu (código $codigo)." -ForegroundColor Yellow
        Write-Host "Confira se o sensor está ligado (wsl -l -v deve mostrar Running) e tente o endereço de novo em 1 minuto." -ForegroundColor Yellow
    }
}

function New-ConexaoPonte([System.Net.Sockets.TcpClient]$Cliente) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'wsl.exe'
    $psi.Arguments = "-d $Distro --exec python3 $script:AJUDANTE_LINUX $PortaPainel"
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true
    $processo = [System.Diagnostics.Process]::Start($psi)
    return [pscustomobject]@{
        Cliente = $Cliente; Rede = $Cliente.GetStream(); Processo = $processo
        Entrada = $processo.StandardInput.BaseStream; Saida = $processo.StandardOutput.BaseStream
        BufSobe = New-Object byte[] 65536; BufDesce = New-Object byte[] 65536
        Leitura = $null; EntradaFechada = $false; Fim = $false; Desde = Get-Date
    }
}

function Close-ConexaoPonte($c) {
    foreach ($acao in { $c.Cliente.Close() }, { if (-not $c.Processo.HasExited) { $c.Processo.Kill() } }, { $c.Processo.Dispose() }) {
        try { & $acao } catch { }
    }
    $c.Fim = $true
}

function Step-ConexaoPonte($c) {
    <# Um passo da cópia nos dois sentidos, sem bloquear: navegador -> Ubuntu e Ubuntu -> navegador. #>
    $mexeu = $false
    try {
        if (-not $c.EntradaFechada) {
            if ($c.Rede.DataAvailable) {
                $n = $c.Rede.Read($c.BufSobe, 0, $c.BufSobe.Length)
                if ($n -gt 0) { $c.Entrada.Write($c.BufSobe, 0, $n); $c.Entrada.Flush(); $mexeu = $true }
            } elseif ($c.Cliente.Client.Poll(0, [System.Net.Sockets.SelectMode]::SelectRead) -and $c.Cliente.Client.Available -eq 0) {
                $c.Entrada.Close(); $c.EntradaFechada = $true   # o navegador terminou de enviar
            }
        }
        if ($null -eq $c.Leitura) { $c.Leitura = $c.Saida.ReadAsync($c.BufDesce, 0, $c.BufDesce.Length) }
        if ($c.Leitura.IsCompleted) {
            $n = $c.Leitura.Result
            $c.Leitura = $null
            if ($n -le 0) { Close-ConexaoPonte $c; return $true }
            $c.Rede.Write($c.BufDesce, 0, $n); $mexeu = $true
        }
        if (((Get-Date) - $c.Desde).TotalMinutes -gt 10) { Close-ConexaoPonte $c }   # conexão esquecida
    } catch {
        Close-ConexaoPonte $c
        return $true
    }
    return $mexeu
}

function Start-Ponte {
    $ouvinte = New-Object System.Net.Sockets.TcpListener ([System.Net.IPAddress]::Loopback), $PortaLocal
    $ouvinte.Start()
    $conexoes = New-Object System.Collections.ArrayList
    while ($true) {
        $mexeu = $false
        while ($ouvinte.Pending()) {
            $cliente = $ouvinte.AcceptTcpClient()
            try { [void]$conexoes.Add((New-ConexaoPonte $cliente)) } catch { $cliente.Close() }
            $mexeu = $true
        }
        foreach ($c in @($conexoes)) {
            if (Step-ConexaoPonte $c) { $mexeu = $true }
            if ($c.Fim) { $conexoes.Remove($c) }
        }
        if (-not $mexeu) { Start-Sleep -Milliseconds 15 }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    if ($Instalar) { Install-Ponte } else { Start-Ponte }
}
