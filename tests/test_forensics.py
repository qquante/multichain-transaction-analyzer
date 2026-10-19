"""Synthetic fixtures only. Live observations are stored separately in output/."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zlib
from wallet_forensics.storage import Store, units, dumps
from wallet_forensics.source import audit, b58decode, classify, finalize_calls, link_ref
from wallet_forensics.net import Network, RpcError, BudgetExceeded, retry_after
from wallet_forensics.evm import (EVMCollector, TRANSFER, V2, V3, V4, USEROP, ENTRYPOINTS,
                                analyze_receipt, decode_swap, decode_initialize, INITIALIZE, topic, DECODER_VERSION)
from wallet_forensics.solana import (SolanaCollector, analyze_transaction, b58encode,
                                    pda, on_curve, PUMP, PROGRAMS, account_keys)
from wallet_forensics.ranking import rank
from wallet_forensics.report import export_report, csv_text

TOKEN="0x"+"1"*40
QUOTE="0x"+"2"*40
USER="0x"+"3"*40
ROUTER="0x"+"4"*40
POOL="0x"+"5"*40
OTHER="0x"+"6"*40
HASH="0x"+"7"*64
BHASH="0x"+"8"*64
PID="0x"+"9"*64

def wa(a):return "0x"+a[2:].rjust(64,"0")
def data(*vals):return "0x"+"".join(f"{x%(1<<256):064x}" for x in vals)
def log(addr,topics,values,index):
    return {"address":addr,"topics":topics,"data":data(*values),"logIndex":hex(index),
            "transactionHash":HASH,"blockHash":BHASH,"blockNumber":"0xa","transactionIndex":"0x0"}
def receipt_fixture(version="v2",sender=USER):
    if version=="v2":sw=log(POOL,[V2,wa(ROUTER),wa(USER)],(0,50,100,0),2)
    elif version=="v3":sw=log(POOL,[V3,wa(ROUTER),wa(USER)],(-100,50,1,1,0),2)
    else:sw=log(POOL,[V4,PID,wa(ROUTER)],(100,-50,1,1,0,3000),2)
    receipt={"status":"0x1","transactionHash":HASH,"blockHash":BHASH,"blockNumber":"0xa","logs":[
      log(TOKEN,[TRANSFER,wa(POOL),wa(USER)],(100,),0),
      log(QUOTE,[TRANSFER,wa(USER),wa(POOL)],(50,),1),sw]}
    tx={"hash":HASH,"blockHash":BHASH,"from":sender,"to":ROUTER,"value":"0x0"}
    mapping={(POOL,PID) if version=="v4" else POOL:(TOKEN,QUOTE)}
    return tx,receipt,mapping

class TempStore(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/"test.sqlite")
    def tearDown(self):
        self.store.close();self.tmp.cleanup()

class SourceTests(TempStore):
    def source(self,messages,broken=False):
        p=Path(self.tmp.name)/"result.json"
        text=json.dumps({"id":1,"messages":messages})
        if broken:text=text.replace('"id": 1,','"id": 1',1)
        p.write_text(text)
        return p
    def msg(self,i,text,t=1788270436,reply=None,edit=None):
        local=dt.datetime.fromtimestamp(t,dt.timezone.utc)-dt.timedelta(hours=3)
        m={"id":i,"date":local.replace(tzinfo=None).isoformat(),"date_unixtime":str(t),"text":text}
        if reply is not None:m["reply_to_message_id"]=reply
        if edit is not None:m["edited_unixtime"]=str(edit)
        return m
    def test_repair_is_in_memory_and_utc_is_exact(self):
        p=self.source([self.msg(1,"Aped https://competition/eth/token/Adem_"+TOKEN)],True)
        before=p.read_bytes();result=audit(p,self.store)
        self.assertEqual(before,p.read_bytes())
        self.assertEqual(result["offset_counts"],{-10800:1})
        c=self.store.all("calls")[0]
        self.assertEqual(c["t0_utc"],"2026-09-01T13:47:16Z")
        self.assertEqual(c["t0_sp"],"2026-09-01T10:47:16-03:00")
    def test_changed_source_rejected(self):
        p=self.source([self.msg(1,"Aped")]);audit(p,self.store)
        p.write_text(json.dumps({"id":1,"messages":[self.msg(2,"Aped")]}))
        with self.assertRaises(ValueError):audit(p,self.store)
    def test_unexpected_json_damage_not_repaired(self):
        p=Path(self.tmp.name)/"result.json";p.write_text('{"messages":[')
        with self.assertRaises(ValueError):audit(p,self.store)
    def test_initial_call_not_double_counted_for_dca_or_repost(self):
        a=" https://competition/eth/token/"+TOKEN
        p=self.source([self.msg(1,"Aped"+a),self.msg(2,"Aped more"+a,1788270500),
                       self.msg(3,"Aped"+a,1788270600)])
        audit(p,self.store)
        self.assertEqual(sum(c["primary"] for c in self.store.all("calls")),1)
    def test_reply_dca_resolves_token(self):
        p=self.source([self.msg(1,"Aped https://competition/eth/token/"+TOKEN),
                       self.msg(2,"Aped more",1788270500,reply=1)])
        audit(p,self.store)
        c=next(c for c in self.store.all("calls") if c["message_id"]==2)
        self.assertEqual(c["classification"],"add");self.assertEqual(c["token"],TOKEN)
    def test_hidden_link_and_maker_preserved(self):
        m=self.msg(1,"And this")
        m["text_entities"]=[{"type":"text_link","text":"chart","href":"https://dexscreener.com/ethereum/"+PID+"?maker="+USER}]
        audit(self.source([m]),self.store)
        self.assertEqual(len(self.store.all("pool_refs")),1)
        self.assertFalse(self.store.all("seeds")[0]["ownership_confirmed"])
    def test_edited_source_flags_and_amount_not_size(self):
        m=self.msg(1,"Aped 60K https://competition/eth/token/"+TOKEN,edit=1788271000)
        audit(self.source([m]),self.store);c=self.store.all("calls")[0]
        self.assertTrue(c["late_edit"]);self.assertTrue(c["timing_uncertain"])
        self.assertFalse(c["amount_in_text_is_trade_size"])
    def test_watch_and_quote_reference_not_entry(self):
        self.assertEqual(classify("im not in watching for entry"),"watch")
        self.assertEqual(classify("Paired 1:1"),"pair_info")
    def test_v4_id_remains_pool_not_wallet(self):
        self.assertEqual(link_ref("https://dexscreener.com/ethereum/"+PID),("ethereum","pool",PID))
    def test_split_is_unique_token_not_repost(self):
        ms=[self.msg(i,"Aped https://competition/eth/token/0x"+f"{i:040x}",1788270436+i) for i in range(1,11)]
        audit(self.source(ms),self.store)
        split=self.store.get("meta","split")
        self.assertEqual((split["discovery"],split["validation"]),(7,3))

class EVMTests(TempStore):
    def test_keccak_not_sha3(self):
        self.assertEqual(TRANSFER,"0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
    def test_all_three_swap_versions_reconcile_same_buyer(self):
        for version in ("v2","v3","v4"):
            with self.subTest(version=version):
                tx,rc,mp=receipt_fixture(version);rows,_=analyze_receipt(tx,rc,TOKEN,mp)
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]["wallet"],USER)
                self.assertTrue(rows[0]["rank_eligible"])
                self.assertEqual(rows[0]["token_raw"],"100")
                self.assertEqual(rows[0]["quote_raw"],"50")
    def test_v4_delta_sign(self):
        _,r,_=receipt_fixture("v4")
        s=decode_swap(r["logs"][-1])
        self.assertEqual((s["delta0"],s["delta1"]),(-100,50))
    def test_v4_manager_collision_is_not_accepted(self):
        tx,r,m=receipt_fixture("v4");r["logs"][-1]["address"]=OTHER
        rows,_=analyze_receipt(tx,r,TOKEN,m)
        self.assertFalse(any(x["rank_eligible"] for x in rows))
    def test_v4_initialize_hash_and_currencies(self):
        from Crypto.Hash import keccak
        k=keccak.new(digest_bits=256)
        k.update(bytes.fromhex(data(int(TOKEN,16),int(QUOTE,16),3000,60,0)[2:]))
        pid="0x"+k.hexdigest()
        event=log(POOL,[INITIALIZE,pid,wa(TOKEN),wa(QUOTE)],(3000,60,0,1,0),0)
        binding=decode_initialize(event)
        self.assertEqual(binding["currencies"],[TOKEN,QUOTE]);self.assertTrue(binding["pool_id_hash_verified"])
        event["topics"][1]=PID
        with self.assertRaises(ValueError):decode_initialize(event)
    def test_failed_receipt_never_becomes_buy(self):
        tx,r,m=receipt_fixture();r["status"]="0x0"
        self.assertEqual(analyze_receipt(tx,r,TOKEN,m)[0],[])
    def test_transfer_only_not_ranked(self):
        tx,r,m=receipt_fixture();r["logs"]=r["logs"][:2]
        rows,_=analyze_receipt(tx,r,TOKEN,m)
        self.assertFalse(rows[0]["rank_eligible"])
        self.assertEqual(rows[0]["classification"],"transfer_only")
    def test_relayer_is_not_assumed_buyer(self):
        tx,r,m=receipt_fixture(sender=OTHER);rows,_=analyze_receipt(tx,r,TOKEN,m)
        self.assertEqual(rows[0]["wallet"],USER);self.assertFalse(rows[0]["rank_eligible"])
    def test_direct_swap_sender_remains_user(self):
        tx,r,m=receipt_fixture();r["logs"][-1]["topics"][1]=wa(USER)
        self.assertTrue(analyze_receipt(tx,r,TOKEN,m)[0][0]["rank_eligible"])
    def test_wrong_hash_rejected(self):
        tx,r,m=receipt_fixture();tx["hash"]="different"
        with self.assertRaises(ValueError):analyze_receipt(tx,r,TOKEN,m)
    def test_erc721_transfer_shape_excluded(self):
        tx,r,m=receipt_fixture();r["logs"]=[log(TOKEN,[TRANSFER,wa(POOL),wa(USER),data(123)],(),0)]
        self.assertEqual(analyze_receipt(tx,r,TOKEN,m)[0],[])
    def test_successful_erc4337_sender_resolved(self):
        tx,r,m=receipt_fixture(sender=OTHER)
        entry=next(iter(ENTRYPOINTS));r["logs"].append(log(entry,[USEROP,HASH,wa(USER),wa(OTHER)],(0,1,100,50),4))
        rows,_=analyze_receipt(tx,r,TOKEN,m)
        self.assertTrue(rows[0]["rank_eligible"]);self.assertEqual(rows[0]["actor_kind"],"erc4337_sender")
    def test_failed_userop_does_not_resolve_actor(self):
        tx,r,m=receipt_fixture(sender=OTHER)
        r["logs"].append(log(next(iter(ENTRYPOINTS)),[USEROP,HASH,wa(USER),wa(OTHER)],(0,0,100,50),4))
        self.assertFalse(analyze_receipt(tx,r,TOKEN,m)[0][0]["rank_eligible"])
    def test_timestamp_search_repeated_seconds(self):
        class C(EVMCollector):
            def block(self,n):
                times=[0,100,100,101,103];i=4 if n=="latest" else n
                return {"number":hex(i),"timestamp":hex(times[i]),"hash":str(i)}
        c=C(self.store,None,"ethereum")
        self.assertEqual(c.first_at(100),1);self.assertEqual(c.first_at(101),3)
        with self.assertRaises(RpcError):c.first_at(104)
    def test_logs_range_split_and_dedup(self):
        class N:
            def rpc(self,chain,method,params):
                q=params[0];a=int(q["fromBlock"],16);b=int(q["toBlock"],16)
                if b-a>1:raise RpcError("range limit",-32005)
                result=[]
                for n in range(a,b+1):
                    r=log(TOKEN,[TRANSFER,wa(USER),wa(OTHER)],(10,),n)
                    r["blockNumber"]=hex(n);r["blockHash"]=str(n);result.extend([r,r])
                return result
        logs=EVMCollector(self.store,N(),"ethereum").logs({"address":TOKEN},1,5)
        self.assertEqual(len(logs),5)
    def test_checkpoint_resume_skips_completed_receipts(self):
        class N:
            def __init__(self):self.rc_calls=0
            def evidence(self,*a):return "fixture"
            def rpc(self,chain,method,params):
                if method=="eth_getTransactionReceipt":
                    self.rc_calls+=1;_,r,_=receipt_fixture();r["transactionHash"]=params[0];return r
                if method=="eth_getTransactionByHash":
                    tx,_,_=receipt_fixture();tx["hash"]=params[0];return tx
                if method=="eth_call":
                    sel=params[0]["data"]
                    return data(18) if sel=="0x313ce567" else wa(TOKEN if sel=="0x0dfe1681" else QUOTE)
                raise AssertionError(method)
        class C(EVMCollector):
            def first_at(self,t):return 10 if t<1000 else 11
            def block(self,n):return {"hash":BHASH,"timestamp":hex(990)}
            def logs(self,*a):
                rows=[]
                for n in (7,9):
                    r=log(TOKEN,[TRANSFER,wa(POOL),wa(USER)],(100,),0)
                    r["transactionHash"]="0x"+str(n)*64;rows.append(r)
                return rows
        n=N();c=C(self.store,n,"ethereum")
        call={"id":"fixture","chain":"ethereum","token":TOKEN,"t0":1000,"message_id":1,"late_edit":False,"timing_uncertain":False}
        first=c.collect(call,tx_limit=1);self.assertEqual(first["state"],"partial")
        second=c.collect(call,tx_limit=1);self.assertEqual(second["state"],"complete_scope")
        self.assertEqual(n.rc_calls,2)
        c.collect(call,tx_limit=1);self.assertEqual(n.rc_calls,2)
        for record in self.store.all("tx_done"):
            record["decoder_version"]=DECODER_VERSION-1
            self.store.put("tx_done",record["job"]+":"+record["hash"],record)
        cov=self.store.get("coverage","fixture:main");cov["decoder_version"]=DECODER_VERSION-1
        self.store.put("coverage","fixture:main",cov)
        replay=c.collect(call,tx_limit=1)
        self.assertEqual(replay["state"],"partial");self.assertEqual(replay["transactions_processed"],1)
        replay=c.collect(call,tx_limit=1)
        self.assertEqual(replay["state"],"complete_scope");self.assertEqual(n.rc_calls,4)

class SolanaTests(TempStore):
    def fixture(self,token2022=False):
        program="CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
        keys=[{"pubkey":"Sponsor","signer":True},{"pubkey":"Buyer","signer":True},
              {"pubkey":"TargetATA","signer":False},{"pubkey":"QuoteATA","signer":False}]
        token_program="TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb" if token2022 else "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
        def bal(index,mint,n):
            return {"accountIndex":index,"mint":mint,"owner":"Buyer","programId":token_program,
                    "uiTokenAmount":{"amount":str(n),"decimals":6}}
        ix={"programId":program,"accounts":["Buyer","TargetATA","QuoteATA"],
            "data":b58encode(hashlib.sha256(b"global:swap_v2").digest()[:8]+b"\0"*16)}
        return {"slot":1,"blockTime":1000,"transaction":{"signatures":["signature"],"message":{"accountKeys":keys,"instructions":[]}},
                "meta":{"err":None,"preTokenBalances":[bal(2,"Mint",0),bal(3,"USDC",100)],
                        "postTokenBalances":[bal(2,"Mint",50),bal(3,"USDC",70)],
                        "innerInstructions":[{"index":0,"instructions":[ix]}],
                        "preBalances":[100,100,1,1],"postBalances":[90,100,1,1]}}
    def test_inner_instruction_owner_not_fee_payer(self):
        rows,d=analyze_transaction(self.fixture(),"Mint")
        self.assertEqual(rows[0]["wallet"],"Buyer");self.assertEqual(rows[0]["fee_payer"],"Sponsor")
        self.assertTrue(rows[0]["rank_eligible"]);self.assertEqual(rows[0]["quote_raw"],"30")
        self.assertEqual(rows[0]["instructions"][0]["inner_index"],0)
    def test_token2022_and_created_account(self):
        tx=self.fixture(True);tx["meta"]["preTokenBalances"]=tx["meta"]["preTokenBalances"][1:]
        rows,_=analyze_transaction(tx,"Mint");self.assertTrue(rows[0]["rank_eligible"])
        self.assertEqual(rows[0]["token_raw"],"50")
    def test_failed_transaction_and_unknown_program(self):
        tx=self.fixture();tx["meta"]["err"]={"InstructionError":[0,"Custom"]}
        self.assertEqual(analyze_transaction(tx,"Mint")[0],[])
        tx=self.fixture();tx["meta"]["innerInstructions"][0]["instructions"][0]["programId"]="Unknown"
        self.assertFalse(analyze_transaction(tx,"Mint")[0][0]["rank_eligible"])
    def test_unrelated_signer_receipt_is_not_swap_beneficiary(self):
        tx=self.fixture()
        tx["transaction"]["message"]["accountKeys"].append({"pubkey":"SponsorATA","signer":False})
        extra={"accountIndex":4,"mint":"Mint","owner":"Sponsor","uiTokenAmount":{"amount":"999","decimals":6}}
        tx["meta"]["postTokenBalances"].append(extra)
        rows,_=analyze_transaction(tx,"Mint")
        sponsor=next(r for r in rows if r["wallet"]=="Sponsor")
        self.assertFalse(sponsor["rank_eligible"])
    def test_historical_anchor_cursor_does_not_start_at_head(self):
        class N:
            def rpc(self,chain,method,params,**kw):
                assert params[1]["before"]=="real-block-signature"
                return [{"signature":"x","blockTime":990},{"signature":"y","blockTime":699}]
        anchor={"before":"real-block-signature","timestamp":1320,"slot":100}
        result=SolanaCollector(self.store,N()).signatures("pool",700,1001,"job",1,anchor)
        self.assertEqual(result["state"],"reached_start");self.assertEqual(set(result["signatures"]),{"x"})
    def test_lookup_table_keys_are_resolved_but_not_signers(self):
        tx={"transaction":{"message":{"accountKeys":["a"],"header":{"numRequiredSignatures":1}}},
            "meta":{"loadedAddresses":{"writable":["b"],"readonly":["c"]}}}
        keys,signers=account_keys(tx)
        self.assertEqual(keys,["a","b","c"]);self.assertEqual(signers,{"a"})
    def test_base58_and_program_address(self):
        raw=bytes(range(32));self.assertEqual(b58decode(b58encode(raw)),raw)
        self.assertEqual(b58decode("1"*32),bytes(32))
        addr=pda([b"bonding-curve",raw],PUMP)
        self.assertEqual(len(b58decode(addr)),32);self.assertFalse(on_curve(b58decode(addr)))
    def test_pagination_resume_and_half_open_window(self):
        class N:
            def __init__(self):self.calls=[]
            def rpc(self,chain,method,params,**kw):
                before=params[1].get("before");self.calls.append(before)
                if before is None:return [{"signature":"a","blockTime":1300},{"signature":"b","blockTime":1200}]
                return [{"signature":"c","blockTime":990},{"signature":"d","blockTime":700},{"signature":"e","blockTime":699}]
        n=N();c=SolanaCollector(self.store,n)
        p=c.signatures("pool",700,1200,"job",1);self.assertEqual(p["state"],"partial")
        p=c.signatures("pool",700,1200,"job",1)
        self.assertEqual(n.calls,[None,"b"]);self.assertEqual(set(p["signatures"]),{"c","d"})
        self.assertEqual(p["state"],"reached_start")
    def test_empty_signature_page_is_not_complete_history(self):
        class N:
            def rpc(self,*a,**kw):return []
        p=SolanaCollector(self.store,N()).signatures("pool",1,2,"job",1)
        self.assertEqual(p["state"],"history_exhausted_unverified")

class NetworkAndOutputTests(TempStore):
    def test_readonly_allowlist(self):
        n=Network(self.store)
        for method in ("eth_sendRawTransaction","sendTransaction","personal_sign","eth_sign"):
            with self.assertRaises(ValueError):n.rpc("ethereum",method,[])
    def test_offline_cache_and_budget(self):
        n=Network(self.store,budget=1,offline=True)
        url="https://test.invalid";key=n.key(url,"eth_chainId",[])
        with self.store.lock,self.store.db:
            self.store.db.execute("INSERT INTO cache VALUES(?,?,?,?,?,?)",(key,url,"eth_chainId","[]",zlib.compress(b'"0x1"'),0))
        self.assertEqual(n._request(url,"eth_chainId",[])[0],"0x1")
        self.assertEqual(n.count,0)
        with self.assertRaises(RpcError):n._request(url,"eth_blockNumber",[])
    def test_http_403_disables_endpoint(self):
        n=Network(self.store)
        err=urllib.error.HTTPError("https://test.invalid",403,"Forbidden",{},None)
        with patch("urllib.request.urlopen",side_effect=err) as opener:
            with self.assertRaises(RpcError):n._request("https://test.invalid","eth_chainId",[])
            with self.assertRaises(RpcError):n._request("https://test.invalid","eth_chainId",[])
            self.assertEqual(opener.call_count,1)
        self.assertTrue(self.store.get("endpoints","https://test.invalid")["blocked_403"])
    def test_null_historical_result_falls_back(self):
        from wallet_forensics.net import CHAINS
        n=Network(self.store)
        urls=CHAINS["bsc"]["rpc"]
        def fake(url,method,params,ttl=None):
            if method=="eth_chainId":return "0x38","identity"
            return (None,"missing") if url==urls[0] else ({"number":"0x1"},"present")
        with patch.object(n,"_request",side_effect=fake):
            self.assertEqual(n.rpc("bsc","eth_getBlockByNumber",["0x1",False]),{"number":"0x1"})
    def test_retry_after_integer_and_http_date(self):
        self.assertEqual(retry_after("15"),15)
        self.assertEqual(retry_after("Thu, 01 Jan 1970 00:00:10 GMT",0),10)
    def test_exact_units_above_float_and_decimal_default_precision(self):
        raw=2**256-1;s=units(raw,18)
        self.assertEqual(s.replace(".",""),str(raw))
        self.assertEqual(units(1,18),"0.000000000000000001")
        self.assertEqual(units(0,18),"0")
    def test_csv_formula_protection_json_canonical(self):
        content=csv_text([{"text":"=HYPERLINK(1)","wallet":USER}])
        self.assertIn("'=HYPERLINK",content)
    def test_html_source_text_cannot_close_script(self):
        self.store.put("issues","x",{"kind":"fixture","value":"</script><script>alert(1)</script>"})
        self.store.put("meta","source",{"sha256":"fixture","messages":0})
        out=export_report(self.store,Path(self.tmp.name)/"report")
        html=Path(out["report"]).read_text(encoding='utf-8')
        self.assertNotIn("</script><script>alert(1)",html)
        self.assertIn("\\u003c/script",html)
        self.assertEqual(json.loads((Path(self.tmp.name)/"report"/"issues.json").read_text(encoding='utf-8'))[0]["value"],"</script><script>alert(1)</script>")
    def test_sqlite_integrity(self):
        self.assertEqual(self.store.integrity(),"ok")

class SeedTests(TempStore):
    def test_seed_context_is_evidence_without_rank_or_ownership_bonus(self):
        from wallet_forensics.seeds import inspect_seeds
        self.store.put("seeds","ethereum:"+USER,{"chain":"ethereum","wallet":USER,"message_id":1})
        self.store.put("calls","a",{"id":"a","message_id":1,"chain":"ethereum","token":TOKEN,"t0":1000})
        tx,receipt,mapping=receipt_fixture()
        class N:
            def evidence(self,*a):return "fixture"
        with patch("wallet_forensics.seeds.EVMCollector") as cls:
            collector=cls.return_value
            collector.first_at.side_effect=[1,20]
            collector.logs.return_value=[{"transactionHash":HASH}]
            collector.rpc.side_effect=[receipt,tx]
            collector.block.return_value={"hash":BHASH,"timestamp":hex(990)}
            collector.currencies.return_value=mapping
            result=inspect_seeds(self.store,N())
        self.assertEqual(result[0]["transactions"],1)
        row=self.store.all("seed_activity")[0]
        self.assertEqual(row["classification"],"reconciled_swap")
        self.assertEqual(row["seconds_before_call"],10)
        self.assertFalse(row["rank_eligible"]);self.assertFalse(row["ownership_confirmed"])

class PurchaseViewTests(TempStore):
    def test_wallet_tokens_include_partial_buys_but_exclude_transfers_and_duplicates(self):
        from wallet_forensics.purchase_views import purchase_views
        call={"id":"a","primary":True,"late_edit":False,"t0_utc":"2026-09-01T00:00:00Z","chain":"ethereum","token":TOKEN}
        valid={"call_id":"a","chain":"ethereum","wallet":USER,"token":TOKEN,"rank_eligible":True,
          "side":"buy","window":"main","seconds_before_call":20,"tx_hash":HASH,"utc":"2026-08-31T23:59:40Z"}
        transfer=dict(valid,tx_hash="transfer",rank_eligible=False,side="receive")
        ambiguous=dict(valid,tx_hash="ambiguous",seconds_before_call=0)
        tables={"calls":[call],"trades":[valid,dict(valid),transfer,ambiguous],"candidates":[],
                "coverage":[{"id":"a:main","state":"partial","decoder_version":DECODER_VERSION}]}
        out=purchase_views(self.store,tables)
        self.assertEqual(len(out["purchases"]),1)
        self.assertEqual(out["wallets"][0]["tokens"][0]["token"],TOKEN)
        self.assertEqual(out["wallets"][0]["pending_tokens"],1)
        self.assertEqual(out["tokens"][0]["wallet_count"],1)
        self.assertFalse(out["purchases"][0]["in_robust_score"])

class RankingTests(TempStore):
    def add(self,cid,token,msg,split="discovery",late=False,delta=20,primary=True):
        self.store.put("calls",cid,{"id":cid,"chain":"ethereum","token":token,"primary":primary,
            "late_edit":late,"split":split,"t0_utc":f"2026-09-{msg:02d}T12:00:00Z","message_id":msg,"resolution":"token_link"})
        self.store.put("coverage",cid+":main",{"id":cid+":main","state":"complete_scope","decoder_version":DECODER_VERSION})
        self.store.put("trades",cid,{"call_id":cid,"chain":"ethereum","token":token,"wallet":USER,"window":"main","side":"buy",
            "rank_eligible":True,"seconds_before_call":delta,"tx_hash":str(msg),"quote_amount":None})
    def test_same_second_and_late_edits_never_boost_score(self):
        self.add("a","t1",1,late=True);self.add("b","t2",2,delta=0)
        rank(self.store);c=self.store.all("candidates")[0]
        self.assertEqual(c["score"],0);self.assertEqual(c["robust_tokens"],0)
    def test_discovery_validation_and_repost_separation(self):
        self.add("a","t1",1);self.add("b","t2",2);self.add("c","t3",3,split="validation")
        self.add("d","t1",4,primary=False)
        rank(self.store);c=self.store.all("candidates")[0]
        self.assertEqual(c["robust_tokens"],3);self.assertEqual(c["discovery_tokens"],2)
        self.assertEqual(c["validation_tokens"],1);self.assertFalse(c["ownership_confirmed"])
    def test_measured_control_activity_reduces_score(self):
        self.add("a","t1",1)
        rank(self.store);before=self.store.all("candidates")[0]["score"]
        self.store.put("coverage","a:control_before",{"id":"a:control_before","state":"complete_scope","transactions":3,"decoder_version":DECODER_VERSION})
        self.store.put("trades","control",{"call_id":"a","chain":"ethereum","token":"t1","wallet":USER,
          "window":"control_before","side":"buy","rank_eligible":True})
        rank(self.store);after=self.store.all("candidates")[0]
        self.assertEqual(after["control_hit_rate"],1);self.assertLess(after["score"],before)
    def test_incomplete_or_old_decoder_window_has_no_robust_score(self):
        self.add("a","t1",1)
        for state,version in (("partial",DECODER_VERSION),("complete_scope",DECODER_VERSION-1)):
            self.store.put("coverage","a:main",{"id":"a:main","state":state,"decoder_version":version})
            rank(self.store);c=self.store.all("candidates")[0]
            self.assertEqual(c["score"],0);self.assertEqual(c["robust_tokens"],0)
    def test_explicit_seed_has_no_score_bonus(self):
        self.store.put("seeds","seed",{"chain":"ethereum","wallet":USER,"message_id":1})
        rank(self.store);self.assertEqual(self.store.all("candidates"),[])

if __name__=="__main__":unittest.main()
