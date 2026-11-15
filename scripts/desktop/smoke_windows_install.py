"""Run only on a disposable Windows CI runner: install NSIS and launch tray service."""
import os,subprocess,time,json,urllib.request
from pathlib import Path
assert os.name=='nt','This installer smoke test requires Windows'
ROOT=Path(__file__).resolve().parents[2]
installers=list((ROOT/'release').glob('*.exe'));assert len(installers)==1,installers
subprocess.run([str(installers[0]),'/S'],check=True,timeout=120)
exe=Path(os.environ['LOCALAPPDATA'])/'Programs/QQmanager/QQmanager.exe'
assert exe.is_file(),f'Installed application missing: {exe}'
p=subprocess.Popen([str(exe),'--background'])
try:
 for _ in range(100):
  try:
   with urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=1) as r:health=json.load(r)
   assert health=={'app':'QQmanager','protocol':1};break
  except Exception:time.sleep(.3)
 else:raise AssertionError('Installed desktop did not start the bundled service')
 with urllib.request.urlopen('http://127.0.0.1:8765/api/state',timeout=2) as r:assert json.load(r)['messages']==[]
 prefs=Path(os.environ['APPDATA'])/'QQmanager/desktop.json'
 assert json.loads(prefs.read_text(encoding='utf-8'))['autoStart'] is True
 print('Windows NSIS installation, installed desktop background launch, clean database and autostart preference passed.')
finally:
 # This runner contains only this test application, never a user QQ process.
 subprocess.run(['taskkill','/IM','QQmanager.exe','/F'],capture_output=True,check=False)
 p.wait(timeout=10)
