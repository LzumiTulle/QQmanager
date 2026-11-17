"""Build the native WebView shell and collect only this version's installers."""
import json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
version=json.loads((ROOT/'package.json').read_text())['version']
cmd=['npx.cmd' if os.name=='nt' else 'npx','tauri','build']
if sys.platform=='darwin':cmd+=['--bundles','app,dmg','--config','src-tauri/tauri.macos.conf.json']
else:cmd+=['--bundles','nsis']
subprocess.run(cmd,cwd=ROOT,check=True)
out=ROOT/'release';out.mkdir(exist_ok=True)
for p in (ROOT/'src-tauri/target/release/bundle').rglob('*'):
 if p.suffix in ('.dmg','.exe') and p.is_file():
  import platform
  arch='arm64' if platform.machine().lower() in ('arm64','aarch64') else 'x64'
  target='win' if os.name=='nt' else 'mac'
  dest=out/f'QQmanager-{version}-{target}-{arch}{p.suffix}'
  shutil.copy2(p,dest);print(dest)
