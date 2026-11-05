"""Guias de instalação do sensor NexoPulse Edge (PDF para os técnicos).

O conteúdo fica aqui, como dados; templates/guias/instalacao.html desenha o PDF. Os comandos usam o
endereço da Central que gerou o guia, então o PDF está sempre de acordo com a versão em uso.
Para mudar um passo, mude aqui (e o que o instalador faz em linux/install.sh ou windows/).
"""
import os
from datetime import datetime

import hub
from version import CENTRAL_VERSION

RAIZ = os.path.dirname(os.path.abspath(__file__))
SISTEMAS = ('linux', 'windows')


def _passo(titulo, texto='', codigo=None, nota=None):
    return {'titulo': titulo, 'texto': texto, 'codigo': codigo, 'nota': nota}


def _comum_central():
    """O que fazer na Central depois que o sensor aparece (igual nos dois sistemas)."""
    return {
        'titulo': 'Deixar o sensor pronto na Central',
        'intro': 'O sensor aparece sozinho na Central em até 1 minuto, na tela NexoPulse > Sensores, no grupo "Sem empresa".',
        'passos': [
            _passo('Alocar a empresa', 'Em Sensores, escolha a empresa dona do local. Até isso, só a MD vê o sensor.'),
            _passo('Dar o nome do local', 'Use o nome que o cliente reconhece (ex.: "UFV Alto Paraná - Sala de controle"). '
                   'O nome dado na Central nunca é trocado pelo sensor.'),
            _passo('Marcar a localização', 'Na página do sensor, em Configurações: "Capturar neste aparelho" com o celular no '
                   'local, "Capturar no sensor", ou clique no mapa.'),
            _passo('Conferir o provedor', 'O sensor descobre o provedor sozinho; corrija no lápis ao lado de "Provedor", na página '
                   'do sensor, se vier errado.'),
            _passo('Montar o diagrama da rede', 'Na página do sensor, em "Editar diagrama", ligue cada equipamento a quem o alimenta '
                   '(switch, roteador, rádio). Pode ser feito depois, inclusive pelo painel local, sem internet.'),
        ],
    }


def _linux(central):
    instalar = f'curl -fsSL {central}/instalar.sh | NOC_SENSOR_NOME="Nome do local" bash'
    return {
        'titulo': 'Instalação no Linux',
        'subtitulo': 'Computador, mini PC ou servidor com Linux, ligado direto na rede do local.',
        'requisitos': [
            'Linux Debian/Ubuntu, Arch/CachyOS, Fedora ou openSUSE (64 bits).',
            'Máquina que fica ligada 24 horas, de preferência num nobreak.',
            'Cabo de rede na rede do local (Wi-Fi funciona, mas o cabo enxerga melhor a rede e cai menos).',
            'Internet para baixar o instalador e falar com a Central.',
            'Um usuário comum com permissão de sudo (o instalador pede a senha quando precisa).',
        ],
        'antes': [
            'O nome do local como vai aparecer na Central.',
            'Se a Central pode atualizar o sistema e reiniciar esta máquina (opção "root", explicada no passo 2).',
            'Onde a máquina fica e quem tem acesso a ela no local.',
        ],
        'secoes': [
            {
                'titulo': 'Instalar',
                'passos': [
                    _passo('Abrir o terminal', 'Entre na máquina com o usuário comum (não como root) e abra um terminal.'),
                    _passo('Rodar o instalador', 'Troque "Nome do local" pelo nome do local e rode:', instalar,
                           'Para a Central também poder atualizar o sistema e reiniciar a máquina, acrescente NEXOPULSE_ROOT=1 '
                           'antes de "bash". Só libere onde fizer sentido.'),
                    _passo('Esperar a mensagem final', 'O instalador baixa o sensor, instala o que falta e liga o serviço. '
                           'No fim aparece "NexoPulse Edge instalado, iniciado e configurado para subir com a máquina".'),
                    _passo('Conferir o serviço', 'Deve aparecer "active (running)" em verde:',
                           'sudo systemctl status nexopulse-edge --no-pager'),
                    _passo('Abrir o painel local', 'De qualquer aparelho da rede do local, abra no navegador o endereço abaixo, '
                           'trocando pelo IP desta máquina. Entre com o usuário e a senha padrão do painel local.',
                           'http://IP-DA-MAQUINA:10000',
                           'Para ver o IP desta máquina: hostname -I'),
                ],
            },
            _comum_central(),
            {
                'titulo': 'Como ele se mantém ligado',
                'lista': [
                    'Liga sozinho junto com a máquina (serviço do sistema, sem precisar de ninguém logado).',
                    'Se o sensor cair, o sistema o liga de novo em 5 segundos, sem limite de tentativas.',
                    'Atualiza sozinho de madrugada (por volta das 4h) quando a Central tem versão nova. '
                    'Se a versão nova não funcionar, volta sozinho para a anterior.',
                    'Sem internet, continua medindo e guardando; manda tudo para a Central quando a internet volta.',
                ],
            },
            {
                'titulo': 'Se algo der errado',
                'tabela': [
                    ('Ver o que o sensor está fazendo', 'journalctl -u nexopulse-edge -n 50 --no-pager'),
                    ('Reiniciar o sensor', 'sudo systemctl restart nexopulse-edge'),
                    ('Instalar de novo (mantém a identidade e a chave)', instalar),
                    ('Liberar as ações de root depois', 'bash ~/nexopulse-edge/linux/liberar-root.sh'),
                    ('Tirar as ações de root', 'bash ~/nexopulse-edge/linux/liberar-root.sh --desfazer'),
                ],
                'notas': [
                    'Não aparece na Central: confira a internet da máquina (ping 1.1.1.1) e o horário do sistema.',
                    'Painel local não abre de outro aparelho: confira se o firewall da máquina libera a porta 10000 na rede local.',
                    'As configurações ficam em /etc/default/nexopulse-edge (depois de mudar, reinicie o sensor).',
                ],
            },
            {
                'titulo': 'Remover o sensor desta máquina',
                'passos': [
                    _passo('Parar e tirar o serviço', '', 'sudo systemctl disable --now nexopulse-edge\n'
                           'sudo rm -f /etc/systemd/system/nexopulse-edge.service /etc/default/nexopulse-edge\n'
                           'sudo systemctl daemon-reload'),
                    _passo('Apagar os arquivos', '', 'rm -rf ~/nexopulse-edge'),
                    _passo('Na Central', 'Em Sensores, use "Excluir o sensor", ou deixe-o em manutenção se ele for voltar.'),
                ],
            },
        ],
    }


