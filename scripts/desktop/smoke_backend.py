"""Exercise the actual frozen server with disposable data, including clean EOF exit."""
import json,os,socket,subprocess,tempfile,time,urllib.request,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
exe=ROOT/'build/backend'/('qqmanager-server.exe' if os.name=='nt' else 'qqmanager-server')
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
with tempfile.TemporaryDirectory() as directory:
 process=subprocess.Popen([str(exe),'--port',str(port),'--data-dir',directory],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env={**os.environ,'QQMANAGER_DESKTOP':'1'})
 try:
  for _ in range(100):
   try:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:health=json.load(response)
    break
   except Exception:
    if process.poll() is not None:raise RuntimeError(process.stdout.read().decode(errors='replace'))
    time.sleep(.2)
  else:raise RuntimeError('Frozen server failed to become ready')
  assert health=={'app':'QQmanager','protocol':1}
  with urllib.request.urlopen(f'http://127.0.0.1:{port}/') as response:assert b'QQmanager' in response.read()
  with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/state') as response:assert json.load(response)['messages']==[]
  process.stdin.close();assert process.wait(timeout=8)==0
  print('Frozen backend: health, bundled web assets, clean initial data, parent EOF exit passed.')
 finally:
  if process.poll() is None:process.kill();process.wait()
