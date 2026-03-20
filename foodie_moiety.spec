# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Foodie Moiety Cross — macOS test build.

This is a minimal build for testing deep link (foodiemoiety://) functionality.
Voice/ML features are excluded to keep the bundle small and the build fast.

Usage:
    pyinstaller foodie_moiety.spec --clean --noconfirm
"""

import sys
import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # SQLite database (base schema required — app doesn't create tables from scratch)
        ('foodie_moiety.db', '.'),
        # Reporting procedure for review mode
        ('csam_reporting_procedure.md', '.'),
        # Fallback images used when recipe/book media is missing
        ('media/default.jpg', 'media'),
        ('media/fm_logo.png', 'media'),
    ],
    hiddenimports=[
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

        # ── ML/voice packages (not needed for deep link testing) ──
        'faster_whisper',
        'openwakeword',
        'piper',
        'llama_cpp',
        'llama_cpp_python',
        'onnxruntime',
        'av',
        'ctranslate2',
        'huggingface_hub',
        'tokenizers',
        'tflite_runtime',
        'tensorflow',

        # ── macOS native audio engine (excluded with voice features) ──
        # NOTE: objc/AppKit kept for SF Symbols (icon-only buttons need them)

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

app = BUNDLE(
    coll,
    name='FoodieMoiety.app',
    icon='media/app_icon.icns',
    bundle_identifier='com.foodiemoiety.cross',
    info_plist={
        'CFBundleName': 'Foodie Moiety',
        'CFBundleDisplayName': 'Foodie Moiety',
        'CFBundleVersion': '1.0.3',
        'CFBundleShortVersionString': '1.0.3',
        'NSHighResolutionCapable': True,
        # Register foodiemoiety:// URL scheme so macOS routes deep links to this app
        'CFBundleURLTypes': [
            {
                'CFBundleURLName': 'com.foodiemoiety.cross',
                'CFBundleURLSchemes': ['foodiemoiety'],
            },
        ],
    },
)
