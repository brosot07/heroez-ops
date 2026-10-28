# PyInstaller spec: builds dist/HeroezOps/HeroezOps.exe (one folder).
# Run from the repo root: pyinstaller packaging/windows/HeroezOps.spec
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    datas=collect_data_files("tzdata") + [(os.path.join(ROOT, "heroez_ops", "defaults"), "heroez_ops/defaults")],
    hiddenimports=collect_submodules("uvicorn") + ["tzdata"],
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HeroezOps", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="HeroezOps")
