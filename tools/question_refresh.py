"""Saved-history question refresh. The native replay is a bounded reference, not a fleet worker."""
from __future__ import annotations

import functools
import inspect
import sqlite3
import time
from array import array
from collections import OrderedDict
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

from app.disputes.forecast import _Walk, atoms, pack_row


def classify_row(fc, dispute, key, steps, row, keep):
    """Facts, classes and available answers read the same before-answer record."""
    import numpy as np

    from app.disputes.forecast import _S, as_of, group_classes

    node = fc.nodes[key]
    row = as_of(row)
    if node.node in ('petition_on_notes', 'holders_involuntary'):
        from app.disputes.notes import occasion, record_row
        origin = occasion(node.context)
        index = next(i for i, step in enumerate(steps) if step[:2] == origin)
        old = fc._keep_late
        fc._keep_late = lambda k, _prefix, r: keep(k, r)
        try:
            return record_row(fc, dispute, steps, key, index, row)
        finally:
            fc._keep_late = old
    live = fc.live(node, row)
    cls = fc.question_class(node, row, live)
    if cls is None:
        raise ValueError(f'Missing conditioning snapshot for {key}')
    conds = fc.spec[node.node].get('situation', ())
    if conds and all(c in row['marks'] for c in conds):
        verdict = next((s[2] for s in steps if s[0] == 'verdict'), '')
        ruling = next((s[2] for s in steps if s[0] == 'post_trial_ruling'), '')
        label = 'award' + verdict.split(':')[1] if verdict.startswith('award:') else verdict or 'claimed'
        after = ('reduced' + ruling.split(':')[1] if ruling.startswith('reduced:') else
                 'set_aside' if ruling == 'set_aside' else label)
        ruled = row['marks']['ruled'] <= row['day']
        walk = _Walk(fc, dispute)
        for selected, value in ((live & ~ruled, label), (live & ruled, after)):
            if selected.any():
                part = np.where(selected, cls, '')
                tagged = walk.context_class(part, row, _S(cls=value), node.node, (node.context.split('|')[0],))
                cls[selected] = tagged[selected]
    if node.node in ('judgment_response', 'financing_at_floor', 'petition_cash_out'):
        if row.get('groups') is None:
            raise ValueError(f'Missing before-answer option groups for {key}')
        fc.grouped.add(key)
        cls = group_classes(cls, row['groups'])
    return fc._split((key,), row, keep, cls)


def question_source(node, positions):
    """The same decision boundaries used by _Walk.rec/court/record_late.

    Return the originating saved step, and a neutral prefix probe when the
    question is immediate. None means its completed chronological record.
    """
    name, context = node.node, node.context.split('|')[0]
    if name in ('petition_on_notes', 'holders_involuntary'):
        from app.disputes.notes import occasion
        origin = occasion(node.context)
        return positions[origin], 'notes'
    aliases = {
        'settlement_offer': ('settle', context, 'no'),
        'settlement_accept': ('settle', context, 'no'),
        'judgment_response': ('judgment_response', context, 'none'),
        'stay_motion': ('stay', context, 'no'),
        'stay_approved': ('stay', context, 'no'),
        'registration_early': ('registration_early' if context == 'I1' else 'enforce',
                               context, 'no'),
        'execute_pre_ruling': ('execute_pre_ruling', 'I1', 'no'),
        'enforce_after_final': ('enforce', 'post', 'none'),
        'financing_at_floor': ('cash_floor', context.removeprefix('floor'), 'neither'),
        'petition_cash_out': ('cash_out', '', 'neither'),
        'offering_closes': ('offering', context, 'no'),
        'holders_act_judgment': ('judgment_default', context, 'no'),
        'holders_act_delisting': ('delisting_notes', context, 'none'),
        'bid_compliance': ('listing', '', 'compliant'),
        'hearing_request': ('listing', '', 'compliant'),
        'appeal': ('appeal', '', 'no'),
        'post_trial_ruling': ('post_trial_ruling', '', 'unchanged'),
        'remittitur_elected': ('post_trial_ruling', '', 'unchanged'),
    }
    if name not in aliases:
        raise ValueError(f'No saved question boundary for {name!r}')
    probe = aliases[name]
    origin = positions[probe[:2]]
    if name in ('stay_approved', 'financing_at_floor', 'petition_cash_out', 'offering_closes',
                'holders_act_judgment') or name == 'judgment_response' and context in ('post', 'ripe'):
        return origin, None
    if name == 'registration_early':
        probe = ('court_order', f'registration_{context}', '')
    elif name in ('bid_compliance', 'hearing_request'):
        probe = ('listing_date', 'compliance' if name == 'bid_compliance' else 'hearing_request', '')
    return origin, probe


