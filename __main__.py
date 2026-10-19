from __future__ import annotations
import argparse
import contextlib
import json
import os
from pathlib import Path
import signal
import sys
import time
from .storage import Store
from .source import audit
from .net import Network, BudgetExceeded, CHAINS
from .pipeline import discover, selected_calls, collect
from .ranking import rank
from .report import export_report

ROOT=Path(__file__).resolve().parent
@contextlib.contextmanager
def exclusive_run(directory):
    path=Path(directory)/"collector.lock"
    path.parent.mkdir(parents=True,exist_ok=True)
    f=path.open("a+b")
    f.seek(0)
    if not f.read(1):f.write(b"0");f.flush()
    f.seek(0)
    try:
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError as exc:
        f.close()
        raise RuntimeError("Another network collector holds this output directory lock.") from exc
    try:
        (Path(directory)/"collector.pid").write_text(str(os.getpid()))
        yield
    finally:
        f.seek(0)
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_UN)
        f.close()

def parse():
    p=argparse.ArgumentParser(description="Read-only wallet forensics for result.json")
    p.add_argument("--input",type=Path,default=ROOT.parent/"result.json")
    p.add_argument("--output",type=Path,default=ROOT/"output")
    p.add_argument("--budget",type=int,default=3000,help="Hard network request budget per invocation")
    p.add_argument("--offline",action="store_true")
    sub=p.add_subparsers(dest="command",required=True)
    for name in ("audit","rank","report","status","stop","verify"):sub.add_parser(name)
    for name in ("probe","discover","collect","run","seed-check"):
        command=sub.add_parser(name)
        command.add_argument("--chains",default="all")
        if name=="probe":command.add_argument("--retry-blocked",action="store_true")
        if name in ("collect","run"):
            command.add_argument("--messages",default="")
            command.add_argument("--max-calls",type=int,default=0)
            command.add_argument("--tx-limit",type=int,default=100)
            command.add_argument("--pages",type=int,default=20)
            command.add_argument("--windows",default="main")
            command.add_argument("--include-updates",action="store_true")
            command.add_argument("--rounds",type=int,default=1,help="0 = until complete, stalled, stopped or budget reached")
    return p.parse_args()

def status(store):
    from collections import Counter
    return {"input":store.get("meta","source"),"collection":store.get("meta","last_collection"),
       "ranking":store.get("meta","ranking"),"coverage":dict(Counter(c["state"] for c in store.all("coverage"))),
       "trade_rows":len(store.all("trades")),"processed_transactions":len(store.all("tx_done")),
       "blocked_endpoints":store.all("endpoints"),"sqlite_integrity":store.integrity()}

def main():
    args=parse()
    if args.budget<1:raise ValueError("budget must be positive")
    args.output.mkdir(parents=True,exist_ok=True)
    store=Store(args.output/"evidence.sqlite")
    def emit(x):print(json.dumps(x,ensure_ascii=False,default=str),flush=True)
    try:
        if args.command=="stop":
            (args.output/"STOP").write_text("requested");emit({"stop_requested":True});return
        if args.command=="status":emit(status(store));return
        if args.command=="audit":emit(audit(args.input,store));return
        if args.command=="verify":
            from .verify import verify
            result=verify(store,args.input);emit(result)
            (args.output/"verification.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
            if result["state"]!="passed":raise SystemExit(1)
            return
        if args.command=="rank":emit(rank(store));return
        if args.command=="report":
            emit(rank(store));emit(export_report(store,args.output));return
        chains=list(CHAINS) if args.chains=="all" else args.chains.split(",")
        if set(chains)-set(CHAINS):raise ValueError("Unknown chain")
        with exclusive_run(args.output):
            (args.output/"STOP").unlink(missing_ok=True)
            net=Network(store,budget=args.budget,offline=args.offline)
            def stop(*_):net.stopped=True
            signal.signal(signal.SIGINT,stop)
            if hasattr(signal,"SIGTERM"):signal.signal(signal.SIGTERM,stop)
            if args.command=="probe":
                if args.retry_blocked:
                    for chain in chains:
                        for endpoint in CHAINS[chain]["rpc"]:
                            store.put("endpoints",endpoint,{"blocked_403":False,"manual_reprobe":True})
                for chain in chains:emit(net.probe(chain))
            elif args.command=="discover":emit(discover(store,net,chains))
            elif args.command=="seed-check":
                from .seeds import inspect_seeds
                emit(inspect_seeds(store,net,chains));emit(export_report(store,args.output))
            else:
                if args.command=="run":
                    emit(audit(args.input,store))
                    emit(discover(store,net,chains))
                messages={int(x) for x in args.messages.split(",") if x}
                calls=selected_calls(store,chains,messages,args.max_calls,args.include_updates)
                windows=tuple(args.windows.split(","))
                if set(windows)-{"main","edit","control_before","control_after"}:raise ValueError("Unknown window")
                rounds=stalled=0
                while not net.stopped and net.count<net.budget:
                    before=(len(store.all("tx_done")),sum(x.get("pages",0) for x in store.all("sol_pages")))
                    emit(collect(store,net,calls,windows,args.tx_limit,args.pages,lambda msg:print(msg,flush=True)))
                    emit(rank(store));emit(export_report(store,args.output))
                    rounds+=1
                    after=(len(store.all("tx_done")),sum(x.get("pages",0) for x in store.all("sol_pages")))
                    stalled=stalled+1 if after==before else 0
                    jobs=[store.get("coverage",c["id"]+":"+w,{}) for c in calls for w in windows
                          if w!="edit" or c["late_edit"]]
                    done=all(j.get("state") in ("complete_scope","excluded_overlapping_call") for j in jobs)
                    if done or stalled>=2 or (args.rounds and rounds>=args.rounds):break
                emit({"stopped":net.stopped,"budget_used":net.count,"budget":net.budget,"rounds":rounds,"stalled_rounds":stalled})
    except BudgetExceeded as exc:
        emit({"state":"checkpointed_budget_stop","reason":str(exc)})
        if store.get("meta","source"):
            rank(store);emit(export_report(store,args.output))
    finally:store.close()

if __name__=="__main__":main()
