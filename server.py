from __future__ import annotations
import json, os, re, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from bot import compose

START = time.time()
STORE = {"category": {}, "merchant": {}, "customer": {}, "trigger": {}}
VERSIONS = {"category": {}, "merchant": {}, "customer": {}, "trigger": {}}
CONV = {}


def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + 'Z'

def parse_iso(value):
    if not value:
        return None
    try:
        from datetime import datetime, timezone
        v = str(value).replace('Z', '+00:00')
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def jload(raw):
    return json.loads(raw.decode('utf-8'))


def context_key(scope, context_id):
    return f"{scope}:{context_id}"


def resolve_context(scope, context_id):
    return STORE.get(scope, {}).get(context_id)


def find_for_trigger(trigger):
    mid = trigger.get('merchant_id')
    cid = trigger.get('customer_id')
    merchant = STORE['merchant'].get(mid)
    customer = STORE['customer'].get(cid) if cid else None
    cat_slug = (merchant or {}).get('category_slug') or trigger.get('payload', {}).get('category')
    category = STORE['category'].get(cat_slug)
    return category or {}, merchant or {}, customer


def conv_id(trigger, merchant, customer):
    base = 'conv_' + (customer.get('customer_id') if customer else merchant.get('merchant_id', 'merchant')) + '_' + trigger.get('id','trigger')
    return re.sub(r'[^A-Za-z0-9_-]+', '_', base)[:180]


