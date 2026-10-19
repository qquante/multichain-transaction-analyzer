from __future__ import annotations
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
import zlib
from email.utils import parsedate_to_datetime
from .storage import dumps, now

CHAINS={
 "robinhood":{"id":4663,"rpc":["https://rpc.mainnet.chain.robinhood.com","https://robinhood-rpc.publicnode.com"],"chunk":2000,"explorer":"https://robinhoodchain.blockscout.com"},
 "bsc":{"id":56,"rpc":["https://bsc.publicnode.com","https://bsc.drpc.org","https://public.1rpc.io/bnb"],"chunk":1000,"explorer":"https://bscscan.com"},
 "base":{"id":8453,"rpc":["https://mainnet.base.org","https://base-rpc.publicnode.com"],"chunk":1000,"explorer":"https://basescan.org"},
 "ethereum":{"id":1,"rpc":["https://eth.drpc.org","https://ethereum.publicnode.com"],"chunk":1000,"explorer":"https://etherscan.io"},
 "hyperevm":{"id":999,"rpc":["https://rpc.hyperliquid.xyz/evm","https://hyperliquid-json-rpc.stakely.io"],"chunk":50,"explorer":"https://hyperevmscan.io"},
 "solana":{"genesis":"5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d","rpc":["https://api.mainnet-beta.solana.com","https://solana-rpc.publicnode.com"],"explorer":"https://solscan.io"}
}
READ_METHODS={"eth_chainId","eth_blockNumber","eth_getBlockByNumber","eth_getBlockByHash",
 "eth_getTransactionReceipt","eth_getTransactionByHash","eth_getCode","eth_call",
 "eth_getLogs","debug_traceTransaction","eth_getBalance","getGenesisHash","getSlot",
 "getAccountInfo","getSignaturesForAddress","getTransaction","getBlockTime","getBlock",
 "getFirstAvailableBlock","minimumLedgerSlot","getBlocks","getMultipleAccounts"}
class RpcError(Exception):
    def __init__(self,message,code=None,endpoint=None):
        super().__init__(message)
        self.code,self.endpoint=code,endpoint
    @property
    def range_error(self):
        return self.code in (-32005,-32002) or any(x in str(self).lower() for x in
             ("range","too many","limit exceeded","response size","timed out","timeout"))
class BudgetExceeded(RpcError): pass

def retry_after(value, current=None):
    current=time.time() if current is None else current
    try:
        return max(0,float(value))
    except (TypeError,ValueError):
        try: return max(0,parsedate_to_datetime(value).timestamp()-current)
        except (TypeError,ValueError,OverflowError): return 5.