class RefreshPlan:
    """One global registry per dispute, populated before assigning calculations.

    Consumers retain their original part/row identities. They refer to a shared
    binding, which refers to one calculation. Neither rows nor financial arrays
    are produced while scanning saved histories.
    """

    def __init__(self, filename, instance_id):
        self.instance_id = instance_id
        self.histories = HistoryIndex()
        self.calculations = Calculations(filename)
        self.db = self.calculations.db
        self.db.executescript('''
            CREATE TABLE bindings (
                id INTEGER PRIMARY KEY, request INTEGER NOT NULL, question TEXT NOT NULL,
                target INTEGER NOT NULL, prefix INTEGER NOT NULL,
                UNIQUE(request,question,target,prefix));
            CREATE TABLE consumers (
                part TEXT NOT NULL, row INTEGER NOT NULL, bindings BLOB NOT NULL,
                PRIMARY KEY(part,row)) WITHOUT ROWID;
            CREATE INDEX binding_request ON bindings(request,id);
        ''')
        self._binding_ids = OrderedDict()

    def add(self, fc, path, part, row):
        from app.analysis.events import plain

        if path.instance_id != self.instance_id:
            raise ValueError('A plan belongs to exactly one dispute and forecaster input set')
        if not any(d.instance_id == self.instance_id for d in fc.disputes):
            raise ValueError('This registry refreshes saved dispute paths, not the separately built ordinary view')
        prefixes, positions = [0], {}
        for i, (name, context, answer) in enumerate(path.steps):
            if (name, context) in positions:
                raise ValueError(f'Repeated decision occasion: {(name, context)}')
            positions[name, context] = i
            # Group labels select consumers' draws; the engine itself reads plain(answer).
            prefixes.append(self.histories.child(prefixes[-1], (name, context, plain(answer))))
        refs = array('Q')
        for key, _, _ in path.classes:
            node = fc.nodes[key]
            target, probe = question_source(node, positions)
            before = prefixes[target]
            if probe == 'notes':
                actor = 'holders' if node.node == 'holders_involuntary' else 'issuer'
                request = self.calculations.add_notes(self.histories, prefixes[-1], target, actor)
                select = -1
            elif probe is None:
                request = self.calculations.add('complete', prefixes[-1])
                select = target
            else:
                request = self.calculations.add('prefix', self.histories.child(before, probe))
                select = -1
            args = request, key, select, before
            binding = self._binding_ids.get(args)
            if binding is None:
                self.db.execute('INSERT OR IGNORE INTO bindings(request,question,target,prefix) VALUES(?,?,?,?)', args)
                binding = self.db.execute('SELECT id FROM bindings WHERE request=? AND question=? AND target=? '
                                          'AND prefix=?', args).fetchone()[0]
                self._binding_ids[args] = binding
                if len(self._binding_ids) > 65536:
                    self._binding_ids.popitem(last=False)
            else:
                self._binding_ids.move_to_end(args)
            refs.append(binding)
        self.db.execute('INSERT INTO consumers(part,row,bindings) VALUES(?,?,?)', (part, row, refs.tobytes()))

    def consumers(self):
        for part, row, blob in self.db.execute('SELECT part,row,bindings FROM consumers ORDER BY part,row'):
            refs = array('Q')
            refs.frombytes(blob)
            yield part, row, refs

    def evaluate(self, fc, dispute):
        """Yield each globally owned result once; all saved consumers keep references."""
        for request, result in self.calculations.evaluate(fc, dispute, self.histories):
            for ident, question, target, prefix in self.db.execute(
                    'SELECT id,question,target,prefix FROM bindings WHERE request=? ORDER BY id', (request,)):
                if target >= 0 and target not in result:
                    raise ValueError(f'Missing completed question record: {question} at {target}')
                yield ident, question, prefix, result[target] if target >= 0 else result

    def write_batches(self, folder, size=512):
        """Stream contiguous shared-prefix work; a request is assigned exactly once."""
        import gzip
        import pickle
        from pathlib import Path

        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        batch, count, handle = -1, 0, None
        try:
            for request in self.calculations.requests(self.histories):
                if count % size == 0:
                    if handle is not None:
                        handle.close()
                    batch += 1
                    handle = gzip.open(folder / f'{batch}.pkl.gz', 'wb', compresslevel=1)
                    pickle.dump(self.instance_id, handle, protocol=5)
                ident, mode, history, target, actor = request
                bindings = list(self.db.execute('SELECT id,question,target,prefix FROM bindings '
                                                'WHERE request=? ORDER BY id', (ident,)))
                pickle.dump((ident, mode, self.histories.materialize(history), target, actor, bindings),
                            handle, protocol=5)
                count += 1
        finally:
            if handle is not None:
                handle.close()
        return {'batches': batch + 1, 'calculations': count}

    def close(self):
        self.calculations.close()


