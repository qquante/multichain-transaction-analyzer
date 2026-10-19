from __future__ import annotations
import collections
import csv
import io
import json
import os
from pathlib import Path
from .storage import now
from .purchase_views import purchase_views

HTML="""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wallet Forensics · Competição</title>
<style>
:root{color-scheme:dark;--bg:#0b1018;--panel:#141d2a;--line:#29374b;--muted:#9cadc3;--ink:#edf4ff;--accent:#73e3bc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px system-ui,sans-serif}
main{max-width:1700px;margin:auto;padding:38px 28px}header{display:flex;justify-content:space-between;gap:20px;align-items:start}
.eyebrow{color:var(--accent);font:12px ui-monospace,monospace;letter-spacing:2px}h1{font-size:36px;margin:10px 0}p{line-height:1.6}
.muted{color:var(--muted)}.badge{border:1px solid var(--line);border-radius:20px;padding:7px 12px;font-size:12px;white-space:nowrap}
.notice{background:#20291f;border:1px solid #596346;border-radius:10px;padding:14px 18px;margin:24px 0;color:#d9e9c4}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}.card{background:var(--panel);border:1px solid var(--line);padding:20px;border-radius:12px}
.value{font-size:29px;font-weight:650;margin-top:9px}.label{color:var(--muted);font-size:13px}
nav{display:flex;flex-wrap:wrap;gap:8px;margin:24px 0 16px}button,.download{background:var(--panel);color:var(--ink);border:1px solid var(--line);padding:10px 14px;border-radius:7px;cursor:pointer}
button[aria-selected=true]{background:var(--accent);color:#071e16;border-color:var(--accent);font-weight:600}
.toolbar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-bottom:15px}
input,select{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:11px;font:inherit}
input{flex:1;min-width:230px}.download{font-size:13px;text-decoration:none}.wrap{overflow:auto;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:13px 14px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);background:var(--panel);white-space:nowrap}td{max-width:450px;overflow-wrap:anywhere}tr:hover td{background:#172131}
.mono{font-family:ui-monospace,monospace;font-size:12px}a{color:var(--accent)}footer{overflow-wrap:anywhere;margin-top:24px;font-size:12px;color:var(--muted);line-height:1.7}
td details{margin:0;min-width:160px;max-width:330px}td details summary{cursor:pointer}td.nowrap{white-space:nowrap}.mono{min-width:175px}details{margin:15px 0;color:var(--muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere}#empty{padding:40px;text-align:center;color:var(--muted)}
[hidden]{display:none!important}.token-list{min-width:220px;max-width:420px;line-height:1.7}.token-chip{padding:3px 7px;margin:2px 4px 2px 0;font-size:12px;border-radius:5px}.address{min-width:320px;font:12px ui-monospace,monospace;white-space:nowrap}.detail-panel{margin:20px 0;padding:20px;border:1px solid var(--accent);border-radius:12px;background:var(--panel)}.detail-panel h2{margin:0 0 8px}.detail-panel h3{overflow-wrap:anywhere;font:14px ui-monospace,monospace}.detail-panel .wrap{margin-top:15px}.pager{display:flex;align-items:center;gap:12px;margin-top:14px}.small{font-size:12px}.record-link{background:transparent;padding:0;border:0;text-align:left;color:var(--accent)}#run-status{padding:10px 0;line-height:1.6}
@media(max-width:760px){main{padding:23px 15px}.cards{grid-template-columns:repeat(2,1fr)}header{display:block}.badge{display:inline-block}h1{font-size:28px}}
</style></head><body><main>
<header><div><div class="eyebrow">ON-CHAIN RESEARCH / POKER</div><h1>Wallets antes das calls</h1>
<p class="muted">Evidências rastreáveis, cinco minutos por call e validação entre tokens.</p></div><span class="badge">SOMENTE LEITURA · SEM CHAVES</span></header>
<div class="notice"><strong>As carteiras são candidatas.</strong> Negociar antes de uma call não demonstra identidade ou controle pelo autor.
Mensagens editadas e dados indisponíveis permanecem sinalizados. “complete_scope” significa apenas coleta dos eventos e contas descobertos.</div>
<div class="cards" id="cards"></div><p id="run-status" class="muted"></p>
<nav role="tablist" id="tabs"></nav>
<div class="toolbar"><input id="search" aria-label="Buscar" placeholder="Buscar wallet, token, mensagem ou hash...">
<select id="chain" aria-label="Filtrar rede"><option value="">Todas as redes</option></select>
<a id="csv" class="download" download>CSV completo</a><a id="json" class="download" download>JSON completo</a></div>
<section id="wallet-detail" class="detail-panel" hidden><div class="toolbar"><h2>Compras desta wallet</h2><button id="copy-wallet">Copiar wallet</button><button id="close-detail">Fechar detalhes</button></div><h3 id="wallet-title"></h3><p id="wallet-summary"></p><div class="wrap"><table><thead><tr><th>Token / contrato</th><th>Quantidade comprada</th><th>Antes da call</th><th>Mensagem / UTC</th><th>Transação</th><th>Cobertura</th></tr></thead><tbody id="purchase-rows"></tbody></table></div></section>
<p id="count" class="muted"></p><div class="wrap"><table><thead id="head"></thead><tbody id="rows"></tbody></table><div id="empty" hidden>Nenhum registro nesta seleção.</div></div>
<div class="pager"><button id="prev-page">Anterior</button><span id="page-number"></span><button id="next-page">Próxima</button></div>
<details><summary>Como interpretar o ranking e a cobertura</summary><p>
O score pondera a recorrência por token na descoberta pela raridade de compradores; controles utilizáveis reduzem o score.
A validação usa os 30% finais dos tokens no tempo. Score não é probabilidade de propriedade.
Volume é separado por ativo e por base de cálculo; não há conversão USD inventada.
A consulta a pools atuais não garante descobrir pools antigos, curvas encerradas ou todas as negociações.
</p><pre id="method"></pre></details>
<p class="muted">Exportações auxiliares: <a href="activity_ranking.csv" download>frequência de compras</a> · <a href="volume_ranking.csv" download>volume por cotação</a> · <a href="rpc_coverage.csv" download>RPCs por método</a> · <a href="v4_pools.json" download>PoolManagers v4</a> · <a href="seed_activity.csv" download>atividade das pistas</a>.</p><footer id="footer"></footer></main>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>

const D=JSON.parse(document.getElementById("payload").textContent);
const configs={
 wallets:{name:"Wallets e compras",cols:["position","chain","wallet","observed_tokens","buy_transactions","token_names","robust_tokens","pending_tokens","actions"]},
 tokens:{name:"Tokens comprados",cols:["chain","token_symbol","token","wallet_count","buy_transactions","message_ids","call_utc","coverage_note","token_actions"]},
 candidates:{name:"Candidatos",cols:["rank","chain","wallet","score","observed_tokens","robust_tokens","discovery_tokens","validation_tokens","days","warnings","actions"]},
 calls:{name:"Calls",cols:["message_id","chain","token_symbol","token","t0_utc","classification","primary","late_edit","text"]},
 trades:{name:"Negociações",cols:["message_id","chain","wallet","side","token_symbol","token","token_amount","quote_token","quote_amount","seconds_before_call","classification","tx_hash"]},
 coverage:{name:"Cobertura",cols:["chain","call_id","window","state","transactions_processed","transactions","error","completeness_note"]},
 seeds:{name:"Pistas explícitas",cols:["chain","wallet","message_id","role","observed_rows","verified_buy_rows","context_transactions","evidence"]},
 links:{name:"Transferências",cols:["chain","from","to","token","raw","tx_hash","interpretation"]},
 issues:{name:"Pendências",cols:["message_id","kind","chain","value","error"]}
};
const labels={position:"#",rank:"Ranking",chain:"Rede",wallet:"Wallet",score:"Score",observed_tokens:"Tokens comprados",buy_transactions:"Compras",token_names:"Quais tokens",robust_tokens:"Tokens no score",pending_tokens:"Tokens pendentes",actions:"Evidências",token_actions:"Compradores",wallet_count:"Wallets",token_symbol:"Nome / ticker",coverage_note:"Cobertura",call_utc:"Call UTC",message_ids:"Mensagens",discovery_tokens:"Descoberta",validation_tokens:"Validação",days:"Dias",warnings:"Ressalvas",message_id:"Mensagem",token:"Contrato / mint",t0_utc:"Publicação UTC",classification:"Classificação",primary:"Primária",late_edit:"Edição tardia",text:"Texto",side:"Lado",token_amount:"Quantidade",quote_token:"Cotação",quote_amount:"Valor na cotação",seconds_before_call:"Antes da call (s)",tx_hash:"Transação",call_id:"Call",window:"Janela",state:"Estado",transactions_processed:"Processadas",transactions:"Identificadas",error:"Erro",completeness_note:"Limite da cobertura",context_transactions:"Atividade ampliada",role:"Contexto",observed_rows:"Ocorrências",verified_buy_rows:"Compras verificadas",evidence:"Evidência",from:"Origem",to:"Destino",raw:"Quantidade bruta",interpretation:"Interpretação",kind:"Tipo",value:"Dado"};
const $=id=>document.getElementById(id),txt=v=>v==null?"—":typeof v==="object"?JSON.stringify(v):String(v);
let active="wallets",pageIndex=0,pageSize=50,filteredCount=0,selectedWallet="";
function card(label,value){const c=document.createElement("div");c.className="card";const l=document.createElement("div");l.className="label";l.textContent=label;const n=document.createElement("div");n.className="value";n.textContent=value;c.append(l,n);$("cards").append(c);}
card("Wallets com compras observadas",D.counts.wallets);card("Compras nos 5 minutos antes da call",D.counts.purchases);
card("Tokens com compras verificadas",D.counts.tokens);card("Calls primárias completas no escopo",D.meta.primary_complete+" / "+D.meta.primary_calls);
const last=D.meta.last_collection||{};
$("run-status").textContent=(last.requests>=last.budget?"Última rodada encerrada: limite de "+last.budget.toLocaleString("pt-BR")+" requisições atingido. ":"")+
 "As compras abaixo são evidências reais já coletadas. Tokens pendentes têm compra observada, mas ainda não entram no score por coleta parcial ou incerteza temporal. Nomes vêm do snapshot de pares; o contrato identifica o token.";
for(const [key,c] of Object.entries(configs)){const b=document.createElement("button");b.textContent=c.name;b.setAttribute("role","tab");b.dataset.key=key;b.onclick=()=>{active=key;pageIndex=0;$("wallet-detail").hidden=true;render()};$("tabs").append(b);}
for(const chain of ["robinhood","bsc","base","ethereum","hyperevm","solana"]){const o=document.createElement("option");o.value=chain;o.textContent=chain;$("chain").append(o);}
function tokenWallets(chain,token){active="wallets";pageIndex=0;$("chain").value=chain;$("search").value=token;$("wallet-detail").hidden=true;render();}
function showWallet(chain,wallet){
 selectedWallet=wallet;
 const rows=D.tables.purchases.filter(p=>p.chain===chain&&p.wallet===wallet);
 $("wallet-title").textContent=wallet;
 $("wallet-summary").textContent=chain+" · "+rows.length+" compras · "+new Set(rows.map(p=>p.token)).size+" contratos distintos. Clique no hash para conferir a transação.";
 $("purchase-rows").replaceChildren();
 for(const p of rows){
  const tr=document.createElement("tr");
  const token=document.createElement("td"),name=document.createElement("strong"),address=document.createElement("div");
  name.textContent=p.token_symbol;address.className="mono small";address.textContent=p.token;token.append(name,address);tr.append(token);
  for(const value of [p.token_amount,p.seconds_before_call+" s",p.message_id+" · "+p.call_utc]){
   const td=document.createElement("td");td.textContent=txt(value);tr.append(td);
  }
  const td=document.createElement("td"),a=document.createElement("a");a.className="tx-link mono";a.href=p.explorer_url;a.target="_blank";a.rel="noopener noreferrer";a.textContent=p.tx_hash;td.append(a);tr.append(td);
  const note=document.createElement("td");note.textContent=p.coverage_note;tr.append(note);
  $("purchase-rows").append(tr);
 }
 $("wallet-detail").hidden=false;$("copy-wallet").textContent="Copiar wallet";
 $("wallet-detail").scrollIntoView({behavior:"instant",block:"start"});
}
$("close-detail").onclick=()=>{$("wallet-detail").hidden=true};
$("copy-wallet").onclick=async()=>{try{await navigator.clipboard.writeText(selectedWallet);$("copy-wallet").textContent="Copiada"}catch{$("copy-wallet").textContent="Selecione o endereço acima"}};
function render(){
 for(const b of $("tabs").children)b.setAttribute("aria-selected",String(b.dataset.key===active));
 const all=D.tables[active]||[],q=$("search").value.toLowerCase(),chain=$("chain").value;
 const filtered=all.filter(r=>(!chain||r.chain===chain)&&(!q||JSON.stringify(r).toLowerCase().includes(q)));
 filteredCount=filtered.length;
 pageIndex=Math.min(pageIndex,Math.max(0,Math.ceil(filteredCount/pageSize)-1));
 $("head").replaceChildren();$("rows").replaceChildren();
 const tr=document.createElement("tr");
 for(const k of configs[active].cols){const th=document.createElement("th");th.textContent=labels[k]||k;tr.append(th)}$("head").append(tr);
 for(const r of filtered.slice(pageIndex*pageSize,(pageIndex+1)*pageSize)){
  const row=document.createElement("tr");
  for(const k of configs[active].cols){
   const td=document.createElement("td");
   if(["wallet","token","tx_hash"].includes(k))td.className=k==="wallet"?"address":"mono";
   if(["chain","position","rank","score","observed_tokens","buy_transactions","robust_tokens","pending_tokens","discovery_tokens","validation_tokens","days"].includes(k))td.classList.add("nowrap");
   if(k==="actions"){const b=document.createElement("button");b.textContent="Ver compras";b.onclick=()=>showWallet(r.chain,r.wallet);td.append(b)}
   else if(k==="token_actions"){const b=document.createElement("button");b.textContent="Ver wallets";b.onclick=()=>tokenWallets(r.chain,r.token);td.append(b)}
   else if(k==="token_names"){
    td.className="token-list";
    for(const t of r.tokens){const b=document.createElement("button");b.className="token-chip";b.textContent=t.symbol;b.title=t.token;b.onclick=()=>tokenWallets(r.chain,t.token);td.append(b)}
   }
   else if(k==="warnings"){const d=document.createElement("details"),summary=document.createElement("summary");summary.textContent=(r[k]||[]).length+" ressalvas";d.append(summary);for(const warning of r[k]||[]){const p=document.createElement("p");p.textContent=warning;d.append(p)}td.append(d)}
   else if(k==="wallet"&&["wallets","candidates"].includes(active)){const b=document.createElement("button");b.className="record-link address";b.textContent=r.wallet;b.onclick=()=>showWallet(r.chain,r.wallet);td.append(b)}
   else if(k==="tx_hash"&&r.explorer_url){const a=document.createElement("a");a.href=r.explorer_url;a.target="_blank";a.rel="noopener noreferrer";a.textContent=txt(r[k]);td.append(a)}
   else td.textContent=txt(r[k]);
   row.append(td);
  }
  $("rows").append(row);
 }
 $("empty").hidden=filteredCount>0;$("count").textContent=filteredCount+" resultados nesta seleção, pesquisando todos os "+D.counts[active]+" registros. Os arquivos exportados também estão completos.";
 $("page-number").textContent="Página "+(pageIndex+1)+" de "+Math.max(1,Math.ceil(filteredCount/pageSize));
 $("prev-page").disabled=pageIndex===0;$("next-page").disabled=(pageIndex+1)*pageSize>=filteredCount;
 $("csv").href=active+".csv";$("json").href=active+".json";
}
$("prev-page").onclick=()=>{pageIndex--;render()};$("next-page").onclick=()=>{pageIndex++;render()};
$("search").oninput=()=>{pageIndex=0;render()};$("chain").onchange=()=>{pageIndex=0;render()};
$("method").textContent=JSON.stringify(D.meta.ranking,null,2);
$("footer").textContent="Atualizado em "+D.generated+" · Entrada SHA-256: "+D.meta.source?.sha256+" · Banco evidence.sqlite · Horário UTC. Nenhuma wallet foi atribuída ao autor.";
render();
</script></body></html>"""