class Network:
    def __init__(self,store,budget=3000,interval=1.,offline=False):
        self.store=store
        self.budget=budget
        self.interval=max(1.,interval)
        self.offline=offline
        self.count=0
        self.cache_hits=0
        self.lock=threading.RLock()
        self.gates={}
        self.last={}
        self.global_gate=threading.Semaphore(3)
        self.verified=set()
        self.provenance={}
        self.stopped=False
        self.stop_file=store.path.parent/"STOP"

    @staticmethod
    def key(endpoint,method,params):
        return hashlib.sha256(dumps([endpoint,method,params]).encode()).hexdigest()

    def _cache(self,key,ttl):
        with self.store.lock:
            row=self.store.db.execute("SELECT data,created FROM cache WHERE key=?",(key,)).fetchone()
        if row and (ttl is None or time.time()-row[1]<ttl):
            self.cache_hits+=1
            return True,json.loads(zlib.decompress(row[0]))
        return False,None

    def _request(self,endpoint,method,params,ttl=None):
        key=self.key(endpoint,method,params)
        hit,value=self._cache(key,ttl)
        if hit: return value,key
        if self.offline:
            raise RpcError("Offline cache miss",endpoint=endpoint)
        disabled=self.store.get("endpoints",endpoint,{})
        if disabled.get("blocked_403"):
            raise RpcError("Endpoint disabled after HTTP 403",403,endpoint)
        if disabled.get("cooldown_until",0)>time.time():
            raise RpcError("Endpoint cooling down after rate limit",429,endpoint)
        if endpoint.startswith("https://public.1rpc.io/"):
            with self.store.lock:
                used=self.store.db.execute("SELECT count(*) FROM requests WHERE endpoint LIKE 'https://public.1rpc.io/%' AND substr(at,1,10)=?",(now()[:10],)).fetchone()[0]
            if used>=200:raise RpcError("1RPC public daily budget reached (200, UTC)",429,endpoint)
        with self.lock:
            rate_key="https://api.dexscreener.com" if method=="GET" else endpoint
            gate=self.gates.setdefault(rate_key,threading.Lock())
        for attempt in range(3):
            if self.stopped or self.stop_file.exists():
                self.stopped=True
                raise BudgetExceeded("Stop requested")
            with gate,self.global_gate:
                wait=self.last.get(rate_key,0)+self.interval-time.monotonic()
                if wait>0: time.sleep(wait)
                with self.lock:
                    if self.count>=self.budget:
                        raise BudgetExceeded(f"Request budget reached ({self.budget}); resume from cache.")
                    self.count+=1
                    request_id=self.count
                self.last[rate_key]=time.monotonic()
                start=time.monotonic()
                try:
                    headers={"Content-Type":"application/json","User-Agent":"Poker-Wallet-Forensics/0.1 (read-only)"}
                    payload=None if method=="GET" else dumps({"jsonrpc":"2.0","id":request_id,"method":method,"params":params}).encode()
                    req=urllib.request.Request(endpoint,payload,headers)
                    with urllib.request.urlopen(req,timeout=20) as response:
                        data=json.load(response)
                    if method!="GET" and "error" in data:
                        error=data["error"]
                        raise RpcError(str(error),error.get("code"),endpoint)
                    value=data if method=="GET" else data.get("result")
                    self.store.request(endpoint,method,params,"ok","",time.monotonic()-start)
                    # A null result is evidence of this request, not a permanent absence.
                    if value is not None:
                        with self.store.lock,self.store.db:
                            self.store.db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?)",
                              (key,endpoint,method,dumps(params),zlib.compress(dumps(value).encode()),time.time()))
                    return value,key
                except urllib.error.HTTPError as exc:
                    detail=f"HTTP {exc.code}: {exc.reason}"
                    exc.close()
                    self.store.request(endpoint,method,params,str(exc.code),detail,time.monotonic()-start)
                    if exc.code==403:
                        self.store.put("endpoints",endpoint,{"blocked_403":True,"at":now(),"method":method,"detail":detail})
                        raise RpcError(detail,403,endpoint) from exc
                    delay=retry_after(exc.headers.get("Retry-After")) if exc.code==429 else min(20,2**(attempt+1))
                    if exc.code==429 and attempt==2:
                        self.store.put("endpoints",endpoint,{"blocked_403":False,"cooldown_until":time.time()+max(60,delay),"at":now(),"detail":detail})
                    if exc.code not in (408,429,500,502,503,504) or attempt==2:
                        raise RpcError(detail,exc.code,endpoint) from exc
                except RpcError as exc:
                    self.store.request(endpoint,method,params,"rpc_error",str(exc),time.monotonic()-start)
                    raise
                except (OSError,ValueError) as exc:
                    self.store.request(endpoint,method,params,"transport_error",str(exc),time.monotonic()-start)
                    if attempt==2: raise RpcError(str(exc),endpoint=endpoint) from exc
                    delay=min(20,2**(attempt+1))
            # Respect full Retry-After outside the concurrency semaphore.
            end=time.monotonic()+delay
            while time.monotonic()<end:
                if self.stopped or self.stop_file.exists(): raise BudgetExceeded("Stop requested")
                time.sleep(min(1,end-time.monotonic()))
        raise RpcError("Retry exhausted",endpoint=endpoint)

    def rpc(self,chain,method,params,endpoint=None,ttl=None):
        if method not in READ_METHODS:
            raise ValueError(f"RPC method is not on the read-only allowlist: {method}")
        config=CHAINS[chain]
        errors=[]
        for url in [endpoint] if endpoint else config["rpc"]:
            try:
                if (chain,url) not in self.verified:
                    identity="getGenesisHash" if chain=="solana" else "eth_chainId"
                    ident,_=self._request(url,identity,[],ttl=3600)
                    expected=config.get("genesis") if chain=="solana" else config["id"]
                    actual=ident if chain=="solana" else int(ident,16)
                    if actual!=expected: raise RpcError(f"Wrong network: {actual} != {expected}",endpoint=url)
                    self.verified.add((chain,url))
                if ttl is None and any(x in dumps(params) for x in ('"latest"','"safe"','"finalized"')):
                    # getTransaction with commitment=finalized is immutable by signature.
                    ttl=30 if method not in ("getTransaction","getBlock") else None
                value,key=self._request(url,method,params,ttl)
                if value is None and method in {"eth_getBlockByNumber","eth_getBlockByHash","eth_getTransactionReceipt","eth_getTransactionByHash","getTransaction","getBlockTime","getBlock"}:
                    raise RpcError("Historical object unavailable (null)",endpoint=url)
                with self.lock:
                    self.provenance[dumps([chain,method,params])]=key
                return value
            except BudgetExceeded: raise
            except RpcError as exc:
                if exc.range_error and method=="eth_getLogs": raise
                errors.append(f"{url}: {exc}")
        raise RpcError("; ".join(errors))

    def evidence(self,chain,method,params):
        return self.provenance.get(dumps([chain,method,params]))

    def get(self,url,ttl=86400):
        # Requests only go to the documented discovery API, not arbitrary lore links.
        if not url.startswith("https://api.dexscreener.com/"):
            raise ValueError("GET URL not allowed")
        value,_=self._request(url,"GET",[],ttl)
        return value

    def probe(self,chain):
        results=[]
        for endpoint in CHAINS[chain]["rpc"]:
            checks=[("getGenesisHash",[]),("getSlot",[{"commitment":"finalized"}])] if chain=="solana" else [
                ("eth_chainId",[]),("eth_getBlockByNumber",["latest",False])]
            for method,params in checks:
                record={"chain":chain,"endpoint":endpoint,"method":method,"at":now(),"scope":"identity_or_head_only"}
                try:
                    value=self.rpc(chain,method,params,endpoint=endpoint,ttl=30)
                    if method=="eth_getBlockByNumber":
                        value={k:value.get(k) for k in ("number","timestamp","hash")}
                    record.update(status="ok",result=value)
                except BudgetExceeded: raise
                except Exception as exc: record.update(status="error",error=str(exc))
                self.store.put("probes",f"{chain}:{endpoint}:{method}",record)
                results.append(record)
                if record["status"]=="error": break
        return results
