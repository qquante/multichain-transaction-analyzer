"""User-facing wallet -> token -> purchase views derived from verified records."""
from collections import defaultdict
from .evm import DECODER_VERSION

def purchase_views(store,tables):
    calls={c["id"]:c for c in tables["calls"]}
    cov={c["id"]:c for c in tables["coverage"]}
    labels={}
    for group in store.all("pairs"):
        chain=group["chain"]
        for pair in group.get("pairs",[]):
            for side in ("baseToken","quoteToken"):
                asset=pair.get(side,{})
                address=asset.get("address","")
                if chain!="solana":address=address.lower()
                if address:
                    labels[(chain,address)]={"symbol":asset.get("symbol"),"name":asset.get("name")}
    def symbol(chain,token):
        return labels.get((chain,token),{}).get("symbol") or "Símbolo indisponível"
    purchases=[];seen=set()
    for t in tables["trades"]:
        call=calls.get(t["call_id"],{})
        if not (t.get("rank_eligible") and t["side"]=="buy" and t["window"]=="main"
                and 0<t["seconds_before_call"]<=300 and call.get("primary")):
            continue
        unique=(t["chain"],t["wallet"],t["token"],t["tx_hash"])
        if unique in seen:continue
        seen.add(unique)
        coverage=cov.get(t["call_id"]+":main",{})
        reason=[]
        if coverage.get("state")!="complete_scope":reason.append("coleta parcial")
        if coverage.get("decoder_version")!=DECODER_VERSION:reason.append("versão pendente")
        if call.get("late_edit"):reason.append("edição tardia")
        keys=("chain","wallet","token","token_amount","token_raw","quote_token","quote_amount","quote_basis",
              "seconds_before_call","tx_hash","explorer_url","utc","message_id","call_id")
        row={k:t.get(k) for k in keys}
        row.update(token_symbol=symbol(t["chain"],t["token"]),
          quote_symbol=symbol(t["chain"],t.get("quote_token")) if t.get("quote_token") else None,
          call_utc=call["t0_utc"],call_sp=call.get("t0_sp"),call_text=call.get("text",""),
          in_robust_score=not reason,coverage_note="; ".join(reason) if reason else "janela completa no escopo",
          split=call.get("split"),ownership_confirmed=False)
        purchases.append(row)
    purchases.sort(key=lambda p:(p["utc"],p["chain"],p["tx_hash"],p["wallet"]))
    by_wallet=defaultdict(list);by_token=defaultdict(list)
    for p in purchases:
        by_wallet[(p["chain"],p["wallet"])].append(p)
        by_token[(p["chain"],p["token"])].append(p)
    candidates={(r["chain"],r["wallet"]):r for r in tables["candidates"]}
    wallets=[]
    for (chain,wallet),rows in by_wallet.items():
        token_keys=sorted({p["token"] for p in rows})
        tokens=[{"token":t,"symbol":symbol(chain,t)} for t in token_keys]
        candidate=candidates.get((chain,wallet),{})
        wallets.append({"chain":chain,"wallet":wallet,"observed_tokens":len(tokens),
            "buy_transactions":len(rows),"days_observed":len({p["call_utc"][:10] for p in rows}),
            "tokens":tokens,"token_names":" · ".join(t["symbol"] for t in tokens),
            "robust_tokens":candidate.get("robust_tokens",0),
            "pending_tokens":len(tokens)-candidate.get("robust_tokens",0),
            "score":candidate.get("score",0),"candidate_rank":candidate.get("rank"),
            "validation_tokens":candidate.get("validation_tokens",0),"ownership_confirmed":False})
    wallets.sort(key=lambda r:(-r["observed_tokens"],-r["buy_transactions"],r["chain"],r["wallet"]))
    for i,r in enumerate(wallets,1):r["position"]=i
    tokens=[]
    for (chain,token),rows in by_token.items():
        tokens.append({"chain":chain,"token":token,"token_symbol":symbol(chain,token),
            "wallet_count":len({p["wallet"] for p in rows}),"buy_transactions":len(rows),
            "message_ids":sorted({p["message_id"] for p in rows}),
            "call_utc":rows[0]["call_utc"],
            "coverage_note":rows[0]["coverage_note"],"ownership_confirmed":False})
    tokens.sort(key=lambda r:(-r["wallet_count"],r["chain"],r["token"]))
    for kind in ("calls","trades"):
        for row in tables[kind]:row["token_symbol"]=symbol(row["chain"],row["token"])
    return {"wallets":wallets,"purchases":purchases,"tokens":tokens}
