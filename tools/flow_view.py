"""Live view of one analysis run, step by step: investigation, walk, assembly, pooling, probability check, question
reading, Jev, financial reduction, analysis page.

The walk is read from its S3 queue (plans, claims, saved tasks, live and failed logs, assembled groups). A later step
reports itself by writing `<prefix>/flow/<step>.json` ({"state", "done", "total", "unit", "note"}); pooling also reads
`<prefix>/pool/progress.json`. Usage: python -m tools.flow_view --profile slope --prefix <queue prefix> [--run <id>]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import boto3

BUCKET = 'slope-walk-462947327980-20261001'
REPO = 'owassmer/Slope_Sparse_Events'
RUN_ID = 'akoustis_20240514-agent_plus_jev-20260929T052558Z'
LATER = [('pool', 'Pooling'), ('mass', 'Probability check'), ('review', 'Question reading'),
         ('jev', 'Clef judgments'), ('reduce', 'Financial reduction'), ('page', 'Analysis page')]
LOCK = threading.Lock()
VIEW: dict = {'updated': None, 'error': None, 'steps': [], 'groups': [], 'failed': [], 'events': [], 'jobs': {}}
EVENTS: deque = deque(maxlen=300)
PATHS = re.compile(r'^\s*(\d+)s part \d+: (?:paths (\d+)|done, (\d+) paths)', re.M)


class Queue:
    """The walk queue's state, refreshed by `poll`."""

    def __init__(self, s3, prefix, runs):
        self.s3, self.prefix, self.runs = s3, prefix, runs
        self.queue = prefix + '/refine-v1'
        self.plans: dict = {}
        self.logs: dict = {}  # task -> {'etag', 'paths', 'seconds', 'done'}
        self.saved: set = set()
        self.seen_failed: set = set()
        self.samples: deque = deque(maxlen=90)  # (time, paths walked in running and saved attempts)
        self.jobs: dict = {}
        self.jobs_at = 0.0

    def list(self, suffix):
        pages = self.s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=self.prefix + suffix)
        return [o for page in pages for o in page.get('Contents', [])]

    def get(self, key):
        try:
            return self.s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
        except self.s3.exceptions.NoSuchKey:
            return None

    def json(self, key):
        raw = self.get(key)
        return None if raw is None else json.loads(raw)

    def load_plans(self):
        if self.plans:
            return
        keys = [o['Key'] for o in self.list('/refine-v1/plans/')]
        with ThreadPoolExecutor(16) as ex:
            for plan in ex.map(self.json, keys):
                if plan and plan.get('roots'):
                    self.plans[plan['job']] = plan

    def read_log(self, obj):
        task = Path(obj['Key']).stem
        old = self.logs.get(task)
        if old and old['etag'] == obj['ETag']:
            return
        text = (self.get(obj['Key']) or b'').decode(errors='replace')
        found = PATHS.findall(text)
        seconds = int(found[-1][0]) if found else 0
        paths = max((int(a or b) for _s, a, b in found), default=0)
        self.logs[task] = {'etag': obj['ETag'], 'paths': paths, 'seconds': seconds, 'done': 'done,' in text}

    def github(self):
        if time.time() - self.jobs_at < 60 or not self.runs:
            return
        counts = {'running': 0, 'queued': 0, 'finished': 0, 'url': ''}
        for run in self.runs:
            out = subprocess.run(['gh', 'run', 'view', str(run), '--repo', REPO, '--json', 'jobs,url'],
                                 capture_output=True, text=True, timeout=30)
            if out.returncode:
                continue
            data = json.loads(out.stdout)
            counts['url'] = data['url']
            for job in data['jobs']:
                key = {'in_progress': 'running', 'queued': 'queued'}.get(job['status'], 'finished')
                counts[key] += 1
        self.jobs, self.jobs_at = counts, time.time()


DIAG_DESC = re.compile(r'^(\d+)m(.*)$')
DIAG_PART = re.compile(r'p(\d+):(\d+)p/(\d+)c')
DIAG_LINES = re.compile(r'^(DISAGREE.*|cache that alone.*|  (?:rows_|mask|div_ok|digest|walked a==b|chain ).*)$', re.M)


def gh_json(*args):
    out = subprocess.run(['gh', 'api', *args], capture_output=True, text=True, timeout=30)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None


