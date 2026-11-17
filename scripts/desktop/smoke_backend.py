"""Exercise the actual frozen server with disposable data, including clean EOF exit."""
import base64,json,os,socket,subprocess,tempfile,time,urllib.request,sys,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
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
  for asset in ('planner.js','planner-model.js','filter-model.js'):
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/'+asset) as response:assert response.status==200
  class Provider(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
    context=json.loads(request['messages'][-1]['content'])
    if 'kind' in context:
     result={'courses':[{'title':'Synthetic course','weekday':1,'start_period':1,'end_period':2,'weeks':[11,13],'location':'A101'}], 'calendar':None,'warnings':[]}
    else:
     row=context['target']
     result={'items':[{'kind':'通知','title':'模拟通知','reason':'明确通知','evidence':[{'message_id':row['id'],'quote':row['content']}]}]}
    body=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}).encode()
    self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  provider=ThreadingHTTPServer(('127.0.0.1',0),Provider);thread=threading.Thread(target=provider.serve_forever,daemon=True);thread.start()
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/session') as response:csrf=json.load(response)['csrf']
   def post(route,body):
    req=urllib.request.Request(f'http://127.0.0.1:{port}'+route,json.dumps(body).encode(),{'Content-Type':'application/json','X-CSRF-Token':csrf})
    with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
   post('/api/llm/config',{'url':f'http://127.0.0.1:{provider.server_port}/v1','model':'smoke-model','key':'synthetic'})
   assert post('/api/llm/test',{'enable':True})['items']==1
   post('/api/planner/calendar',{'anchor_date':'2026-11-16','anchor_week':11,'total_weeks':19})
   draft=post('/api/planner/import',{'name':'synthetic.csv','data':base64.b64encode(b'Course,Monday,1-2,11\nSynthetic course,A101').decode(),'kind':'courses','allow_model':True})
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/planner?date=2026-11-16') as response:assert json.load(response)['courses']==[]
   assert post('/api/planner/apply',{'id':draft['id']})['added']==1
   assert post('/api/planner/apply',{'id':draft['id']})['added']==0
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/planner?date=2026-11-16') as response:plan=json.load(response)
   assert plan['week']==11 and len(plan['days'][0]['courses'])==1
   course=plan['courses'][0];post('/api/planner/course',{**course,'location':'B202'})
   post('/api/planner/course/delete',{'id':course['id']});post('/api/planner/course/restore',{'id':course['id']})
  finally:provider.shutdown();provider.server_close();thread.join()
  process.stdin.close();assert process.wait(timeout=8)==0
  print('Frozen backend: health, assets, model validation, timetable import/review/edit/calendar, EOF exit passed.')
 finally:
  if process.poll() is None:process.kill();process.wait()
