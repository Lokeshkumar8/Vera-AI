import json,glob,sys,os
sys.path.insert(0,os.path.dirname(__file__))
from bot import compose
ROOT=os.path.dirname(os.path.dirname(__file__))
exp=os.path.join(ROOT,'expanded')
load=lambda p: {json.load(open(f))['merchant_id']:json.load(open(f)) for f in glob.glob(p+'/*.json')}
merchants=load(os.path.join(exp,'merchants'))
customers={json.load(open(f))['customer_id']:json.load(open(f)) for f in glob.glob(os.path.join(exp,'customers','*.json'))}
triggers={json.load(open(f))['id']:json.load(open(f)) for f in glob.glob(os.path.join(exp,'triggers','*.json'))}
categories={json.load(open(f))['slug']:json.load(open(f)) for f in glob.glob(os.path.join(exp,'categories','*.json'))}
pairs=json.load(open(os.path.join(exp,'test_pairs.json')))['pairs']
out=os.path.join(os.path.dirname(__file__),'submission.jsonl')
with open(out,'w',encoding='utf8') as f:
 for p in pairs:
  m=merchants[p['merchant_id']]; t=triggers[p['trigger_id']]; c=customers.get(p.get('customer_id')); cat=categories[m['category_slug']]
  x=compose(cat,m,t,c); f.write(json.dumps({'test_id':p['test_id'],**x},ensure_ascii=False)+'\n')
print(out)
