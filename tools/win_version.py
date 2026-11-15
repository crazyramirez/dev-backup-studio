"""Escribe build/version_info.txt (propiedades del .exe de Windows) e imprime APP_VERSION.

GitHub Actions usa la versión impresa para nombrar el instalador y comprobar que
coincide con la etiqueta publicada.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(ROOT, "backup_studio.py"), encoding="utf-8") as fh:
    version = re.search(r'^APP_VERSION = "([\d.]+)"', fh.read(), re.M)[1]
nums = tuple(int(x) for x in version.split(".")) + (0,) * (4 - len(version.split(".")))

info = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040A04B0', [
      StringStruct('CompanyName', 'crazyramirez'),
      StringStruct('FileDescription', 'DEV Backup Studio'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'DEV Backup Studio'),
      StringStruct('OriginalFilename', 'DEV Backup Studio.exe'),
      StringStruct('ProductName', 'DEV Backup Studio'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [0x040A, 1200])])
  ]
)
"""
os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
with open(os.path.join(ROOT, "build", "version_info.txt"), "w", encoding="utf-8") as fh:
    fh.write(info)
print(version)
