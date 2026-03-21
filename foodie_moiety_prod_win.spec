# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Foodie Moiety Cross — Windows production build.

Includes voice/ML models (Vosk, wake word) and references the clean
production database. No BUNDLE() (macOS-only), no PyObjC.

Prerequisites:
    python create_prod_db.py          # Generate dist/foodie_moiety_prod.db
    pip install -r requirements-build.txt pyinstaller

Usage:
    pyinstaller foodie_moiety_prod_win.spec --clean --noconfirm
"""

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# Collect full packages that PyInstaller misses via hiddenimports alone
_extra_datas = []
_extra_binaries = []
_extra_hiddenimports = []
for pkg in ('vosk', 'openwakeword', 'tqdm', 'yaml'):
    d, b, h = collect_all(pkg)
    _extra_datas += d
    _extra_binaries += b
    _extra_hiddenimports += h

# openwakeword's ONNX preprocessing models (melspectrogram, embedding) aren't
# included in the pip package — they're downloaded on first use. We keep copies
# in models/wakeword/openwakeword_resources/ and bundle them where the library expects.
_extra_datas.append(('models/wakeword/openwakeword_resources', 'openwakeword/resources/models'))

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=_extra_binaries,
    datas=_extra_datas + [
        # Clean production database (schema + default tags, no recipes)
        ('dist/foodie_moiety.db', '.'),
        ('config.json', '.'),
        # Reporting procedure for review mode
        ('csam_reporting_procedure.md', '.'),
        # Fallback images used when recipe/book media is missing
        ('media/default.jpg', 'media'),
        # ── ML models ──
        ('models/vosk/small-en-us', 'models/vosk/small-en-us'),
        ('models/wakeword/hey_foodie.onnx', 'models/wakeword'),
    ],
    hiddenimports=_extra_hiddenimports + [
        # PySide6 multimedia plugins — commonly missed by PyInstaller
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetwork',
        # App modules imported dynamically or conditionally
        'numpy',
        # boto3/botocore for Cognito auth (transitive via pycognito)
        'boto3',
        'botocore',
        'pycognito',
        # ML/voice — ensure PyInstaller finds these
        'vosk',
        'openwakeword',
        'onnxruntime',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # ── Heavy unused Qt modules ──
        'PySide6.QtWebEngine',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DExtras',
        'PySide6.Qt3DAnimation',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuickWidgets',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtDesigner',
        'PySide6.QtHelp',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtRemoteObjects',
        'PySide6.QtSensors',
        'PySide6.QtSerialPort',
        'PySide6.QtPositioning',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtTest',

        # ── Unused ML packages ──
        'piper',              # TTS removed
        'llama_cpp',          # LLM removed
        'llama_cpp_python',
        'av',                 # Blocked by av stub (FFmpeg conflict)
        'faster_whisper',     # Whisper model not bundled — Vosk is default
        'ctranslate2',
        'huggingface_hub',
        'tokenizers',
        'tflite_runtime',
        'tensorflow',

        # ── macOS-only ──
        'objc',
        'AppKit',
        'Foundation',
        'services.audio_engine_mac',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FoodieMoiety',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # --windowed: no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,  # We handle argv ourselves (deep link URL)
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='media/app_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='FoodieMoiety',
)
