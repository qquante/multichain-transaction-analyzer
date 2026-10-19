"""Bounded inspection of explicitly posted maker leads; never ownership attribution."""
from .evm import EVMCollector, TRANSFER, analyze_receipt, decode_swap, transfers
from .net import BudgetExceeded, RpcError, CHAINS
from .storage import now, utc, units

def inspect_seeds(store, net, chains=None, seconds_before=3600, seconds_after=300):
    results=[]
    for seed in store.all("seeds"):
        chain=seed["chain"]
        if chains and chain not in chains:continue
        related=[c for c in store.all("calls") if c["message_id"]==seed["message_id"] and c["chain"]==chain]
        if chain=="solana" or not related:
            results.append({"wallet":seed["wallet"],"state":"no_resolved_token_context","ownership_confirmed":False})
            continue
        collector=EVMCollector(store,net,chain)
        wallet_topic="0x"+seed["wallet"][2:].rjust(64,"0")
        for call in related:
            key=f"{chain}:{seed['wallet']}:{call['token']}"
            result={"id":key,"wallet":seed["wallet"],"chain":chain,"token":call["token"],
                "message_id":seed["message_id"],"ownership_confirmed":False,
                "start_utc":utc(call["t0"]-seconds_before),"end_utc":utc(call["t0"]+seconds_after),"at":now()}
            try:
                a=collector.first_at(call["t0"]-seconds_before);b=collector.first_at(call["t0"]+seconds_after)-1
                events=collector.logs({"address":call["token"],"topics":[TRANSFER,wallet_topic]},a,b)
                events+=collector.logs({"address":call["token"],"topics":[TRANSFER,None,wallet_topic]},a,b)
                hashes=sorted({x["transactionHash"] for x in events});rows=[]
                pairs=store.get("pairs",f"{chain}:{call['token']}",{}).get("pairs",[])
                for h in hashes:
                    rc=collector.rpc("eth_getTransactionReceipt",[h]);tx=collector.rpc("eth_getTransactionByHash",[h])
                    block=collector.block(int(rc["blockNumber"],16))
                    if block["hash"].lower()!=rc["blockHash"].lower() or tx["blockHash"].lower()!=rc["blockHash"].lower():
                        raise ValueError("Seed context block hash mismatch")
                    stamp=int(block["timestamp"],16)
                    if not call["t0"]-seconds_before<=stamp<call["t0"]+seconds_after:
                        raise ValueError("Seed context outside time window")
                    swaps=[v for l in rc.get("logs",[]) if (v:=decode_swap(l))]
                    currencies=collector.currencies(swaps,pairs)
                    trades,_=analyze_receipt(tx,rc,call["token"],currencies)
                    matches=[x for x in trades if x["wallet"]==seed["wallet"]]
                    if not matches:
                        # Preserve gross movements even if the wallet nets to zero.
                        matches=[{"wallet":seed["wallet"],"side":"net_zero_or_unresolved",
                                  "classification":"transfer_evidence_only","rank_eligible":False}]
                    for row in matches:
                        row.update(id=f"{key}:{h}",chain=chain,token=call["token"],message_id=call["message_id"],
                          tx_hash=h,utc=utc(stamp),timestamp=stamp,seconds_before_call=call["t0"]-stamp,
                          window="seed_context",rank_eligible=False,ownership_confirmed=False,
                          evidence_keys=[net.evidence(chain,"eth_getTransactionReceipt",[h]),net.evidence(chain,"eth_getTransactionByHash",[h])],
                          explorer_url=CHAINS[chain]["explorer"]+"/tx/"+h,
                          gross_token_transfers=[dict(f,raw=str(f["raw"])) for f in transfers(rc)
                              if f["token"]==call["token"] and seed["wallet"] in (f["from"],f["to"])])
                        store.put("seed_activity",row["id"],row);rows.append(row)
                result.update(state="complete_token_transfer_scope",transactions=len(hashes),rows=len(rows),
                   limitation="Only the referenced token and bounded period; no ownership inference or exhaustive wallet history.")
            except BudgetExceeded:raise
            except Exception as exc:result.update(state="error",error=str(exc))
            store.put("seed_checks",key,result)
            results.append(result)
            seed.update(context_check=result,
              context_transactions=sum(x.get("wallet")==seed["wallet"] and x.get("chain")==chain for x in store.all("seed_activity")))
            store.put("seeds",f"{chain}:{seed['wallet']}",seed)
    return results
