# -*- mode: python ; coding: utf-8 -*-


import certifi

a = Analysis(
    ['agente_v2.py'],
    pathex=[],
    binaries=[],
    # Garante que o bundle de certificados do certifi vá junto no executável (o fix de SSL depende disso)
    datas=[(certifi.where(), 'certifi')],
    hiddenimports=['speedtest', 'jwt', 'certifi', 'cryptography'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='agente_v2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
