"""Build on the target OS/architecture; no developer data enters the bundle."""
import subprocess,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
os.environ['PYINSTALLER_CONFIG_DIR']=str(ROOT/'build/pyinstaller-cache')
extra=['--add-data',str(ROOT/'native-tools/ocr.ps1')+(';' if sys.platform=='win32' else ':')+'native-tools']
extra+=['--add-data',str(ROOT/'scripts/napcat-entrypoint.sh')+(';' if sys.platform=='win32' else ':')+'scripts']
if sys.platform=='darwin':
    helper=ROOT/'build/native-tools/qqmanager-ocr';helper.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(['xcrun','swiftc','-module-cache-path',str(ROOT/'build/swift-module-cache'),str(ROOT/'native-tools/ocr.swift'),'-O','-o',str(helper)],check=True)
    subprocess.run(['codesign','--force','--sign','-',str(helper)],check=True)
    extra+=['--add-binary',str(helper)+':native-tools']
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','qqmanager-server','--distpath',str(ROOT/'build/pyinstaller'),'--workpath',str(ROOT/'build/pyinstaller-work'),'--specpath',str(ROOT/'build'),'--add-data',str(ROOT/'web')+(';' if sys.platform=='win32' else ':')+'web',*extra,'--collect-all','pypdf','--collect-all','tzdata','--collect-all','certifi',str(ROOT/'run.py')],check=True,cwd=ROOT)
import shutil
out=ROOT/'build/backend'
if out.exists():shutil.rmtree(out)
shutil.copytree(ROOT/'build/pyinstaller/qqmanager-server',out,symlinks=True)
print(out)