def atomic_write(path,content):
    tmp=path.with_name(path.name+".tmp")
    tmp.write_text(content,encoding="utf-8")
    os.replace(tmp,path)

def csv_text(rows):
    cols=list(dict.fromkeys(k for row in rows for k in row))
    out=io.StringIO(newline="")
    if not cols:return ""
    w=csv.DictWriter(out,fieldnames=cols)
    w.writeheader()
    for row in rows:
        clean={}
        for k,v in row.items():
            if isinstance(v,(dict,list)):v=json.dumps(v,ensure_ascii=False)
            if isinstance(v,str) and v.lstrip().startswith(("=","+","-","@")):
                # Preserve exact strings in JSON; protect CSV opened in spreadsheet software.
                v="'"+v
            clean[k]=v
        w.writerow(clean)
    return out.getvalue()

def export_report(store,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    kinds=("calls","trades","candidates","coverage","seeds","links","issues","probes","activity_ranking","volume_ranking","v4_pools","seed_activity","seed_checks")
    tables={kind:store.all(kind) for kind in kinds}
    tables.update(purchase_views(store,tables))
    with store.lock:
        requests=store.db.execute("SELECT endpoint,method,status,count(*),min(at),max(at),min(params),max(params) FROM requests GROUP BY endpoint,method,status").fetchall()
    tables["rpc_coverage"]=[dict(zip(("endpoint","method","status","requests","first_requested_utc","last_requested_utc","sample_params_1","sample_params_2"),r)) for r in requests]
    tables["calls"].sort(key=lambda r:(r["t0"],r["message_id"]))
    tables["candidates"].sort(key=lambda r:r["rank"])
    tables["trades"].sort(key=lambda r:(r["timestamp"],r["chain"],r["tx_hash"]))
    for kind,rows in tables.items():
        atomic_write(directory/(kind+".json"),json.dumps(rows,ensure_ascii=False,indent=2))
        atomic_write(directory/(kind+".csv"),csv_text(rows))
    meta={"source":store.get("meta","source"),"ranking":store.get("meta","ranking"),
          "discovery":store.get("meta","discovery"),"last_collection":store.get("meta","last_collection"),
          "primary_calls":sum(c["primary"] for c in tables["calls"]),
          "primary_complete":sum(c["primary"] and any(v["call_id"]==c["id"] and v.get("window")=="main" and v["state"]=="complete_scope" for v in tables["coverage"]) for c in tables["calls"]),
          "completed_windows":sum(c["state"]=="complete_scope" for c in tables["coverage"])}
    payload={"generated":now(),"meta":meta,"counts":{k:len(v) for k,v in tables.items()},
             "tables":tables}
    keep={"message_id","chain","wallet","side","token_symbol","token","token_amount","quote_token","quote_amount","seconds_before_call","classification","tx_hash","explorer_url","utc","window","call_id"}
    payload["tables"]=dict(tables)
    payload["tables"]["trades"]=[{k:v for k,v in row.items() if k in keep} for row in tables["trades"]]
    data=json.dumps(payload,ensure_ascii=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    atomic_write(directory/"report.html",HTML.replace("__PAYLOAD__",data))
    atomic_write(directory/"summary.json",json.dumps({k:v for k,v in payload.items() if k!="tables"},ensure_ascii=False,indent=2))
    return {"report":str((directory/"report.html").resolve()),"counts":payload["counts"],"generated":payload["generated"]}
