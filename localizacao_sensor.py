"""Localização capturada na própria máquina do sensor, a pedido da Central.

Tenta, na ordem, a fonte mais precisa disponível e devolve a primeira que funcionar:

1. GPS da antena Starlink (API local em 192.168.100.1). Precisa da opção
   "Permitir acesso à localização na rede local" ligada no app da Starlink.
2. Localização do Windows, quando o sensor roda no WSL (powershell.exe pela
   integração do WSL). Precisa da localização do Windows ligada para apps da área de trabalho.
3. Aproximada pela internet (geolocalização do IP público). Pode errar por dezenas
   de quilômetros, e na Starlink costuma apontar a cidade do ponto de saída da rede.

Cada tentativa que falha entra em `tentativas`, para a tela explicar o que faltou.
"""
import glob
import json
import os
import shutil
import subprocess
import urllib.request

POWERSHELL_PADRAO = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

SCRIPT_WINDOWS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::High)
$w.Start()
$t = 0
while ($w.Status -ne 'Ready' -and $w.Permission -ne 'Denied' -and $t -lt 100) { Start-Sleep -Milliseconds 200; $t++ }
if ($w.Permission -eq 'Denied') { 'NEGADO'; exit 3 }
$c = $w.Position.Location
if ($c.IsUnknown) { 'DESCONHECIDO'; exit 4 }
$i = [System.Globalization.CultureInfo]::InvariantCulture
'{0};{1};{2}' -f $c.Latitude.ToString($i), $c.Longitude.ToString($i), $c.HorizontalAccuracy.ToString($i)
"""


def _coordenadas_validas(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return lat, lon


def pela_starlink(starlink_grpc, alvo):
    if starlink_grpc is None:
        return None, "Starlink: biblioteca da antena não instalada neste sensor."
    contexto = starlink_grpc.ChannelContext(target=alvo)
    try:
        dados = starlink_grpc.location_data(context=contexto)
    except Exception:
        return None, "Starlink: nenhuma antena respondeu na rede deste sensor."
    finally:
        try:
            contexto.close()
        except Exception:
            pass
    if dados.get("latitude") is None:
        return None, ("Starlink: a antena respondeu, mas não libera a posição. No app da Starlink, "
                      "abra Configurações > Avançado e ligue \"Permitir acesso à localização na rede local\".")
    par = _coordenadas_validas(dados.get("latitude"), dados.get("longitude"))
    if not par:
        return None, "Starlink: a antena devolveu uma posição inválida."
    return {"latitude": par[0], "longitude": par[1], "accuracy_m": None, "location_source": "starlink"}, None


def _ambiente_wsl():
    """Serviço do systemd no WSL não herda WSL_INTEROP; sem ele o powershell.exe não abre."""
    ambiente = dict(os.environ)
    if not ambiente.get("WSL_INTEROP"):
        sockets = sorted(glob.glob("/run/WSL/*_interop"), key=lambda p: os.path.getmtime(p), reverse=True)
        if sockets:
            ambiente["WSL_INTEROP"] = sockets[0]
    return ambiente


def no_wsl():
    try:
        with open("/proc/version", encoding="utf-8", errors="ignore") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def pelo_windows(rodar=subprocess.run):
    if not no_wsl():
        return None, None  # não é Windows: nem conta como tentativa
    powershell = shutil.which("powershell.exe") or (POWERSHELL_PADRAO if os.path.exists(POWERSHELL_PADRAO) else None)
    if not powershell:
        return None, "Windows: não foi possível chamar o PowerShell do Windows a partir do sensor."
    try:
        r = rodar([powershell, "-NoProfile", "-NonInteractive", "-Command", SCRIPT_WINDOWS],
                  capture_output=True, text=True, timeout=40, env=_ambiente_wsl(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None, "Windows: o serviço de localização não respondeu a tempo."
    saida = (r.stdout or "").strip().splitlines()
    ultima = saida[-1].strip() if saida else ""
    if r.returncode == 3 or ultima == "NEGADO":
        return None, ("Windows: a localização está desligada. Em Configurações > Privacidade e segurança > Localização, "
                      "ligue \"Serviços de localização\" e \"Permitir que aplicativos da área de trabalho acessem sua localização\".")
    if r.returncode == 4 or ultima == "DESCONHECIDO" or r.returncode != 0:
        return None, "Windows: o computador não conseguiu descobrir onde está (sem Wi-Fi ou GPS para estimar)."
    partes = ultima.split(";")
    par = _coordenadas_validas(*(partes[:2] + [None, None])[:2])
    if not par:
        return None, "Windows: resposta de localização inválida."
    try:
        precisao = float(partes[2]) if len(partes) > 2 else None
    except ValueError:
        precisao = None
    if precisao is not None and not (0 <= precisao < 1e6):
        precisao = None
    return {"latitude": par[0], "longitude": par[1], "accuracy_m": precisao, "location_source": "windows"}, None


def pela_internet(abrir=urllib.request.urlopen):
    try:
        with abrir("https://ipwho.is/", timeout=6) as resposta:
            dados = json.load(resposta)
    except Exception:
        return None, "Internet: o serviço de localização por IP não respondeu."
    if dados.get("success") is not True:
        return None, "Internet: o serviço de localização por IP não reconheceu este endereço."
    par = _coordenadas_validas(dados.get("latitude"), dados.get("longitude"))
    if not par:
        return None, "Internet: resposta inválida."
    cidade = ", ".join(x for x in (dados.get("city"), dados.get("region")) if x)
    return {"latitude": par[0], "longitude": par[1], "accuracy_m": None, "location_source": "ip",
            "referencia": cidade or None}, None


def capturar(starlink_grpc=None, alvo_starlink="192.168.100.1:9200", fontes=None):
    """Devolve {"ok": bool, "local": {...} ou None, "tentativas": [motivos de quem falhou]}."""
    fontes = fontes or (lambda: pela_starlink(starlink_grpc, alvo_starlink), pelo_windows, pela_internet)
    tentativas = []
    for fonte in fontes:
        try:
            local, motivo = fonte()
        except Exception as e:  # nunca derruba o agente
            local, motivo = None, f"falha inesperada ({type(e).__name__})"
        if local:
            return {"ok": True, "local": local, "tentativas": tentativas}
        if motivo:
            tentativas.append(motivo)
    return {"ok": False, "local": None, "tentativas": tentativas}


if __name__ == "__main__":
    try:
        import starlink_grpc as _sg
    except Exception:
        _sg = None
    print(json.dumps(capturar(_sg), indent=2, ensure_ascii=False))
