"""Assemble immutable recovery inputs before refreshing saved question histories."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from tools.pool_fleet import fetch, put


def inspect_archive(path, source, plan):
    """Bind every saved unit to its exact replacement events; never adopt an error file."""
    units = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            match = re.fullmatch(r'unit-(\d+)\.pkl\.gz', Path(member.filename).name)
            if not match:
                continue
            number = int(match[1])
            if number in units or not 0 <= number < len(plan['units']):
                raise ValueError(f'Duplicate or unexpected unit {number}')
            raw = archive.read(member)
            value = pickle.loads(gzip.decompress(raw))
            expected = plan['units'][number]
            if value['unit'] != number or value['old_events'] != expected['old_events']:
                raise ValueError(f'Unit {number} does not match replacement ownership')
            outputs = value['outputs']
            if len(outputs) != len(expected['targets']):
                raise ValueError(f'Unit {number} has missing continuation outputs')
            for output, (prefix, _method, _phase) in zip(outputs, expected['targets'], strict=True):
                if output['parent'].steps != prefix or len(output['paths']) != len(output['equivalence']):
                    raise ValueError(f'Unit {number} has mismatched parent or financial records')
                if not output['paths'] or output['max_probability_error'] > 1e-10:
                    raise ValueError(f'Unit {number} did not pass its recorded mass gate')
            units[number] = {'source': source, 'member': member.filename,
                             'sha256': hashlib.sha256(raw).hexdigest(),
                             'paths': sum(len(o['paths']) for o in outputs)}
    return units


def select(catalogs, count):
    """Latest successful unit wins; each source revision supplies a unit at most once."""
    selected, seen = {}, set()
    for catalog in sorted(catalogs, key=lambda c: c['generation']):
        for text, value in catalog['units'].items():
            number = int(text)
            pair = catalog['generation'], number
            if pair in seen:
                raise ValueError(f'Duplicate unit {number} in generation {pair[0]}')
            seen.add(pair)
            if not 0 <= number < count:
                raise ValueError(f'Unexpected unit {number}')
            selected[number] = value
    if set(selected) != set(range(count)):
        raise ValueError(f'Missing {count - len(selected)} recovery units')
    return selected


def collect(worker):
    root = Path('var/notes-assembly')
    root.mkdir(parents=True, exist_ok=True)
    fetch(os.environ['NOTES_ASSEMBLY_MANIFEST_URL'], root / 'manifest.json')
    manifest = json.loads((root / 'manifest.json').read_text())
    fetch(manifest['plan'], root / 'plan.pkl')
    raw = (root / 'plan.pkl').read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['plan_sha256']:
        raise ValueError('Replacement plan checksum mismatch')
    plan = pickle.loads(raw)
    for source in manifest['tasks'][worker]:
        path = root / 'source.zip'
        with path.open('wb') as stream:
            subprocess.run(['gh', 'api', f"repos/owassmer/Slope_Sparse_Events/actions/artifacts/{source['id']}/zip"],
                           stdout=stream, check=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if source.get('digest') and source['digest'] != 'sha256:' + digest:
            raise ValueError('GitHub artifact digest mismatch')
        units = inspect_archive(path, source['key'], plan)
        put(source['output'], path.read_bytes())
        report = {'generation': source['generation'], 'run': source['run'], 'artifact': source['id'],
                  'archive': source['key'], 'sha256': digest, 'units': units,
                  'plan_sha256': manifest['plan_sha256'], 'production_ready': False}
        put(source['catalog_output'], json.dumps(report).encode())
        (root / f"catalog-{source['id']}.json").write_text(json.dumps(report))
        print({'run': source['run'], 'artifact': source['id'], 'units': len(units),
               'paths': sum(u['paths'] for u in units.values()), 'saved': True}, flush=True)
        path.unlink()


if __name__ == '__main__':
    collect(int(sys.argv[1]))