def _windows(central):
    instalar = (f'irm {central}/instalar-windows.ps1 -OutFile $env:TEMP\\nexopulse.ps1\n'
                'powershell -ExecutionPolicy Bypass -File $env:TEMP\\nexopulse.ps1 -NomeSensor "Nome do local"')
    verificar = (f'irm {central}/verificar-windows.ps1 -OutFile $env:TEMP\\verificar.ps1\n'
                 'powershell -ExecutionPolicy Bypass -File $env:TEMP\\verificar.ps1')
    vigia = (f'irm {central}/vigia-windows.ps1 -OutFile $env:TEMP\\vigia.ps1\n'
             'powershell -ExecutionPolicy Bypass -File $env:TEMP\\vigia.ps1 -Instalar')
    religar = ("Start-ScheduledTask -TaskName 'NexoPulse Edge (WSL)'\n"
               'Start-Sleep -Seconds 15\n'
               'wsl -d Ubuntu -u root -- systemctl restart nexopulse-edge\n'
               'wsl -d Ubuntu -- systemctl is-active nexopulse-edge')
    return {
        'titulo': 'Instalação no Windows',
        'subtitulo': 'O sensor roda como Linux dentro do Windows (WSL), enxergando a rede real do local.',
        'requisitos': [
            'Windows 11 versão 22H2 ou mais nova (o Windows 10 não serve: falta a rede espelhada do WSL).',
            'Virtualização ligada na BIOS (Intel VT-x ou AMD-V). Quase todo PC de 2018 para cá já vem com ela ligada.',
            'Um usuário Administrador do Windows.',
            'PC que fica ligado 24 horas, na tomada (o instalador desliga a suspensão na tomada).',
            'Cabo de rede na rede do local, e internet.',
        ],
        'antes': [
            'O nome do local como vai aparecer na Central.',
            'Tempo para reiniciar o PC uma vez, se o WSL ainda não estiver instalado.',
            'Uma senha para o usuário do Linux (é pedida na primeira vez); anote-a junto da documentação do local.',
        ],
        'secoes': [
            {
                'titulo': 'Instalar',
                'passos': [
                    _passo('Abrir o PowerShell como Administrador', 'Menu Iniciar, digite PowerShell, clique com o botão direito '
                           'e escolha "Executar como administrador".'),
                    _passo('Rodar o instalador', 'Troque "Nome do local" pelo nome do local e rode as duas linhas:', instalar,
                           'Para a Central também poder atualizar e reiniciar o Linux do sensor, acrescente -Root no fim.'),
                    _passo('Primeira vez: Ubuntu e reinício', 'Se o PC ainda não tinha o WSL, o instalador instala o Ubuntu e para. '
                           'Siga o que ele mostrar em amarelo: reinicie o Windows se ele pedir, abra o "Ubuntu" pelo menu '
                           'Iniciar, crie o usuário e a senha do Linux, feche o Ubuntu e rode o mesmo comando de novo.'),
                    _passo('Senha do Linux', 'Na segunda rodada, o instalador pede a senha do usuário do Linux (a que você criou). '
                           'Ao digitar, nada aparece na tela; é normal.'),
                    _passo('Conferir o resultado', 'No fim aparece uma lista com OK em cada item: serviço, painel neste PC, painel '
                           'pelo IP da rede e "Liga sozinho". Também aparece "Vigia instalado".'),
                    _passo('Abrir o painel local', 'Neste PC, pelo atalho "Painel NexoPulse" na área de trabalho '
                           '(http://127.0.0.1:10010). Nos outros aparelhos da rede, pelo IP do PC na porta 10000. '
                           'Entre com o usuário e a senha padrão do painel local.'),
                ],
            },
            _comum_central(),
            {
                'titulo': 'Como ele se mantém ligado',
                'lista': [
                    'A tarefa "NexoPulse Edge (WSL)" liga o sensor junto com o Windows, mesmo sem ninguém entrar no PC '
                    '(em alguns PCs, só depois que alguém entra; o instalador avisa qual foi o caso).',
                    'O vigia ("NexoPulse Vigia") confere a cada 2 minutos: se o Linux desligou, se o sensor parou ou travou, '
                    'religa sozinho, sem reiniciar o PC. O que ele fez fica em C:\\ProgramData\\NexoPulse\\vigia.log.',
                    'Na tomada, o PC não suspende nem hiberna (um PC dormindo é um sensor desligado).',
                    'O sensor atualiza sozinho de madrugada (por volta das 4h) e volta para a versão anterior se a nova falhar.',
                    'Sem internet, continua medindo e guardando; manda tudo para a Central quando a internet volta.',
                ],
                'aviso': 'Manutenção no PC sem o vigia religar o sensor: crie o arquivo '
                         'C:\\ProgramData\\NexoPulse\\vigia.pausado e apague quando terminar.',
            },
            {
                'titulo': 'Se algo der errado',
                'intro': 'Primeiro rode a verificação (PowerShell como Administrador). Ela não muda nada no PC: mostra o que '
                         'está OK e, em cada falha, o que fazer.',
                'codigo': verificar,
                'tabela': [
                    ('Religar o sensor na hora', religar),
                    ('Instalar só o vigia (PC instalado antes dele)', vigia),
                    ('Ver o que o sensor está fazendo', 'wsl -d Ubuntu -- journalctl -u nexopulse-edge -n 50 --no-pager'),
                    ('Instalar de novo (mantém a identidade e a chave)', 'O mesmo comando do passo 2.'),
                ],
                'notas': [
                    '"IP visto pelo sensor" começando com 172: o WSL não está com a rede espelhada. Rode o instalador de novo e '
                    'depois "wsl --shutdown".',
                    'Painel não abre nos outros aparelhos: rode o instalador de novo (ele libera o firewall do Windows e do WSL).',
                    'Rede do Windows marcada como "Pública": mude para "Privada" nas configurações de rede.',
                    'O sensor só volta quando alguém entra no Windows: alguns PCs não ligam o WSL sem usuário logado. '
                    'Configure o Windows para entrar sozinho, ou deixe o usuário logado com a tela bloqueada.',
                ],
            },
            {
                'titulo': 'Remover o sensor deste PC',
                'passos': [
                    _passo('Tirar o vigia e as tarefas', 'PowerShell como Administrador:',
                           "powershell -ExecutionPolicy Bypass -File C:\\ProgramData\\NexoPulse\\vigia-nexopulse.ps1 -Remover\n"
                           "Unregister-ScheduledTask -TaskName 'NexoPulse Edge (WSL)' -Confirm:$false\n"
                           "Unregister-ScheduledTask -TaskName 'NexoPulse Painel local (neste PC)' -Confirm:$false"),
                    _passo('Parar o sensor no Linux', '', 'wsl -d Ubuntu -u root -- systemctl disable --now nexopulse-edge',
                           'Se o Ubuntu foi instalado só para o sensor, dá para apagá-lo inteiro com: wsl --unregister Ubuntu '
                           '(apaga tudo o que estiver nele).'),
                    _passo('Na Central', 'Em Sensores, use "Excluir o sensor", ou deixe-o em manutenção se ele for voltar.'),
                ],
            },
        ],
    }


def guia(sistema, central, agora=None):
    """Conteúdo do guia de um sistema, pronto para o template."""
    dados = {'linux': _linux, 'windows': _windows}[sistema](central.rstrip('/'))
    agora = datetime.fromtimestamp(agora, hub.FUSO_BR) if agora else datetime.now(hub.FUSO_BR)
    comando = next(p['codigo'] for p in dados['secoes'][0]['passos'] if p['titulo'] == 'Rodar o instalador')
    return {**dados, 'comando': comando, 'sistema': sistema, 'central': central.rstrip('/'), 'versao': CENTRAL_VERSION,
            'gerado_em': agora.strftime('%d/%m/%Y')}


def gerar_pdf(sistema, central, agora=None):
    """PDF em bytes (o WeasyPrint só é importado aqui, como no NexoReport)."""
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    env = Environment(loader=FileSystemLoader(os.path.join(RAIZ, 'templates', 'guias')), autoescape=True)
    html = env.get_template('instalacao.html').render(g=guia(sistema, central, agora), base=RAIZ)
    return HTML(string=html, base_url=RAIZ).write_pdf()
