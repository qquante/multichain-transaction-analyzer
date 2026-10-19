"""Offline checks on collected evidence and the original input; no network calls."""
from pathlib import Path
import hashlib
import json
import math
import zlib
from .storage import now
from .evm import DECODER_VERSION

def verify(store,input_path):
    errors=[]
    source=store.get("meta","source",{})
    actual=hashlib.sha256(Path(input_path).read_bytes()).hexdigest()
    if actual!=source.get("sha256"):errors.append("Input hash differs from audited source")
    if store.integrity()!="ok":errors.append("SQLite integrity failure")
    calls={c["id"]:c for c in store.all("calls")}
    primary=[c for c in calls.values() if c["primary"]]
    if len({(c["chain"],c["token"]) for c in primary})!=len(primary):
        errors.append("Duplicate primary chain/token")
    if sum(c["split"]=="discovery" for c in primary)!=math.ceil(len(primary)*.7):
        errors.append("Unexpected chronological split")
    checked=0;cache_keys=set()
    for trade in store.all("trades"):
        c=calls.get(trade["call_id"])
        if not c:
            errors.append("Trade without source call: "+trade["id"]);continue
        t0=c["t0"];window=trade["window"]
        spans={"main":(t0-300,t0+1),"control_before":(t0-1800,t0-1500),
               "control_after":(t0+900,t0+1200),"edit":((c.get("edited") or t0)-300,(c.get("edited") or t0)+1)}
        start,end=spans[window]
        if not start<=trade["timestamp"]<end:errors.append("Trade outside window: "+trade["id"])
        if trade["seconds_before_call"]!=t0-trade["timestamp"]:errors.append("Incorrect time delta: "+trade["id"])
        if trade["rank_eligible"] and trade["same_second"]:errors.append("Ambiguous second ranked: "+trade["id"])
        if trade["rank_eligible"] and trade["classification"]!="reconciled_swap":errors.append("Unresolved trade ranked: "+trade["id"])
        if trade.get("ownership_confirmed"):errors.append("Unsupported ownership assertion")
        for key in trade.get("evidence_keys",[]):
            if key:cache_keys.add(key)
            else:errors.append("Missing evidence key: "+trade["id"])
        checked+=1
    for key in cache_keys:
        with store.lock:row=store.db.execute("SELECT data FROM cache WHERE key=?",(key,)).fetchone()
        if not row:errors.append("Evidence cache missing: "+key);continue
        try:json.loads(zlib.decompress(row[0]))
        except (ValueError,zlib.error):errors.append("Invalid evidence cache: "+key)
    for cov in store.all("coverage"):
        if cov["state"]=="complete_scope" and cov.get("decoder_version")==DECODER_VERSION:
            if cov.get("transactions_processed")!=cov.get("transactions"):errors.append("Incomplete window marked complete: "+cov["id"])
    for candidate in store.all("candidates"):
        if candidate.get("ownership_confirmed"):errors.append("Candidate ownership asserted")
    result={"at":now(),"state":"passed" if not errors else "failed","sha256":actual,
            "calls":len(calls),"primary_calls":len(primary),"trade_rows_checked":checked,
            "evidence_cache_entries_checked":len(cache_keys),"sqlite_integrity":store.integrity(),"errors":errors}
    store.put("meta","verification",result)
    return result
