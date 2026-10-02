"""Local-only shared-context discriminator. Does not edit production source."""
import sys,inspect,textwrap,numpy as np
from contextlib import ExitStack
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,'.')
import app.disputes.forecast as F
import tools.notes_recover as R
from app.analysis.events import BIG
oldnode=F._Walk.node;oldsituation=F._Walk.situation;oldcapture=R.capture;oldsplit=F.Forecaster._split

def classified(w,name,ctx,assumptions,branches,groups):
 if w.d is None:return False
 n=w.fc.new_node(w.d,name,*ctx,assumptions=assumptions,branches=branches)
 return groups is not None or bool(w.fc.CLASSES and w.pend and w.fc.event_forecast(n) and n.question_id not in w.fc.no_cash)

def contexts(w,s,ctx,row,conds):
 bits=np.zeros(len(row['day']),dtype=np.int64)
 for i,c in enumerate(conds):bits|=(np.asarray(row['marks'][c])<=row['day']).astype(np.int64)<<i
 out=np.full(len(bits),'',dtype=object)
 day=row['day'];live=(day<w.N)&((row['petition']<0)|(day<row['petition']))
 for bit in sorted(set(bits[live])):
  selected=live&(bits==bit)
  tr=SimpleNamespace(day=[np.where(selected,day,BIG)],marks=row['marks'],petition=row['petition'])
  out[selected]='|'.join(w._tags(s,conds,ctx,tr,()))
 return out

def condition_class(cls,row,conds,w,s,ctx):
 if cls is None or not conds:return cls
 text=contexts(w,s,ctx,row,conds);out=cls.copy()
 for t in set(text[cls!='']):
  on=(cls!='')&(text==t)
  out[on]=np.strings.add(np.strings.add(cls[on].astype(str),'.ctx'),t.encode().hex()).astype(object)
 return out

def situation(w,s,probe,name,ctx):
 tags=oldsituation(w,s,probe,name,ctx)
 if w.d is None or not w.pend:return tags
 conds=list(w.fc.spec[name].get('situation',()))
 if not conds:return tags
 at=probe if isinstance(probe[0],tuple) else (probe,)
 tr=w._trace(s.steps+at);day=tr.day[-1];live=(day<w.N)&((tr.petition<0)|(day<tr.petition))
 row={'day':day,'petition':tr.petition,'marks':tr.marks}
 before=contexts(w,s,ctx,row,conds)
 for watch in w._watch:
  if watch.read or not set(conds)&set(watch.marks):continue
  marks={**tr.marks,**{c:np.minimum(tr.marks[c],v) for c,v in watch.marks.items()}}
  after=contexts(w,s,ctx,{**row,'marks':marks},conds)
  if np.any(live&(before!=after)):watch.read=True
 return tags
src=textwrap.dedent(inspect.getsource(oldnode))
src=src.replace('        tags = self.situation', '        tags = self.situation')
src=src.replace('    if groups is not None:\n', '    if classified(self,name,ctx,assumptions,branches,groups):\n        tags = ()\n    if groups is not None:\n',1)
src=src.replace('situation_class(row, groups >= 0)', "condition_class(situation_class(row, groups >= 0), row, list(self.fc.spec[name].get('situation', ())), self, s, ctx)")
src=src.replace('situation_class(row, self.fc.live(self.fc.nodes[k], row))', "condition_class(situation_class(row, self.fc.live(self.fc.nodes[k], row)), row, list(self.fc.spec[name].get('situation', ())), self, s, ctx)")
ns=dict(F.__dict__,classified=classified,condition_class=condition_class);exec(src,ns);newnode=ns['node']

def split(fc,keys,row,keep,cls=None):
 if cls is not None and 'note_context' in row:
  cls=cls.astype(object)
  for text in set(row['note_context'][cls!='']):
   on=(cls!='')&(row['note_context']==text)
   cls[on]=np.strings.add(cls[on].astype(str),'.ctx'+str(text).encode().hex()).astype(object)
 return oldsplit(fc,keys,row,keep,cls)
