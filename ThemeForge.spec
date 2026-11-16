# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path

root = Path(SPECPATH).resolve()

hiddenimports = []
hiddenimports += collect_submodules('themeforge')


a = Analysis(
    [str(root / 'scripts' / 'themeforge_portable_entry.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Legacy NLP extras are excluded. The optional pinned ONNX neighbor adapter
    # has no lexical fallback; its small runtime libraries are collected normally.
    # Never collect model caches or study data.
    excludes=['sentence_transformers', 'transformers', 'torch', 'torchvision',
              'torchaudio', 'tensorflow', 'keras', 'kiwipiepy', 'kiwipiepy_model'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ThemeForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name='ThemeForge',
)
