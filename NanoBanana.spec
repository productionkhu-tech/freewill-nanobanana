# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None
base_dir = os.path.dirname(os.path.abspath(SPECPATH))

a = Analysis(
    [os.path.join(base_dir, 'launcher.py')],
    pathex=[base_dir],
    binaries=[],
    datas=[
        (os.path.join(base_dir, 'templates'), 'templates'),
        (os.path.join(base_dir, 'static'), 'static'),
        (os.path.join(base_dir, 'VERSION'), '.'),
        (os.path.join(base_dir, 'app.py'), '.'),
        (os.path.join(base_dir, 'updater.py'), '.'),
    ],
    hiddenimports=[
        'flask', 'jinja2', 'markupsafe', 'werkzeug',
        'google.genai', 'google.genai.types',
        'PIL', 'PIL.Image', 'PIL.ImageGrab', 'PIL.ImageDraw', 'PIL.ImageFont',
        'PIL.PngImagePlugin',
        'webview', 'webview.platforms.edgechromium',
        'google.auth', 'google.auth.transport.requests',
        'google.oauth2',
        'clr_loader', 'pythonnet',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter.test', 'unittest', 'pytest'],
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
    name='NanoBanana',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='NanoBanana',
)
