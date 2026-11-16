"""Build credential-free experimental plugin and Windows source distributions."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parent.parent
out=ROOT/'dist';out.mkdir(exist_ok=True)
plugin=ROOT/'plugins/qqmanager-native'
files=['core.cjs','main.cjs','media.cjs','manifest.json']
with zipfile.ZipFile(out/'QQmanager-native-plugin-0.1.0.zip','w',zipfile.ZIP_DEFLATED) as z:
    for name in files:z.write(plugin/name,name)
    z.write(ROOT/'docs/NATIVE-PLUGIN.md','README.md')
with zipfile.ZipFile(out/'QQmanager-Windows-experimental.zip','w',zipfile.ZIP_DEFLATED) as z:
    for name in ['run.py','requirements.txt','start-windows.cmd']:
        z.write(ROOT/name,'QQmanager/'+name)
    for folder,pattern in [('qqmanager','*.py'),('web','*'),('plugins/qqmanager-native','*'),('scripts/native','pair_windows.py')]:
        for file in sorted((ROOT/folder).glob(pattern)):
            if file.is_file() and file.suffix in ('.py','.js','.cjs','.json','.html','.css'):
                z.write(file,'QQmanager/'+str(file.relative_to(ROOT)))
    z.write(ROOT/'docs/NATIVE-PLUGIN.md','QQmanager/README.md')
manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob('*.zip'))}
(out/'SHA256SUMS.json').write_text(json.dumps(manifest,indent=2)+'\n')
for name,digest in manifest.items():print(name,digest)