class HistoryIndex:
    """Intern (parent, step) links in one pass; do not copy every growing prefix."""

    def __init__(self):
        self.parents = array('q', [-1])
        self.labels = array('I', [0])
        self.steps = [None]
        self.step_ids = {}
        self.children = {}
        self.first_child = array('q', [-1])
        self.next_sibling = array('q', [-1])

    def child(self, parent, step):
        sid = self.step_ids.get(step)
        if sid is None:
            sid = len(self.steps)
            self.steps.append(step)
            self.step_ids[step] = sid
        key = (parent, sid)
        found = self.children.get(key)
        if found is None:
            found = len(self.parents)
            self.parents.append(parent)
            self.labels.append(sid)
            self.children[key] = found
            self.first_child.append(-1)
            self.next_sibling.append(self.first_child[parent])
            self.first_child[parent] = found
        return found

    def add(self, steps):
        at = 0
        for step in steps:
            at = self.child(at, step)
        return at

    def find(self, steps):
        at = 0
        for step in steps:
            sid = self.step_ids.get(step)
            if sid is None:
                return None
            at = self.children.get((at, sid))
            if at is None:
                return None
        return at

    def materialize(self, at):
        steps = []
        while at:
            steps.append(self.steps[self.labels[at]])
            at = self.parents[at]
        return tuple(reversed(steps))

    def depth_first(self):
        """Visit each interned link once, keeping shared prefixes adjacent."""
        at = 0
        while True:
            yield at
            if self.first_child[at] >= 0:
                at = self.first_child[at]
                continue
            while at and self.next_sibling[at] < 0:
                at = self.parents[at]
            if not at:
                return
            at = self.next_sibling[at]

    def replace_step(self, history, index, step):
        """Re-intern only the changed suffix, without constructing prefix tuples."""
        suffix = []
        at = history
        while at:
            suffix.append(at)
            at = self.parents[at]
        if not 0 <= index < len(suffix):
            raise IndexError(index)
        old = suffix[-index - 1]
        at = self.child(self.parents[old], step)
        for child in reversed(suffix[:len(suffix) - index - 1]):
            at = self.child(at, self.steps[self.labels[child]])
        return at


class Calculations:
    """Deduplicate immutable calculation requests before calculating or serializing rows.

    The caller supplies a history ID from one shared HistoryIndex. Prefix and
    completed-history requests occupy different namespaces; no prefix-only key
    can satisfy a completed-history reconstruction. Population masks belong to
    consumers of these all-draw calculations, not to their identity.
    """

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        # IDs belong to the accompanying in-memory history index. Never silently
        # reopen a prior index with unrelated IDs.
        self.db.execute('''CREATE TABLE calculations (
            id INTEGER PRIMARY KEY, mode TEXT NOT NULL, history INTEGER NOT NULL,
            target INTEGER NOT NULL, actor TEXT NOT NULL,
            UNIQUE(mode, history, target, actor))''')
        self.db.execute('CREATE INDEX by_history ON calculations(history,id)')
        self._ids = OrderedDict()

    def add(self, mode, history, target=-1, actor=''):
        if mode not in ('prefix', 'complete', 'notes'):
            raise ValueError(mode)
        if mode != 'notes':
            target, actor = -1, ''
        elif target < 0 or not actor:
            raise ValueError('Notes requests require their decision index and actor')
        key = mode, history, target, actor
        if key in self._ids:
            self._ids.move_to_end(key)
            return self._ids[key]
        self.db.execute('INSERT OR IGNORE INTO calculations(mode,history,target,actor) VALUES(?,?,?,?)', key)
        ident = self.db.execute('SELECT id FROM calculations WHERE mode=? AND history=? AND target=? AND actor=?',
                               key).fetchone()[0]
        self._ids[key] = ident
        if len(self._ids) > 65536:
            self._ids.popitem(last=False)
        return ident

    def add_notes(self, histories, history, target, actor):
        """Intern the existing notes counterfactual before doing any financial work.

        Different answers at this trigger share one calculation when their entire
        remaining history agrees. Earlier-calendar events later in the traversal
        remain in the suffix and therefore in the identity.
        """
        steps = histories.materialize(history)
        node, context, _ = steps[target]
        if node not in ('judgment_default', 'delisting_notes', 'nonpayment'):
            raise ValueError(f'Not a notes trigger: {steps[target]!r}')
        quiet = 'due' if node == 'nonpayment' else 'accelerated'
        canonical = histories.replace_step(history, target, (node, context, quiet))
        return self.add('notes', canonical, target, actor)

    def requests(self, histories):
        self.db.commit()
        # Insertion IDs do not imply prefix locality: a later source part may
        # revisit an old parent. Walk links rather than sorting materialized tuples.
        for history in histories.depth_first():
            yield from self.db.execute('SELECT id,mode,history,target,actor FROM calculations '
                                       'WHERE history=? ORDER BY id', (history,))

    def evaluate(self, fc, dispute, histories):
        """Direct engine reads, retaining its prefix cache between neighboring requests.

        Completed reconstructions serve every captured question on that same
        history. They are not recalculated separately for each descendant's
        reference to the result. Notes retain their existing chronological
        counterfactual. This produces facts only, never adopts classifications.
        """
        from app.analysis.events import event_trace
        from app.disputes.forecast import DisputePath, _Prefix
        from app.disputes.notes import decision_row

        if fc.draws.prefixes is None:
            fc.draws.prefixes = {}
        for ident, mode, history, target, actor in self.requests(histories):
            steps = histories.materialize(history)
            if mode == 'notes':
                row, _ = decision_row(fc, dispute, steps, target, actor)
                yield ident, row
                continue
            trace = event_trace(dispute, DisputePath(instance_id=dispute.instance_id, steps=steps,
                                                    outcome='', edges=()),
                                fc.setup, fc.m, fc.draws, fc.sens, day_only=mode == 'prefix')
            yield ident, (fc.row_of(_Prefix.of(trace, digest=False)) if mode == 'prefix' else trace.questions)

    def close(self):
        self.db.commit()
        self.db.close()