class Diag:
    """The merge-decision check (workflow join-check): each job posts '<minutes>m p<partition>:<paths>p/<checks>c ...'
    as a commit status every 2 minutes, with REPORTED (a partition stopped at a merge decision and saved its report)
    or ENDED once its partitions finish. Jobs 9 and 10 walk partitions 0 and 4 exactly as production and report at
    the known listing merge; jobs 0-8 decide every merge also with the walk caches empty. Reports are read from the
    artifacts the watcher downloads into `results`."""

    def __init__(self, run, results):
        self.run, self.results = run, Path(results).expanduser()
        self.sha = self.url = None
        self.at, self.cached = 0.0, (None, [])

    def view(self, now):
        if not self.run or now - self.at < 60:
            return self.cached
        self.at = now
        if self.sha is None:
            info = gh_json(f'repos/{REPO}/actions/runs/{self.run}') or {}
            self.sha, self.url = info.get('head_sha'), info.get('html_url')
        jobs = {}
        for j in (gh_json(f'repos/{REPO}/actions/runs/{self.run}/jobs?per_page=100') or {}).get('jobs', []):
            m = re.search(r'\((\d+)\)', j['name'])
            if m:
                jobs[int(m.group(1))] = (j['status'], j.get('conclusion'))
        posted = {}
        for s in gh_json(f'repos/{REPO}/commits/{self.sha}/statuses?per_page=100') or [] if self.sha else []:
            ctx = s['context']
            if ctx.startswith('join-check/') and ctx not in posted:  # newest first
                posted[ctx] = s['description'] or ''
        rows = []
        for ctx, desc in posted.items():
            job = int(ctx.split('/')[1])
            m = DIAG_DESC.match(desc)
            minutes, rest = (int(m.group(1)), m.group(2)) if m else (0, desc)
            status, conclusion = jobs.get(job, ('unknown', None))
            flag = 'reported' if 'REPORTED' in rest else 'ended' if 'ENDED' in rest else (
                'walking' if status == 'in_progress' else conclusion or status)
            for p, paths, checks in DIAG_PART.findall(rest):
                rows.append({'job': job, 'partition': int(p), 'mode': 'production replica' if job >= 9 else 'every merge',
                             'paths': int(paths), 'checks': int(checks), 'minutes': minutes, 'state': flag})
        rows.sort(key=lambda r: (r['mode'] != 'production replica', r['partition']))
        found = []
        if self.results.is_dir():
            for f in sorted(self.results.rglob('log-*.txt')):
                found += [f'{f.stem.replace("log-", "partition ")}: {x}' for x in DIAG_LINES.findall(f.read_text())]
        walking = sum(r['state'] == 'walking' for r in rows)
        reported = sorted({r['partition'] for r in rows if r['state'] == 'reported'})
        checks = sum(r['checks'] for r in rows if r['mode'] == 'every merge')
        done_jobs = sum(s == 'completed' for s, _ in jobs.values())
        replica = [r for r in rows if r['mode'] == 'production replica']
        detail = [f'{len(jobs)} GitHub jobs: {len(jobs) - done_jobs} running, {done_jobs} ended; '
                  f'{walking} of {len(rows)} partitions walking',
                  *(f"Partition {r['partition']} (production replica): {r['paths']:,} paths, {r['minutes']} min, "
                    f"{r['state']}" for r in replica),
                  'Partition 0 reached the known bad merge about 55 minutes in last time; partition 4 is the control',
                  f'{checks:,} merge decisions checked against a cold computation: '
                  + (f'disagreement reported by partition {", ".join(map(str, reported))}' if reported
                     else 'all agree so far'),
                  *found[:40]]
        state = 'done' if found else 'failed' if jobs and done_jobs == len(jobs) else 'running' if jobs else 'waiting'
        note = ('Finds the cache that makes a merge decision depend on what the process walked before (the cause of '
                'the 34 unassembled groups). Results: ' + ('below' if found else 'not yet'))
        step_ = step('diag', 'Merge-decision check (group 4)', state, checks, None, 'merge checks', note, detail)
        step_['url'] = self.url
        self.cached = (step_, rows)
        return self.cached


def event(when, text, kind='info'):
    EVENTS.append({'time': when, 'text': text, 'kind': kind})


def step(key, name, state, done=None, total=None, unit='', note='', detail=None):
    return {'key': key, 'name': name, 'state': state, 'done': done, 'total': total, 'unit': unit, 'note': note,
            'detail': detail or []}


