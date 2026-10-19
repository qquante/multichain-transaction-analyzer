from __future__ import annotations
import concurrent.futures
import collections
import threading
import time
from .source import finalize_calls, put_call
from .net import BudgetExceeded
from .evm import EVMCollector
from .solana import SolanaCollector
from .storage import now

def discover(store,net,chains=None):
    errors=[]
    # Resolve pool URLs first; retain the explicit base-token assumption in the call.
    for ref in store.all("pool_refs"):
        if chains and ref["chain"] not in chains:continue
        try:
            result=net.get(f"https://api.dexscreener.com/latest/dex/pairs/{ref['chain']}/{ref['value']}")
            pairs=result.get("pairs") or []
            m=store.get("messages",ref["message_id"])
            for pair in pairs:
                if pair.get("chainId")!=ref["chain"]:continue
                token=pair["baseToken"]["address"]
                if ref["chain"]!="solana":token=token.lower()
                put_call(store,m,ref["chain"],token,"dex_pair_base_token_assumption")
                store.put("pool_resolution",f"{ref['message_id']}:{ref['value']}",{"reference":ref,"pair":pair})
        except BudgetExceeded:raise
        except Exception as exc:
            errors.append({"ref":ref,"error":str(exc)})
    grouped=collections.defaultdict(set)
    for c in store.all("calls"):
        if chains and c["chain"] not in chains:continue
        grouped[c["chain"]].add(c["token"])
    done=0
    for chain,tokens in grouped.items():
        tokens=sorted(tokens)
        for index in range(0,len(tokens),30):
            chunk=tokens[index:index+30]
            try:
                pairs=net.get(f"https://api.dexscreener.com/tokens/v1/{chain}/"+",".join(chunk))
                if not isinstance(pairs,list):raise ValueError("Unexpected DEX Screener response")
                for token in chunk:
                    matches=[]
                    for p in pairs:
                        if p.get("chainId")!=chain:continue
                        a=p.get("baseToken",{}).get("address","")
                        b=p.get("quoteToken",{}).get("address","")
                        if chain!="solana":a,b=a.lower(),b.lower()
                        if token in (a,b):matches.append(p)
                    previous=store.get("pairs",f"{chain}:{token}",{})
                    if previous.get("expanded_token_pairs"):
                        matches=list({p["pairAddress"]:p for p in previous.get("pairs",[])+matches}.values())
                    store.put("pairs",f"{chain}:{token}",{"chain":chain,"token":token,"pairs":matches,
                       "expanded_token_pairs":previous.get("expanded_token_pairs",False),
                       "source":"DEX Screener public API","at":now(),"discovery_exhaustive":False})
                    done+=1
            except BudgetExceeded:raise
            except Exception as exc:
                errors.append({"chain":chain,"tokens":chunk,"error":str(exc)})
    finalize_calls(store)
    result={"at":now(),"tokens_queried":done,"errors":errors,"status":"complete_requests" if not errors else "partial",
            "historical_pool_discovery_complete":False}
    store.put("meta","discovery",result)
    return result

def ensure_pairs(store,net,chain,token):
    key=f"{chain}:{token}"
    current=store.get("pairs",key,{})
    if current.get("expanded_token_pairs"):return current
    pairs=net.get(f"https://api.dexscreener.com/token-pairs/v1/{chain}/{token}")
    if not isinstance(pairs,list):raise ValueError("Unexpected token-pairs response")
    merged={p["pairAddress"]:p for p in current.get("pairs",[])}
    for p in pairs:
        if p.get("chainId")==chain:merged[p["pairAddress"]]=p
    current.update(chain=chain,token=token,pairs=list(merged.values()),expanded_token_pairs=True,
        discovery_exhaustive=False,source="DEX Screener token-pairs + retained earlier discovery",at=now())
    store.put("pairs",key,current)
    return current

def selected_calls(store,chains=None,messages=None,max_calls=0,include_updates=False):
    calls=[]
    for c in store.all("calls"):
        if chains and c["chain"] not in chains:continue
        if messages:
            if c["message_id"] not in messages:continue
        elif not c["primary"] and not (include_updates and c["classification"] in ("add","sell")):
            continue
        calls.append(c)
    calls.sort(key=lambda c:(c["t0"],c["message_id"],c["id"]))
    return calls[:max_calls] if max_calls else calls

def collect(store,net,calls,windows=("main",),tx_limit=100,page_limit=20,progress=print):
    grouped=collections.defaultdict(list)
    for call in calls:grouped[call["chain"]].append(call)
    results=[];errors=[];snap_lock=threading.Lock();last_snapshot=[time.monotonic()]
    def snapshot():
        if time.monotonic()-last_snapshot[0]<60:return
        with snap_lock:
            if time.monotonic()-last_snapshot[0]<60:return
            from .ranking import rank
            from .report import export_report
            with store.lock:
                rank(store);export_report(store,store.path.parent)
            last_snapshot[0]=time.monotonic()
    def run_chain(item):
        chain,chain_calls=item
        collector=SolanaCollector(store,net) if chain=="solana" else EVMCollector(store,net,chain)
        out=[]
        for call in chain_calls:
            try:
                ensure_pairs(store,net,chain,call["token"])
            except BudgetExceeded:return out
            except Exception as exc:
                store.put("issues",f"pairs:{call['id']}",{"call_id":call["id"],"chain":chain,"kind":"pool_discovery_error","error":str(exc)})
            for window in windows:
                if window=="edit" and not call["late_edit"]:continue
                if window.startswith("control"):
                    a,b=(call["t0"]-1800,call["t0"]-1500) if window=="control_before" else (call["t0"]+900,call["t0"]+1200)
                    overlaps=any(c["id"]!=call["id"] and c["token"]==call["token"] and
                      c["classification"] in ("entry","add") and a<c["t0"]+1 and b>c["t0"]-300 for c in store.all("calls") if c["chain"]==chain)
                    if overlaps:
                        record={"id":call["id"]+":"+window,"call_id":call["id"],"chain":chain,"token":call["token"],
                          "window":window,"state":"excluded_overlapping_call","updated":now()}
                        store.put("coverage",record["id"],record);out.append(record);continue
                try:
                    kwargs={"tx_limit":tx_limit}
                    if chain=="solana":kwargs["page_limit"]=page_limit
                    record=collector.collect(call,window,**kwargs)
                    out.append(record)
                    snapshot()
                    progress(f"{chain} msg={call['message_id']} {window}: {record['state']} "
                             f"tx={record.get('transactions_processed',0)}/{record.get('transactions','?')}")
                except BudgetExceeded:
                    return out
                if net.stopped:return out
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,len(grouped))) as executor:
        futures=[executor.submit(run_chain,item) for item in grouped.items()]
        for f in concurrent.futures.as_completed(futures):
            try:results.extend(f.result())
            except Exception as exc:errors.append(f"{type(exc).__name__}: {exc}")
    summary={"at":now(),"jobs_attempted":len(results),"states":dict(collections.Counter(r["state"] for r in results)),
             "requests":net.count,"cache_hits":net.cache_hits,"errors":errors,"budget":net.budget}
    store.put("meta","last_collection",summary)
    return summary