def rebind_population(path, classes, valid, n):
    """Apply validated live classes and support without changing saved events/edges.

    `classes` contains full-draw arrays, retaining the original value on nonlive
    draws. Eligibility and sibling probability conservation are caller gates.
    Every other class's codes must narrow with the path's population as well.
    """
    import numpy as np

    from app.disputes.forecast import class_entry, pack_mask, path_mask

    original = path_mask(path, n)
    original = np.ones(n, dtype=bool) if original is None else original
    valid = np.asarray(valid, dtype=bool)
    if valid.shape != original.shape or np.any(valid & ~original):
        raise ValueError('A refreshed population may only narrow its saved support')
    unknown = classes.keys() - {k for k, _, _ in path.classes}
    if unknown:
        raise ValueError(f'Cannot silently add unsaved decision references: {unknown}')
    out = []
    for key, tags, codes in path.classes:
        if key in classes:
            out.append(class_entry(key, classes[key], valid))
        else:
            out.append((key, tags, None if codes is None else
                        np.frombuffer(codes, np.int8)[valid[original]].tobytes()))
    return replace(path, mask=pack_mask(valid), classes=tuple(out))


def decode_classes(entry, mask, n):
    """Expand saved codes while preserving -1 as nonlive, never as the last tag."""
    import numpy as np

    _, tags, codes = entry
    mask = np.ones(n, dtype=bool) if mask is None else mask
    out = np.full(n, '', dtype=object)
    if codes is None:
        if len(tags) != 1:
            raise ValueError('Uniform class encoding must have exactly one tag')
        out[mask] = tags[0]
    else:
        values = np.frombuffer(codes, np.int8)
        if len(values) != np.count_nonzero(mask) or np.any(values < -1) or np.any(values >= len(tags)):
            raise ValueError('Invalid saved class encoding')
        live = values >= 0
        out[np.flatnonzero(mask)[live]] = np.asarray(tags, dtype=object)[values[live]]
    return out


def run_batch(fc, source, output):
    """A long-lived worker can consume successive batches with the same engine caches."""
    import gzip
    import os
    import pickle
    from pathlib import Path

    from app.analysis.events import event_questions
    from app.disputes.forecast import _ROW_BLOBS, DisputePath, class_entry
    from app.disputes.notes import decision_row

    output = Path(output)
    partial = output.with_suffix(output.suffix + '.partial')
    count = 0
    if fc.draws.prefixes is None:
        fc.draws.prefixes = {}
    with gzip.open(source, 'rb') as src, gzip.open(partial, 'wb', compresslevel=1) as dst:
        instance = pickle.load(src)
        dispute = next(d for d in fc.disputes if d.instance_id == instance)
        pickle.dump(instance, dst, protocol=5)
        while True:
            try:
                request = pickle.load(src)
            except EOFError:
                break
            ident, mode, steps, target, actor, bindings = request[:6]
            mask = None
            if len(request) == 7:
                import numpy as np
                mask = np.unpackbits(np.frombuffer(request[6], np.uint8), count=fc.draws.n).astype(bool)
                if mask.all():
                    mask = None
                elif not mask.any():
                    raise ValueError('A calculation has no saved consumer population')
            if mode == 'notes':
                result, _ = decision_row(fc, dispute, steps, target, actor, mask)
            else:
                result, _ = event_questions(dispute, DisputePath(instance, steps, '', ()),
                                            fc.setup, fc.m, fc.draws, fc.sens,
                                            indices=tuple({b[2] for b in bindings}),
                                            day_only=mode == 'prefix',
                                            rows=None if mask is None else tuple(mask for _ in steps))
            records = []
            classified, packed = {}, {}
            for binding, key, select, prefix in bindings:
                row = result if mode == 'notes' else result[select]
                node = fc.nodes[key]
                identity = (node.node, node.context.split("|")[0], select,
                            fc.uses_appeal_status(node), fc._classified(key))
                if identity in classified:
                    blob, entry = classified[identity]
                    if node.node in ("judgment_response", "financing_at_floor", "petition_cash_out"):
                        fc.grouped.add(key)
                    records.append((binding, key, prefix, blob, (key, *entry[1:])))
                    continue
                enriched = []

                def keep(_key, r, enriched=enriched, row=row):
                    if not enriched:
                        enriched.append({**r, 'day': row['day']})

                cls = classify_row(fc, dispute, key, steps, row, keep)
                blob = pack_row(enriched[0] if enriched else row, cache=packed)
                entry = class_entry(key, cls, None)
                classified[identity] = blob, entry
                records.append((binding, key, prefix, blob, entry))
            pickle.dump((ident, records), dst, protocol=5)
            _ROW_BLOBS.clear()  # batches stream; the global registry owns reuse, not process lifetime
            count += 1
            if count % 25 == 0:
                print({'calculated': count, 'batch': Path(source).name}, flush=True)
        pickle.dump(('complete', count), dst, protocol=5)
    os.replace(partial, output)
    return count


