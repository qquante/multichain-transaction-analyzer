from __future__ import annotations
import collections
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from .storage import utc

ALIASES = {"eth":"ethereum","ethereum":"ethereum","bsc":"bsc","bnb":"bsc","base":"base",
           "sol":"solana","solana":"solana","robinhood":"robinhood","rh":"robinhood",
           "hyperevm":"hyperevm","hl":"hyperevm"}
EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")
POOLID = re.compile(r"^0x[0-9a-fA-F]{64}$")
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def b58decode(value):
    n = 0
    for c in value:
        n = n*58 + BASE58.index(c)
    return b"\0"*(len(value)-len(value.lstrip("1"))) + (n.to_bytes((n.bit_length()+7)//8,"big") if n else b"")

def address(value, chain):
    value = unquote(value).removeprefix("Adem_").strip()
    if chain == "solana":
        try:
            return value if len(b58decode(value)) == 32 else None
        except ValueError:
            return None
    return value.lower() if EVM.fullmatch(value) else None

def flatten(message):
    value = message.get("text", "")
    text = value if isinstance(value, str) else "".join(x if isinstance(x,str) else x.get("text","") for x in value)
    links = re.findall(r"https?://[^\s]+", text)
    for entity in list(message.get("text_entities",[])) + (value if isinstance(value,list) else []):
        if isinstance(entity,dict):
            link = entity.get("href") or entity.get("url")
            if link:
                links.append(link)
    return text, list(dict.fromkeys(x.rstrip(".,)") for x in links))

def link_ref(url):
    parsed = urlparse(url)
    bits = [unquote(x) for x in parsed.path.split("/") if x]
    host = (parsed.hostname or "").lower()
    if host == "competition" and len(bits)>=3 and bits[1]=="token":
        chain = ALIASES.get(bits[0])
        return (chain, "token", bits[2]) if chain else None
    if host.endswith("dexscreener.com") and len(bits)>=2:
        chain=ALIASES.get(bits[0])
        return (chain,"pool",bits[1]) if chain else None
    if host.endswith("pump.fun") and len(bits)>=2 and bits[0]=="coin":
        return "solana","token",bits[1]
    if host.endswith("defined.fi") and len(bits)>=3 and bits[0]=="token":
        chain=ALIASES.get(bits[1])
        return (chain,"token",bits[2]) if chain else None
    if host.endswith("flap.sh") and len(bits)>=2:
        chain=ALIASES.get(bits[0])
        return (chain,"token",bits[1]) if chain else None
    if host == "rh-scan.com" and len(bits)>=2 and bits[0]=="address":
        return "robinhood","address",bits[1]
    return None

def classify(text, parent_text=""):
    t=text.lower()
    if re.search(r"\b(not in|watching for entry|not bought|didn.t buy|not buying)\b",t):
        return "watch"
    if re.search(r"\b(sold|selling|exited|took profit|take profit)\b",t):
        return "sell" if re.search(r"\b(i|im|i'm|my)\b",t) else "commentary"
    if re.search(r"\b(dca|dca.d|aped more|bought more|back to|moved to)\b",t):
        return "add"
    if re.search(r"\b(aped|bought|buying)\b",t):
        return "entry"
    if re.search(r"^\s*and this\b",t) and re.search(r"\b(aped|bought)\b",parent_text,re.I):
        return "entry"
    if re.search(r"\b(paired|pair token|quote token)\b",t):
        return "pair_info"
    if re.search(r"\b(ath|hit|\d+x|dex paid)\b",t):
        return "result"
    if "competition/r/" in t or re.search(r"\b(ref link|own bots)\b",t):
        return "advertisement"
    return "reference"

def load_source(path):
    original=Path(path).read_bytes()
    text=original.decode("utf-8-sig")
    repair=None
    try:
        doc=json.loads(text)
    except json.JSONDecodeError:
        fixed,n=re.subn(r'("id"\s*:\s*1)(\s*"messages"\s*:)',r'\1,\2',text,count=1)
        if n!=1:
            raise ValueError("Unexpected JSON defect; source was not changed.")
        doc=json.loads(fixed)
        repair={"kind":"missing_comma_before_messages","replacements":n,"applied_in_memory_only":True}
    if not isinstance(doc.get("messages"),list):
        raise ValueError("messages must be an array")
    return doc,hashlib.sha256(original).hexdigest(),repair

def audit(path, store):
    doc,sha,repair=load_source(path)
    previous=store.get("meta","source")
    if previous and previous["sha256"]!=sha:
        raise ValueError("Input hash changed: choose a new output directory to prevent mixing evidence.")
    offsets=collections.Counter()
    messages={}
    allrefs=[]
    for raw in doc["messages"]:
        text,links=flatten(raw)
        epoch=int(raw["date_unixtime"])
        naive=dt.datetime.fromisoformat(raw["date"])
        if naive.tzinfo:
            offset=int(naive.utcoffset().total_seconds())
        else:
            offset=int(naive.replace(tzinfo=dt.timezone.utc).timestamp())-epoch
        offsets[offset]+=1
        edit=int(raw["edited_unixtime"]) if raw.get("edited_unixtime") else None
        m={"id":raw["id"],"t0":epoch,"t0_utc":utc(epoch),"date_source":raw["date"],
           "utc_offset_seconds":offset,"edited":edit,"edited_utc":utc(edit) if edit else None,
           "edit_delay":edit-epoch if edit else None,"timing_uncertain":edit is not None,
           "late_edit":bool(edit and edit-epoch>300),"reply":raw.get("reply_to_message_id"),
           "text":text,"links":links,"refs":[]}
        for link in links:
            ref=link_ref(link)
            if not ref:
                continue
            chain,kind,value=ref
            norm=address(value,chain)
            if kind=="pool" and chain!="solana" and POOLID.fullmatch(value):
                norm=value.lower()
            if not norm:
                store.put("issues",f"invalid:{m['id']}:{value}",{"message_id":m["id"],"kind":"invalid_reference","value":value,"chain":chain})
                continue
            r={"chain":chain,"kind":kind,"value":norm,"url":link,"message_id":m["id"]}
            m["refs"].append(r)
            allrefs.append(r)
            maker=parse_qs(urlparse(link).query).get("maker",[])
            for item in maker:
                wallet=address(item,chain)
                if wallet:
                    store.put("seeds",chain+":"+wallet,{"chain":chain,"wallet":wallet,"message_id":m["id"],
                        "role":"maker_link","ownership_confirmed":False,"evidence":link})
            if kind=="address":
                store.put("seeds",chain+":"+norm,{"chain":chain,"wallet":norm,"message_id":m["id"],
                    "role":"address_reference_context_unverified","ownership_confirmed":False,"evidence":link})
        messages[m["id"]]=m

    known=collections.defaultdict(set)
    for ref in allrefs:
        if ref["kind"]=="token":
            known[ref["value"]].add(ref["chain"])
    ordered=sorted(messages.values(),key=lambda m:(m["t0"],m["id"]))
    for index,m in enumerate(ordered):
        parent=messages.get(m["reply"],{})
        context=parent.get("text","")
        m["classification"]=classify(m["text"],context)
        # A token-only post adjacent to explicit buying text is contextual, not silently certain.
        if m["classification"]=="reference" and index:
            prev=ordered[index-1]
            if m["t0"]-prev["t0"]<=15 and classify(prev["text"])=="entry" and not prev["refs"]:
                m["classification"]="entry"
                m["context_message_id"]=prev["id"]
        bare=re.sub(r"https?://\S+","",m["text"])
        naked=re.findall(r"(?<![\w])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])",bare)
        for value in naked:
            value=value.lower()
            chains=set(known.get(value,set()))
            if len(chains)!=1:
                explicit=re.search(r"\bon\s+(base|eth|bsc|rh|robinhood|hl|hyperevm)\b",m["text"],re.I)
                chains={ALIASES[explicit[1].lower()]} if explicit else set()
            if len(chains)==1:
                chain=next(iter(chains))
                if not any(r["kind"]=="token" and r["value"]==value for r in m["refs"]):
                    m["refs"].append({"chain":chain,"kind":"token","value":value,"url":None,"message_id":m["id"],"resolution":"text_context"})
            else:
                store.put("issues",f"chain:{m['id']}:{value}",{"message_id":m["id"],"value":value,"kind":"chain_unresolved"})
        if any(r["kind"]=="address" for r in m["refs"]):
            contexts=[x for x in ordered if 0<=m["t0"]-x["t0"]<=60]
            for r in m["refs"]:
                if r["kind"]=="address":
                    seed=store.get("seeds",r["chain"]+":"+r["value"])
                    if any(re.search(r"\bdev(eloper)?\b",x["text"],re.I) for x in contexts):
                        seed["role"]="developer_context_reference"
                        seed["context_message_ids"]=[x["id"] for x in contexts]
                        store.put("seeds",r["chain"]+":"+r["value"],seed)
        bare_without_evm=re.sub(r"0x[0-9a-fA-F]+","",bare)
        for mint in re.findall(r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])",bare_without_evm):
            if address(mint,"solana") and not any(r["value"]==mint for r in m["refs"]):
                m["refs"].append({"chain":"solana","kind":"token","value":mint,"url":None,"message_id":m["id"],"resolution":"bare_base58_requires_mint_validation"})
        if not m["refs"] and m["classification"] in ("add","sell") and parent.get("refs"):
            for r in parent["refs"]:
                if r["kind"]=="token":
                    m["refs"].append(dict(r,message_id=m["id"],resolution="reply"))
        for ref in m["refs"]:
            if ref["kind"]=="token":
                put_call(store,m,ref["chain"],ref["value"],ref.get("resolution","token_link"))
            elif ref["kind"]=="pool":
                store.put("pool_refs",f"{m['id']}:{ref['chain']}:{ref['value']}",dict(ref,t0=m["t0"]))
        store.put("messages",m["id"],m)
    finalize_calls(store)
    summary={"sha256":sha,"input":str(Path(path).resolve()),"repair":repair,"messages":len(messages),
             "offset_counts":dict(offsets),"first_utc":utc(min(m["t0"] for m in ordered)),
             "last_utc":utc(max(m["t0"] for m in ordered)),
             "edited_messages":sum(m["edited"] is not None for m in ordered),
             "edited_over_300s":sum(m["late_edit"] for m in ordered),"calls":len(store.all("calls"))}
    store.put("meta","source",summary)
    return summary

def put_call(store,m,chain,token,resolution):
    key=f"{chain}:{token}:{m['id']}"
    store.put("calls",key,{"id":key,"message_id":m["id"],"chain":chain,"token":token,
        "t0":m["t0"],"t0_utc":m["t0_utc"],"t0_sp":dt.datetime.fromtimestamp(m["t0"],dt.timezone(dt.timedelta(hours=-3))).isoformat(),
        "text":m["text"],"classification":m["classification"],"edited":m["edited"],
        "edit_delay":m["edit_delay"],"timing_uncertain":m["timing_uncertain"],"late_edit":m["late_edit"],
        "resolution":resolution,"reply":m["reply"],"primary":False,"split":None,
        "amount_in_text_is_trade_size":False})

def finalize_calls(store):
    calls=sorted(store.all("calls"),key=lambda c:(c["t0"],c["message_id"],c["id"]))
    earliest={}
    primary={}
    for c in calls:
        key=(c["chain"],c["token"])
        earliest.setdefault(key,c["t0"])
        if c["classification"]=="entry":
            primary.setdefault(key,c["id"])
    ordered=list(primary.values())
    cut=math.ceil(len(ordered)*.7)
    split={cid:("discovery" if i<cut else "validation") for i,cid in enumerate(ordered)}
    for c in calls:
        key=(c["chain"],c["token"])
        c["first_observed_t0"]=earliest[key]
        c["primary"]=primary.get(key)==c["id"]
        c["split"]=split.get(primary.get(key))
        store.put("calls",c["id"],c)
    store.put("meta","split",{"rule":"global chronological unique token; ceil(70%) discovery; rest validation",
                             "tokens":len(ordered),"discovery":cut,"validation":len(ordered)-cut})
