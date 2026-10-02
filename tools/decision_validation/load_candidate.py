import sys,ast
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
sys.path.insert(0,'.')
import app.analysis.events as E
import app.disputes.forecast as F

def patches():
 out=[]
 for filename,module,targets in [('events.py',E,{'Chain':{'advance','finish'}}),('forecast.py',F,{'_Prefix':{'of'}})]:
  src=Path('tools/decision_validation/candidate',filename).read_text()
  for cls in ast.parse(src).body:
   if not isinstance(cls,ast.ClassDef) or cls.name not in targets:continue
   for method in cls.body:
    if not isinstance(method,ast.FunctionDef) or method.name not in targets[cls.name]:continue
    ns=dict(module.__dict__);exec(compile(ast.Module(body=[method],type_ignores=[]),filename,'exec'),ns)
    out.append(patch.object(getattr(module,cls.name),method.name,ns[method.name]))
 out.extend([patch.object(E.Trace,'question_petition',None,create=True),patch.object(E.Chain,'UNSEEN',E.Chain.UNSEEN|{'question_petition'}),patch.object(E,'LIGHT_FIELDS',E.LIGHT_FIELDS+('question_petition',))])
 return out

import importlib.util
import tools.notes_resume as R
_spec=importlib.util.spec_from_file_location('petition_resume_candidate','tools/decision_validation/candidate/notes_resume.py')
_resume=importlib.util.module_from_spec(_spec);sys.modules[_spec.name]=_resume;_spec.loader.exec_module(_resume)
base_patches=patches
def patches():
 return base_patches()+[patch.object(R,'capture',_resume.capture)]
