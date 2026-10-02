"""Candidate: partition appeal facts only in questions that actually render them."""
import inspect
import re
import textwrap
from unittest.mock import patch

from app.disputes import forecast as F
from tools.decision_validation import context_candidate as C


def consumes_appeal(fc, node):
    dispute = next((d for d in fc.disputes if d.instance_id == node.instance_id), None)
    fields = fc.texts(node.node, dispute).get('situation_keys', ())
    tags = set(node.context.split('|'))
    return bool(set(fields) & {'judgment_status', 'appeal_deadline'}) or bool(tags & {'final', 'appealed'})


def question_class(fc, node, row, live=None):
    result = F.situation_class(row, live)
    if result is None or consumes_appeal(fc, node):
        return result
    return F.np.array([re.sub(r'\.appeal[0-5](?=\.|$)', '', tag) for tag in result], dtype=object)


def compile_node(source):
    source = source.replace('situation_class(row, groups >= 0)',
                            'question_class(self.fc, self.fc.nodes[k], row, groups >= 0)')
    source = source.replace('situation_class(row, self.fc.live(self.fc.nodes[k], row))',
                            'question_class(self.fc, self.fc.nodes[k], row, self.fc.live(self.fc.nodes[k], row))')
    ns = dict(F.__dict__, question_class=question_class, classified=C.classified, condition_class=C.condition_class)
    exec(compile(source, '<scoped-appeal-node>', 'exec'), ns)
    return ns['node']


def patches():
    source = textwrap.dedent(inspect.getsource(F.Forecaster._split))
    source = source.replace('situation_class(row, self.live(self.nodes[keys[0]], row))',
                            'question_class(self, self.nodes[keys[0]], row, self.live(self.nodes[keys[0]], row))')
    ns = dict(F.__dict__, question_class=question_class)
    exec(compile(source, '<scoped-appeal-split>', 'exec'), ns)
    return [patch.object(F._Walk, 'node', compile_node(textwrap.dedent(inspect.getsource(F._Walk.node)))),
            patch.object(F.Forecaster, '_split', ns['_split']),
            patch.object(C, 'oldsplit', ns['_split']),
            patch.object(C, 'newnode', compile_node(C.src))]
