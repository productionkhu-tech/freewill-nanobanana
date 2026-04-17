# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None
# SPECPATH is already the directory containing this spec file in modern
# PyInstaller. An earlier dirname() call was peeling off the `src` segment
# on the build machine, so launcher.py couldn't be located.
base_dir = SPECPATH
icon_path = os.path.join(base_dir, 'app.ico')

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
        (icon_path, '.'),
    ],
    hiddenimports=[
        'flask', 'jinja2', 'markupsafe', 'werkzeug',
        'google.genai', 'google.genai.types',
        'PIL', 'PIL.Image', 'PIL.ImageGrab', 'PIL.ImageDraw', 'PIL.ImageFont',
        'PIL.PngImagePlugin',
        'webview', 'webview.platforms.edgechromium',
        'google.auth', 'google.auth.transport.requests',
        'google.auth.crypt', 'google.auth.crypt.es256', 'google.auth.crypt.rsa',
        'google.oauth2', 'google.oauth2.service_account',
        'clr_loader', 'pythonnet',
        'tkinter', 'tkinter.filedialog',
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

# ONEFILE build — a single self-extracting NanoBanana.exe. This matches the
# updater's whole-EXE swap model (ship one file, replace one file). The old
# spec used COLLECT() = onedir, which left a sibling _internal/ folder that
# would drift out of sync after an update and caused mysterious launch
# failures. Do NOT reintroduce COLLECT here.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='NanoBanana',
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
    icon=icon_path if os.path.isfile(icon_path) else None,
)
