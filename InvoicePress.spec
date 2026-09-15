# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['invoice_pdf_printer/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[('invoice_pdf_printer/assets', 'invoice_pdf_printer/assets')],
    hiddenimports=['fitz'],
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
    [],
    exclude_binaries=True,
    name='InvoicePress',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='InvoicePress',
)
app = BUNDLE(
    coll,
    name='InvoicePress.app',
    icon=None,
    bundle_identifier=None,
)
