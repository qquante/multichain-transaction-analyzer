from __future__ import annotations
DECODER_VERSION=4
import collections
import math
from decimal import Decimal
from Crypto.Hash import keccak
from .net import BudgetExceeded, CHAINS, RpcError
from .storage import utc, now, units
ZERO="0x"+"0"*40
DEAD="0x"+"0"*36+"dead"

def topic(signature):
    k=keccak.new(digest_bits=256);k.update(signature.encode());return "0x"+k.hexdigest()
TRANSFER=topic("Transfer(address,address,uint256)")
V2=topic("Swap(address,uint256,uint256,uint256,uint256,address)")
V3=topic("Swap(address,address,int256,int256,uint160,uint128,int24)")
V4=topic("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
INITIALIZE=topic("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)")
USEROP=topic("UserOperationEvent(bytes32,address,address,uint256,bool,uint256,uint256)")
ENTRYPOINTS={
 "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789",
 "0x0000000071727de22e5e9d8baf0edac6f37da032",
 "0x4337084d9e255ff0702461cf8895ce9e3b5ff108"}
SWAPS=[V2,V3,V4]
def word_address(value): return "0x"+value[-40:].lower()
def words(data):
    data=data.removeprefix("0x")
    if len(data)%64: raise ValueError("Malformed ABI data")
    return [int(data[i:i+64],16) for i in range(0,len(data),64)]
def signed(value,bits=256):
    # ABI signed integers are sign extended to 256 bits.
    return value-(1<<256) if value & (1<<255) else value

def decode_swap(log):
    ts=log.get("topics",[])
    if not ts: return None
    try:
        w=words(log.get("data","0x"));kind=ts[0].lower()
        common={"emitter":log["address"].lower(),"log_index":int(log.get("logIndex","0x0"),16),"block_number":int(log.get("blockNumber","0x0"),16)}
        if kind==V2 and len(w)==4 and len(ts)==3:
            return dict(common,protocol="v2",delta0=w[0]-w[2],delta1=w[1]-w[3],pool_id=None,
                        sender=word_address(ts[1]),recipient=word_address(ts[2]))
        if kind==V3 and len(w)==5 and len(ts)==3:
            return dict(common,protocol="v3",delta0=signed(w[0]),delta1=signed(w[1]),pool_id=None,
                        sender=word_address(ts[1]),recipient=word_address(ts[2]))
        if kind==V4 and len(w)==6 and len(ts)==3:
            # v4 emits caller deltas; normalize to pool deltas (positive = pool receives).
            return dict(common,protocol="v4",delta0=-signed(w[0]),delta1=-signed(w[1]),pool_id=ts[1].lower(),
                        sender=word_address(ts[2]),recipient=None)
    except (KeyError,ValueError):
        return None
    return None

def decode_initialize(log):
    ts=log.get("topics",[])
    if len(ts)!=4 or ts[0].lower()!=INITIALIZE:return None
    w=words(log.get("data","0x"))
    if len(w)!=5:return None
    a,b=word_address(ts[2]),word_address(ts[3])
    encoded=bytes.fromhex(a[2:].rjust(64,"0")+b[2:].rjust(64,"0"))+b"".join(x.to_bytes(32,"big") for x in w[:3])
    k=keccak.new(digest_bits=256);k.update(encoded)
    if "0x"+k.hexdigest()!=ts[1].lower():raise ValueError("Initialize PoolKey hash mismatch")
    return {"manager":log["address"].lower(),"pool_id":ts[1].lower(),"currencies":[a,b],
        "fee":w[0],"tick_spacing":signed(w[1]),"hooks":"0x"+f"{w[2]:040x}",
        "pool_id_hash_verified":True,"source":"Initialize event + keccak256 PoolKey",
        "initialization_tx":log["transactionHash"],"initialization_block":int(log["blockNumber"],16)}

def transfers(receipt):
    rows=[]
    for l in receipt.get("logs",[]):
        ts=l.get("topics",[])
        if len(ts)==3 and ts[0].lower()==TRANSFER and len(l.get("data",""))==66:
            rows.append({"token":l["address"].lower(),"from":word_address(ts[1]),
                         "to":word_address(ts[2]),"raw":int(l["data"],16),"log_index":int(l["logIndex"],16)})
    return rows

def analyze_receipt(tx,receipt,target,pool_tokens):
    """No inference from tx.from alone: reconcile target flows and known swap assets."""
    if int(receipt.get("status","0x0"),16)!=1:
        return [],{"reason":"failed_transaction","swaps":0}
    if tx.get("hash","").lower()!=receipt.get("transactionHash","").lower():
        raise ValueError("Transaction / receipt hash mismatch")
    target=target.lower()
    events=transfers(receipt)
    ledger=collections.defaultdict(lambda:collections.Counter())
    for event in events:
        ledger[event["from"]][event["token"]]-=event["raw"]
        ledger[event["to"]][event["token"]]+=event["raw"]
    swaps=[s for l in receipt.get("logs",[]) if (s:=decode_swap(l))]
    related=[]
    infrastructure={ZERO,DEAD,target}
    for swap in swaps:
        infrastructure.add(swap["emitter"])
        infrastructure.add(swap["sender"])
        currencies=pool_tokens.get((swap["emitter"],swap["pool_id"]) if swap["pool_id"] else swap["emitter"])
        if currencies and target in currencies:
            swap["currencies"]=currencies
            related.append(swap)
    sender=tx.get("from","").lower()
    actors={sender}
    for l in receipt.get("logs",[]):
        ts=l.get("topics",[])
        if l["address"].lower() in ENTRYPOINTS and len(ts)==4 and ts[0].lower()==USEROP:
            op_words=words(l.get("data","0x"))
            if len(op_words)==4 and op_words[1]==1:
                actors.add(word_address(ts[2]))
    destination=tx.get("to")
    if destination and destination.lower() not in actors: infrastructure.add(destination.lower())
    infrastructure.difference_update(actors)
    output=[]
    for wallet,balances in ledger.items():
        delta=balances[target]
        if not delta or wallet in infrastructure: continue
        direction="buy" if delta>0 else "sell"
        directional=[s for s in related if (s["delta0"] if s["currencies"][0]==target else s["delta1"])*delta<0]
        verified=bool(directional and wallet in actors)
        # Keep recipients separate from actual signers / smart accounts.
        level="reconciled_swap" if verified else ("swap_beneficiary_unresolved" if related else "transfer_only")
        quote=None;quote_raw=None;basis=None
        active_actors=[a for a in actors if ledger.get(a,{}).get(target,0)]
        if len(related)==1 and len(active_actors)==1:
            s=related[0];a,b=s["currencies"]
            quote=b if a==target else a
            actor_quote=balances.get(quote,0)
            if actor_quote and actor_quote*delta<0:
                quote_raw=str(abs(actor_quote));basis="wallet_token_net_flow"
            else:
                quote_raw=str(abs(s["delta1"] if a==target else s["delta0"]))
                basis="pool_swap_amount_not_wallet_total"
        output.append({"wallet":wallet,"side":direction if related else ("receive" if delta>0 else "send"),
            "token_raw":str(abs(delta)),"token_net_raw":str(delta),"quote_token":quote,"quote_raw":quote_raw,
            "quote_basis":basis,"classification":level,"rank_eligible":verified,
            "actor_kind":"transaction_signer" if wallet==sender else ("erc4337_sender" if wallet in actors else "unresolved_recipient"),
            "tx_sender":sender,"tx_to":destination,"protocols":sorted({s["protocol"] for s in related}),
            "swap_log_indices":[s["log_index"] for s in related],
            "swap_pools":[{"manager":s["emitter"],"pool_id":s["pool_id"],"protocol":s["protocol"]} for s in related],
            "ownership_confirmed":False,"usd_value":None})
    return output,{"reason":"decoded" if related else "unknown_protocol_or_transfer","swaps":len(related),
                   "unknown_swap_emitters":sorted({s["emitter"] for s in swaps if s not in related})}

class EVMCollector:
    def __init__(self,store,net,chain):
        self.store,self.net,self.chain=store,net,chain

    def rpc(self,method,params,**kwargs):
        return self.net.rpc(self.chain,method,params,**kwargs)

    def block(self,n):
        value=self.rpc("eth_getBlockByNumber",[hex(n) if isinstance(n,int) else n,False])
        if not value: raise RpcError(f"Historical block unavailable: {n}")
        self.store.put("block_headers",f"{self.chain}:{int(value['number'],16)}",{
            "chain":self.chain,"number":int(value["number"],16),"timestamp":int(value["timestamp"],16),"hash":value["hash"]})
        return value

    def first_at(self,target):
        saved=self.store.get("boundaries",f"{self.chain}:{target}")
        if saved: return saved["number"]
        anchors=[b for b in self.store.all("block_headers") if b["chain"]==self.chain]
        lower=[b for b in anchors if b["timestamp"]<target]
        upper=[b for b in anchors if b["timestamp"]>=target]
        if upper:
            u=min(upper,key=lambda b:b["number"]);hi,ht=u["number"],u["timestamp"]
        else:
            head=self.block("latest");hi=int(head["number"],16);ht=int(head["timestamp"],16)
        if target>ht: raise RpcError("Requested window is newer than the chain head")
        if lower:
            l=max(lower,key=lambda b:b["number"]);lo,lt=l["number"],l["timestamp"]
        else:
            step=10000;lo=max(0,hi-step);lb=self.block(lo);lt=int(lb["timestamp"],16)
            while lt>=target and lo>0:
                hi,ht=lo,lt;step*=2;lo=max(0,lo-step);lb=self.block(lo);lt=int(lb["timestamp"],16)
        if lo==0 and lt>=target:
            answer=0
        else:
            # Interpolation with a strict shrinking bracket; handles repeated timestamps.
            while hi-lo>1:
                guess=lo+int((target-lt)*(hi-lo)/max(1,ht-lt))
                mid=max(lo+1,min(hi-1,guess))
                mb=self.block(mid);mt=int(mb["timestamp"],16)
                if mt<target:lo,lt=mid,mt
                else:hi,ht=mid,mt
            answer=hi
        check=self.block(answer)
        if int(check["timestamp"],16)<target: raise ValueError("Invalid timestamp lower bound")
        self.store.put("boundaries",f"{self.chain}:{target}",{"number":answer,"target":target,"hash":check["hash"],"timestamp":int(check["timestamp"],16)})
        return answer

    def logs(self,query,start,end):
        if start>end: return []
        result=[]
        size=CHAINS[self.chain]["chunk"]
        cursor=start
        while cursor<=end:
            stop=min(end,cursor+size-1)
            params=[dict(query,fromBlock=hex(cursor),toBlock=hex(stop))]
            try:
                part=self.rpc("eth_getLogs",params)
                if part is None: raise RpcError("Null logs result")
                if len(part)>=10000 and stop>cursor:
                    size=max(1,size//2);continue
                if len(part)>=10000:
                    raise RpcError("Potentially truncated single-block response")
            except RpcError as exc:
                if not isinstance(exc,BudgetExceeded) and exc.range_error and size>1:
                    size=max(1,size//2);continue
                raise
            for row in part:
                bn=int(row["blockNumber"],16)
                if not cursor<=bn<=stop:
                    raise ValueError("RPC returned a log outside the requested block range")
                if row.get("removed"): continue
                result.append(row)
            cursor=stop+1
        unique={(l["blockHash"],l["transactionHash"],l["logIndex"]):l for l in result}
        return sorted(unique.values(),key=lambda l:(int(l["blockNumber"],16),int(l["transactionIndex"],16),int(l["logIndex"],16)))

    def currencies(self,swaps,pairs):
        mapping={}
        by_id={p.get("pairAddress","").lower():p for p in pairs if p.get("chainId")==self.chain}
        for s in swaps:
            if s["protocol"]=="v4":
                bind_key=f"{self.chain}:{s['emitter']}:{s['pool_id']}"
                cached=self.store.get("v4_pools",bind_key)
                if cached and cached.get("pool_id_hash_verified"):
                    mapping[(s["emitter"],s["pool_id"])]=tuple(cached["currencies"])
                    continue
                if cached and cached.get("state")=="unresolved":continue
                try:
                    pair=by_id.get(s["pool_id"],{})
                    created=pair.get("pairCreatedAt")
                    if created:
                        a=self.first_at(created//1000-60);b=self.first_at(created//1000+61)-1
                    else:
                        b=s["block_number"];a=max(0,b-2000)
                    events=self.logs({"address":s["emitter"],"topics":[INITIALIZE,s["pool_id"]]},a,b)
                    for event in events:
                        binding=decode_initialize(event)
                        if binding:
                            self.store.put("v4_pools",bind_key,binding)
                            mapping[(s["emitter"],s["pool_id"])]=tuple(binding["currencies"])
                            break
                    if (s["emitter"],s["pool_id"]) not in mapping:
                        self.store.put("v4_pools",bind_key,{"state":"unresolved","manager":s["emitter"],
                            "pool_id":s["pool_id"],"range_checked":[a,b],"pool_id_hash_verified":False})
                except BudgetExceeded:raise
                except (RpcError,ValueError) as exc:
                    self.store.put("issues","v4:"+bind_key,{"chain":self.chain,"kind":"v4_binding_pending","value":s["pool_id"],"error":str(exc)})
                continue
            key=s["emitter"]
            cached=self.store.get("pool_tokens",f"{self.chain}:{key}")
            if cached:
                mapping[key]=tuple(cached["tokens"]);continue
            try:
                vals=[]
                for selector in ("0x0dfe1681","0xd21220a7"):
                    value=self.rpc("eth_call",[{"to":key,"data":selector},"latest"])
                    if not value or len(value)!=66:raise ValueError("Not a token0/token1 pool")
                    vals.append(word_address(value))
                if vals[0]==vals[1]:raise ValueError("Identical pool tokens")
                mapping[key]=tuple(vals)
                self.store.put("pool_tokens",f"{self.chain}:{key}",{"tokens":vals,"provenance":"token0/token1 at latest","pool":key})
            except BudgetExceeded:raise
            except (RpcError,ValueError):
                pass
        return mapping

    def decimals(self,token):
        if token==ZERO:return 18
        saved=self.store.get("decimals",f"{self.chain}:{token}")
        if saved is not None:return saved["decimals"]
        try:
            data=self.rpc("eth_call",[{"to":token,"data":"0x313ce567"},"latest"])
            n=int(data,16)
            if not 0<=n<=255:raise ValueError("Invalid decimals")
            self.store.put("decimals",f"{self.chain}:{token}",{"decimals":n,"source":"eth_call latest"})
            return n
        except BudgetExceeded:raise
        except (RpcError,ValueError,TypeError):return None

    def collect(self,call,window="main",tx_limit=100):
        t0=call["t0"]
        spans={"main":(t0-300,t0+1),"control_before":(t0-1800,t0-1500),
               "control_after":(t0+900,t0+1200),"edit":((call.get("edited") or t0)-300,(call.get("edited") or t0)+1)}
        start_time,end_time=spans[window]
        job=f"{call['id']}:{window}"
        old=self.store.get("coverage",job,{})
        current_pool_keys=sorted(p.get("pairAddress","") for p in self.store.get("pairs",f"{self.chain}:{call['token']}",{}).get("pairs",[]))
        if old.get("state")=="complete_scope" and old.get("decoder_version")==DECODER_VERSION and old.get("pool_keys")==current_pool_keys:return old
        coverage=dict(old,id=job,call_id=call["id"],chain=self.chain,token=call["token"],window=window,
          start_utc=utc(start_time),end_utc=utc(end_time),state="running",updated=now(),
          source_independently_verified=False,pool_discovery_exhaustive=False,ownership_confirmed=False)
        coverage.pop("error",None)
        self.store.put("coverage",job,coverage)
        try:
            scan=self.store.get("scans",job)
            pairs=self.store.get("pairs",f"{self.chain}:{call['token']}",{}).get("pairs",[])
            pool_keys=sorted(p.get("pairAddress","") for p in pairs)
            if scan and scan.get("pool_keys")!=pool_keys:scan=None
            if not scan:
                start=self.first_at(start_time);end=self.first_at(end_time)-1
                logs=self.logs({"address":call["token"],"topics":[TRANSFER]},start,end)
                # Also discover swaps settled via singleton/internal accounting, when pools are known.
                for p in pairs:
                    pa=p.get("pairAddress","")
                    if len(pa)==42:
                        logs+=self.logs({"address":pa,"topics":[SWAPS]},start,end)
                v4ids=[p["pairAddress"].lower() for p in pairs if len(p.get("pairAddress",""))==66]
                if v4ids:
                    # Restrict by exact pool IDs; this is not a chain-wide unfiltered scan.
                    logs+=self.logs({"topics":[V4,v4ids]},start,end)
                hashes=list(dict.fromkeys(l["transactionHash"] for l in logs))
                scan={"start":start,"end":end,"hashes":hashes,"logs":len(logs),"at":now(),"pool_keys":pool_keys}
                self.store.put("scans",job,scan)
            hashes=scan["hashes"]
            coverage.update(block_start=scan["start"],block_end=scan["end"],logs=scan["logs"],transactions=len(hashes),
                rank_window_end_utc=utc(t0),boundary_probe_included=window in ("main","edit"))
            self.store.put("coverage",job,coverage)
            processed=0
            for h in hashes:
                done_key=f"{job}:{h}"
                if (self.store.get("tx_done",done_key) or {}).get("decoder_version")==DECODER_VERSION:continue
                if processed>=tx_limit:break
                rp=[h];receipt=self.rpc("eth_getTransactionReceipt",rp)
                tx=self.rpc("eth_getTransactionByHash",rp)
                if receipt is None or tx is None:
                    self.store.put("gaps",done_key,{"job":job,"hash":h,"reason":"transaction_or_receipt_unavailable"})
                    continue
                b=self.block(int(receipt["blockNumber"],16));stamp=int(b["timestamp"],16)
                if b["hash"].lower()!=receipt["blockHash"].lower() or tx["blockHash"].lower()!=receipt["blockHash"].lower():
                    raise ValueError("Block hash mismatch; possible reorg or inconsistent provider")
                if not start_time<=stamp<end_time:raise ValueError("Transaction outside time window")
                for flow in transfers(receipt):
                    flow["raw"]=str(flow["raw"])
                    flow.update(chain=self.chain,tx_hash=h,timestamp=stamp,utc=utc(stamp))
                    self.store.put("flows",f"{self.chain}:{h}:{flow['log_index']}",flow)
                if int(tx.get("value","0x0"),16) and tx.get("to"):
                    self.store.put("flows",f"{self.chain}:{h}:native",{"chain":self.chain,"tx_hash":h,"timestamp":stamp,
                      "from":tx["from"].lower(),"to":tx["to"].lower(),"token":"native","raw":str(int(tx["value"],16))})
                swaps=[s for l in receipt.get("logs",[]) if (s:=decode_swap(l))]
                currencies=self.currencies(swaps,pairs)
                rows,diagnostic=analyze_receipt(tx,receipt,call["token"],currencies)
                token_dec=self.decimals(call["token"]) if rows else None
                for row in rows:
                    row.update(id=f"{job}:{h}:{row['wallet']}",call_id=call["id"],message_id=call["message_id"],
                       chain=self.chain,token=call["token"],window=window,tx_hash=h,block=int(receipt["blockNumber"],16),
                       block_hash=receipt["blockHash"],timestamp=stamp,utc=utc(stamp),seconds_before_call=t0-stamp,
                       same_second=stamp==t0,late_edit=call["late_edit"],timing_uncertain=call["timing_uncertain"],
                       token_decimals=token_dec,token_amount=units(row["token_raw"],token_dec) if token_dec is not None else None,
                       receipt_status=receipt["status"],evidence_keys=[self.net.evidence(self.chain,"eth_getTransactionReceipt",rp),
                          self.net.evidence(self.chain,"eth_getTransactionByHash",rp)],
                       explorer_url=CHAINS[self.chain]["explorer"]+"/tx/"+h)
                    row["rank_eligible"]=row["rank_eligible"] and not row["same_second"]
                    if row["quote_token"]:
                        qd=self.decimals(row["quote_token"]);row["quote_decimals"]=qd
                        row["quote_amount"]=units(row["quote_raw"],qd) if qd is not None and row["quote_raw"] else None
                self.store.replace_prefix("trades",f"{job}:{h}:",rows)
                self.store.put("tx_done",done_key,{"job":job,"hash":h,"rows":len(rows),"diagnostic":diagnostic,"decoder_version":DECODER_VERSION})
                processed+=1
                coverage.update(new_transactions_this_pass=processed,last_hash=h,updated=now())
                if processed%10==0:self.store.put("coverage",job,coverage)
            coverage["pool_keys"]=current_pool_keys
            done=sum((self.store.get("tx_done",f"{job}:{h}") or {}).get("decoder_version")==DECODER_VERSION for h in hashes)
            completed=[self.store.get("tx_done",f"{job}:{h}") for h in hashes]
            coverage["transactions_decoded_as_swaps"]=sum(bool(x and x["diagnostic"].get("swaps")) for x in completed)
            coverage["transactions_protocol_unresolved"]=sum(bool(x and x["diagnostic"].get("reason")=="unknown_protocol_or_transfer") for x in completed)
            coverage.update(state="complete_scope" if done==len(hashes) else "partial",
                block_start=scan["start"],block_end=scan["end"],logs=scan["logs"],transactions=len(hashes),
                transactions_processed=done,empty_window=not hashes,
                completeness_note="Complete only for discovered token/pool events; discovery and decoding may remain incomplete.")
        except BudgetExceeded as exc:
            coverage.update(state="partial",error=str(exc));self.store.put("coverage",job,coverage);raise
        except Exception as exc:
            coverage.update(state="error",error=f"{type(exc).__name__}: {exc}")
        coverage["decoder_version"]=DECODER_VERSION
        coverage["updated"]=now();self.store.put("coverage",job,coverage)
        return coverage