def action_for(trigger, merchant, customer, category):
    msg = compose(category, merchant, trigger, customer)
    return msg


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def _send(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        p=urlparse(self.path).path
        if p=='/v1/healthz':
            self._send(200,{"status":"ok","uptime_seconds":int(time.time()-START),"contexts_loaded":{k:len(v) for k,v in STORE.items()}}); return
        if p=='/v1/metadata':
            self._send(200,{"team_name":"Vera Message Engine","team_members":[],"model":"deterministic-rules","approach":"trigger router + category/merchant/customer grounded composer","version":"0.1.0"}); return
        self._send(404,{"error":"not_found"})
    def do_POST(self):
        p=urlparse(self.path).path
        try: data=jload(self.rfile.read(int(self.headers.get('Content-Length','0'))))
        except Exception as e: self._send(400,{"error":"invalid_json","details":str(e)}); return
        if p=='/v1/context': return self.context(data)
        if p=='/v1/tick': return self.tick(data)
        if p=='/v1/reply': return self.reply(data)
        self._send(404,{"error":"not_found"})
    def context(self,d):
        scope=d.get('scope'); cid=d.get('context_id'); ver=d.get('version'); payload=d.get('payload')
        if scope not in STORE: return self._send(400,{"accepted":False,"reason":"invalid_scope","details":scope})
        if not cid or not isinstance(ver,int) or not isinstance(payload,dict): return self._send(400,{"accepted":False,"reason":"malformed_context"})
        old=VERSIONS[scope].get(cid)
        if old is not None and ver<old: return self._send(409,{"accepted":False,"reason":"stale_version","current_version":old})
        if old==ver: return self._send(200,{"accepted":True,"ack_id":"ack_noop_"+cid,"stored_at":now_iso()})
        STORE[scope][cid]=payload; VERSIONS[scope][cid]=ver
        self._send(200,{"accepted":True,"ack_id":"ack_"+cid+"_"+str(ver),"stored_at":now_iso()})
    def tick(self,d):
        actions=[]
        now_ts = parse_iso(d.get('now')) or time.time()
        for tid in d.get('available_triggers',[])[:20]:
            t=STORE['trigger'].get(tid)
            if not t: continue
            category,merchant,customer=find_for_trigger(t)
            if not merchant: continue
            out=action_for(t,merchant,customer,category)
            if not out['body']: continue
            cid=conv_id(t,merchant,customer)
            existing=CONV.get(cid)
            if existing:
                if existing.get('ended'): continue
                wake=existing.get('wake_at')
                if wake is None or now_ts < wake:
                    continue
                existing['wake_at']=None
                existing['sent'].append(out['body'])
                existing['trigger']=t
            else:
                CONV[cid]={"merchant_id":merchant.get('merchant_id'),"customer_id":customer.get('customer_id') if customer else None,"trigger":t,"category":category,"merchant":merchant,"customer":customer,"sent":[out['body']],"auto_replies":0,"ended":False,"wake_at":None}
            a={"conversation_id":cid,"merchant_id":merchant.get('merchant_id'),"customer_id":customer.get('customer_id') if customer else None,"send_as":out['send_as'],"trigger_id":t.get('id'),"template_name":"vera_contextual_v1","template_params":[],**out}
            actions.append(a)
        self._send(200,{"actions":actions})
    def reply(self,d):
        cid=d.get('conversation_id'); state=CONV.get(cid)
        msg=str(d.get('message','')).strip()
        if not state: return self._send(200,{"action":"end","rationale":"Unknown conversation; no safe context is available to continue."})
        low=msg.lower()
        if re.search(r'\b(stop|unsubscribe|do not message|don.t message|not interested|leave me alone)\b',low):
            state['ended']=True; return self._send(200,{"action":"end","rationale":"Merchant/customer explicitly declined or opted out; ending the conversation and suppressing further sends."})
        history=[x for x in state.get('replies',[])]
        if history and msg==history[-1]: state['auto_replies']+=1
        else: state['auto_replies']=0
        state.setdefault('replies',[]).append(msg)
        if state['auto_replies']==1:
            state['wake_at'] = (parse_iso(d.get('received_at')) or time.time()) + 86400
            state['sent'].append("Looks like an auto-reply 😊 When you’re back, just reply YES and I’ll pick this up.")
            return self._send(200,{"action":"send","body":"Looks like an auto-reply 😊 When you’re back, just reply YES and I’ll pick this up.","cta":"binary_yes_no","rationale":"Detected a repeated reply; giving one lightweight owner-facing prompt before backing off."})
        if state['auto_replies']==2:
            state['wake_at'] = (parse_iso(d.get('received_at')) or time.time()) + 86400
            return self._send(200,{"action":"wait","wait_seconds":86400,"rationale":"The same canned reply repeated; backing off for 24 hours rather than burning turns."})
        if state['auto_replies']>=3:
            state['ended']=True; return self._send(200,{"action":"end","rationale":"Repeated identical auto-replies provide no engagement signal; closing the conversation."})
        if re.search(r'\b(yes|ok|okay|go ahead|lets do it|let.s do it|confirm|do it|sure)\b',low):
            state['wake_at'] = None
            state['sent'].append("Great — I’ll move to the action you just approved. Reply CONFIRM when you want me to use the supplied merchant details for the final draft.")
            return self._send(200,{"action":"send","body":"Great — I’ll move to the action you just approved. Reply CONFIRM when you want me to use the supplied merchant details for the final draft.","cta":"binary_confirm_cancel","rationale":"Explicit positive intent detected; switching from qualification to action instead of asking another discovery question."})
        if re.search(r'\b(gst|tax filing|unrelated|cricket score|weather)\b',low):
            return self._send(200,{"action":"send","body":"That’s outside Vera’s merchant-growth scope. I’ll keep this thread focused on the original merchant task — want me to continue with the draft?","cta":"open_ended","rationale":"Out-of-scope question politely redirected to the active merchant-growth thread."})
        if re.search(r'\b(later|tomorrow|busy|not now|give me time)\b',low):
            state['wake_at'] = (parse_iso(d.get('received_at')) or time.time()) + 1800
            return self._send(200,{"action":"wait","wait_seconds":1800,"rationale":"Merchant asked for time; backing off rather than adding another immediate nudge."})
        # Use original trigger context to continue with a grounded next step.
        out=compose(state['category'],state['merchant'],state['trigger'],state['customer'])
        state['wake_at'] = None
        state['sent'].append(out['body'])
        return self._send(200,{"action":"send","body":out['body'],"cta":out['cta'],"rationale":"Continuing the active thread with the same grounded context while avoiding a new unsupported claim."})

if __name__=='__main__':
    port=int(os.environ.get('PORT','8080'))
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
