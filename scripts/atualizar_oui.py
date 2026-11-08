#!/usr/bin/env python3
"""Gera dados/oui.tsv.gz (prefixo de MAC -> fabricante) a partir do registro público do IEEE.

Junta os três blocos do registro: MA-L (prefixo de 24 bits), MA-M (28) e MA-S (36). O arquivo
gerado tem uma linha por prefixo, "PREFIXO<TAB>Fabricante", com o prefixo em hexadecimal
maiúsculo (6, 7 ou 9 dígitos). A Central consulta essa base offline (oui.py).

Uso: python scripts/atualizar_oui.py                 (baixa do IEEE)
     python scripts/atualizar_oui.py oui.csv mam.csv oui36.csv   (usa arquivos já baixados)
"""
import csv
import gzip
import io
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / 'dados' / 'oui.tsv.gz'
FONTES = ('https://standards-oui.ieee.org/oui/oui.csv',
          'https://standards-oui.ieee.org/oui28/mam.csv',
          'https://standards-oui.ieee.org/oui36/oui36.csv')
DIGITOS = {'MA-L': 6, 'MA-M': 7, 'MA-S': 9}
# O IEEE aparece como dono dos blocos que ele subdivide em MA-M/MA-S; isso não é um fabricante.
SEM_FABRICANTE = {'private', 'ieee registration authority'}


def baixar(url):
    pedido = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (NexoPulse; atualizar_oui)'})
    with urllib.request.urlopen(pedido, timeout=120) as resposta:
        return resposta.read().decode('utf-8')


def ler_registro(texto):
    """Devolve {prefixo: fabricante}. Ignora linhas fora do formato e nomes vazios ou 'Private'."""
    prefixos = {}
    for linha in csv.DictReader(io.StringIO(texto)):
        bloco = (linha.get('Registry') or '').strip()
        prefixo = (linha.get('Assignment') or '').strip().upper()
        nome = ' '.join((linha.get('Organization Name') or '').split())
        if bloco not in DIGITOS or len(prefixo) != DIGITOS[bloco] or not nome or nome.lower() in SEM_FABRICANTE:
            continue
        if any(c not in '0123456789ABCDEF' for c in prefixo):
            continue
        prefixos[prefixo] = nome.replace('\t', ' ')
    return prefixos


def main(argv):
    textos = [Path(a).read_text(encoding='utf-8') for a in argv] if argv else [baixar(u) for u in FONTES]
    prefixos = {}
    for texto in textos:
        prefixos.update(ler_registro(texto))
    if len(prefixos) < 30000:
        sys.exit(f'registro incompleto: só {len(prefixos)} prefixos')
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    corpo = ''.join(f'{p}\t{n}\n' for p, n in sorted(prefixos.items()))
    # mtime fixo: o arquivo só muda quando o registro muda.
    with open(DESTINO, 'wb') as bruto, gzip.GzipFile(fileobj=bruto, mode='wb', mtime=0, compresslevel=9) as gz:
        gz.write(corpo.encode('utf-8'))
    print(f'{len(prefixos)} prefixos gravados em {DESTINO.relative_to(RAIZ)} ({DESTINO.stat().st_size // 1024} KB)')


if __name__ == '__main__':
    main(sys.argv[1:])
