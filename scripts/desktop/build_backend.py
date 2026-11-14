"""Build on the target OS/architecture; no developer data enters the bundle."""
import subprocess,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
os.environ['PYINSTALLER_CONFIG_DIR']=str(ROOT/'build/pyinstaller-cache')
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','qqmanager-server','--distpath',str(ROOT/'build/pyinstaller'),'--workpath',str(ROOT/'build/pyinstaller-work'),'--specpath',str(ROOT/'build'),'--add-data',str(ROOT/'web')+(';' if sys.platform=='win32' else ':')+'web','--collect-all','pypdf','--collect-all','tzdata',str(ROOT/'run.py')],check=True,cwd=ROOT)
import shutil
out=ROOT/'build/backend'
if out.exists():shutil.rmtree(out)
shutil.copytree(ROOT/'build/pyinstaller/qqmanager-server',out,symlinks=True)
print(out)
