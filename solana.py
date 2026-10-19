from __future__ import annotations
DECODER_VERSION=4
import collections
import hashlib
from decimal import Decimal
from .source import b58decode, BASE58
from .storage import utc, now, units
from .net import BudgetExceeded, RpcError, CHAINS

PUMP="6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PROGRAMS={
 PUMP:"pump",
 "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA":"pumpswap",
 "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK":"raydium_clmm",
 "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C":"raydium_cpmm",
 "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8":"raydium_amm_v4",
 "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG":"meteora_damm_v2"}
TOKEN_PROGRAMS={"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA","TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"}
WSOL="So11111111111111111111111111111111111111112"
INSTRUCTIONS={hashlib.sha256(("global:"+n).encode()).digest()[:8]:n for n in (
 "swap","swap_v2","swap_base_input","swap_base_output","buy","sell","buy_exact_sol_in",
 "buy_v2","sell_v2","buy_exact_quote_in_v2","buy_exact_quote_in")}

def b58encode(raw):
    n=int.from_bytes(raw,"big");s=""
    while n: n,r=divmod(n,58);s=BASE58[r]+s
    return "1"*(len(raw)-len(raw.lstrip(b"\0")))+s

def on_curve(raw):
    p=2**255-19;y=int.from_bytes(raw,"little")&((1<<255)-1)
    if y>=p:return False
    d=(-121665*pow(121666,-1,p))%p
    denominator=(d*y*y+1)%p
    if not denominator:return False
    x2=((y*y-1)*pow(denominator,-1,p))%p
    return x2==0 or pow(x2,(p-1)//2,p)==1

def pda(seeds,program):
    for bump in range(255,-1,-1):
        raw=hashlib.sha256(b"".join(seeds)+bytes([bump])+b58decode(program)+b"ProgramDerivedAddress").digest()
        if not on_curve(raw):return b58encode(raw)
    raise ValueError("No program address bump")

def account_keys(transaction):
    msg=transaction["transaction"]["message"]
    values=msg.get("accountKeys",[])
    if values and isinstance(values[0],dict):
        keys=[x["pubkey"] for x in values]
        signers={x["pubkey"] for x in values if x.get("signer")}
    else:
        keys=list(values)
        signers=set(keys[:msg.get("header",{}).get("numRequiredSignatures",0)])
        loaded=transaction.get("meta",{}).get("loadedAddresses",{})
        keys+=loaded.get("writable",[])+loaded.get("readonly",[])
    return keys,signers

def instruction_rows(transaction,keys):
    outer=transaction["transaction"]["message"].get("instructions",[])
    instructions=[(i,None,x) for i,x in enumerate(outer)]
    for group in transaction.get("meta",{}).get("innerInstructions",[]) or []:
        instructions.extend((group["index"],i,x) for i,x in enumerate(group["instructions"]))
    output=[]
    for parent,inner,ins in instructions:
        program=ins.get("programId")
        if program is None and "programIdIndex" in ins: program=keys[ins["programIdIndex"]]
        accounts=[keys[x] if isinstance(x,int) else x for x in ins.get("accounts",[])]
        name=None
        if program in PROGRAMS:
            try:
                data=b58decode(ins.get("data",""))
                if PROGRAMS[program]=="raydium_amm_v4" and data and data[0] in (9,11):
                    name="swap_base_in" if data[0]==9 else "swap_base_out"
                else:name=INSTRUCTIONS.get(data[:8])
            except (ValueError,IndexError):pass
        output.append({"program":program,"protocol":PROGRAMS.get(program),"name":name,
                       "accounts":accounts,"outer_index":parent,"inner_index":inner})
    return output

def analyze_transaction(value,target):
    meta=value.get("meta") or {}
    if meta.get("err") is not None:return [],{"reason":"failed_transaction"}
    keys,signers=account_keys(value)
    instructions=instruction_rows(value,keys)
    pre=meta.get("preTokenBalances",[]) or []
    post=meta.get("postTokenBalances",[]) or []
    # Key by token account index; owner may be omitted on one side for creation/closure.
    before={r["accountIndex"]:r for r in pre};after={r["accountIndex"]:r for r in post}
    ledger=collections.defaultdict(collections.Counter);decimals={};target_accounts=set();owner_accounts=collections.defaultdict(set);missing_owners=0
    for index in set(before)|set(after):
        a=before.get(index,{});b=after.get(index,{})
        mint=b.get("mint") or a.get("mint")
        owner=b.get("owner") or a.get("owner")
        if not owner:
            missing_owners+=1;continue
        if a.get("mint") and b.get("mint") and a["mint"]!=b["mint"]:
            continue
        ai=a.get("uiTokenAmount",{});bi=b.get("uiTokenAmount",{})
        amount=int(bi.get("amount",0))-int(ai.get("amount",0))
        ledger[owner][mint]+=amount
        decimals[mint]=bi.get("decimals",ai.get("decimals"))
        if mint==target and index<len(keys):
            target_accounts.add(keys[index]);owner_accounts[owner].add(keys[index])
    swaps=[x for x in instructions if x["name"] and target_accounts.intersection(x["accounts"])]
    out=[]
    for owner,balances in ledger.items():
        delta=balances[target]
        if not delta or owner not in signers:continue
        owner_swaps=[x for x in swaps if owner_accounts[owner].intersection(x["accounts"])]
        verified=bool(owner_swaps)
        other=[(mint,n) for mint,n in balances.items() if mint!=target and n*delta<0]
        quote=None;qraw=None;qd=None;basis=None
        if len(other)==1:
            quote,n=other[0];qraw=str(abs(n));qd=decimals.get(quote);basis="wallet_token_net_flow"
        elif owner in keys:
            index=keys.index(owner);pb=meta.get("preBalances",[]);ab=meta.get("postBalances",[])
            if index<len(pb) and index<len(ab):
                native=ab[index]-pb[index]
                if native*delta<0:
                    quote="SOL";qraw=str(abs(native));qd=9;basis="native_balance_change_includes_fees_and_rent"
        td=decimals.get(target)
        out.append({"wallet":owner,"side":("buy" if delta>0 else "sell") if verified else ("receive" if delta>0 else "send"),
          "token_raw":str(abs(delta)),"token_net_raw":str(delta),"token_decimals":td,
          "token_amount":units(abs(delta),td) if td is not None else None,
          "quote_token":quote,"quote_raw":qraw,"quote_decimals":qd,
          "quote_amount":units(qraw,qd) if qraw and qd is not None else None,
          "quote_basis":basis,"classification":"reconciled_swap" if verified else "transfer_only",
          "rank_eligible":verified,"actor_kind":"solana_signer_token_owner","signers":sorted(signers),
          "fee_payer":keys[0] if keys else None,"protocols":sorted({x["protocol"] for x in owner_swaps}),
          "instructions":[{k:x[k] for k in ("program","name","outer_index","inner_index")} for x in owner_swaps],
          "ownership_confirmed":False,"usd_value":None})
    return out,{"reason":"decoded" if swaps else "unknown_protocol_or_transfer","missing_token_owners":missing_owners,
                "unrecognized_programs":sorted({x["program"] for x in instructions if not x["protocol"] and x["program"] not in TOKEN_PROGRAMS})}

class SolanaCollector:
    def __init__(self,store,net,chain="solana"):
        self.store,self.net,self.chain=store,net,chain
    def rpc(self,method,params,**kw):return self.net.rpc("solana",method,params,**kw)

    def history_anchor(self,end):
        """Start before a real signature strictly after the window (Agave cursor semantics)."""
        cached=self.store.get("sol_anchors",str(end))
        if cached:return cached
        target=end+120
        hi=self.rpc("getSlot",[{"commitment":"finalized"}])
        def timed(slot):
            try:return slot,self.rpc("getBlockTime",[slot])
            except BudgetExceeded:raise
            except RpcError:
                produced=self.rpc("getBlocks",[slot,slot+64,{"commitment":"finalized"}])
                if not produced:raise RpcError("No produced slot near anchor probe")
                return produced[0],self.rpc("getBlockTime",[produced[0]])
        hi,ht=timed(hi)
        if ht<target:raise RpcError("Window too recent for finalized history anchor")
        step=max(10000,int((ht-target)*3))
        lo,lt=timed(max(0,hi-step))
        for _ in range(8):
            if lt<target:break
            step*=2;lo,lt=timed(max(0,hi-step))
        if lt>=target:raise RpcError("Cannot bracket Solana historical time")
        for _ in range(32):
            if hi-lo<=8 or target<=ht<=target+15:break
            guess=max(lo+1,min(hi-1,lo+int((target-lt)*(hi-lo)/max(1,ht-lt))))
            slot,stamp=timed(guess)
            if slot>=hi:break
            if stamp<target:lo,lt=slot,stamp
            else:hi,ht=slot,stamp
        params=[hi,{"encoding":"json","transactionDetails":"signatures","rewards":False,
                    "maxSupportedTransactionVersion":0,"commitment":"finalized"}]
        block=self.rpc("getBlock",params)
        if not block or not block.get("signatures") or (block.get("blockTime") or 0)<end+60:
            raise RpcError("Invalid historical cursor block")
        result={"slot":hi,"timestamp":block["blockTime"],"before":block["signatures"][0],
                "blockhash":block["blockhash"],"window_end":end,"evidence_key":self.net.evidence("solana","getBlock",params),
                "semantics":"Agave before cursor locates the transaction slot; anchor is strictly after the window"}
        self.store.put("sol_anchors",str(end),result)
        return result

    def signatures(self,source,start,end,job,page_limit,anchor=None):
        key=f"{job}:{source}"
        progress=self.store.get("sol_pages",key,{"source":source,"before":None,"pages":0,"signatures":{},"state":"partial"})
        if progress["state"]=="reached_start":return progress
        if anchor and not progress.get("anchor") and (not progress["before"] or progress.get("oldest_timestamp",0)>anchor["timestamp"]):
            progress["previous_cursor"]=progress["before"]
            progress["before"]=anchor["before"];progress["anchor"]=anchor
            progress["state"]="partial"
        for _ in range(page_limit):
            config={"limit":1000,"commitment":"finalized"}
            if progress["before"]:config["before"]=progress["before"]
            params=[source,config]
            page=self.rpc("getSignaturesForAddress",params,ttl=86400)
            if page is None:raise RpcError("Null signature page")
            if not page:
                progress["state"]="history_exhausted_unverified";break
            if progress["before"]==page[-1]["signature"]:
                raise RpcError("Signature pagination did not advance")
            times=[]
            for row in page:
                stamp=row.get("blockTime")
                if stamp is None:
                    progress.setdefault("unknown_time",[]).append(row["signature"]);continue
                times.append(stamp)
                if start<=stamp<end:
                    progress["signatures"][row["signature"]]=row
            progress["before"]=page[-1]["signature"];progress["pages"]+=1
            if times:
                progress["oldest_timestamp"]=min(times)
                if min(times)<start:progress["state"]="reached_start"
            self.store.put("sol_pages",key,progress)
            if progress["state"]=="reached_start":break
        self.store.put("sol_pages",key,progress)
        return progress

    def collect(self,call,window="main",tx_limit=100,page_limit=20):
        t0=call["t0"]
        spans={"main":(t0-300,t0+1),"control_before":(t0-1800,t0-1500),
               "control_after":(t0+900,t0+1200),"edit":((call.get("edited") or t0)-300,(call.get("edited") or t0)+1)}
        start,end=spans[window];job=f"{call['id']}:{window}"
        coverage=self.store.get("coverage",job,{})
        current_pool_keys=sorted(p.get("pairAddress","") for p in self.store.get("pairs",f"solana:{call['token']}",{}).get("pairs",[]))
        if coverage.get("state")=="complete_scope" and coverage.get("decoder_version")==DECODER_VERSION and coverage.get("pool_keys")==current_pool_keys:return coverage
        coverage.update(id=job,call_id=call["id"],chain="solana",token=call["token"],window=window,
          start_utc=utc(start),end_utc=utc(end),state="running",updated=now(),
          pool_discovery_exhaustive=False,source_independently_verified=False,ownership_confirmed=False)
        coverage.pop("error",None)
        self.store.put("coverage",job,coverage)
        try:
            pairs=self.store.get("pairs",f"solana:{call['token']}",{}).get("pairs",[])
            sources={p["pairAddress"] for p in pairs}
            sources.add(call["token"]) # supplementary only; mint history alone is not complete trade history.
            if call["token"].endswith("pump"):
                for seed in (b"bonding-curve",b"bonding-curve-v2"):
                    sources.add(pda([seed,b58decode(call["token"])],PUMP))
            signatures={};states=[];anchor=None
            try:anchor=self.history_anchor(end)
            except BudgetExceeded:raise
            except (RpcError,ValueError) as exc:
                coverage["anchor_error"]=str(exc)
            for source in sorted(sources):
                progress=self.signatures(source,start,end,job,page_limit,anchor)
                signatures.update(progress["signatures"])
                states.append({"source":source,"state":progress["state"],"pages":progress["pages"],
                               "oldest_timestamp":progress.get("oldest_timestamp"),"unknown_time":len(progress.get("unknown_time",[]))})
            coverage["sources"]=states
            processed=0
            for signature,row in sorted(signatures.items(),key=lambda item:(item[1]["blockTime"],item[0])):
                done_key=f"{job}:{signature}"
                if (self.store.get("tx_done",done_key) or {}).get("decoder_version")==DECODER_VERSION:continue
                if row.get("err") is not None:
                    self.store.put("tx_done",done_key,{"job":job,"hash":signature,"rows":0,
                      "diagnostic":{"reason":"failed_transaction","source":"getSignaturesForAddress err","error":row["err"]},
                      "decoder_version":DECODER_VERSION})
                    continue
                if processed>=tx_limit:break
                params=[signature,{"commitment":"finalized","encoding":"jsonParsed","maxSupportedTransactionVersion":0}]
                tx=self.rpc("getTransaction",params)
                if tx is None:
                    self.store.put("gaps",done_key,{"job":job,"hash":signature,"reason":"transaction_unavailable"});continue
                if tx.get("meta") is None:
                    self.store.put("gaps",done_key,{"job":job,"hash":signature,"reason":"transaction_metadata_missing"});continue
                if signature not in tx.get("transaction",{}).get("signatures",[]):
                    raise ValueError("Solana transaction signature mismatch")
                stamp=tx.get("blockTime")
                if stamp is None:
                    self.store.put("gaps",done_key,{"job":job,"hash":signature,"reason":"block_time_unknown"});continue
                if not start<=stamp<end:raise ValueError("Solana transaction outside requested time window")
                rows,diagnostic=analyze_transaction(tx,call["token"])
                for trade in rows:
                    trade.update(id=f"{job}:{signature}:{trade['wallet']}",call_id=call["id"],message_id=call["message_id"],
                      chain="solana",token=call["token"],window=window,tx_hash=signature,slot=tx["slot"],
                      timestamp=stamp,utc=utc(stamp),seconds_before_call=t0-stamp,same_second=stamp==t0,
                      late_edit=call["late_edit"],timing_uncertain=call["timing_uncertain"],block_time_estimated=True,
                      evidence_keys=[self.net.evidence("solana","getTransaction",params)],explorer_url="https://solscan.io/tx/"+signature)
                    trade["rank_eligible"]=trade["rank_eligible"] and not trade["same_second"]
                self.store.replace_prefix("trades",f"{job}:{signature}:",rows)
                self.store.put("tx_done",done_key,{"job":job,"hash":signature,"rows":len(rows),"diagnostic":diagnostic,"decoder_version":DECODER_VERSION})
                processed+=1
            coverage["pool_keys"]=current_pool_keys
            done=sum((self.store.get("tx_done",f"{job}:{h}") or {}).get("decoder_version")==DECODER_VERSION for h in signatures)
            history_ok=all(x["state"]=="reached_start" and x["unknown_time"]==0 for x in states)
            coverage.update(state="complete_scope" if history_ok and done==len(signatures) else "partial",
              transactions=len(signatures),transactions_processed=done,history_boundary_reached=history_ok,
              completeness_note="Discovered accounts only; mint query is supplementary. Empty history is not proof of no trades.")
        except BudgetExceeded as exc:
            coverage.update(state="partial",error=str(exc));self.store.put("coverage",job,coverage);raise
        except Exception as exc:
            coverage.update(state="error",error=f"{type(exc).__name__}: {exc}")
        coverage["decoder_version"]=DECODER_VERSION
        coverage["updated"]=now();self.store.put("coverage",job,coverage)
        return coverage
