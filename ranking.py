from __future__ import annotations
import collections
import math
import statistics
from decimal import Decimal, getcontext
from .storage import now
from .evm import transfers, DECODER_VERSION

def rank(store):
    getcontext().prec=400
    calls={c["id"]:c for c in store.all("calls")}
    coverage={c["id"]:c for c in store.all("coverage")}
    trades=store.all("trades")
    main=[t for t in trades if t.get("rank_eligible") and t["window"]=="main" and t["side"]=="buy"
          and 0<t["seconds_before_call"]<=300 and calls.get(t["call_id"],{}).get("primary")]
    by_call=collections.defaultdict(set);by_wallet=collections.defaultdict(list)
    for t in main:
        by_call[t["call_id"]].add(t["wallet"])
        by_wallet[(t["chain"],t["wallet"])].append(t)
    controls=collections.defaultdict(set)
    for t in trades:
        if t.get("rank_eligible") and t["side"]=="buy" and t["window"].startswith("control"):
            controls[(t["chain"],t["wallet"],t["call_id"])].add(t["window"])
    result=[]
    for (chain,wallet),rows in by_wallet.items():
        observed={t["call_id"] for t in rows}
        robust={cid for cid in observed if not calls[cid]["late_edit"] and coverage.get(cid+":main",{}).get("state")=="complete_scope" and coverage.get(cid+":main",{}).get("decoder_version")==DECODER_VERSION}
        discovery={cid for cid in robust if calls[cid]["split"]=="discovery"}
        validation={cid for cid in robust if calls[cid]["split"]=="validation"}
        weighted=sum(1/math.log2(2+len(by_call[cid])) for cid in discovery)
        tested=hits=0
        for cid in robust:
            for window in ("control_before","control_after"):
                cov=coverage.get(cid+":"+window,{})
                # Zero-activity / incomplete controls don't establish a usable baseline.
                if cov.get("state")=="complete_scope" and cov.get("transactions",0)>0 and cov.get("decoder_version")==DECODER_VERSION:
                    tested+=1
                    hits+=window in controls.get((chain,wallet,cid),set())
        rate=hits/tested if tested else None
        score=weighted*(1-rate) if rate is not None else weighted
        days={calls[cid]["t0_utc"][:10] for cid in robust}
        label="candidate"
        if len(robust)>=3 and len(days)>=2:label="recurring_candidate"
        if discovery and validation and len(robust)>=3 and len(days)>=2:label="recurs_in_temporal_validation"
        warnings=["Wallet ownership is not established.","Edited source text has no version history.",
                  "Historical pool discovery is not guaranteed exhaustive."]
        if tested==0:warnings.append("No usable control windows yet.")
        if not discovery:warnings.append("Appears only in validation or uncertain calls; not a discovered-and-validated candidate.")
        if len(robust)<3:warnings.append("Fewer than three distinct tokens with non-late-edit calls.")
        if any(coverage.get(cid+":main",{}).get("state")!="complete_scope" for cid in observed):
            warnings.append("One or more matched windows have incomplete collection and contribute no robust score.")
        if any(calls[cid]["resolution"]=="dex_pair_base_token_assumption" for cid in observed):
            warnings.append("Includes a pool URL resolved using the displayed base token.")
        amount_by_quote=collections.defaultdict(Decimal);seen=set()
        for t in rows:
            key=(t["tx_hash"],t["token"],t["wallet"])
            if key in seen:continue
            seen.add(key)
            if t.get("quote_amount") is not None and t.get("quote_basis") in ("wallet_token_net_flow","pool_swap_amount_not_wallet_total"):
                amount_by_quote[(t["quote_token"],t["quote_basis"])]+=Decimal(t["quote_amount"])
        result.append({"id":chain+":"+wallet,"chain":chain,"wallet":wallet,"score":round(score,8),
          "score_formula":"sum(1/log2(2+buyers_in_call)) over discovery tokens, times (1-control_hit_rate) when measured",
          "classification":label,"ownership_confirmed":False,"observed_tokens":len(observed),
          "robust_tokens":len(robust),"discovery_tokens":len(discovery),"validation_tokens":len(validation),
          "validation_confirms_discovery":bool(discovery and validation),"days":len(days),
          "median_seconds_before":statistics.median(t["seconds_before_call"] for t in rows),
          "buy_transactions":len(seen),"control_windows_tested":tested,"control_hits":hits,"control_hit_rate":rate,
          "volume_by_quote":[{"token":a,"basis":b,"amount":str(n)} for (a,b),n in amount_by_quote.items()],
          "message_ids":sorted({calls[cid]["message_id"] for cid in observed}),
          "call_ids":sorted(observed),"evidence_transactions":sorted({t["tx_hash"] for t in rows}),
          "warnings":warnings})
    result.sort(key=lambda r:(-r["score"],-r["validation_tokens"],-r["days"],r["wallet"]))
    store.clear("candidates")
    for i,row in enumerate(result,1):
        row["rank"]=i;store.put("candidates",row["id"],row)
    store.clear("activity_ranking");store.clear("volume_ranking")
    activity=sorted(result,key=lambda r:(-r["buy_transactions"],-r["observed_tokens"],r["id"]))
    volumes=collections.defaultdict(list)
    for i,r in enumerate(activity,1):
        store.put("activity_ranking",r["id"],{"rank":i,"chain":r["chain"],"wallet":r["wallet"],
          "buy_transactions":r["buy_transactions"],"observed_tokens":r["observed_tokens"],
          "scope":"Observed primary-window buys; may include incomplete windows or edited calls; ownership unconfirmed"})
        for v in r["volume_by_quote"]:
            volumes[(r["chain"],v["token"],v["basis"])].append((Decimal(v["amount"]),r["wallet"]))
    for (chain,token,basis),values in volumes.items():
        for i,(amount,wallet) in enumerate(sorted(values,reverse=True),1):
            key=f"{chain}:{token}:{basis}:{wallet}"
            store.put("volume_ranking",key,{"rank_within_asset_and_basis":i,"chain":chain,"quote_token":token,
               "quote_basis":basis,"wallet":wallet,"amount":str(amount),"ownership_confirmed":False})
    # A posted maker is a lead, not a boost to the score.
    for seed in store.all("seeds"):
        matches=[t for t in trades if t["chain"]==seed["chain"] and t["wallet"]==seed["wallet"]]
        seed.update(observed_rows=len(matches),matched_messages=sorted({t["message_id"] for t in matches}),
                    verified_buy_rows=sum(t.get("rank_eligible",False) and t["side"]=="buy" for t in matches))
        store.put("seeds",seed["chain"]+":"+seed["wallet"],seed)
    wallets={(r["chain"],r["wallet"]) for r in result} | {(s["chain"],s["wallet"]) for s in store.all("seeds")}
    store.clear("links")
    for f in store.all("flows"):
        if (f["chain"],f["from"]) in wallets and (f["chain"],f["to"]) in wallets and f["from"]!=f["to"]:
            f["interpretation"]="Observed transfer in collected transactions; not proof of common control or funding history completeness."
            store.put("links",f"{f['chain']}:{f['tx_hash']}:{f.get('log_index','native')}",f)
    summary={"at":now(),"candidates":len(result),"ownership_confirmed":0,
             "eligible_buy_rows":len(main),"discovery_split":store.get("meta","split")}
    store.put("meta","ranking",summary)
    return summary