def walk_view(q: Queue, now: float):
    q.load_plans()
    with ThreadPoolExecutor(4) as ex:
        claims, done, live, failed, groups = ex.map(
            q.list, ['/refine-v1/claims/', '/refine-v1/done/', '/refine-v1/live/', '/refine-v1/failed/', '/done/'])
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(q.read_log, live))
    saved = {Path(o['Key']).stem: o for o in done}
    active = {Path(o['Key']).stem: o for o in claims
              if now - o['LastModified'].timestamp() < 300 and Path(o['Key']).stem not in saved}
    for ident in sorted(set(saved) - q.saved):
        if q.saved:  # the first poll lists what was already saved without announcing it
            event(saved[ident]['LastModified'].isoformat(), f'Task {ident} saved', 'good')
    q.saved = set(saved)
    attempts = []
    for o in failed:
        ident, _, attempt = Path(o['Key']).stem.partition('-')
        task_id = '-'.join(Path(o['Key']).stem.split('-')[:2])
        attempts.append({'task': task_id, 'time': o['LastModified'].isoformat(), 'key': o['Key']})
        if o['Key'] not in q.seen_failed:
            q.seen_failed.add(o['Key'])
            event(o['LastModified'].isoformat(), f'Task {task_id} failed; it returns to the queue', 'bad')
    assembled = {int(Path(o['Key']).stem) for o in groups if Path(o['Key']).stem.isdigit()}
    total = sum(p['partitions'] for p in q.plans.values())
    walked = sum(v['paths'] for v in q.logs.values())
    q.samples.append((now, walked))
    old = next((s for s in q.samples if now - s[0] <= 900), q.samples[0])
    rate = (walked - old[1]) / (now - old[0]) if now - old[0] > 60 else None
    left, got, want = 0.0, 0, 0.0
    for job, plan in q.plans.items():
        per = plan['estimated_histories'] / plan['partitions']
        for i in plan['indexes']:
            ident = f'{job}-{i}'
            if ident in saved and q.logs.get(ident, {}).get('paths'):
                got, want = got + q.logs[ident]['paths'], want + per
    scale = got / want if want else 1.0  # finished tasks' actual paths over their 2 October estimate
    for job, plan in q.plans.items():
        per = plan['estimated_histories'] / plan['partitions'] * scale
        for i in plan['indexes']:
            ident = f'{job}-{i}'
            if ident not in saved:
                left += max(0.0, per - q.logs.get(ident, {}).get('paths', 0))
    eta = (datetime.fromtimestamp(now + left / rate, UTC).strftime('%H:%M UTC on %a %d %b')
           if rate and rate > 0 else None)
    on = {'github': 0, 'aws': 0}
    with ThreadPoolExecutor(16) as ex:
        for c in ex.map(lambda k: q.json(k), [o['Key'] for o in active.values()]):
            if c:
                on['aws' if c.get('host', '').startswith('aws') else 'github'] += 1
    q.github()
    jobs = q.jobs
    detail = [f"{len(active)} tasks walking now: {on['github']} on GitHub, {on['aws']} on AWS",
              f"GitHub jobs: {jobs.get('running', 0)} running, {jobs.get('queued', 0)} queued" if jobs else '',
              f'{walked:,} paths walked in the current and saved attempts',
              f'{rate:,.0f} paths a second across the fleet (last 15 minutes)' if rate else 'Rate: measuring',
              (f'Estimated finish: {eta} (2 October path counts scaled by {scale:.2f}, from {len(saved)} finished tasks)'
               if eta else ''),
              f'{len(attempts)} failed attempts (listed below)' if attempts else 'No failed attempts']
    walk = step('walk', 'Walk the corrected tree', 'done' if total and len(saved) >= total else 'running',
                len(saved), total, 'tasks saved', f'{len(q.plans)} groups, {total:,} tasks', [d for d in detail if d])
    walk.update(paths=walked, paths_est=round(scale * sum(p['estimated_histories'] for p in q.plans.values())),
                rate=round(rate, 1) if rate else None)
    grid = []
    for job in range(100):
        plan = q.plans.get(job)
        if not plan:
            continue
        ids = [f'{job}-{i}' for i in plan['indexes']]
        grid.append({'job': job, 'total': len(ids), 'saved': sum(i in saved for i in ids),
                     'active': sum(i in active for i in ids), 'failed': sum(a['task'] in ids for a in attempts),
                     'assembled': job in assembled, 'depth': plan['depth'], 'paths': plan['estimated_histories']})
    walk_done = bool(total) and len(saved) >= total
    blocked = walk_done and len(assembled) < 100
    assembly = step('assembly', 'Assemble the 100 groups',
                    'done' if len(assembled) >= 100 else 'held' if blocked else 'running' if assembled else 'waiting',
                    len(assembled), 100, 'groups assembled',
                    (f'{100 - len(assembled)} groups fail the coverage check: their tasks disagree on the layout of '
                     'the branch they share, because a merge decision depends on what each process walked before. '
                     'The assembled groups are not trusted until the cause is known (merge-decision check below).'
                     if blocked else 'A group assembles as soon as every one of its tasks is saved'))
    return walk, assembly, grid, attempts, jobs.get('url', '')