class Results:
    """Stream calculated bindings, then restrict facts to adopted saved populations."""

    def __init__(self, db):
        self.db = db
        db.executescript('''
            CREATE TABLE IF NOT EXISTS results (
                binding INTEGER PRIMARY KEY, row BLOB NOT NULL, classes BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS finished_requests (request INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS wanted (binding INTEGER PRIMARY KEY, mask BLOB NOT NULL);
        ''')
        db.create_function('mask_union', 2, lambda a, b:
                           (int.from_bytes(a, 'little') | int.from_bytes(b, 'little')).to_bytes(len(a), 'little'))
        self.get = functools.lru_cache(maxsize=256)(self._get)

    def ingest(self, output, instance):
        import gzip
        import pickle

        count = 0
        with gzip.open(output, 'rb') as fh, self.db:
            if pickle.load(fh) != instance:
                raise ValueError('Result belongs to another dispute')
            while True:
                request, records = pickle.load(fh)
                if request == 'complete':
                    if records != count or fh.read(1):
                        raise ValueError('Incomplete or trailing result records')
                    break
                expected = list(self.db.execute('SELECT id,question,prefix FROM bindings '
                                                'WHERE request=? ORDER BY id', (request,)))
                if [(b, k, p) for b, k, p, _, _ in records] != expected:
                    raise ValueError('Calculated bindings differ from their global assignment')
                if self.db.execute('SELECT 1 FROM finished_requests WHERE request=?', (request,)).fetchone():
                    count += 1
                    continue
                for binding, _key, _prefix, row, classes in records:
                    self.db.execute('INSERT INTO results VALUES(?,?,?)',
                                    (binding, row, pickle.dumps(classes, protocol=5)))
                self.db.execute('INSERT INTO finished_requests VALUES(?)', (request,))
                count += 1
        return count

    def require_complete(self, expected):
        # Ingest commits every assigned binding and its completion marker together.
        # Check exact request coverage without scanning the much larger row BLOBs.
        finished = self.db.execute('SELECT COUNT(*) FROM finished_requests').fetchone()[0]
        matched = self.db.execute('SELECT COUNT(*) FROM finished_requests f '
                                  'JOIN calculations c ON c.id=f.request').fetchone()[0]
        if finished != expected or matched != expected:
            raise ValueError(f'Incomplete refresh: {finished} completed requests, '
                             f'{matched} assigned, expected {expected}')

    def _get(self, binding, n, whole=False):
        import pickle

        from app.disputes.forecast import lazy_row, unpack_row

        found = self.db.execute('SELECT b.question,r.row,r.classes FROM results r JOIN bindings b '
                                'ON r.binding=b.id WHERE b.id=?', (binding,)).fetchone()
        if found is None:
            raise ValueError(f'No calculated result for binding {binding}')
        key, row, codes = found
        return key, (unpack_row if whole else lazy_row)(row), decode_classes(pickle.loads(codes), None, n)

    def rebind(self, fc, path, part, index):
        import numpy as np

        from app.analysis.events import group_branches
        from app.disputes.forecast import pack_mask, path_mask

        n = fc.draws.n
        mask = path_mask(path, n)
        mask = np.ones(n, dtype=bool) if mask is None else mask
        valid, classes = mask.copy(), {}
        blob = self.db.execute('SELECT bindings FROM consumers WHERE part=? AND row=?', (part, index)).fetchone()
        if blob is None:
            raise ValueError(f'Unplanned source history {part}:{index}')
        refs = array('Q')
        refs.frombytes(blob[0])
        if len(refs) != len(path.classes):
            raise ValueError('Saved class references changed after planning')
        for binding, original in zip(refs, path.classes, strict=True):
            key, row, refreshed = self.get(binding, n)
            if key != original[0]:
                raise ValueError('Binding order differs from its saved history')
            live = fc.live(fc.nodes[key], row) & mask
            if np.any(live & (refreshed == '')):
                raise ValueError(f'Live decision has no refreshed class: {key}')
            cls = decode_classes(original, mask, n)
            cls[live] = refreshed[live]
            classes[key] = cls
            if fc.nodes[key].node in ('judgment_response', 'financing_at_floor', 'petition_cash_out'):
                fc.grouped.add(key)
                # pack_row omits groups when the decision is live on no draws.
                if not live.any():
                    continue
                if row['groups'] is None:
                    raise ValueError(f'Live decision has no refreshed option groups: {key}')
                branch = next(b for k, b in path.edges if k == key)
                for group in set(row['groups'][live]):
                    if branch not in group_branches(fc.nodes[key].node, int(group)):
                        valid &= ~(live & (row['groups'] == group))
            for tag in set(cls[live]):
                fc.class_key(key, tag)
        for binding in refs:
            self.db.execute('INSERT INTO wanted VALUES(?,?) ON CONFLICT(binding) '
                            'DO UPDATE SET mask=mask_union(mask,excluded.mask)', (binding, pack_mask(valid)))
        return rebind_population(path, classes, valid, n)

    def facts(self, fc):
        """One record per distinct conditioning prefix/content, after support is known."""
        import numpy as np
        import xxhash

        from app.analysis.events import BIG
        from app.disputes.forecast import _ROW_BLOBS

        self.db.execute('DROP TABLE IF EXISTS temp.unique_facts')
        self.db.execute('CREATE TEMP TABLE unique_facts ('
                        'question TEXT, prefix INTEGER, digest BLOB, row BLOB, '
                        'PRIMARY KEY(question,prefix,digest)) WITHOUT ROWID')
        for binding, prefix, mask in self.db.execute(
                'SELECT w.binding,b.prefix,w.mask FROM wanted w JOIN bindings b ON w.binding=b.id ORDER BY w.binding'):
            # Rebinding reads only day/petition/groups; keep its cache compact.
            # Serialization needs all fields, one row at a time, without caching them.
            key, row, cls = self._get(binding, fc.draws.n, whole=True)
            on = np.unpackbits(np.frombuffer(mask, np.uint8), count=fc.draws.n).astype(bool)
            if not on.any():
                continue
            selected = {**row, 'day': np.where(on, row['day'], BIG)}
            # Classes already include context, groups and after_failed. Do not append them again.
            for tag in sorted(set(cls[on & fc.live(fc.nodes[key], row)]) - {''}):
                one = {**selected, 'day': np.where(cls == tag, selected['day'], BIG)}
                question, blob = fc.class_key(key, tag), pack_row(one)
                identity = question, prefix, xxhash.xxh3_128_digest(blob)
                inserted = self.db.execute('INSERT OR IGNORE INTO unique_facts VALUES(?,?,?,?)', (*identity, blob))
                if inserted.rowcount:
                    yield question, prefix, blob
                elif self.db.execute('SELECT row FROM unique_facts WHERE question=? AND prefix=? AND digest=?',
                                     identity).fetchone()[0] != blob:
                    raise ValueError('Fact content digest collision')
            _ROW_BLOBS.clear()


