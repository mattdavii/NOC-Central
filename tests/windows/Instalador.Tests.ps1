# Roda no CI em Windows (windows-latest, Windows PowerShell 5.1 + Pester 5): .github/workflows/ci.yml
BeforeAll {
    $raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    . (Join-Path $raiz 'windows\instalar-nexopulse-wsl.ps1')
    . (Join-Path $raiz 'windows\verificar-nexopulse-windows.ps1')
    $script:raiz = $raiz
}

Describe 'Arquivos' {
    It 'tem BOM UTF-8 (o Windows PowerShell 5.1 lê acentos certos)' {
        foreach ($f in 'instalar-nexopulse-wsl.ps1', 'verificar-nexopulse-windows.ps1', 'ponte-painel-local.ps1') {
            $bytes = [System.IO.File]::ReadAllBytes((Join-Path $script:raiz "windows\$f"))
            $bytes[0..2] | Should -Be @(0xEF, 0xBB, 0xBF)
        }
    }
    It 'abre sem erro de sintaxe no Windows PowerShell' {
        foreach ($f in 'instalar-nexopulse-wsl.ps1', 'verificar-nexopulse-windows.ps1', 'ponte-painel-local.ps1') {
            $tokens = $null; $erros = $null
            [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $script:raiz "windows\$f"), [ref]$tokens, [ref]$erros) | Out-Null
            $erros | Should -BeNullOrEmpty
        }
    }
}

Describe 'Versão do Windows' {
    It 'exige build 22621 (Windows 11 22H2) para a rede espelhada' {
        Test-RedeEspelhadaSuportada 19045 | Should -BeFalse
        Test-RedeEspelhadaSuportada 22621 | Should -BeTrue
        Test-RedeEspelhadaSuportada 26100 | Should -BeTrue
    }
}

Describe 'Ubuntu ainda não pronto' {
    It 'com reinício pendente, manda reiniciar antes de abrir o Ubuntu' {
        $m = Get-MensagemDistroPendente -Distro 'Ubuntu' -Reiniciar $true
        $m[0] | Should -Be 'FALTA REINICIAR O WINDOWS.'
        ($m -join ' ') | Should -Match 'wsl --install -d Ubuntu'
    }
    It 'sem reinício pendente, manda criar o usuário Linux' {
        $m = Get-MensagemDistroPendente -Distro 'Ubuntu' -Reiniciar $false
        ($m -join ' ') | Should -Match 'crie o usuário e a senha do Linux'
        ($m -join ' ') | Should -Not -Match 'REINICIAR'
    }
    It 'Test-ReinicioPendente devolve um booleano' {
        Test-ReinicioPendente | Should -BeOfType [bool]
    }
}

Describe '.wslconfig' {
    It 'cria a seção [wsl2] num arquivo vazio' {
        $saida = @(Get-WslConfigAtualizado @())
        $saida | Should -Be @('[wsl2]', 'networkingMode=mirrored', 'vmIdleTimeout=-1')
        $saida.Count | Should -Be 3
    }
    It 'troca o modo NAT e preserva o resto do arquivo' {
        $antes = @('[wsl2]', 'memory=4GB', 'networkingMode=NAT', 'vmIdleTimeout=60000', '', '[experimental]', 'autoMemoryReclaim=gradual')
        $depois = Get-WslConfigAtualizado $antes
        ($depois -match '^networkingMode=') | Should -Be @('networkingMode=mirrored')
        ($depois -match '^vmIdleTimeout=') | Should -Be @('vmIdleTimeout=-1')
        $depois | Should -Contain 'memory=4GB'
        $depois | Should -Contain 'autoMemoryReclaim=gradual'
    }
    It 'grava o arquivo linha a linha (não "System.Object[]")' {
        $arquivo = Join-Path $env:TEMP 'wslconfig-teste'
        Set-Content -Path $arquivo -Value @(Get-WslConfigAtualizado @('[wsl2]', 'memory=4GB')) -Encoding ASCII
        Get-Content $arquivo | Should -Be @('[wsl2]', 'networkingMode=mirrored', 'vmIdleTimeout=-1', 'memory=4GB')
        Remove-Item $arquivo
    }
    It 'é idempotente (rodar o instalador de novo não duplica nada)' {
        $uma = Get-WslConfigAtualizado @('[wsl2]', 'memory=4GB')
        $duas = Get-WslConfigAtualizado $uma
        $duas | Should -Be $uma
    }
    It 'a verificação lê o que o instalador escreve' {
        $cfg = Get-WslConfigAtualizado @('[experimental]', 'networkingMode=NAT')
        Get-OpcaoWslConfig $cfg 'networkingMode' | Should -Be 'mirrored'
        Get-OpcaoWslConfig $cfg 'vmIdleTimeout' | Should -Be '-1'
        Get-OpcaoWslConfig @('[experimental]', 'networkingMode=mirrored') 'networkingMode' | Should -BeNullOrEmpty
    }
}

