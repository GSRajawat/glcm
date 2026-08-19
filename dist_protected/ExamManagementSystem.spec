# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_app.py'],
    pathex=[],
    binaries=[],
    datas=[('app.py', '.'), ('auth.py', '.'), ('db.py', '.'), ('admin_panel.py', '.'), ('cs_panel.py', '.'), ('student_portal.py', '.'), ('owner_panel.py', '.'), ('data_ingestion.py', '.'), ('seat_assignment.py', '.'), ('reporting.py', '.'), ('remuneration.py', '.'), ('assets', 'assets'), ('.streamlit', '.streamlit')],
    hiddenimports=[],
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
    name='ExamManagementSystem',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
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
