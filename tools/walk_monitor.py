"""Local consolidated view of the live GitHub/AWS walk logs."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import threading
import time
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import boto3

BUCKET = 'slope-walk-462947327980-20261001'
PREFIX = 'walk-36781427817/fresh-balanced'
RUN = '36963960032'
REPO = 'owassmer/Slope_Sparse_Events'
LOCK = threading.RLock()
EVENTS = deque(maxlen=12000)
TASKS = {}
TEXT = {}
ETAGS = {}
STATE = {'phase': 'Preparing corrected branch list', 'total': None, 'done': 0, 'active': 0,
         'updated': None, 'error': None, 'jobs': [], 'raw_histories': 0}


def emit(task, text, when=None, source=None):
    with LOCK:
        EVENTS.append({'time': when or datetime.now(UTC).isoformat(), 'task': task,
                       'source': source or TASKS.get(task, {}).get('host', ''), 'text': text,
                       'error': bool(re.search(r'error|traceback|failed|exception', text, re.I))})


def ingest(task, content, when=None, source=None):
    lines = content.splitlines()
    old = TEXT.get(task, [])
    common = 0
    while common < min(len(old), len(lines)) and old[common] == lines[common]:
        common += 1
    for line in lines[common:]:
        if line.strip():
            emit(task, line, when, source)
    with LOCK:
        TEXT[task] = lines
        row = TASKS.setdefault(task, {'id': task, 'host': source or '', 'state': 'log'})
        row['tail'] = lines[-250:]
        row['last_line'] = next((s for s in reversed(lines) if s.strip()), '')
        counts = [int(value) for line in lines
                  for pair in re.findall(r'\bpaths (\d+)|\b(\d+) paths\b', line)
                  for value in pair if value]
        if counts:
            row['histories'] = max(counts)


def objects(client, suffix):
    return [o for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=PREFIX + suffix)
            for o in page.get('Contents', [])]


def body(client, key):
    return client.get_object(Bucket=BUCKET, Key=key)['Body'].read()


def poll(client):
    last_github = 0
    workflow_logs = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        while True:
            started = time.monotonic()
            try:
                if STATE['total'] is None:
                    try:
                        ready = json.loads(body(client, PREFIX + '/ready.json'))
                        with LOCK:
                            STATE.update(total=ready['tasks'], roots=ready['roots'], matched_costs=ready['matched_costs'])
                        emit('queue', f"Published {ready['tasks']} tasks covering {ready['roots']} native branches")
                    except client.exceptions.NoSuchKey:
                        pass
                listings = list(pool.map(lambda suffix: objects(client, suffix),
                                         ['/refine-v1/claims/', '/refine-v1/done/', '/refine-v1/live/', '/monitor/']))
                claims, done, logs, host_logs = listings
                completed = {Path(o['Key']).stem for o in done}
                active = {Path(o['Key']).stem for o in claims} - completed

                def fetch_claim(o, completed=completed):
                    ident = Path(o['Key']).stem
                    if ident in completed or (ident in TASKS and TASKS[ident].get('host')):
                        return
                    c = json.loads(body(client, o['Key']))
                    with LOCK:
                        TASKS.setdefault(ident, {'id': ident}).update(host=c['host'], state='active')
                    emit(ident, 'Task claimed', source=c['host'])

                def fetch_done(o):
                    ident = Path(o['Key']).stem
                    if TASKS.get(ident, {}).get('state') == 'complete':
                        return
                    value = json.loads(body(client, o['Key']))
                    text = body(client, value['source'] + '/walk.log').decode(errors='replace')
                    ingest(ident, text, o['LastModified'].isoformat())
                    with LOCK:
                        TASKS.setdefault(ident, {'id': ident})['state'] = 'complete'
                    emit(ident, 'Output saved successfully', o['LastModified'].isoformat())

                def fetch_log(o, completed=completed):
                    ident = Path(o['Key']).stem
                    if ident in completed or ETAGS.get(o['Key']) == o['ETag']:
                        return
                    ingest(ident, body(client, o['Key']).decode(errors='replace'), o['LastModified'].isoformat(),
                           'AWS startup' if '/monitor/' in o['Key'] else None)
                    ETAGS[o['Key']] = o['ETag']

                for fn, rows in ((fetch_claim, claims), (fetch_log, logs + host_logs), (fetch_done, done)):
                    for future in [pool.submit(fn, row) for row in rows]:
                        future.result()
                with LOCK:
                    for ident in active:
                        TASKS.setdefault(ident, {'id': ident})['state'] = 'active'
                    STATE.update(done=len(completed), active=len(active),
                                 raw_histories=sum(t.get('histories', 0) for key, t in TASKS.items()
                                                   if re.fullmatch(r'\d+-\d+', key)),
                                 phase=('Walking corrected tree' if STATE['total'] else 'Preparing corrected branch list'),
                                 updated=datetime.now(UTC).isoformat(), error=None)
                    if STATE['total'] is not None and len(completed) == STATE['total']:
                        STATE['phase'] = 'Walk outputs complete — assembly and global checks next'
                if time.monotonic() - last_github > 30:
                    data = json.loads(subprocess.check_output(
                        ['gh', 'run', 'view', RUN, '--repo', REPO, '--json', 'jobs'], text=True, timeout=25))
                    with LOCK:
                        STATE['jobs'] = [{'name': j['name'], 'status': j['status'], 'conclusion': j['conclusion'],
                                          'url': j['url'], 'step': next((s['name'] for s in j['steps']
                                                                       if s['status'] == 'in_progress'), '')}
                                         for j in data['jobs']]
                    for j in data['jobs']:
                        if j['status'] == 'completed' and j['databaseId'] not in workflow_logs:
                            result = subprocess.run(['gh', 'api', f"repos/{REPO}/actions/jobs/{j['databaseId']}/logs"],
                                                    text=True, capture_output=True, timeout=25)
                            if result.returncode == 0:
                                ingest('workflow-' + str(j['databaseId']), result.stdout, source=j['name'])
                                workflow_logs.add(j['databaseId'])
                    last_github = time.monotonic()
            except Exception as error:
                with LOCK:
                    STATE['error'] = f'{type(error).__name__}: {error}'
            time.sleep(max(1, 10 - (time.monotonic() - started)))


PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>Slope · Live walk</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#10151c;color:#e8edf3;font:15px system-ui,sans-serif}main{max-width:1450px;margin:auto;padding:30px}h1{font-size:26px;margin:0 0 8px}.muted{color:#99a9bb}a{color:#85c7ff}.top{display:flex;justify-content:space-between;gap:20px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0}.card{background:#1a232f;padding:18px;border-radius:10px}.number{font-size:28px;font-weight:650;margin-top:7px}progress{width:100%;height:12px;accent-color:#74d5b5}.toolbar{display:flex;gap:12px;align-items:center;margin:20px 0}input,select,button{background:#202d3d;color:inherit;border:1px solid #3a4d63;border-radius:6px;padding:9px}.logs{height:55vh;overflow:auto;background:#0c1118;border:1px solid #293647;border-radius:8px;padding:10px;font:12px ui-monospace,monospace}.line{display:grid;grid-template-columns:90px 125px 155px 1fr;gap:8px;border-bottom:1px solid #18212d;padding:5px 2px}.text{white-space:pre-wrap;overflow-wrap:anywhere}.error{color:#ff9a9a}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:8px;border-bottom:1px solid #293647}.badge{background:#233f3a;color:#9de7c9;padding:5px 10px;border-radius:20px}details{margin-top:20px}#warning{margin:10px 0}.foot{margin:12px 0;font-size:12px} @media(max-width:800px){.cards{grid-template-columns:1fr 1fr}.line{grid-template-columns:70px 80px 1fr}.source{display:none}}
</style></head><body><main><div class="top"><div><h1>Slope · Live walk</h1><div class="muted">One view of GitHub and AWS execution</div></div><div><a href="https://github.com/owassmer/Slope_Sparse_Events/actions/runs/36963960032" target="_blank">GitHub run ↗</a><div id="updated" class="muted"></div></div></div>
<p><span id="phase" class="badge">Connecting…</span></p><div id="warning" class="error"></div>
<div class="cards"><div class="card">Tasks saved<div id="done" class="number">—</div></div><div class="card">Active tasks<div id="active" class="number">—</div></div><div class="card">Raw histories emitted<div id="paths" class="number">—</div></div><div class="card">GitHub jobs running<div id="workers" class="number">—</div></div></div><progress id="progress" value="0" max="1"></progress>
<div class="toolbar"><select id="task"><option value="">All sources</option></select><input id="search" placeholder="Search all logs"><label><input type="checkbox" id="errors"> Errors only</label><button id="pause">Pause display</button></div>
<div id="logs" class="logs"></div><div class="muted foot">Refreshes every 10 seconds. Times show when log updates were observed. Raw history counts update as workers log them; they are not the final pooled path count. Task logs are live; GitHub workflow logs join this view when each job finishes.</div>
<details><summary>Tasks and latest messages</summary><table><thead><tr><th>Task</th><th>Worker</th><th>Status</th><th>Histories</th><th>Latest message</th></tr></thead><tbody id="tasks"></tbody></table></details>
<details><summary>GitHub worker status</summary><table><tbody id="jobs"></tbody></table></details></main>
<script>
let data=null,paused=false;const el=id=>document.getElementById(id),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),num=x=>Number(x||0).toLocaleString();
function render(){if(!data||paused)return;let s=data.state;el('phase').textContent=s.phase;el('updated').textContent=s.updated?'Updated '+new Date(s.updated).toLocaleTimeString():'';el('warning').textContent=s.error?'Update error: '+s.error:'';el('done').textContent=num(s.done)+' / '+(s.total?num(s.total):'preparing');el('active').textContent=num(s.active);el('paths').textContent=num(s.raw_histories);el('workers').textContent=s.jobs.filter(j=>j.status==='in_progress').length+' / 40';el('progress').max=s.total||1;el('progress').value=s.done;
let current=el('task').value,ids=[...new Set(data.events.map(e=>e.task))].sort();el('task').innerHTML='<option value="">All sources</option>'+ids.map(id=>'<option value="'+esc(id)+'">'+esc(id)+'</option>').join('');el('task').value=current;
let query=el('search').value.toLowerCase(),only=el('errors').checked;let events=data.events.filter(e=>(!current||e.task===current)&&(!only||e.error)&&(!query||(e.text+' '+e.task+' '+e.source).toLowerCase().includes(query))).slice(-1000);
let box=el('logs'),bottom=box.scrollHeight-box.scrollTop-box.clientHeight<60;box.innerHTML=events.map(e=>'<div class="line '+(e.error?'error':'')+'"><span class="muted">'+esc(new Date(e.time).toLocaleTimeString())+'</span><span>'+esc(e.task)+'</span><span class="source muted">'+esc(e.source)+'</span><span class="text">'+esc(e.text)+'</span></div>').join('')||'<div class="muted">Waiting for log output…</div>';if(bottom)box.scrollTop=box.scrollHeight;
el('tasks').innerHTML=data.tasks.filter(t=>!t.id.startsWith('workflow-')).sort((a,b)=>(a.state==='active'?-1:1)-(b.state==='active'?-1:1)||a.id.localeCompare(b.id)).map(t=>'<tr><td><a target="_blank" href="/api/task?id='+encodeURIComponent(t.id)+'">'+esc(t.id)+'</a></td><td>'+esc(t.host)+'</td><td>'+esc(t.state)+'</td><td>'+num(t.histories)+'</td><td class="text">'+esc(t.last_line)+'</td></tr>').join('');el('jobs').innerHTML=s.jobs.map(j=>'<tr><td><a href="'+esc(j.url)+'" target="_blank">'+esc(j.name)+'</a></td><td>'+esc(j.conclusion||j.status)+'</td><td>'+esc(j.step)+'</td></tr>').join('');}
async function refresh(){try{let r=await fetch('/api/status');if(!r.ok)throw Error(r.status);data=await r.json();render()}catch(e){el('warning').textContent='Live view disconnected: '+e.message}}
['task','search','errors'].forEach(id=>el(id).addEventListener('input',render));el('pause').onclick=()=>{paused=!paused;el('pause').textContent=paused?'Resume display':'Pause display';render()};refresh();setInterval(refresh,10000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        target = urlparse(self.path)
        if target.path == '/api/status':
            with LOCK:
                payload = json.dumps({'state': STATE, 'events': sorted(EVENTS, key=lambda e: e['time']),
                                      'tasks': [{k: v for k, v in t.items() if k != 'tail'}
                                                for t in TASKS.values()]}).encode()
            kind = 'application/json'
        elif target.path == '/api/task':
            ident = parse_qs(target.query).get('id', [''])[0]
            with LOCK:
                payload = '\n'.join(TEXT.get(ident, [])).encode()
            kind = 'text/plain; charset=utf-8'
        elif target.path == '/':
            payload, kind = PAGE.encode(), 'text/html; charset=utf-8'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--credentials', required=True)
    parser.add_argument('--port', type=int, default=18766)
    args = parser.parse_args()
    c = json.loads(Path(args.credentials).read_text())
    client = boto3.client('s3', region_name='us-east-2', aws_access_key_id=c['AccessKeyId'],
                          aws_secret_access_key=c['SecretAccessKey'], aws_session_token=c['Token'])
    threading.Thread(target=poll, args=(client,), daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Live consolidated logs: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
