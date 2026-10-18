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
  5. Clona o repositório e roda linux/install.sh dentro do WSL.
  6. Cria uma tarefa agendada que liga o WSL quando o Windows inicia.

  Rode num PowerShell como Administrador:
    Set-ExecutionPolicy -Scope Process Bypass
    .\instalar-nexopulse-wsl.ps1 -NomeSensor "Loja Centro"
#>
param(
    [string]$Distro = 'Ubuntu',
    [string]$Repositorio = 'https://github.com/mattdavii/NOC-Central.git',
    [string]$Branch = 'main',
    [string]$NomeSensor = ''
)
$ErrorActionPreference = 'Stop'

function Passo($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }

# 1. Rede espelhada exige Windows 11 22H2 (build 22621) ou mais novo.
$build = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
if ($build -lt 22621) {
    Write-Host "Este Windows (build $build) não suporta a rede espelhada do WSL." -ForegroundColor Red
    Write-Host "Sem ela o sensor enxergaria só a rede virtual do WSL. Atualize para o Windows 11 22H2+."
    exit 1
}

# 2. Distribuição Linux.
Passo "Verificando o WSL e a distribuição $Distro"
$instaladas = (wsl.exe --list --quiet 2>$null) -replace "`0", '' | Where-Object { $_.Trim() }
if (-not ($instaladas -contains $Distro)) {
    wsl.exe --install -d $Distro
    Write-Host "`n$Distro foi instalado. Abra o '$Distro' pelo menu Iniciar, crie o usuário Linux" -ForegroundColor Yellow
    Write-Host "e depois rode este script de novo (pode ser preciso reiniciar o Windows)." -ForegroundColor Yellow
    exit 0
}

# 3. .wslconfig: rede espelhada e sem desligamento por ociosidade.
Passo 'Configurando a rede espelhada do WSL'
$wslconfig = Join-Path $env:USERPROFILE '.wslconfig'
$linhas = @()
if (Test-Path $wslconfig) {
    Copy-Item $wslconfig "$wslconfig.bak-$(Get-Date -Format yyyyMMddHHmmss)"
    $linhas = Get-Content $wslconfig | Where-Object { $_ -notmatch '^\s*(networkingMode|vmIdleTimeout)\s*=' }
}
if (-not ($linhas -match '^\s*\[wsl2\]\s*$')) { $linhas += '[wsl2]' }
$saida = @()
foreach ($l in $linhas) {
    $saida += $l
    if ($l -match '^\s*\[wsl2\]\s*$') { $saida += 'networkingMode=mirrored'; $saida += 'vmIdleTimeout=-1' }
}
Set-Content -Path $wslconfig -Value $saida -Encoding ASCII

# 4. systemd dentro da distribuição.
Passo 'Ativando o systemd no WSL'
wsl.exe -d $Distro -u root -- sh -c "grep -q '^systemd=true' /etc/wsl.conf 2>/dev/null || printf '\n[boot]\nsystemd=true\n' >> /etc/wsl.conf"
wsl.exe --shutdown
Start-Sleep -Seconds 3

# 5. Repositório e instalador Linux (pede a senha do usuário Linux para o sudo).
Passo 'Instalando o NexoPulse Edge dentro do WSL'
$script = @"
set -e
command -v git >/dev/null || (sudo apt-get update && sudo apt-get install -y git)
if [ ! -d "`$HOME/NOC-Central/.git" ]; then git clone --branch '$Branch' '$Repositorio' "`$HOME/NOC-Central"; fi
cd "`$HOME/NOC-Central"
bash linux/install.sh
if [ -n '$NomeSensor' ]; then
  sudo sed -i '/^#\? *NOC_SENSOR_NOME=/d' /etc/default/noc-sensor
  echo 'NOC_SENSOR_NOME=$NomeSensor' | sudo tee -a /etc/default/noc-sensor >/dev/null
  sudo systemctl restart noc-sensor
fi
"@
wsl.exe -d $Distro -- bash -lc $script

# 6. Liga o WSL na inicialização do Windows; o systemd sobe o sensor.
Passo 'Criando a inicialização automática'
$acao = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument "-d $Distro --exec /bin/true"
$gatilhos = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME))
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName 'NexoPulse Edge (WSL)' -Action $acao -Trigger $gatilhos -Principal $principal -Force | Out-Null

Passo 'Pronto'
Write-Host 'O sensor deve aparecer na Central em alguns segundos, com o nome deste computador.'
Write-Host "Status: wsl -d $Distro -- systemctl status noc-sensor --no-pager"