def refresh(fc, dispute, paths):
    """Small reference check only: not the production refresh scheduling unit.

    Keep source edges and populations; refresh facts/classes on exactly the saved histories.

    A saved no-event path already says whether its watched question was asked.
    Use that fact when replaying a subset, whose other watch-reading descendants
    may belong to a different worker.
    """
    prefixes = HistoryIndex()
    source = {}
    watches = set()
    for p in paths:
        prefixes.add(p.steps)
        key = (p.steps, p.outcome, p.mask)
        if key in source:
            raise ValueError('Refresh input repeats a saved history')
        source[key] = p
        names = {fc.nodes[k].node for edge, _ in p.edges for k in atoms(edge)}
        for i, (node, ctx, _branch) in enumerate(p.steps):
            if node in ('appeal', 'execute_pre_ruling') and node in names:
                watches.add((p.steps[:i], node, ctx))
    walk = _Walk(fc, dispute)
    rows, seen = [], set()
    record = fc.record
    keep_late = fc._keep_late
    started = time.monotonic()

    def recorded(keys, trace):
        result = record(keys, trace)
        for key, blob in result:
            identity = ('rec', key, fc._rec_at, blob)
            if identity not in seen:
                seen.add(identity)
                rows.append(identity)
        fc.facts.clear()
        return result

    def late(key, before, row):
        blob = pack_row(row)
        identity = ('late', key, before, blob)
        if identity not in seen:
            seen.add(identity)
            rows.append(identity)

    watched = _Walk._watched
    emitted = _Walk.emit

    def saved_watch(self, state, no, watch, then):
        watched(self, state, no, watch, then)
        return (state.steps, no[0], no[1]) in watches

    def emit(self, state, outcome):
        emitted(self, state, outcome)
        got = self.out[-1]
        key = (got.steps, got.outcome, got.mask)
        if key not in source:
            raise ValueError('Replay emitted an unsaved history or changed its population')
        if len(self.out) % 100 == 0:
            print({'refreshed': len(self.out), 'saved': len(paths),
                   'seconds': round(time.monotonic() - started, 1)}, flush=True)

    def bounded(function):
        @functools.wraps(function)
        def call(self, state, *args, **kwargs):
            if prefixes.find(state.steps) is None:
                return None
            return function(self, state, *args, **kwargs)
        return call

    fc.record, fc._keep_late = recorded, late
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(_Walk, '_watched', saved_watch))
            stack.enter_context(patch.object(_Walk, 'emit', emit))
            for name, function in list(vars(_Walk).items()):
                if not callable(function) or name.startswith('__'):
                    continue
                signature = inspect.signature(function)
                params = list(signature.parameters)
                if len(params) > 1 and params[1] in ('s', 'state') and name != '_watched' and \
                        signature.return_annotation in ('None', None):
                    stack.enter_context(patch.object(_Walk, name, bounded(function)))
            walk.run()
    finally:
        fc.record = record
        fc._keep_late = keep_late
    result = []
    found = set()
    for got in walk.out:
        key = (got.steps, got.outcome, got.mask)
        saved = source[key]
        if got.edges != saved.edges:
            raise ValueError(f'Native replay changed saved decision edges: '
                             f'added={set(got.edges) - set(saved.edges)!r}, '
                             f'missing={set(saved.edges) - set(got.edges)!r}')
        if key in found:
            raise ValueError('Native replay emitted a saved history twice')
        found.add(key)
        result.append(replace(saved, classes=got.classes))
    if found != source.keys():
        raise ValueError(f'Native replay missed {len(source.keys() - found)} saved histories')
    return result, rows, walk.keys


