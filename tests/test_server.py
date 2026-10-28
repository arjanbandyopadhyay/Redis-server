"""Integration tests. Usage: python3 tests/test_server.py main|after_restart [port]
  1) start the server, run `main`, stop it with Ctrl-C (writes the dump)
  2) start it again, run `after_restart` (checks persistence + TTL)."""
import socket, sys, time
PORT=int(sys.argv[2]) if len(sys.argv)>2 else 6382
def c(*a):
    o=b"*%d\r\n"%len(a)
    for x in a:
        x=x if isinstance(x,bytes) else x.encode(); o+=b"$%d\r\n%s\r\n"%(len(x),x)
    return o
class Cl:
    def __init__(s):
        s.s=socket.create_connection(("127.0.0.1",PORT),timeout=3); s.f=s.s.makefile("rb")
    def reply(s):
        l=s.f.readline()
        if not l: return None
        t,b=l[:1],l[1:-2]
        if t in b"+-:": return l[:-2]
        if t==b"$":
            n=int(b); 
            if n<0: return None
            d=s.f.read(n+2); return d[:-2]
        if t==b"*": return [s.reply() for _ in range(int(b))]
    def cmd(s,*a): s.s.sendall(c(*a)); return s.reply()
ok=fail=0
def check(name,cond,extra=""):
    global ok,fail
    if cond: ok+=1; print("PASS",name)
    else: fail+=1; print("FAIL",name,extra)
mode=sys.argv[1]
if mode=="main":
    # 1. malformed input must not kill the server
    bad=[b"*abc\r\n", b"*3\r\n$x\r\n", b"*1\r\n$-5\r\n", b"*99999999999\r\n", b"*2\r\n$3\r\nGET\r\n:1\r\n", b"\x00\xff\xfe\r\n*\r\n"]
    for b in bad:
        x=Cl(); x.s.sendall(b)
        try: r=x.s.recv(100)
        except Exception as e: r=repr(e)
        x.s.close()
        p=Cl().cmd("PING")
        check(f"malformed {b[:14]!r} -> server alive", p==b"+PONG", p)
    # 2. pipelining
    x=Cl(); x.s.sendall(c("SET","p1","a")+c("SET","p2","b")+c("GET","p1")+c("GET","p2")+c("PING"))
    r=[x.reply() for _ in range(5)]
    check("pipeline 5 cmds in one write", r==[b"+OK",b"+OK",b"a",b"b",b"+PONG"], r)
    x=Cl(); x.s.sendall(b"".join(c("SET",f"k{i}",f"v{i}") for i in range(2000)))
    r=[x.reply() for _ in range(2000)]
    check("pipeline 2000 SETs", all(v==b"+OK" for v in r) and len(r)==2000)
    # 3. command split across packets
    x=Cl(); raw=c("SET","split","hello"); x.s.sendall(raw[:7]); time.sleep(.2); x.s.sendall(raw[7:20]); time.sleep(.2); x.s.sendall(raw[20:])
    check("command split over 3 sends", x.reply()==b"+OK" and x.cmd("GET","split")==b"hello")
    # 4. large value (> old 1024-byte buffer)
    big=(b"0123456789"*30000)  # 300 KB
    x=Cl(); x.cmd("SET","big",big); g=x.cmd("GET","big")
    check("300KB value round-trip", g==big, len(g) if g else g)
    # 5. SET clears TTL + overwrites other types
    x=Cl(); x.cmd("SET","t","v"); x.cmd("EXPIRE","t","1"); x.cmd("SET","t","v2"); time.sleep(1.5)
    check("SET clears old TTL", x.cmd("GET","t")==b"v2", x.cmd("GET","t"))
    x.cmd("RPUSH","lk","a"); x.cmd("SET","lk","x")
    check("SET over list key -> type string", x.cmd("TYPE","lk")==b"+string", x.cmd("TYPE","lk"))
    keys=x.cmd("KEYS","*"); check("no duplicate key after type overwrite", keys.count(b"lk")==1, keys)
    # 6. regression: commands from test_all.sh
    x=Cl(); x.cmd("FLUSHALL")
    check("ECHO", x.cmd("ECHO","Hello")==b"+Hello")
    x.cmd("RPUSH","ml","a","b","c"); check("LLEN", x.cmd("LLEN","ml")==b":3")
    x.cmd("LPUSH","ml","start"); check("LGET", x.cmd("LGET","ml")==[b"start",b"a",b"b",b"c"], x.cmd("LGET","ml"))
    check("LPOP", x.cmd("LPOP","ml")==b"start"); check("RPOP", x.cmd("RPOP","ml")==b"c")
    check("LINDEX -1", x.cmd("LINDEX","ml","-1")==b"b")
    x.cmd("HSET","u","name","Alice","age","30"); check("HGET", x.cmd("HGET","u","name")==b"Alice")
    check("HLEN", x.cmd("HLEN","u")==b":2"); check("HEXISTS", x.cmd("HEXISTS","u","age")==b":1")
    x.cmd("SET","rn","1"); x.cmd("RENAME","rn","rn2"); check("RENAME", x.cmd("GET","rn2")==b"1")
    check("unknown cmd handled", x.cmd("NOPE").startswith(b"-"))
    # data for persistence test
    x.cmd("FLUSHALL")
    x.cmd("SET","greeting","hello world"); x.cmd("SET","empty",""); x.cmd("SET","nl","line1\nline2\r\nline3")
    x.cmd("SET","key with space","v"); x.cmd("RPUSH","lst","a b","c:d",""); x.cmd("HSET","h","url","http://x.com:80/a b","f:ield","va lue")
    x.cmd("SET","ttl","alive"); x.cmd("EXPIRE","ttl","3"); x.cmd("SET","plain","stays")
if mode=="after_restart":
    x=Cl()
    check("persist: value with space", x.cmd("GET","greeting")==b"hello world", x.cmd("GET","greeting"))
    check("persist: empty value", x.cmd("GET","empty")==b"")
    check("persist: newline/CRLF value", x.cmd("GET","nl")==b"line1\nline2\r\nline3")
    check("persist: key with space", x.cmd("GET","key with space")==b"v")
    check("persist: list items w/ spaces, colon, empty", x.cmd("LGET","lst")==[b"a b",b"c:d",b""], x.cmd("LGET","lst"))
    check("persist: hash value w/ colon+space", x.cmd("HGET","h","url")==b"http://x.com:80/a b")
    check("persist: hash field w/ colon", x.cmd("HGET","h","f:ield")==b"va lue")
    check("persist: TTL key alive right after restart", x.cmd("GET","ttl")==b"alive")
    time.sleep(3.5)
    check("persist: TTL still enforced after restart", x.cmd("GET","ttl") is None)
    check("persist: non-TTL key survives", x.cmd("GET","plain")==b"stays")
print(f"== {ok} passed, {fail} failed ==")
sys.exit(1 if fail else 0)