def later_steps(q: Queue):
    out = []
    for key, name in LATER:
        info = q.json(f'{q.prefix}/flow/{key}.json')
        if info is None and key == 'pool':
            progress = q.json(f'{q.prefix}/pool/progress.json')
            if progress:
                info = {'state': 'done' if progress.get('ready') else 'running',
                        'note': progress.get('stage', '').replace('_', ' '),
                        'done': progress.get('partitions'), 'total': progress.get('total_partitions'),
                        'unit': 'pieces'}
        info = info or {}
        out.append(step(key, name, info.get('state', 'waiting'), info.get('done'), info.get('total'),
                        info.get('unit', ''), info.get('note', ''), info.get('detail')))
    return out


def poll(q: Queue, diag: Diag):
    """The walk (S3) and the merge check (GitHub) refresh independently: an expired AWS login keeps the last walk
    view on the page with the error, and the merge check still updates."""
    walk_part: dict = {}
    later: list = []
    while True:
        now = time.time()
        errors = []
        try:
            walk, assembly, grid, attempts, run_url = walk_view(q, now)
            later = later_steps(q)
            walk_part = {'walk': walk, 'assembly': assembly, 'groups': grid, 'run_url': run_url,
                         'failed': sorted(attempts, key=lambda a: a['time'], reverse=True)[:40]}
        except Exception as error:  # shown on the page; the next poll retries
            errors.append(f'walk (S3): {type(error).__name__}: {error}')
        try:
            diag_step, diag_rows = diag.view(now)
        except Exception as error:
            diag_step, diag_rows = None, []
            errors.append(f'merge check (GitHub): {type(error).__name__}: {error}')
        investigation = step('investigation', 'Investigation record', 'done', note=f'Recorded run {RUN_ID}')
        steps = [investigation, *(walk_part[k] for k in ('walk', 'assembly') if k in walk_part),
                 *([diag_step] if diag_step else []), *later]
        with LOCK:
            VIEW.update(steps=steps, groups=walk_part.get('groups', []), failed=walk_part.get('failed', []),
                        events=sorted(EVENTS, key=lambda e: e['time'], reverse=True)[:60],
                        run_url=walk_part.get('run_url', ''), diag=diag_rows,
                        updated=datetime.now(UTC).isoformat(), error='; '.join(errors) or None)
        time.sleep(max(5, 20 - (time.time() - now)))


PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Slope · analysis run</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f6f7f9;color:#1d2433;font:15px/1.45 system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 24px 60px}h1{font-size:22px;margin:0}h2{font-size:16px;margin:32px 0 10px}
.sub{color:#5d6878;font-size:13px}a{color:#1a5fb4}.head{display:flex;justify-content:space-between;align-items:end;gap:16px}
.steps{background:#fff;border:1px solid #dde1e7;border-radius:10px;margin-top:20px}
.step{display:grid;grid-template-columns:34px minmax(0,1fr) minmax(0,330px);gap:14px;padding:14px 18px;border-top:1px solid #edf0f3;align-items:start}
.step:first-child{border-top:0}.mark{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-size:13px;font-weight:700;color:#fff}
.done .mark{background:#2e8b57}.running .mark{background:#1a5fb4}.waiting .mark{background:#b8bfc9}.held .mark{background:#c88a12}.failed .mark{background:#c0392b}
.name{font-weight:600}.state{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#5d6878;margin-left:8px}
.note{color:#5d6878;font-size:13px}ul{margin:6px 0 0;padding-left:18px;font-size:13px}li{margin:2px 0}
.bar{height:10px;background:#e7eaee;border-radius:5px;overflow:hidden;margin-top:6px}.bar i{display:block;height:100%;background:#1a5fb4}
.done .bar i{background:#2e8b57}.count{font-size:13px;margin-top:4px;text-align:right;color:#3b4555}.live{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}.bar.p i{background:#7fb3e6}
.grid{display:grid;grid-template-columns:repeat(20,minmax(0,1fr));gap:4px}.cell{height:34px;border-radius:4px;background:#e7eaee;position:relative;overflow:hidden;border:2px solid transparent}
.cell i{position:absolute;left:0;bottom:0;width:100%;background:#7fb3e6}.cell.asm i{background:#2e8b57}.cell.act{border-color:#1a5fb4}
.cell b{position:absolute;top:2px;left:4px;font-size:10px;font-weight:600;color:#1d2433}.cell.bad:after{content:"";position:absolute;right:3px;top:3px;width:7px;height:7px;border-radius:50%;background:#c0392b}
.legend{font-size:12px;color:#5d6878;margin-top:8px}.cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}
.panel{background:#fff;border:1px solid #dde1e7;border-radius:10px;padding:10px 14px;max-height:340px;overflow:auto;font-size:13px}
.row{display:grid;grid-template-columns:92px minmax(0,1fr);gap:8px;padding:4px 0;border-top:1px solid #f0f2f5}.row:first-child{border-top:0}
.tbl{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}.tbl th{text-align:left;font-weight:600;color:#5d6878;font-size:12px;padding:4px 6px}.tbl td{padding:4px 6px;border-top:1px solid #f0f2f5}.st-reported{color:#c0392b;font-weight:600}.st-walking{color:#1a5fb4}.st-ended{color:#2e8b57}
.good{color:#2e8b57}.bad{color:#c0392b}.err{color:#c0392b;margin-top:10px}
</style></head><body><main>
<div class="head"><div><h1>Akoustis Technologies · review date 14 May 2024</h1>
<div class="sub">Slope line forecast through 10 Nov 2024 · from investigation to the analysis page</div></div>
<div style="text-align:right"><div id="live" class="live">—</div><div class="sub">paths walked (live)</div>
<div class="sub"><a id="run" target="_blank" href="#">GitHub run ↗</a> · <span id="upd">connecting…</span></div></div></div>
<div id="err" class="err"></div><div id="steps" class="steps"></div>
<div id="diagwrap" style="display:none"><h2>Merge-decision check by partition · <a id="diagrun" target="_blank" href="#">run ↗</a></h2>
<div class="panel" style="max-height:none"><table class="tbl"><thead><tr><th>Partition</th><th>Mode</th><th>Paths walked</th><th>Merge checks passed</th><th>Minutes</th><th>State</th></tr></thead><tbody id="diag"></tbody></table></div>
<div class="legend">Production replica: walks exactly as the production task did and decides the known bad merge (listing: compliant vs hearing) cold, testing each cache. Every merge: each merge decision is also computed with the walk caches empty; the first disagreement stops the job and saves its report. Updated from GitHub every 2 minutes.</div></div>
<h2>Walk by group</h2><div id="grid" class="grid"></div>
<div class="legend">Each square is one of the 100 groups, heaviest first. Fill: share of its tasks saved (green once the group is assembled). Blue outline: tasks walking now. Red dot: a failed attempt. Hover for numbers.</div>
<div class="cols"><div><h2>Recent events</h2><div id="events" class="panel"></div></div>
<div><h2>Failed attempts</h2><div id="failed" class="panel"></div></div></div>
</main><script>
const el=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const label={done:'done',running:'in progress',waiting:'waiting',held:'held',failed:'failed'};
const hm=t=>new Date(t).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
function render(v){el('upd').textContent=v.updated?'updated '+new Date(v.updated).toLocaleTimeString():'';
el('err').textContent=v.error?'Update error: '+v.error:'';if(v.run_url)el('run').href=v.run_url;
el('steps').innerHTML=v.steps.map((s,n)=>{const pct=s.total?Math.min(100,100*s.done/s.total):0;
const count=s.total?`${(s.done||0).toLocaleString()} / ${s.total.toLocaleString()} ${esc(s.unit)}`:'';
const pp=s.paths_est?Math.min(100,100*(s.paths||0)/s.paths_est):0;
const pbar=s.paths!=null?`<div class="bar p" style="margin-top:12px"><i style="width:${pp}%"></i></div><div class="count">${(s.paths||0).toLocaleString()} / ~${(s.paths_est||0).toLocaleString()} paths${s.rate?' · '+Math.round(s.rate).toLocaleString()+' a second':''}</div>`:'';
const det=(s.state==='running'||s.state==='failed'||s.state==='held')&&s.detail.length?'<ul>'+s.detail.map(d=>'<li>'+esc(d)+'</li>').join('')+'</ul>':'';
return `<div class="step ${s.state}"><div class="mark">${s.state==='done'?'✓':n+1}</div><div><span class="name">${esc(s.name)}</span><span class="state">${label[s.state]||esc(s.state)}</span><div class="note">${esc(s.note)}</div>${det}</div><div>${s.total?`<div class="bar"><i style="width:${pct}%"></i></div><div class="count">${count}</div>`:''}${pbar}</div></div>`}).join('');
const w=v.steps.find(x=>x.key==='walk');if(w&&w.paths!=null)el('live').textContent=w.paths.toLocaleString();
el('grid').innerHTML=v.groups.map(g=>`<div class="cell ${g.assembled?'asm':''} ${g.active?'act':''} ${g.failed?'bad':''}" title="Group ${g.job}: ${g.saved}/${g.total} tasks saved, ${g.active} walking, ${g.failed} failed attempts, depth ${g.depth}, ~${g.paths.toLocaleString()} paths (2 Oct estimate)${g.assembled?', assembled':''}"><i style="height:${100*g.saved/g.total}%"></i><b>${g.job}</b></div>`).join('');
const dg=v.diag||[];el('diagwrap').style.display=dg.length?'':'none';const ds=v.steps.find(x=>x.key==='diag');if(ds&&ds.url)el('diagrun').href=ds.url;
el('diag').innerHTML=dg.map(r=>`<tr><td>${r.partition}</td><td>${esc(r.mode)}</td><td>${r.paths.toLocaleString()}</td><td>${r.mode==='every merge'?r.checks.toLocaleString():'—'}</td><td>${r.minutes}</td><td class="st-${esc(r.state)}">${esc(r.state)}</td></tr>`).join('');
el('events').innerHTML=v.events.map(e=>`<div class="row"><span class="sub">${hm(e.time)}</span><span class="${e.kind}">${esc(e.text)}</span></div>`).join('')||'<div class="sub">Nothing yet.</div>';
el('failed').innerHTML=v.failed.map(f=>`<div class="row"><span class="sub">${hm(f.time)}</span><span>Task ${esc(f.task)} · <a target="_blank" href="/log?key=${encodeURIComponent(f.key)}">log</a></span></div>`).join('')||'<div class="sub">None.</div>'}
async function tick(){try{const r=await fetch('/api/view');render(await r.json())}catch(e){el('err').textContent='Viewer disconnected: '+e.message}}
tick();setInterval(tick,15000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    queue: Queue

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/api/view':
            with LOCK:
                body, kind = json.dumps(VIEW).encode(), 'application/json'
        elif url.path == '/log':
            key = parse_qs(url.query).get('key', [''])[0]
            if not key.startswith(self.queue.prefix + '/refine-v1/'):
                self.send_error(404)
                return
            body, kind = self.queue.get(key) or b'(not found)', 'text/plain; charset=utf-8'
        elif url.path == '/':
            body, kind = PAGE.encode(), 'text/html; charset=utf-8'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--run', type=int, nargs='*', default=[])
    parser.add_argument('--port', type=int, default=18766)
    parser.add_argument('--diag-run', type=int, default=None, help='join-check workflow run id')
    parser.add_argument('--diag-results', default='~/.hermes/profiles/connor/cache/scratch/split/joins')
    args = parser.parse_args()
    s3 = boto3.Session(profile_name=args.profile).client('s3', region_name='us-east-2')
    queue = Queue(s3, args.prefix, args.run)
    Handler.queue = queue
    threading.Thread(target=poll, args=(queue, Diag(args.diag_run, args.diag_results)), daemon=True).start()
    print(f'Analysis run view: http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
