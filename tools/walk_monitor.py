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
PREFIX = 'walk-36781427817/fresh-balanced-coarse'
POOL_PREFIX = 'walk-36781427817/fresh-balanced-coarse/pool'
TRACK_RUN = None
EXTRA_PREFIX = None
RUN = '36964784016'
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
    if common == 0 and old and lines:
        tail = old[-20:]
        for i in range(len(lines) - len(tail) + 1):
            if lines[i:i + len(tail)] == tail:
                common = i + len(tail)
                break
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


def objects(client, suffix, prefix=None):
    return [o for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=(prefix or PREFIX) + suffix)
            for o in page.get('Contents', [])]


def body(client, key):
    return client.get_object(Bucket=BUCKET, Key=key)['Body'].read()


def poll(client):
    global RUN
    last_github = 0
    workflow_logs = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        while True:
            started = time.monotonic()
            try:
                try:
                    progress = json.loads(body(client, POOL_PREFIX + '/progress.json'))
                except client.exceptions.NoSuchKey:
                    progress = None
                if progress:
                    with LOCK:
                        STATE['pool'] = progress
                        STATE['phase'] = 'Pooling: ' + progress['stage'].replace('_', ' ')
                    ingest('pool-progress', json.dumps(progress, indent=2), source='Pool coordinator')
                    for obj in objects(client, '/fleet-v3/logs/', POOL_PREFIX) + objects(client, '/merge-logs/', POOL_PREFIX) + objects(client, '/reduce-logs/', POOL_PREFIX):
                        if ETAGS.get(obj['Key']) != obj['ETag']:
                            ingest(obj['Key'].split('/')[-2] + '-' + Path(obj['Key']).stem,
                                   body(client, obj['Key']).decode(errors='replace'),
                                   obj['LastModified'].isoformat(), 'GitHub pooling')
                            ETAGS[obj['Key']] = obj['ETag']
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
                primary_done = len(done)
                replacement_ids = set()
                for extra_prefix in EXTRA_PREFIX or []:
                    extra = list(pool.map(lambda suffix, p=extra_prefix: objects(client, suffix, p),
                                          ['/refine-v1/claims/', '/refine-v1/done/', '/refine-v1/live/']))
                    replacement_ids.update(Path(o['Key']).stem for o in extra[0] + extra[1] + extra[2])
                    claims += extra[0]
                    done += extra[1]
                    logs += extra[2]
                completed = {Path(o['Key']).stem for o in done}
                now = datetime.now(UTC)
                active = {Path(o['Key']).stem for o in claims
                          if (now - o['LastModified']).total_seconds() < 90} - completed

                def fetch_claim(o, completed=completed):
                    ident = Path(o['Key']).stem
                    if ident in completed or (ident in TASKS and TASKS[ident].get('host')):
                        return
                    try:
                        c = json.loads(body(client, o['Key']))
                    except client.exceptions.NoSuchKey:
                        return  # a finished task can release its lease after the listing
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
                    for ident, row in TASKS.items():
                        if re.fullmatch(r'\d+-\d+', ident) and ident not in completed and ident not in active:
                            row['state'] = 'awaiting retry'
                    for ident in active:
                        TASKS.setdefault(ident, {'id': ident})['state'] = 'active'
                    STATE.update(done=primary_done, active=len(active),
                                 replacement_active=len(active & replacement_ids),
                                 raw_histories=sum(t.get('histories', 0) for key, t in TASKS.items()
                                                   if re.fullmatch(r'\d+-\d+', key) and key not in replacement_ids),
                                 phase=('Walking corrected tree' if STATE['total'] else 'Preparing corrected branch list'),
                                 updated=datetime.now(UTC).isoformat(), error=None)
                    if STATE['total'] is not None and primary_done == STATE['total']:
                        STATE['phase'] = 'Walk outputs complete — assembly and global checks next'
                    if STATE.get('pool'):
                        STATE['phase'] = 'Pooling: ' + STATE['pool']['stage'].replace('_', ' ')
                if time.monotonic() - last_github > 30:
                    recent = json.loads(subprocess.check_output(
                        ['gh', 'run', 'list', '--repo', REPO, '--branch', 'fresh-walk',
                         '--workflow', 'fresh-walk.yml', '--limit', '10', '--json', 'databaseId,url,status'],
                        text=True, timeout=25))
                    runs = [r for r in recent if (r['databaseId'] in TRACK_RUN if TRACK_RUN else
                            r['status'] != 'completed' or r['databaseId'] in {36965503657, 36971374732, 36971460782})]
                    pool_runs = json.loads(subprocess.check_output(
                        ['gh', 'run', 'list', '--repo', REPO, '--branch', 'pool-fleet',
                         '--workflow', 'pool-fleet.yml', '--limit', '1', '--json', 'databaseId,url,status'],
                        text=True, timeout=25))
                    merge_runs = json.loads(subprocess.check_output(
                        ['gh', 'run', 'list', '--repo', REPO, '--branch', 'merge-fleet',
                         '--limit', '1', '--json', 'databaseId,url,status'],
                        text=True, timeout=25))
                    reduce_runs = json.loads(subprocess.check_output(
                        ['gh', 'run', 'list', '--repo', REPO, '--branch', 'reduce-fleet',
                         '--limit', '1', '--json', 'databaseId,url,status'],
                        text=True, timeout=25))
                    catalog_runs = json.loads(subprocess.check_output(
                        ['gh', 'run', 'list', '--repo', REPO, '--branch', 'archive-catalog',
                         '--limit', '1', '--json', 'databaseId,url,status'],
                        text=True, timeout=25))
                    runs += pool_runs + merge_runs + reduce_runs + catalog_runs
                    jobs = []
                    for run in runs:
                        data = json.loads(subprocess.check_output(
                            ['gh', 'run', 'view', str(run['databaseId']), '--repo', REPO, '--json', 'jobs'],
                            text=True, timeout=25))
                        for job in data['jobs']:
                            jobs.append({**job, 'name': f"{run['databaseId']} · {job['name']}"})
                    with LOCK:
                        STATE['run_url'] = recent[0]['url']
                        STATE['jobs'] = [{'name': j['name'], 'status': j['status'], 'conclusion': j['conclusion'],
                                          'url': j['url'], 'step': next((s['name'] for s in j['steps']
                                                                       if s['status'] == 'in_progress'), '')}
                                         for j in jobs]
                    log_started, log_count = time.monotonic(), 0
                    for j in sorted(jobs, key=lambda j: j.get('completedAt') or '', reverse=True):
                        if log_count >= 4 or time.monotonic() - log_started > 5:
                            break
                        if j['status'] == 'completed' and j['databaseId'] not in workflow_logs:
                            log_count += 1
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


PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>Slope · Live computation</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#10151c;color:#e8edf3;font:15px system-ui,sans-serif}main{max-width:1450px;margin:auto;padding:30px}h1{font-size:26px;margin:0 0 8px}.muted{color:#99a9bb}a{color:#85c7ff}.top{display:flex;justify-content:space-between;gap:20px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0}.card{background:#1a232f;padding:18px;border-radius:10px}.number{font-size:28px;font-weight:650;margin-top:7px}progress{width:100%;height:12px;accent-color:#74d5b5}.toolbar{display:flex;gap:12px;align-items:center;margin:20px 0}input,select,button{background:#202d3d;color:inherit;border:1px solid #3a4d63;border-radius:6px;padding:9px}.logs{height:55vh;overflow:auto;background:#0c1118;border:1px solid #293647;border-radius:8px;padding:10px;font:12px ui-monospace,monospace}.line{display:grid;grid-template-columns:90px 125px 155px 1fr;gap:8px;border-bottom:1px solid #18212d;padding:5px 2px}.text{white-space:pre-wrap;overflow-wrap:anywhere}.error{color:#ff9a9a}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:8px;border-bottom:1px solid #293647}.badge{background:#233f3a;color:#9de7c9;padding:5px 10px;border-radius:20px}details{margin-top:20px}#warning{margin:10px 0}.foot{margin:12px 0;font-size:12px} @media(max-width:800px){.cards{grid-template-columns:1fr 1fr}.line{grid-template-columns:70px 80px 1fr}.source{display:none}}
</style></head><body><main><div class="top"><div><h1>Slope · Live computation</h1><div class="muted">One view of GitHub and AWS execution</div></div><div><a id="runlink" href="https://github.com/owassmer/Slope_Sparse_Events/actions/workflows/fresh-walk.yml" target="_blank">GitHub run ↗</a><div id="updated" class="muted"></div></div></div>
<p><span id="phase" class="badge">Connecting…</span></p><div id="warning" class="error"></div>
<div class="cards"><div class="card"><span id="done-label">Tasks saved</span><div id="done" class="number">—</div></div><div class="card"><span id="active-label">Active tasks</span><div id="active" class="number">—</div></div><div class="card"><span id="paths-label">Histories reported (minimum)</span><div id="paths" class="number">—</div></div><div class="card">GitHub jobs running<div id="workers" class="number">—</div></div></div><progress id="progress" value="0" max="1"></progress>
<div id="pool" class="card" hidden></div>
<div class="toolbar"><select id="task"><option value="">All sources</option></select><input id="search" placeholder="Search all logs"><label><input type="checkbox" id="errors"> Errors only</label><button id="pause">Pause display</button></div>
<div id="logs" class="logs"></div><div id="foot" class="muted foot">Refreshes every 10 seconds. Times show when log updates were observed. Workers report history counts every 1,000 paths; a missing count does not mean an idle worker. Refinement lines show traversal, not saved output. Reported counts they are not the final pooled path count. Task logs are live; GitHub workflow logs join this view when each job finishes.</div>
<details><summary>Tasks and latest messages</summary><table><thead><tr><th>Task</th><th>Worker</th><th>Status</th><th>Histories</th><th>Latest message</th></tr></thead><tbody id="tasks"></tbody></table></details>
<details><summary>GitHub worker status</summary><table><tbody id="jobs"></tbody></table></details></main>
<script>
let data=null,paused=false;const el=id=>document.getElementById(id),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),num=x=>Number(x||0).toLocaleString();
function render(){if(!data||paused)return;let s=data.state;if(s.run_url)el('runlink').href=s.run_url;el('phase').textContent=s.phase;el('updated').textContent=s.updated?'Updated '+new Date(s.updated).toLocaleTimeString():'';el('warning').textContent=(s.error||s.inventory_error)?'Update error: '+(s.error||s.inventory_error):'';el('done-label').textContent=s.done_label||'Tasks saved';el('done').textContent=num(s.done)+' / '+(s.total?num(s.total):'preparing');el('active-label').textContent=s.refresh?'Recent unfinished batches':'Active tasks';el('active').textContent=num(s.active)+(s.replacement_active?' ('+num(s.replacement_active)+' replacements)':'');el('paths-label').textContent=s.histories_label||(s.refresh?'Batches collected':'Histories reported (minimum)');el('paths').textContent=num(s.stage_histories??(s.refresh?s.collected:s.raw_histories));el('workers').textContent=s.jobs.filter(j=>j.status==='in_progress').length+' / '+s.jobs.length;el('progress').max=s.total||1;el('progress').value=s.done;
if(s.refresh)el('foot').textContent='Refreshes every 10 seconds. Saved batches have reached storage; collected batches have been incorporated by the coordinator. Recent unfinished batches are observed from storage scans, not a count of worker processes or occupied cores. Inventory counts can lag; collected progress refreshes separately. The log pane shows recent worker updates.';let p=s.pool;el('pool').hidden=!p;if(p){el('pool').textContent=(s.refresh?'Question refresh · ':'Pooling · ')+p.stage.replaceAll('_',' ')+(p.total_partitions?' · '+num(p.partitions)+' / '+num(p.total_partitions)+' pieces saved · '+num(p.buckets_complete)+' / 16 batches assembled':'')+(p.total_jobs?' · '+num(p.jobs_complete)+' / '+num(p.total_jobs)+' jobs saved'+(p.total_parts?' · '+num(p.completed_output_parts)+' / '+num(p.total_parts)+' output parts':''):'')+(p.error?' · '+p.error:'')+' · updated '+new Date((p.observed_at||p.time)*1000).toLocaleTimeString();}
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


def refresh_progress(client, prefix):
    progress = json.loads(body(client, prefix + '/progress.json'))
    if progress.get('stage') in ('prepare_assembly', 'streaming_github_assembly'):
        try:
            streaming = json.loads(body(client, prefix + '/stream-progress.json'))
            if time.time() - streaming.get('time', 0) < 60:
                return streaming
        except Exception:
            pass
    return progress


def poll_refresh_counts(client, prefix):
    """Inventory scans must not block the lightweight progress heartbeat."""
    while True:
        try:
            progress = refresh_progress(client, prefix)
            if progress.get('logs_prefix'):
                base = progress['logs_prefix'].removesuffix('/logs/')
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    outputs, claims, logs = executor.map(
                        lambda suffix, base=base: objects(client, suffix, base),
                        ('/outputs/', '/claims/', '/logs/'))
                saved = {Path(o['Key']).name.removesuffix('.pkl.gz').removesuffix('.tgz') for o in outputs}
                now = time.time()
                live = {Path(o['Key']).stem for o in logs
                        if now - o['LastModified'].timestamp() < 90}
                live.update(Path(o['Key']).stem for o in claims
                            if now - o['LastModified'].timestamp() < 90)
                with LOCK:
                    STATE.update(done=len(saved), active=len(live - saved),
                                 counts_updated=datetime.now(UTC).isoformat())
        except Exception as error:
            with LOCK:
                STATE['inventory_error'] = str(error)
        else:
            with LOCK:
                STATE['inventory_error'] = None
        time.sleep(10)


def poll_refresh_stats(client, prefix):
    """Refresh headline counts independently of downloading worker logs."""
    last_github = 0
    while True:
        started = time.monotonic()
        try:
            progress = refresh_progress(client, prefix)
            with LOCK:
                STATE.update(refresh=True, phase=progress['stage'].replace('_', ' '), pool=progress,
                             total=progress.get('total_batches', progress.get('total', progress.get('inputs_ready', progress.get('balanced_batches')))),
                             collected=progress.get('completed_batches', progress.get('completed', 0)),
                             updated=datetime.now(UTC).isoformat(), error=None)
            with LOCK:
                STATE.update(stage_histories=progress.get('histories'),
                             histories_label='Histories processed' if 'histories' in progress else None,
                             done_label='Tasks saved')
                if not progress.get('logs_prefix'):
                    STATE.update(done=progress.get('sources', progress.get('archives', progress.get('completed_batches', progress.get('completed', 0)))),
                                 total=progress.get('total_sources', progress.get('total_archives', progress.get('total_batches', progress.get('total')))),
                                 active=progress.get('active', 0), done_label='Source files processed' if 'sources' in progress else 'Tasks processed')
            if time.monotonic() - last_github > 30:
                runs = json.loads(subprocess.check_output(
                    ['gh', 'run', 'list', '--repo', REPO, '--branch', progress.get('github_branch', 'question-refresh'),
                     '--limit', '1', '--json', 'databaseId,url'], text=True, timeout=25))
                if runs:
                    data = json.loads(subprocess.check_output(
                        ['gh', 'run', 'view', str(runs[0]['databaseId']), '--repo', REPO,
                         '--json', 'jobs'], text=True, timeout=25))
                    jobs = [{'name': j['name'], 'status': j['status'], 'conclusion': j['conclusion'],
                             'url': j['url'], 'step': next((x['name'] for x in j['steps']
                                                         if x['status'] == 'in_progress'), '')}
                            for j in data['jobs']]
                    with LOCK:
                        STATE.update(jobs=jobs, run_url=runs[0]['url'])
                last_github = time.monotonic()
        except Exception as error:
            with LOCK:
                STATE['error'] = str(error)
        time.sleep(max(1, 10 - (time.monotonic() - started)))


def poll_refresh(client, prefix):
    """The current question repair, without reloading completed walk archives."""
    while True:
        try:
            progress = refresh_progress(client, prefix)
            progress.setdefault('time', progress.get('observed_at', time.time()))
            details = [progress['stage'].replace('_', ' ')]
            for field, label in (('sources', 'source files read'), ('histories', 'histories indexed'),
                                 ('rewritten_batches', 'batches with required draw masks'),
                                 ('balanced_batches', 'balanced batches prepared'),
                                 ('requests', 'shared calculations'), ('requested_draws', 'required draw calculations'),
                                 ('completed_batches', 'calculation batches saved')):
                if field in progress:
                    details.append(f'{progress[field]:,} {label}')
            summary = ' · '.join(details)
            if TEXT.get('refresh-preparation') != [summary]:
                ingest('refresh-preparation', summary,
                       datetime.fromtimestamp(progress['time'], UTC).isoformat(), 'Queue preparation')
            ingest('refresh-index', body(client, prefix + '/index.log').decode(errors='replace'),
                   source='AWS coordinator')
            logs = objects(client, '', progress['logs_prefix']) if progress.get('logs_prefix') else []
            # Load recent changed logs first; archive downloads must not delay live updates.
            changed = sorted((o for o in logs if ETAGS.get(o['Key']) != o['ETag']),
                             key=lambda o: o['LastModified'], reverse=True)[:128]

            def fetch_log(obj):
                tag = 'refresh-' + Path(obj['Key']).stem
                content = body(client, obj['Key']).decode(errors='replace')
                ingest(tag, content, obj['LastModified'].isoformat(), 'Question refresh worker')
                with LOCK:
                    TASKS[tag]['state'] = 'complete' if "'complete':" in content else 'active'
                    ETAGS[obj['Key']] = obj['ETag']

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                list(executor.map(fetch_log, changed))
        except Exception as error:
            with LOCK:
                STATE['error'] = str(error)
        time.sleep(10)


def main():
    global PREFIX, TRACK_RUN, EXTRA_PREFIX
    parser = argparse.ArgumentParser()
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument('--credentials')
    auth.add_argument('--profile')
    parser.add_argument('--extra-prefix', nargs='+')
    parser.add_argument('--refresh-prefix')
    parser.add_argument('--run', type=int, nargs='+')
    parser.add_argument('--prefix', default=PREFIX)
    parser.add_argument('--port', type=int, default=18766)
    args = parser.parse_args()
    PREFIX = args.prefix
    TRACK_RUN = args.run
    EXTRA_PREFIX = args.extra_prefix
    if args.profile:
        client = boto3.Session(profile_name=args.profile).client('s3', region_name='us-east-2')
    else:
        c = json.loads(Path(args.credentials).read_text())
        client = boto3.client('s3', region_name='us-east-2', aws_access_key_id=c['AccessKeyId'],
                              aws_secret_access_key=c['SecretAccessKey'], aws_session_token=c['Token'])
    threading.Thread(target=poll_refresh if args.refresh_prefix else poll,
                     args=(client, args.refresh_prefix) if args.refresh_prefix else (client,), daemon=True).start()
    if args.refresh_prefix:
        threading.Thread(target=poll_refresh_stats, args=(client, args.refresh_prefix), daemon=True).start()
        threading.Thread(target=poll_refresh_counts, args=(client, args.refresh_prefix), daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Live consolidated logs: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
