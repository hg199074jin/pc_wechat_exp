# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

datas = [('src', 'src'), ('..\\tools\\silk_decoder.exe', 'tools')]
binaries = []
hiddenimports = ['Crypto.Cipher.AES', 'Crypto.Util.Padding', 'flask', 'werkzeug', 'jinja2', 'blackboxprotobuf', 'zstandard', 'openpyxl', 'jieba', 'jieba.posseg', 'requests', 'socks', 'pypinyin', 'apscheduler.schedulers.background', 'apscheduler.triggers.interval', 'apscheduler.jobstores.memory', 'apscheduler.executors.pool', 'docx']
tmp_ret = collect_all('jieba')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# apscheduler uses dynamic imports; collect its data/submodules so the bundled
# exe can start the AI-analysis and knowledge-scan schedulers.
tmp_ret = collect_all('apscheduler')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Resolve the entry script relative to this spec file so the build works from
# any checkout location (the old path was hardcoded to one developer's machine).
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
_entry = os.path.join(_spec_dir, 'main.py')

a = Analysis(
    [_entry],
    pathex=[_spec_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', '_pytest', 'tkinter', '_tkinter', 'turtle', 'idlelib', 'ensurepip', 'pip', 'setuptools', 'wheel', 'pkg_resources', 'multiprocessing', 'concurrent.futures.process', 'lib2to3', 'xmlrpc', 'pydoc', 'doctest', 'bdb'],
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
    name='wechat_exp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