Describe 'Firewall (de verdade, no Windows do CI)' {
    AfterAll {
        'NexoPulse-Edge-Painel', 'NexoPulse-Edge-MNDP' | ForEach-Object {
            Remove-NetFirewallRule -Name $_ -ErrorAction SilentlyContinue
            if (Get-Command Remove-NetFirewallHyperVRule -ErrorAction SilentlyContinue) { Remove-NetFirewallHyperVRule -Name $_ -ErrorAction SilentlyContinue }
        }
    }
    It 'libera a porta do painel e o UDP 5678 só para a rede local' {
        $r = Set-RegrasFirewall -Porta 10000
        $r.Regra | Should -Be @('NexoPulse-Edge-Painel', 'NexoPulse-Edge-MNDP')
        $painel = Get-NetFirewallRule -Name 'NexoPulse-Edge-Painel'
        $painel.Direction | Should -Be 'Inbound'
        $painel.Action | Should -Be 'Allow'
        ($painel | Get-NetFirewallPortFilter).LocalPort | Should -Be '10000'
        ($painel | Get-NetFirewallAddressFilter).RemoteAddress | Should -Be 'LocalSubnet'
        ((Get-NetFirewallRule -Name 'NexoPulse-Edge-MNDP') | Get-NetFirewallPortFilter).Protocol | Should -Be 'UDP'
    }
    It 'rodar de novo não duplica as regras' {
        Set-RegrasFirewall -Porta 10000 | Out-Null
        @(Get-NetFirewallRule -Name 'NexoPulse-Edge-Painel').Count | Should -Be 1
    }
}

Describe 'Tarefa de inicialização (de verdade, no Windows do CI)' {
    AfterAll { Unregister-ScheduledTask -TaskName 'NexoPulse Edge (WSL)' -Confirm:$false -ErrorAction SilentlyContinue }
    It 'registra com gatilho no boot e no logon, rodando wsl.exe na distribuição' {
        Register-TarefaInicializacao -Distro 'Ubuntu'
        $t = Get-ScheduledTask -TaskName 'NexoPulse Edge (WSL)'
        $t.Actions[0].Execute | Should -Be 'wsl.exe'
        $t.Actions[0].Arguments | Should -Be '-d Ubuntu --exec /bin/sleep infinity'
        # Sem limite de tempo e reinicia se cair: é ela que mantém o Ubuntu ligado.
        $t.Settings.ExecutionTimeLimit | Should -Be 'PT0S'
        $t.Settings.RestartCount | Should -Be 999
        @($t.Triggers).Count | Should -Be 2
        $t.Principal.LogonType | Should -Be 'S4U'
        $t.Settings.DisallowStartIfOnBatteries | Should -BeFalse
    }
    It 'pode ser refeita para rodar no logon (quando o S4U não sobe o WSL)' {
        Register-TarefaInicializacao -Distro 'Ubuntu' -LogonType Interactive
        $t = Get-ScheduledTask -TaskName 'NexoPulse Edge (WSL)'
        $t.Principal.LogonType | Should -Be 'Interactive'
        $t.Actions[0].Execute | Should -Be 'conhost.exe'
        $t.Actions[0].Arguments | Should -Be '--headless wsl.exe -d Ubuntu --exec /bin/sleep infinity'
    }
}

Describe 'Painel e IP' {
    It 'Wait-Painel desiste no prazo e devolve 0 quando nada responde' {
        $inicio = Get-Date
        Wait-Painel 'http://127.0.0.1:9/' -Segundos 4 | Should -Be 0
        ((Get-Date) - $inicio).TotalSeconds | Should -BeLessThan 30
    }
    It 'Test-Painel devolve 0 quando nada responde' {
        Test-Painel 'http://127.0.0.1:1/login' | Should -Be 0
    }
    It 'acha o IPv4 da rede com gateway' {
        Get-IpRedeLocal | Should -Match '^\d+\.\d+\.\d+\.\d+$'
    }
}

Describe 'Verificação numa máquina sem o sensor' {
    It 'roda até o fim, aponta as falhas e sai com código 1' {
        $saida = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $script:raiz 'windows\verificar-nexopulse-windows.ps1') -Relatorio "$env:TEMP\nexo-verif.txt" 2>&1 | Out-String
        $LASTEXITCODE | Should -Be 1
        $saida | Should -Match 'NexoPulse Edge no Windows'
        $saida | Should -Match 'FALHA'
        Get-Content "$env:TEMP\nexo-verif.txt" -Raw | Should -Match 'Windows'
    }
}
