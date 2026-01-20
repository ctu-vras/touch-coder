# -*- mode: python ; coding: utf-8 -*-

import os
import sys

spec_path = os.path.abspath(sys.argv[0]) if sys.argv else os.path.abspath("TinyTouch.spec")
project_root = os.path.abspath(os.path.dirname(spec_path))
src_root = os.path.join(project_root, "src")

datas = [
    (os.path.join(project_root, "config.json"), "."),
    (os.path.join(project_root, "icons"), "icons"),
]

a = Analysis(
    ["src/main.py"],
    pathex=[src_root],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PIL._tkinter_finder",
        "PIL._tkinter",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TinyTouch",
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
