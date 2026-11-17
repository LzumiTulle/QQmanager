"""Check distributable inputs and produce checksums, without reading user data."""
from pathlib import Path
import hashlib,json,re
ROOT=Path(__file__).resolve().parents[2]
package=json.loads((ROOT/'package.json').read_text(encoding='utf-8'))
config=json.loads((ROOT/'src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
assert config['bundle']['resources']=={'../build/backend/':'backend/','../plugins/qqmanager-native/':'qq-plugin/','../docs/DESKTOP.md':'DESKTOP.md'}
assert 'electron' not in package['devDependencies']
for folder in ('build/backend','plugins/qqmanager-native','desktop'):
 for p in (ROOT/folder).rglob('*'):
  if not p.is_file():continue
  assert p.name not in ('qqmanager.db','config.json','credentials.json','desktop.json','status.json','outbox.json'),p
  if p.suffix in ('.js','.cjs','.py','.json','.md','.html'):
   data=p.read_text(encoding='utf-8',errors='replace')
   assert not re.search(r'sk-[A-Za-z0-9]{24,}',data),p
   assert str(Path.home()) not in data,p
files=sorted(p for p in (ROOT/'release').iterdir() if p.suffix in ('.dmg','.zip','.exe') and package['version'] in p.name)
assert files,'No installers produced'
(ROOT/'release/SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in files))
print('Release inputs audited; checksums written for',len(files),'artifacts.')
