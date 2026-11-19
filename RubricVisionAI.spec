# PyInstaller build file for RubricVisionAI.exe (run build.bat on Windows).
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = collect_submodules("google.genai", filter=lambda m: ".tests" not in m) + collect_submodules(
    "hachoir", filter=lambda m: not m.startswith(("hachoir.editor", "hachoir.subfile", "hachoir.wx", "hachoir.urwid"))
)

a = Analysis(
    ["RubricVisionAI.pyw"],
    pathex=[],
    binaries=[],
    datas=[("assets/rubricvision.ico", "assets"), ("assets/rubricvision.png", "assets")]
    + collect_data_files("certifi"),
    hiddenimports=hidden,
    excludes=["pytest", "IPython", "matplotlib", "numpy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RubricVisionAI",
    icon="assets/rubricvision.ico",
    console=False,
    upx=False,
    version=None,
)