def restrict_populations(control_file, walked, folder):
    """Union actual consumer draws before dispatch, without replaying finance.

    This separate pass also supports an already prepared registry. Compact arrays
    map binding IDs to requests; one integer holds each request's population.
    """
    import gzip
    import json
    import pickle
    from pathlib import Path

    folder = Path(folder)
    if (folder / 'populations.json').exists():
        return json.loads((folder / 'populations.json').read_text())
    prepared = json.loads((folder / 'prepared.json').read_text())
    with open(control_file, 'rb') as fh:
        if pickle.load(fh)['walked'] != prepared['histories']:
            raise ValueError('Population preparation differs from saved control coverage')
    from app.analysis.setup import DRAWS
    n = DRAWS
    if n % 8:
        raise ValueError('Packed production draw masks require a multiple of eight')
    stores, total = {}, 0
    for iid, item in prepared['instances'].items():
        db = sqlite3.connect(f'file:{folder / item["folder"] / "plan.sqlite"}?mode=ro', uri=True)
        last = db.execute('SELECT MAX(id) FROM bindings').fetchone()[0] or 0
        mapping = array('I', [0]) * (last + 1)
        for ident, request in db.execute('SELECT id,request FROM bindings'):
            mapping[ident] = request
        count = db.execute('SELECT MAX(id) FROM calculations').fetchone()[0] or 0
        stores[iid] = db, mapping, [0] * (count + 1)
    sources = sorted(Path(walked).glob('part*.pkl'), key=lambda p: int(p.stem[4:]))
    for number, source in enumerate(sources, 1):
        with source.open('rb') as fh:
            paths = pickle.load(fh)
        for iid, (db, mapping, masks) in stores.items():
            for index, blob in db.execute('SELECT row,bindings FROM consumers WHERE part=? ORDER BY row', (source.name,)):
                path = paths[index]
                if path.instance_id != iid:
                    raise ValueError('Consumer population belongs to a different dispute')
                mask = (1 << n) - 1 if path.mask is None else int.from_bytes(path.mask, 'little')
                refs = array('Q')
                refs.frombytes(blob)
                for binding in refs:
                    request = mapping[binding]
                    masks[request] |= mask
                total += 1
        if number % 10 == 0 or number == len(sources):
            progress = {'stage': 'share_required_draws', 'sources': number, 'total_sources': len(sources),
                        'histories': total, 'jev_started': False}
            (folder / 'progress.json').write_text(json.dumps(progress))
            print(progress, flush=True)
    if total != prepared['histories']:
        raise ValueError('Consumer population coverage differs')
    requests, draws = 0, 0
    for iid, item in prepared['instances'].items():
        db, _, masks = stores[iid]
        for source in sorted((folder / item['folder'] / 'inputs').glob('*.pkl.gz')):
            temporary = source.with_suffix('.pending')
            with gzip.open(source, 'rb') as src, gzip.open(temporary, 'wb', compresslevel=1) as dst:
                instance = pickle.load(src)
                if instance != iid:
                    raise ValueError('Input batch dispute differs')
                pickle.dump(instance, dst, protocol=5)
                while True:
                    try:
                        request = pickle.load(src)
                    except EOFError:
                        break
                    mask = masks[request[0]]
                    if not mask:
                        raise ValueError('Calculation has no saved population')
                    pickle.dump((*request[:6], mask.to_bytes(n // 8, 'little')), dst, protocol=5)
                    requests += 1
                    draws += mask.bit_count()
            temporary.replace(source)
        db.close()
    result = {'requests': requests, 'requested_draws': draws, 'unrestricted_draws': requests * n,
              'histories': total, 'jev_started': False}
    (folder / 'populations.json').write_text(json.dumps(result, indent=2))
    print(result, flush=True)
    return result


def balance_batches(folder, max_requests=128, work_limit=131072):
    """Bound each contiguous batch by required draw/step work, not compressed bytes."""
    import gzip
    import json
    import pickle
    from pathlib import Path

    folder = Path(folder)
    prepared = json.loads((folder / 'prepared.json').read_text())
    if not (folder / 'populations.json').exists():
        raise ValueError('Draw populations must be prepared before balancing')
    costs = {}
    for iid, item in prepared['instances'].items():
        root = folder / item['folder']
        output = root / 'balanced'
        output.mkdir(exist_ok=True)
        # A failed preparation can be repeated from the untouched original inputs.
        for old in output.glob('*.pkl.gz'):
            old.unlink()
        number, count, cost, handle = -1, 0, 0, None
        try:
            for source in sorted((root / 'inputs').glob('*.pkl.gz'), key=lambda p: int(p.name.split('.')[0])):
                with gzip.open(source, 'rb') as src:
                    if pickle.load(src) != iid:
                        raise ValueError('Batch dispute differs')
                    while True:
                        try:
                            request = pickle.load(src)
                        except EOFError:
                            break
                        if len(request) != 7:
                            raise ValueError('Every calculation must carry its required draws')
                        work = max(1, len(request[2])) * int.from_bytes(request[6], 'little').bit_count()
                        if handle is None or count >= max_requests or cost + work > work_limit:
                            if handle is not None:
                                handle.close()
                            number += 1
                            count, cost = 0, 0
                            handle = gzip.open(output / f'{number}.pkl.gz', 'wb', compresslevel=1)
                            pickle.dump(iid, handle, protocol=5)
                        pickle.dump(request, handle, protocol=5)
                        count += 1
                        cost += work
                        costs[f'{item["folder"]}-{number}'] = cost
        finally:
            if handle is not None:
                handle.close()
    (folder / 'balanced.json').write_text(json.dumps(costs))
    return costs


def prepare_saved(run, control_file, walked, out):
    """Index the preserved raw artifacts once; no walker or financial calculation."""
    import json
    import pickle
    from pathlib import Path

    from app.disputes import pool

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with open(control_file, 'rb') as fh:
        control = pickle.load(fh)
    fc = pool.forecaster(run, control)
    plans, counts, total = {}, {}, 0
    sources = sorted(Path(walked).glob('part*.pkl'), key=lambda p: int(p.stem[4:]))
    started = time.monotonic()
    for number, source in enumerate(sources, 1):
        with source.open('rb') as fh:
            paths = pickle.load(fh)
        for index, path in enumerate(paths):
            if path.instance_id not in plans:
                ident = len(plans)
                folder = out / str(ident)
                folder.mkdir()
                plans[path.instance_id] = RefreshPlan(folder / 'plan.sqlite', path.instance_id)
                counts[path.instance_id] = {'folder': str(ident), 'histories': 0}
            plans[path.instance_id].add(fc, path, source.name, index)
            counts[path.instance_id]['histories'] += 1
        total += len(paths)
        for plan in plans.values():
            plan.db.commit()
        progress = {'stage': 'indexing_saved_histories', 'sources': number, 'total_sources': len(sources),
                    'histories': total, 'seconds': round(time.monotonic() - started, 1), 'jev_started': False}
        (out / 'progress.json').write_text(json.dumps(progress))
        print(progress, flush=True)
        del paths
    for instance, plan in plans.items():
        folder = out / counts[instance]['folder']
        counts[instance].update(plan.write_batches(folder / 'inputs'))
        counts[instance]['bindings'] = plan.db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0]
        counts[instance]['prefix_links'] = len(plan.histories.parents)
        plan.close()
        print({'instance': instance, **counts[instance]}, flush=True)
    if total != control['walked']:
        raise ValueError(f'Preserved raw coverage differs: {total} != {control["walked"]}')
    result = {'run': run, 'instances': counts, 'histories': total, 'sources': len(sources),
              'stage': 'refresh_inputs_prepared', 'jev_started': False}
    (out / 'prepared.json').write_text(json.dumps(result, indent=2))
    print(result, flush=True)
    return result


if __name__ == '__main__':
    import sys

    if len(sys.argv) == 6 and sys.argv[1] == 'prepare':
        prepare_saved(*sys.argv[2:])
    else:
        raise SystemExit('usage: python -m tools.question_refresh prepare RUN CONTROL WALKED OUTPUT')
