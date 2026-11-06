import io
import os
import subprocess
import tarfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# sudo falso: roda o comando sem privilégio (tirando -o/-g do install) e registra a chamada.
SUDO_FALSO = '''#!/bin/bash
echo "$*" >> "$LOG_SUDO"
if [[ "$1" == visudo ]]; then exit "${VISUDO_FALHA:-0}"; fi
if [[ "$1" == install ]]; then
  shift; args=()
  while (($#)); do case "$1" in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac; done
  exec install "${args[@]}"
fi
exec "$@"
'''


def _ambiente(tmp_path, **extra):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / 'sudo').write_text(SUDO_FALSO)
    (bin_dir / 'sudo').chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", LOG_SUDO=str(tmp_path / 'sudo.log'),
               NEXOPULSE_ADMIN_DESTINO=str(tmp_path / 'sbin/nexopulse-admin'),
               NEXOPULSE_SUDOERS_DESTINO=str(tmp_path / 'sudoers.d/nexopulse-edge'), **extra)
    (tmp_path / 'sbin').mkdir(exist_ok=True)
    (tmp_path / 'sudoers.d').mkdir(exist_ok=True)
    return env


def _rodar(env, *args):
    return subprocess.run(['bash', str(RAIZ / 'linux/liberar-root.sh'), *args], env=env, capture_output=True, text=True)


def test_releases_only_the_helper_and_can_undo(tmp_path):
    env = _ambiente(tmp_path)
    r = _rodar(env)
    assert r.returncode == 0, r.stderr
    ajudante, regra = tmp_path / 'sbin/nexopulse-admin', tmp_path / 'sudoers.d/nexopulse-edge'
    assert ajudante.read_bytes() == (RAIZ / 'linux/nexopulse-admin').read_bytes()
    assert oct(ajudante.stat().st_mode & 0o777) == '0o755'
    usuario = subprocess.run(['id', '-un'], capture_output=True, text=True).stdout.strip()
    assert regra.read_text().strip() == f'{usuario} ALL=(root) NOPASSWD: {ajudante}'
    assert oct(regra.stat().st_mode & 0o777) == '0o440'
    assert (tmp_path / 'sudo.log').read_text().splitlines()[0].startswith('visudo -cf ')
    r = _rodar(env, '--desfazer')
    assert r.returncode == 0 and not ajudante.exists() and not regra.exists()


def test_invalid_rule_is_never_installed(tmp_path):
    env = _ambiente(tmp_path, VISUDO_FALHA='1')
    assert _rodar(env).returncode != 0
    assert not (tmp_path / 'sudoers.d/nexopulse-edge').exists()
    assert not (tmp_path / 'sbin/nexopulse-admin').exists()


def test_installers_offer_the_root_option(client):
    with tarfile.open(fileobj=io.BytesIO(client.get('/agente/pacote.tar.gz').data), mode='r:gz') as tar:
        assert 'nexopulse-edge/linux/liberar-root.sh' in tar.getnames()
    assert 'NEXOPULSE_ROOT=1' in client.get('/instalar.sh').get_data(as_text=True)
    assert '[switch]$Root' in client.get('/instalar-windows.ps1').get_data(as_text=True)
    install = (RAIZ / 'linux/install.sh').read_text()
    assert 'if [[ "${NEXOPULSE_ROOT:-0}" == "1" ]]' in install and 'liberar-root.sh' in install


def test_central_serve_a_verificacao_do_windows(client):
    r = client.get('/verificar-windows.ps1')
    assert r.status_code == 200 and 'attachment' in r.headers['Content-Disposition']
    texto = r.get_data(as_text=True)
    assert texto.startswith('\ufeff') and 'function Invoke-Verificacao' in texto     # BOM preservado
    instalador = client.get('/instalar-windows.ps1').get_data(as_text=True)
    assert instalador.startswith('\ufeff') and 'Set-RegrasFirewall' in instalador


def test_central_serve_o_vigia_do_windows(client):
    r = client.get('/vigia-windows.ps1')
    assert r.status_code == 200 and 'attachment' in r.headers['Content-Disposition']
    texto = r.get_data(as_text=True)
    assert texto.startswith('﻿') and 'function Get-DecisaoVigia' in texto and 'function Install-Vigia' in texto
    instalador = client.get('/instalar-windows.ps1').get_data(as_text=True)
    assert '/vigia-windows.ps1' in instalador and '-Instalar -Distro $Distro' in instalador


def test_guias_de_instalacao_so_para_o_master(client, login):
    login()
    pagina = client.get('/guias')
    assert pagina.status_code == 200
    texto = pagina.get_data(as_text=True)
    assert '/guias/linux.pdf' in texto and '/guias/windows.pdf' in texto and '/instalar-windows.ps1' in texto
    pdf = client.get('/guias/windows.pdf?baixar=1')
    assert pdf.status_code == 200 and pdf.data.startswith(b'%PDF') and 'attachment' in pdf.headers['Content-Disposition']
    assert client.get('/guias/linux.pdf').headers['Content-Disposition'].startswith('inline')
    assert client.get('/guias/macos.pdf').status_code == 404
    login(2, 'Cliente')
    assert client.get('/guias').status_code == 403
    assert client.get('/guias/linux.pdf').status_code == 403


def test_guias_trazem_os_comandos_desta_central():
    import guias
    g = guias.guia('windows', 'https://central.exemplo.com.br/')
    texto = repr(g)
    assert 'https://central.exemplo.com.br/instalar-windows.ps1' in g['comando']
    assert '/vigia-windows.ps1' in texto and '/verificar-windows.ps1' in texto and 'nexopulse.up.railway.app' not in texto
    assert 'curl -fsSL https://central.exemplo.com.br/instalar.sh' in guias.guia('linux', 'https://central.exemplo.com.br')['comando']


def test_instalador_libera_o_painel_so_para_a_rede_local():
    install = (RAIZ / 'linux/install.sh').read_text()
    assert 'ufw allow from "${rede}" to any port "${PORTA_PAINEL}"' in install
    assert 'firewall-cmd --permanent --add-rich-rule' in install
    assert 'ufw allow 10000' not in install            # nunca aberto para qualquer origem
