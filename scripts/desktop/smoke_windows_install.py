"""Run only on a disposable Windows CI runner: install NSIS and launch tray service."""
import os,subprocess,time,json,urllib.request
from pathlib import Path
assert os.name=='nt','This installer smoke test requires Windows'
ROOT=Path(__file__).resolve().parents[2]
installers=list((ROOT/'release').glob('*.exe'));assert len(installers)==1,installers
subprocess.run([str(installers[0]),'/S'],check=True,timeout=120)
package=json.loads((ROOT/'package.json').read_text(encoding='utf-8'))
# Tauri NSIS current-user destination.
exe=Path(os.environ['LOCALAPPDATA'])/'QQmanager'/'QQmanager.exe'
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
 # The backend becomes healthy before Tauri finishes tray/login setup.
 # Wait for that separate startup milestone instead of racing the first write.
 for _ in range(100):
  try:
   settings=json.loads(prefs.read_text(encoding='utf-8'))
   if settings.get('autoStart') is True:break
  except (FileNotFoundError,json.JSONDecodeError):pass
  time.sleep(.1)
 else:raise AssertionError('Installed desktop did not finish autostart setup')
 data=Path(settings['dataDirectory'])
 for _ in range(100):
  try:
   runtime=json.loads((data/'desktop-runtime.json').read_text(encoding='utf-8'))
   if runtime.get('webview_loaded') and runtime.get('renderer')=='WebView2':break
  except (FileNotFoundError,json.JSONDecodeError):pass
  time.sleep(.2)
 else:raise AssertionError('WebView2 did not load the local application')
 # Single instance must reuse the running window and backend.
 second=subprocess.Popen([str(exe),'--background']);second.wait(timeout=15)
 assert p.poll() is None
 print('Windows NSIS installation, installed desktop background launch, clean database WebView2 page loading, single instance and autostart preference passed.')
finally:
 # This runner contains only this test application, never a user QQ process.
 subprocess.run(['taskkill','/IM','QQmanager.exe','/F'],capture_output=True,check=False)
 p.wait(timeout=10)

for _ in range(50):
 try:urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=.3)
 except Exception:break
 time.sleep(.1)
else:raise AssertionError('Backend survived parent exit; EOF cleanup failed')
print('Backend EOF cleanup passed.')
