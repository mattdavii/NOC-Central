"""Fabricante pelo MAC, consultando offline o registro público do IEEE (dados/oui.tsv.gz).

O IEEE atribui prefixos de 24 bits (MA-L), 28 (MA-M) e 36 (MA-S); vale o mais específico.
MACs com o bit "administrado localmente" ligado não vêm de fábrica: celulares e notebooks
atuais usam MAC aleatório por rede, e aí o prefixo não diz nada sobre a marca.
"""
import gzip
import os
import re
import threading

ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dados', 'oui.tsv.gz')
MAC_PRIVADO = 'MAC privado (aleatório)'
_HEX = re.compile(r'[^0-9A-F]')
_base = None
_trava = threading.Lock()


def _carregar():
    global _base
    with _trava:
        if _base is None:
            base = {}
            try:
                with gzip.open(ARQUIVO, 'rt', encoding='utf-8') as arquivo:
                    for linha in arquivo:
                        prefixo, _, nome = linha.rstrip('\n').partition('\t')
                        if prefixo and nome:
                            base[prefixo] = nome
            except OSError:
                pass  # sem a base, todo fabricante fica "Desconhecido"
            _base = base
    return _base


def normalizar(mac):
    """'aa-bb-cc-dd-ee-ff' -> 'AA:BB:CC:DD:EE:FF'; None se não for um MAC de 48 bits."""
    digitos = _HEX.sub('', str(mac or '').upper())
    if len(digitos) != 12:
        return None
    return ':'.join(digitos[i:i + 2] for i in range(0, 12, 2))


def mac_privado(mac):
    """Bit 'administrado localmente' (segundo bit do primeiro octeto) ligado."""
    normal = normalizar(mac)
    return bool(normal) and bool(int(normal[:2], 16) & 0x02)


def fabricante(mac):
    """Devolve (fabricante, fonte). fonte: 'ieee', 'mac_privado' ou None (não identificado)."""
    normal = normalizar(mac)
    if not normal:
        return None, None
    digitos = normal.replace(':', '')
    if int(digitos[:2], 16) & 0x01:
        return None, None  # multicast/broadcast não identifica equipamento
    if mac_privado(normal):
        return MAC_PRIVADO, 'mac_privado'
    base = _carregar()
    for tamanho in (9, 7, 6):
        nome = base.get(digitos[:tamanho])
        if nome:
            return nome, 'ieee'
    return None, None


# Nome comercial para as marcas mais comuns em redes de clientes. O registro do IEEE traz a razão
# social ("Hangzhou Hikvision Digital Technology Co.,Ltd."); na tela e na planilha a marca curta
# ajuda mais, e a razão social continua no JSON. A ordem importa: o primeiro termo que casar vale.
MARCAS = (
    ('HIKVISION', 'Hikvision'), ('DAHUA', 'Dahua'), ('INTELBRAS', 'Intelbras'), ('UBIQUITI', 'Ubiquiti'),
    ('ROUTERBOARD', 'MikroTik'), ('MIKROTIK', 'MikroTik'), ('TP-LINK', 'TP-Link'), ('MERCUSYS', 'Mercusys'),
    ('TENDA', 'Tenda'), ('D-LINK', 'D-Link'), ('MOTOROLA', 'Motorola'), ('LENOVO', 'Lenovo'),
    ('APPLE', 'Apple'), ('SAMSUNG', 'Samsung'), ('XIAOMI', 'Xiaomi'), ('HUAWEI', 'Huawei'),
    ('HONOR DEVICE', 'Honor'), ('ZTE CORPORATION', 'ZTE'), ('OPPO', 'Oppo'), ('VIVO MOBILE', 'vivo'),
    ('REALME', 'realme'), ('ONEPLUS', 'OnePlus'), ('ESPRESSIF', 'Espressif (IoT)'), ('TUYA', 'Tuya (IoT)'),
    ('RASPBERRY PI', 'Raspberry Pi'), ('POSITIVO', 'Positivo'), ('MULTILASER', 'Multilaser'),
    ('GRANDSTREAM', 'Grandstream'), ('YEALINK', 'Yealink'), ('FORTINET', 'Fortinet'), ('CISCO MERAKI', 'Cisco Meraki'),
    ('CISCO', 'Cisco'), ('HEWLETT PACKARD ENTERPRISE', 'HPE'), ('HEWLETT', 'HP'), ('DELL', 'Dell'),
    ('INTEL CORP', 'Intel'), ('AMAZON TECHNOLOGIES', 'Amazon'), ('GOOGLE', 'Google'),
    ('LG ELECTRONICS', 'LG'), ('SONY', 'Sony'), ('SEIKO EPSON', 'Epson'), ('BROTHER INDUSTRIES', 'Brother'),
    ('CANON INC', 'Canon'), ('RICOH COMPANY', 'Ricoh'), ('KYOCERA', 'Kyocera'), ('XEROX', 'Xerox'),
    ('LEXMARK', 'Lexmark'), ('ZEBRA TECHNOLOGIES', 'Zebra'), ('CONTROL ID', 'Control iD'), ('ELSYS', 'Elsys'),
    ('FURUKAWA', 'Furukawa'), ('PARKS S/A', 'Parks'), ('NOKIA', 'Nokia'), ('ASUSTEK', 'ASUS'),
    ('MICROSOFT', 'Microsoft'), ('VMWARE', 'VMware'), ('ASROCK', 'ASRock'), ('MICRO-STAR', 'MSI'),
    ('GIGA-BYTE', 'Gigabyte'), ('RUIJIE', 'Ruijie'), ('JUNIPER NETWORKS', 'Juniper'), ('ALCATEL', 'Alcatel-Lucent'),
    ('SYNOLOGY', 'Synology'), ('QNAP', 'QNAP'), ('SAGEMCOM', 'Sagemcom'), ('ARCADYAN', 'Arcadyan'),
    ('ASKEY', 'Askey'), ('SERCOMM', 'Sercomm'), ('HON HAI', 'Foxconn'), ('FOXCONN', 'Foxconn'), ('AZUREWAVE', 'AzureWave'),
    ('REALTEK', 'Realtek'),
)
_SUFIXOS = re.compile(r'[\s,.]*\b(co\.?|corp\.?|corporation|inc\.?|incorporated|ltd\.?|ltda\.?|limited|llc|gmbh|s\.?a\.?|s/a|'
                      r'pte\.?|plc|ag|bv|oy|ab|company)\b\.?', re.IGNORECASE)


def marca(registro):
    """Marca curta a partir da razão social do IEEE ('TP-LINK TECHNOLOGIES CO.,LTD.' -> 'TP-Link')."""
    if not registro:
        return None
    maiusculo = registro.upper()
    for termo, nome in MARCAS:
        if termo in maiusculo:
            return nome
    curto = _SUFIXOS.sub('', registro).strip(' ,.-')
    return curto or registro
