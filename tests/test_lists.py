"""Usage: python3 tests/test_lists.py <port> [seed]
WARNING: runs FLUSHALL - do not run between test_server.py "main" and "after_restart".
Differential test for list commands: random ops against a server,
compared with a pure-Python model. Prints a hash of every reply so two
builds (vector vs deque) can be compared for IDENTICAL behaviour."""
import socket, sys, random, hashlib
from collections import deque
PORT=int(sys.argv[1]); SEED=int(sys.argv[2]) if len(sys.argv)>2 else 1
def c(*a):
    o=b"*%d\r\n"%len(a)
    for x in a:
        x=x.encode(); o+=b"$%d\r\n%s\r\n"%(len(x),x)
    return o
s=socket.create_connection(("127.0.0.1",PORT),timeout=5); f=s.makefile("rb")
def reply():
    l=f.readline(); t,b=l[:1],l[1:-2]
    if t in b"+-:": return l[:-2]
    if t==b"$":
        n=int(b)
        return None if n<0 else f.read(n+2)[:-2]
    if t==b"*": return [reply() for _ in range(int(b))]
def cmd(*a): s.sendall(c(*a)); return reply()

rnd=random.Random(SEED); model=deque(); K="L"
cmd("FLUSHALL"); h=hashlib.sha256(); bad=0
def norm(i): return i if i>=0 else len(model)+i
for step in range(3000):
    op=rnd.choice(["LPUSH","RPUSH","LPOP","RPOP","LREM","LSET","LINDEX","LLEN","LPUSH","RPUSH"])
    v=rnd.choice("abcde")
    if op=="LPUSH": r=cmd("LPUSH",K,v); model.appendleft(v)
    elif op=="RPUSH": r=cmd("RPUSH",K,v); model.append(v)
    elif op=="LPOP": r=cmd("LPOP",K); exp=model.popleft() if model else None; bad+= (r!=(exp.encode() if exp else None))
    elif op=="RPOP": r=cmd("RPOP",K); exp=model.pop() if model else None; bad+= (r!=(exp.encode() if exp else None))
    elif op=="LREM":
        cnt=rnd.choice([0,1,2,-1,-2,3]); r=cmd("LREM",K,str(cnt),v)
        lst=list(model); removed=0
        if cnt==0: new=[x for x in lst if x!=v]; removed=len(lst)-len(new)
        elif cnt>0:
            new=[]
            for x in lst:
                if x==v and removed<cnt: removed+=1
                else: new.append(x)
        else:
            new=[]
            for x in reversed(lst):
                if x==v and removed<-cnt: removed+=1
                else: new.append(x)
            new.reverse()
        model=deque(new); bad+=(r!=b":%d"%removed)
    elif op=="LSET":
        i=rnd.randint(-4,6); r=cmd("LSET",K,str(i),v); j=norm(i)
        if 0<=j<len(model): model[j]=v
    elif op=="LINDEX":
        i=rnd.randint(-4,6); r=cmd("LINDEX",K,str(i)); j=norm(i)
        exp=model[j].encode() if 0<=j<len(model) else None; bad+=(r!=exp)
    else: r=cmd("LLEN",K); bad+=(r!=b":%d"%len(model))
    h.update(repr((op,r)).encode())
    if step%250==0:
        g=cmd("LGET",K) or []; bad+=([x.decode() for x in g]!=list(model))
g=cmd("LGET",K) or []; bad+=([x.decode() for x in g]!=list(model))
print(f"port {PORT} seed {SEED}: model mismatches={bad}  final_len={len(model)}  reply_hash={h.hexdigest()[:16]}")
sys.exit(1 if bad else 0)
