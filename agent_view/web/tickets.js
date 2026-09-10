  /* ---------- TICKETS view (Phase 1: read-only, reads /api/tickets) ---------- */
  // Defaults: the queue as it is worked — only `active` tickets, newest first
  // (the operator opens the page to see what just came in). Pills/headers change both.
  var TIX={projects:[]},tixMod=new Set(),tixCat=new Set(),tixStat=new Set(["active"]),tixQ="";
  var tixSort={key:"created",dir:"desc"};   // default sort: created newest first (priority is a tie-break via the header)
  var TIX_PAL=["#38bdf8","#f59e0b","#22c55e","#ec4899","#a78bfa","#e11d48","#2dd4bf","#64748b"];
  function modColor(n){var h=0;n=String(n||"");for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))>>>0;return TIX_PAL[h%TIX_PAL.length];}
  function priClass(p){p=String(p||"").toLowerCase();return (p==="critical"||p==="major"||p==="minor")?p:"none";}
  function safeUrl(u){u=String(u||"");return /^https?:\/\//i.test(u)?u:"";}  // block javascript:/data: in the attachment href (URL-context sink)
  function allTix(){var out=[];(TIX.projects||[]).forEach(function(p){(p.tickets||[]).forEach(function(t){out.push({t:t,mod:p.module,dir:p.dir,rev:p.rev});});});return out;}
  function uniq(a){var s=[],seen={};a.forEach(function(x){if(x&&!seen[x]){seen[x]=1;s.push(x);}});return s;}
  var TK_STATES=["working_today","ignore_today","ignore_indefinitely","solved_manually","nonsense","done"];
  var TK_SCOL={working_today:"#38e6ff",ignore_today:"#fbbf24",ignore_indefinitely:"#8b949e",solved_manually:"#a78bfa",nonsense:"#fb5a7e",done:"#4ade80"};
  var tixSel={};   // key dir|id -> {dir,id}
  var tixOpen=null;   // {dir,id} while the detail modal is open — lets an SSE outbox update re-render its drafts
  function projRev(dir){var r=0;(TIX.projects||[]).forEach(function(p){if(p.dir===dir)r=p.rev;});return r;}
  // POST a validated triage patch; on a 409 (someone else wrote) re-pull and retry once.
  function postTriage(dir,id,patch,retry){
    return fetch("/api/tickets/triage",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({dir:dir,id:id,patch:patch,rev:projRev(dir)})}).then(function(r){return r.json().then(function(j){return {code:r.status,j:j};});})
      .then(function(res){
        if(res.code===200){applyTriage(dir,id,res.j.triage,res.j.rev,res.j.outbox);return true;}
        if(res.code===409&&!retry){
          // The 409 body carries the rev the store is at NOW (a background runs[] write
          // bumps it every Stop of a measured run) — adopt it, then retry once. Re-pulling
          // /api/tickets alone could hand back a 20 s-old cached rev and 409 again.
          if(res.j&&res.j.rev!=null){(TIX.projects||[]).forEach(function(p){if(p.dir===dir)p.rev=res.j.rev;});return postTriage(dir,id,patch,true);}
          return fetch("/api/tickets").then(function(r){return r.json();}).then(function(d){TIX=d;return postTriage(dir,id,patch,true);});}
        // Anything else the server refused is SAID OUT LOUD. It used to return
        // false here and vanish: the operator clicked, nothing moved, and no
        // reason appeared anywhere. That silence is how four tickets sat `done`
        // locally for days while staying open on the helpdesk.
        hudToast((res.j&&res.j.error)?res.j.error:("Izmena odbijena ("+res.code+")."),true);
        return false;});
  }
  function applyTriage(dir,id,triage,rev,outbox){(TIX.projects||[]).forEach(function(p){if(p.dir!==dir)return;if(rev!=null)p.rev=rev;(p.tickets||[]).forEach(function(t){if(String(t.id)===String(id)){t.triage=triage||{};if(outbox!=null)t.outbox=outbox;}});});
    if(mode==="tickets"){renderTickets();}
    if(tixOpen&&tixOpen.dir===dir&&tixOpen.id===String(id))renderOutbox(dir,id);}
  /* --- module -> repo mapping: the user maps each module to a repo folder on disk --- */
  // POST the path as-is (server stores a local trusted path); mirrors postTriage.
  function postRepo(dir,repo){
    return fetch("/api/tickets/repo",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({dir:dir,repo:repo})}).then(function(r){return r.json().then(function(j){return {code:r.status,j:j};});})
      .then(function(res){if(res.code===200){applyRepo(res.j.dir,res.j.repo);return true;}return false;}).catch(function(){return false;});
  }
  // update the model + only the one visible row (no full rebuild, so an in-flight
  // "saved" note and other rows' half-typed input survive the server's SSE echo)
  function applyRepo(dir,repo){(TIX.projects||[]).forEach(function(p){if(p.dir===dir)p.folder=repo;});
    if(mode==="tickets")refreshModRow(dir,repo);}
  function refreshModRow(dir,repo){var host=$("tk-modmap");if(!host)return;
    Array.prototype.forEach.call(host.querySelectorAll(".tk-mm-row"),function(row){if(row.getAttribute("data-dir")!==String(dir))return;
      var mapped=!!(repo&&String(repo).trim()),cur=row.querySelector(".tk-mm-cur"),inp=row.querySelector(".tk-mm-in");
      if(cur){cur.textContent=mapped?repo:"unmapped";cur.className="tk-mm-cur"+(mapped?"":" unmapped");cur.title=repo||"";}
      if(inp&&document.activeElement!==inp)inp.value=repo||"";});}
  function renderModuleMap(){var host=$("tk-modmap");if(!host)return;
    var projs=(TIX.projects||[]).slice().sort(function(a,b){var x=String(a.module||a.dir).toLowerCase(),y=String(b.module||b.dir).toLowerCase();return x<y?-1:x>y?1:0;});
    host.innerHTML='<div class="tk-mm-head">Module &rarr; repo folder on disk</div>'+
      (projs.length?projs.map(function(p){var col=modColor(p.module||p.dir),mapped=!!(p.folder&&String(p.folder).trim());
        return '<div class="tk-mm-row" data-dir="'+esc(p.dir)+'">'+
          '<span class="tk-mm-mod" style="color:'+col+';border:1px solid '+col+'">'+esc(p.module||p.dir)+'</span>'+
          '<span class="tk-mm-key" title="module key">'+esc(p.dir)+'</span>'+
          '<span class="tk-mm-cur'+(mapped?"":" unmapped")+'" title="'+esc(p.folder||"")+'">'+(mapped?esc(p.folder):"unmapped")+'</span>'+
          '<input class="tk-mm-in" spellcheck="false" placeholder="C:\\path\\to\\repo" value="'+esc(p.folder||"")+'">'+
          '<button class="btn tk-mm-save">save</button><span class="tk-mm-ok"></span>'+
          '<div class="tk-mm-note"><label>stalna napomena za AI (važi za SVAKI tiket ovog projekta; merodavna za dizajn — npr. "tenant-based: generalizuj sve, pali/gasi po paketu, nikad hardkodovan tip prijave")</label>'+
          '<textarea class="tk-mm-noteta" rows="2" placeholder="npr. Aplikacija je multi-tenant za sve tipove biznisa: sve generalizovano i konfigurabilno po tenantu, svaka funkcionalnost gasiva feature flagom / paketom bez uticaja na ostatak…">'+esc(p.ai_note||"")+'</textarea>'+
          '<button class="btn tk-mm-notesave">save note</button><span class="tk-mm-noteok"></span></div>'+
        '</div>';}).join(""):'<div class="tix-nocom">No modules loaded.</div>');
    Array.prototype.forEach.call(host.querySelectorAll(".tk-mm-row"),function(row){
      var dir=row.getAttribute("data-dir"),inp=row.querySelector(".tk-mm-in"),ok=row.querySelector(".tk-mm-ok");
      function save(){var v=(inp.value||"").trim();ok.style.color="var(--hud-dim)";ok.textContent="\u2026";
        postRepo(dir,v).then(function(good){ok.style.color=good?"var(--acc)":"var(--bad)";ok.textContent=good?"saved \u2713":"failed";setTimeout(function(){ok.textContent="";},2000);});}
      row.querySelector(".tk-mm-save").onclick=save;
      var nta=row.querySelector(".tk-mm-noteta"),nok=row.querySelector(".tk-mm-noteok");
      row.querySelector(".tk-mm-notesave").onclick=function(){nok.style.color="var(--hud-dim)";nok.textContent="\u2026";
        fetch("/api/tickets/project_note",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({dir:dir,note:nta.value||""})})
          .then(function(r){return r.ok;}).catch(function(){return false;})
          .then(function(good){nok.style.color=good?"var(--acc)":"var(--bad)";nok.textContent=good?"saved \u2713":"failed";setTimeout(function(){nok.textContent="";},2000);
            if(good)(TIX.projects||[]).forEach(function(pp){if(pp.dir===dir)pp.ai_note=nta.value||"";});});};
      inp.onkeydown=function(e){if(e.key==="Enter"){e.preventDefault();save();}};});}
  function fetchTickets(){fetch("/api/tickets").then(function(r){return r.json();}).then(function(d){TIX=d||{projects:[]};buildTixFilters();renderTickets();renderModuleMap();
    $("tk-sub").innerHTML=d.error?('<b style="color:#fb7185">'+esc(d.error)+'</b>'):('<b>'+allTix().length+'</b> tickets · '+(TIX.projects||[]).length+' modules · root '+esc(TIX.root||""));
    refreshGemBudget();   // tickets view load (incl. after a rescan re-pulls the store)
    refreshTkProfilesToggle();   // F4: tickets view load — keeps the pill honest if flipped elsewhere
  }).catch(function(){$("tk-sub").textContent="server not reachable";});}
  function tixPills(host,items,set){$(host).innerHTML=items.map(function(x){return '<span class="tk-pill'+(set.has(x)?" on":"")+'" data-v="'+esc(x)+'">'+esc(x)+'</span>';}).join("");
    Array.prototype.forEach.call($(host).querySelectorAll(".tk-pill"),function(el){el.onclick=function(){var v=el.getAttribute("data-v");if(set.has(v))set.delete(v);else set.add(v);buildTixFilters();renderTickets();};});}
  function buildTixFilters(){var all=allTix();
    tixPills("tk-fmod",uniq((TIX.projects||[]).map(function(p){return p.module;})),tixMod);
    tixPills("tk-fcat",uniq(all.map(function(x){return x.t.category;})),tixCat);
    tixPills("tk-fstat",uniq(["active"].concat(all.map(function(x){return x.t.status;}))),tixStat);}   // "active" pill always present: it is the default filter
  /* --- sorting + row helpers for the tickets table (all client-side over loaded tickets) --- */
  var TIX_PRANK={critical:0,major:1,minor:2,none:3};
  function prOf(t){return (t.triage&&t.triage.priority_override)||t.priority;}
  function priRank(p){return TIX_PRANK[priClass(p)];}
  // d.m.Y date-only; "" for empty/bad. (fmtAt gives a full LOCALE string; this gives a fixed d.m.Y date — different jobs.)
  function fmtDMY(s){var t=tsOf(s);if(t==null)return "";var d=new Date(t);
    return ("0"+d.getDate()).slice(-2)+"."+("0"+(d.getMonth()+1)).slice(-2)+"."+d.getFullYear();}
  function dlOf(t){return t.helpdesk?tsOf(t.helpdesk.deadline):null;}
  // the "h" column value: AI estimate wins over the helpdesk one (matches the server's row.estimate.by precedence)
  function estOf(t){var e=t&&t.estimate;if(!e)return null;return e.ai_h!=null?e.ai_h:(e.helpdesk_h!=null?e.helpdesk_h:null);}
  // trim a float to at most 2dp with no trailing zeros ("2.5", "3", "1.83" — never "2.50")
  function estH(v){if(v==null)return "";var n=Math.round(v*100)/100;return String(n);}
  function commentCount(t){return (t.helpdesk&&typeof t.helpdesk.comment_count==="number")?t.helpdesk.comment_count:(Array.isArray(t.comments)?t.comments.length:0);}
  function tixDone(t){return !!(t.helpdesk&&t.helpdesk.is_closed)||!!(t.triage&&(t.triage.state==="done"||t.triage.state==="solved_manually"));}
  function isOverdue(t){var d=dlOf(t);return d!=null&&d<Date.now()&&!tixDone(t);}
  function cmpNum(x,y,dir){if(x==null&&y==null)return 0;if(x==null)return 1;if(y==null)return -1;return (x<y?-1:x>y?1:0)*dir;} // nulls last, always
  function cmpStr(x,y,dir){x=String(x==null?"":x).trim().toLowerCase();y=String(y==null?"":y).trim().toLowerCase();
    if(!x&&!y)return 0;if(!x)return 1;if(!y)return -1;return (x<y?-1:x>y?1:0)*dir;}
  function newestFirst(A,B){return cmpNum(tsOf(A.created),tsOf(B.created),-1);}  // created descending, empties last (nulls-last lives in cmpNum before the dir flip)
  function cmpRows(a,b){var A=a.t,B=b.t,dir=tixSort.dir==="desc"?-1:1,k=tixSort.key;
    if(k==="created")  return cmpNum(tsOf(A.created),tsOf(B.created),dir)||newestFirst(A,B);
    if(k==="deadline") return cmpNum(dlOf(A),dlOf(B),dir)||newestFirst(A,B);
    if(k==="module")   return cmpStr(a.mod,b.mod,dir)||newestFirst(A,B);
    if(k==="category") return cmpStr(A.category,B.category,dir)||newestFirst(A,B);
    if(k==="comments") return ((commentCount(A)-commentCount(B))*dir)||newestFirst(A,B);
    if(k==="est")       return cmpNum(estOf(A),estOf(B),dir)||newestFirst(A,B);
    return ((priRank(prOf(A))-priRank(prOf(B)))*dir)||newestFirst(A,B);}  // priority (default)
  var TIX_DEFDIR={created:"desc",comments:"desc",deadline:"asc",priority:"asc",module:"asc",category:"asc",est:"desc"};
  function updateSortIndicators(){Array.prototype.forEach.call(document.querySelectorAll("#view-tickets th.srt"),function(th){
    if(th.getAttribute("data-sort")===tixSort.key)th.setAttribute("data-sorted",tixSort.dir);else th.removeAttribute("data-sorted");});}
  function renderTickets(){updateSortIndicators();renderReopenLane();var rows=allTix().filter(function(x){
      if(tixIsReopenOpen(x.t))return false;   // a reopened-OPEN ticket lives ONLY in the Vraćeni/Dopune lane, never the table (ONE membership helper — tixIsReopenOpen)
      if(tixMod.size&&!tixMod.has(x.mod))return false;if(tixCat.size&&!tixCat.has(x.t.category))return false;if(tixStat.size&&!tixStat.has(x.t.status))return false;
      if(tixQ){if((x.t.id+" "+x.t.title).toLowerCase().indexOf(tixQ)<0)return false;}return true;}).sort(cmpRows);
    $("tk-rows").innerHTML=rows.map(function(x){var t=x.t,col=modColor(x.mod),key=x.dir+"|"+t.id,ts=(t.triage&&t.triage.state)||"";var pr=(t.triage&&t.triage.priority_override)||t.priority;
      var opts='<option value=""'+(ts===""?" selected":"")+'>&mdash; unset &mdash;</option>'+TK_STATES.map(function(s){return '<option'+(s===ts?" selected":"")+'>'+s+'</option>';}).join("");
      var crd=fmtDMY(t.created),dld=fmtDMY(t.helpdesk&&t.helpdesk.deadline),cc=commentCount(t),over=isOverdue(t);
      return '<tr class="'+(tixSel[key]?"sel-row":"")+'" data-dir="'+esc(x.dir)+'" data-id="'+esc(t.id)+'">'+
        '<td><input type="checkbox" class="tk-cbx tk-rcbx" data-key="'+esc(key)+'" data-dir="'+esc(x.dir)+'" data-id="'+esc(t.id)+'" '+(tixSel[key]?"checked":"")+'></td>'+
        '<td class="tk-id nm" style="--mc:'+col+'">#'+esc(t.id)+'</td>'+
        '<td class="tk-ttl">'+esc(t.title)+'</td>'+
        '<td class="tk-who" title="'+esc(t.customer||"")+'">'+esc(t.customer||"—")+'</td>'+
        '<td><span class="tk-mod" style="color:'+col+';border:1px solid '+col+'">'+esc(x.mod)+'</span></td>'+
        '<td>'+esc(t.category||"—")+'</td>'+
        '<td><span class="tk-pri '+priClass(pr)+'">'+esc(pr||"—")+'</span></td>'+
        '<td class="tk-date">'+(crd?esc(crd):'<span class="tk-dash">&mdash;</span>')+'</td>'+
        '<td class="tk-date tk-dl'+(over?" over":"")+'" title="'+(over?"overdue":"")+'">'+(dld?esc(dld):'<span class="tk-dash">&mdash;</span>')+'</td>'+
        '<td class="tk-est">'+tkEstCell(t)+'</td>'+
        '<td><span class="tk-cc'+(cc?"":" zero")+'">'+cc+'</span></td>'+
        '<td>'+esc(t.status||"—")+'</td>'+
        '<td><select class="tk-tstate" style="--sc:'+(TK_SCOL[ts]||"var(--hud-mut)")+'">'+opts+'</select></td>'+
        '<td>'+tkAiCell(t)+'</td></tr>';}).join("");
    Array.prototype.forEach.call($("tk-rows").querySelectorAll("tr"),function(tr){tr.onclick=function(){openTix(tr.getAttribute("data-dir"),tr.getAttribute("data-id"));};});
    Array.prototype.forEach.call($("tk-rows").querySelectorAll(".tk-tstate"),function(sl){var tr=sl.closest("tr");sl.onclick=function(e){e.stopPropagation();};sl.onchange=function(){sl.style.setProperty("--sc",TK_SCOL[sl.value]||"var(--hud-mut)");postTriage(tr.getAttribute("data-dir"),tr.getAttribute("data-id"),{state:sl.value}).then(function(ok){if(!ok){sl.style.setProperty("--sc","var(--bad)");sl.title="čuvanje nije uspelo (stanje se promenilo u međuvremenu) — osveži";}});};});
    Array.prototype.forEach.call($("tk-rows").querySelectorAll(".tk-rcbx"),function(c){c.onclick=function(e){e.stopPropagation();var k=c.getAttribute("data-key");if(c.checked)tixSel[k]={dir:c.getAttribute("data-dir"),id:c.getAttribute("data-id")};else delete tixSel[k];c.closest("tr").classList.toggle("sel-row",c.checked);syncBulk();};});
    $("tk-empty").style.display=rows.length?"none":"block";$("tk-empty").textContent=rows.length?"":"No tickets match. (Claude pulls tickets into .claude/tiketi.json via /tickets-pull — Phase 3.)";}
  // the AI column: "AI ✓" when the Gemini/Claude TRIAGE (analysis.means/needs) exists,
  // "📖" when the reader's persisted "predlog upita" (reading.prompt) exists
  function tkHasTriage(t){var a=t&&t.analysis;return !!(a&&(a.means||a.needs||a.summary));}
  function tkHasReading(t){var r=t&&t.reading;return !!(r&&r.prompt);}
  function tkAiCell(t){var out="";
    out+=tkHasTriage(t)?'<span class="tk-ai" title="trijaža ('+esc((t.analysis&&t.analysis.by)||"")+' '+esc(fmtAt((t.analysis&&t.analysis.updated_at)||""))+')">AI ✓</span>':'<span class="tk-ai none">—</span>';
    if(tkHasReading(t))out+=' <span class="tk-ai rd" title="predlog upita sačuvan '+esc(fmtAt((t.reading&&t.reading.updated_at)||""))+'">&#128214;</span>';
    var w=t.work||{};
    if(w.open)out+=' <span class="tk-wk-pill" title="Claude radi na ovom tiketu (u toku)">&#9201;</span>';
    else if(w.sessions>0){var mini=tkWorkMini(w.hours);if(mini)out+='<div class="tk-wk-mini" title="'+esc(tkWorkSessText(w.sessions)+" · "+tkWorkDur(w.hours,w.sessions))+'">'+esc(mini)+'</div>';}
    return out;}
  // the "h" column: row.estimate (always present, all four keys always present — see /api/tickets contract).
  // AI wins over helpdesk (server's own `by` precedence); actual_h (measured) rides along, muted, when > 0.
  function tkEstCell(t){var est=(t&&t.estimate)||{};var h=est.ai_h!=null?est.ai_h:est.helpdesk_h;
    if(h==null)return '<span class="tk-dash">&mdash;</span>';
    var isAi=est.ai_h!=null;
    var ai=(t.triage&&t.triage.ai_estimate)||{};
    var ttl=isAi?("AI procena"+(ai.basis?(" · zašto: "+ai.basis):"")):"";
    var out='<span class="'+(isAi?"ai":"hd")+'"'+(ttl?(' title="'+esc(ttl)+'"'):'')+'>'+esc(estH(h))+'</span>';
    if(est.actual_h!=null&&est.actual_h>0)out+=' <span class="act">/ '+esc(estH(est.actual_h))+'</span>';
    return out;}
  function syncBulk(){var n=Object.keys(tixSel).length;$("tk-bulk").classList.toggle("show",n>0);$("tk-bulkn").textContent=n;}
  // find a ticket row {t,mod,dir,rev} by dir+id across all projects
  function tixFind(dir,id){var f=null;allTix().forEach(function(x){if(x.dir===dir&&String(x.t.id)===String(id))f=x;});return f;}
  function tixRole(r){return (r==="customer"||r==="engineer")?r:"other";}
  // attachments -> links labelled "attachment"; safeUrl() blocks non-http(s) hrefs (XSS guard)
  function tixAttsHtml(list){return (Array.isArray(list)?list:[]).map(function(a){var u=safeUrl(a&&a.url);
    return u?'<a class="tix-att" href="'+esc(u)+'" target="_blank" rel="noopener" title="'+esc((a&&a.name)||"")+'">attachment</a>':'';}).join("");}
  // comment thread — every field is untrusted helpdesk content, so esc()/safeUrl() throughout
  function tixCommentsHtml(list){if(!Array.isArray(list)||!list.length)return '<div class="tix-nocom">No comments</div>';
    return list.map(function(c){var role=tixRole(c.author_role),atts=tixAttsHtml(c.attachments);
      return '<div class="tix-cmt r-'+role+'"><div class="tix-cmt-h"><span class="tix-cmt-a">'+esc(c.author||"—")+'</span>'+
        '<span class="tix-role r-'+role+'">'+role+'</span><span class="tix-cmt-t">'+esc(fmtAt(c.at))+'</span></div>'+
        '<div class="tix-cmt-b">'+esc(c.body||"")+'</div>'+(atts?'<div class="tix-cmt-att">'+atts+'</div>':'')+'</div>';}).join("");}
  // the user's drafted comments (outbox); unposted ones can be removed by index
  function renderOutbox(dir,id){var host=$("tx-outbox");if(!host)return;var f=tixFind(dir,id);var box=(f&&Array.isArray(f.t.outbox))?f.t.outbox:[];
    host.innerHTML=box.map(function(d,i){var posted=!!d.posted,atts=tixAttsHtml(d.attachments);
      return '<div class="tix-draft'+(posted?" posted":"")+'"><div class="tix-draft-b">'+esc(d.body||"")+
        (atts?'<div class="tix-cmt-att">'+atts+'</div>':'')+
        '<span class="tix-draft-tag">'+(posted?"sent":"draft — sent on close")+'</span></div>'+
        (posted?"":'<span class="tix-draft-x" data-i="'+i+'" title="remove draft">&times;</span>')+'</div>';}).join("");
    Array.prototype.forEach.call(host.querySelectorAll(".tix-draft-x"),function(el){el.onclick=function(){postTriage(dir,id,{outbox_remove:parseInt(el.getAttribute("data-i"),10)});};});}
  function openTix(dir,id){var f=tixFind(dir,id);if(!f)return;var t=f.t,col=modColor(f.mod),an=t.analysis||{},tri=t.triage||{};
    tixOpen={dir:dir,id:String(id)};
    $("tix-id").textContent="#"+t.id;$("tix-id").style.color=col;$("tix-title").textContent=t.title;
    var ho=$("tix-open"),hu=safeUrl(t.url);
    if(hu){ho.href=hu;ho.style.display="";}else{ho.removeAttribute("href");ho.style.display="none";}
    var stateOpts='<option value=""'+((tri.state||"")===""?" selected":"")+'>— unset —</option>'+TK_STATES.map(function(s){return '<option'+(s===(tri.state||"")?" selected":"")+'>'+s+'</option>';}).join("");
    var priOpts=["","critical","major","minor"].map(function(p){return '<option value="'+p+'"'+((tri.priority_override||"")===p?" selected":"")+'>'+(p||"— keep source ("+esc(t.priority||"—")+") —")+'</option>';}).join("");
    var h='<div class="lbl">Original (immutable)</div><div class="orig">'+esc(t.desc||"—")+'</div>'+
      '<div class="kv"><span>customer</span><b>'+esc(t.customer||"—")+'</b></div>'+
      tkRatingKv(t)+
      tkCreatorProfileKv(t)+
      '<div class="kv"><span>module · category</span><b>'+esc(f.mod)+' · '+esc(t.category||"—")+'</b></div>'+
      '<div class="kv"><span>priority · status (source)</span><b>'+esc(t.priority||"—")+' · '+esc(t.status||"—")+'</b></div>'+
      tkSentSummaryKv(t)+
      tkShotsKv(t)+
      tkWorkKv(t.work)+
      tkEstKv(t)+
      tkRoundsBlock(t)+
      tkRunsBlock(t.runs)+
      (safeUrl(t.attachment)?'<div class="kv"><span>attachment</span><b><a href="'+esc(safeUrl(t.attachment))+'" target="_blank" rel="noopener">open ↗</a></b></div>':'')+
      (t.notes?('<div class="lbl">Notes</div><div class="orig">'+esc(t.notes)+'</div>'):'')+
      '<div class="lbl">Comments</div>'+
      '<div class="tix-thread" id="tx-thread">'+tixCommentsHtml(t.comments)+'</div>'+
      '<div class="lbl">New comment</div>'+
      '<div class="tix-compose">'+
        '<textarea id="tx-newcom" rows="3" placeholder="Write a comment…"></textarea>'+
        '<div class="tix-compose-row"><button class="btn" id="tx-addcom">Add comment</button></div>'+
        '<div class="tix-hint">Attach a file to a comment via a link on the helpdesk (the API doesn\'t accept files).</div>'+
        '<div class="tix-drafts" id="tx-outbox"></div>'+
      '</div>'+
      '<div class="lbl">Your triage</div>'+
      '<div class="tk-field"><label>state</label><select class="tk-fsel" id="tx-state">'+stateOpts+'</select></div>'+
      '<div class="tk-field"><label>priority override</label><select class="tk-fsel" id="tx-pri">'+priOpts+'</select></div>'+
      '<div class="tk-field"><label>your order / predicted hours</label><input class="tk-fsel" id="tx-order" value="'+esc(tri.order!=null?tri.order:"")+'" placeholder="e.g. 2"></div>'+
      '<div class="tk-field"><label>kontekst za AI — tvoja reč je MERODAVNA (šta korisnik stvarno hoće; koji prilog je samo primer iz druge aplikacije; šta ignorisati)</label><textarea id="tx-ctx" rows="3" placeholder="npr. Slika je primer iz ODS-a, nije naša ruta. Treba: prijava NEPODUDARANJA podataka (popis kaže 10 TV, ima ih 5)…">'+esc(tri.context||"")+'</textarea></div>'+
      '<div class="tk-field"><label>rezolucija za KUPCA (jedino što ide na helpdesk pri zatvaranju — bez commit-ova, fajlova, internih napomena)</label><textarea id="tx-res" rows="3" placeholder="Šta je urađeno i kako se koristi, jezikom kupca.">'+esc(tri.resolution||"")+'</textarea></div>'+
      '<div class="tk-field"><label>interni izveštaj (ostaje u store-u; commit-ovi, fajlovi, otvorene stavke)</label><textarea id="tx-rep" rows="2">'+esc(tri.report||"")+'</textarea></div>'+
      '<div style="display:flex;gap:.5rem;justify-content:flex-end;margin-top:.4rem"><button class="btn" id="tx-save">Save triage</button><span id="tx-saved" style="color:var(--acc);font-size:.72rem;align-self:center"></span></div>';
    // AI triage (Rescan / analyze_gemini / Claude triage) — the REAL schema:
    // means / needs / confidence / missing / plan / suggested_agents / suggested_skills / complexity / by / updated_at
    // (the old summary/real_problem/suggested_solution shape is still shown if a legacy row has it)
    var chips=function(arr){return (Array.isArray(arr)&&arr.length)?('<div class="chips">'+arr.map(function(x){return '<span class="chip">'+esc(x)+'</span>';}).join("")+'</div>'):"";};
    var brainBlock=function(ag,sk){ag=Array.isArray(ag)?ag:[];sk=Array.isArray(sk)?sk:[];if(!ag.length&&!sk.length)return "";
      return '<div class="interp"><div class="t">Brain — agenti · skillovi</div><div class="chips">'+ag.map(function(x){return '<span class="chip ag">'+esc(x)+'</span>';}).join("")+(ag.length&&sk.length?'<span class="chip-sep">·</span>':"")+sk.map(function(x){return '<span class="chip sk">'+esc(x)+'</span>';}).join("")+'</div></div>';};
    if(tkHasTriage(t)){h+='<div class="lbl">AI trijaža <span class="tix-when">'+esc(an.by||"")+' · '+esc(fmtAt(an.updated_at||""))+(an.confidence?' · pouzdanost '+esc(an.confidence):"")+(an.complexity?' · '+esc(an.complexity):"")+'</span></div>';
      if(an.summary)h+='<div class="interp"><div class="t">summary</div>'+esc(an.summary)+'</div>';
      if(an.means)h+='<div class="interp"><div class="t">šta misli</div>'+esc(an.means)+'</div>';
      if(an.needs||an.real_problem)h+='<div class="interp"><div class="t">šta stvarno treba</div>'+esc(an.needs||an.real_problem)+'</div>';
      if(an.missing)h+='<div class="interp"><div class="t">nedostaje / pitati</div>'+esc(an.missing)+'</div>';
      if(an.suggested_solution)h+='<div class="interp"><div class="t">suggested solution</div>'+esc(an.suggested_solution)+'</div>';
      if(Array.isArray(an.plan)&&an.plan.length)h+='<div class="interp"><div class="t">plan</div><ol class="tix-plan">'+an.plan.map(function(x){return '<li>'+esc(x)+'</li>';}).join("")+'</ol></div>';
      h+=brainBlock(an.suggested_agents,an.suggested_skills);
      h+=chips(an.affected_files);}
    else{h+='<div class="lbl">AI trijaža</div><div style="color:var(--hud-dim);font-size:.76rem">Nije trijažirano — Rescan (Gemini) ili /brain:tickets triage puni ovaj blok.</div>';}
    // the reader's persisted "predlog upita" (Analiziraj + predlog upita)
    var rd=t.reading||{};
    h+='<div class="lbl">Predlog upita (ticket-reader)'+(tkHasReading(t)?' <span class="tix-when">'+esc(rd.by||"")+' · '+esc(fmtAt(rd.updated_at||""))+(rd.complexity?' · '+esc(rd.complexity):"")+'</span>':"")+'</div>';
    if(tkHasReading(t)){
      h+='<div class="interp"><div class="t">šta stvarno treba</div>'+esc(rd.real_need||"—")+'</div>';
      if(rd.page)h+='<div class="kv"><span>stranica / ruta</span><b>'+esc(rd.page)+'</b></div>';
      h+=brainBlock(rd.suggested_agents,rd.suggested_skills);
      if(Array.isArray(rd.open_questions)&&rd.open_questions.length)h+='<div class="interp"><div class="t">otvorena pitanja</div>'+rd.open_questions.map(function(q){return '· '+esc(q);}).join("<br>")+'</div>';
      if(Array.isArray(rd.attachments)&&rd.attachments.length)h+='<div class="interp"><div class="t">prilozi ('+rd.attachments.length+')</div><div class="chips">'+rd.attachments.map(function(a){a=a||{};var u=safeUrl(a.url);var lbl=esc(a.name||"?")+' <i>'+esc(a.kind||"?")+(a.status==="read"?"":" · "+esc(a.status||"?"))+'</i>';return u?'<a class="chip att" href="'+esc(u)+'" target="_blank" rel="noopener">'+lbl+'</a>':'<span class="chip att">'+lbl+'</span>';}).join("")+'</div></div>';
      h+='<div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.4rem"><button class="btn" id="tx-showread">&#128302; Prikaži ceo predlog upita</button><button class="btn" id="tx-reread" title="Ponovo pošalje tiket AI-ju i PREGAZI sačuvani predlog">&#8635; Analiziraj ponovo</button></div>';}
    else{h+='<div style="color:var(--hud-dim);font-size:.76rem;margin-bottom:.4rem">Nema sačuvanog predloga upita.</div><button class="btn" id="tx-reread">&#128302; Analiziraj + predlog upita</button>';}
    $("tix-body").innerHTML=h;$("tixmodal").classList.add("open");
    renderOutbox(dir,id);
    if($("tx-sentsum"))$("tx-sentsum").onclick=function(){closeTix();tkSentOpen({module:f.dir,ticket:String(t.id)});};   // log key = file stem (dir), not the display module
    // same idiom, same key: the manifest's ticket is "<file stem>#<id>" (scripts/visual/shoot.py)
    if($("tx-shots"))$("tx-shots").onclick=function(){closeTix();tkShOpen({ticket:f.dir+"#"+String(t.id)});};
    // "gotovo": the operator knows this run is over, instead of waiting for the idle reaper.
    // Same finaliser SessionEnd uses (server-side); re-pull + re-open so the closed run's
    // summary shows up in "poslednje sesije" right away.
    if($("tx-workend"))$("tx-workend").onclick=function(){var btn=this,errEl=$("tx-workend-err");
      var wid=t.work&&t.work.open_work_id;if(!wid)return;
      if(errEl)errEl.textContent="";btn.disabled=true;btn.textContent="…";
      aiPost("/api/tickets/work/end",{work_id:wid}).then(function(d){
        if(d&&d.error){btn.disabled=false;btn.innerHTML="&#10003; gotovo";if(errEl)errEl.textContent=d.error;return;}
        fetch("/api/tickets").then(function(r){return r.json();}).then(function(dd){
          TIX=dd||{projects:[]};buildTixFilters();renderTickets();openTix(dir,id);
        }).catch(function(){openTix(dir,id);});
      }).catch(function(e){btn.disabled=false;btn.innerHTML="&#10003; gotovo";if(errEl)errEl.textContent=aiErrText(e);});};
    // Phase 7 "nauči" — the SAME two-step the lane's Klasifikuj uses (do NOT invent a
    // new launch path): POST /reopen/learn -> {prompt,cwd} -> the EXISTING
    // /api/claude/launch door. The button only launches; the propose-only/consult-only
    // discipline lives in the launched learning_prompt. Success/error land in the
    // adjacent note span (accent/bad inline colour — no app.css touch).
    if($("tx-reopenlearn"))$("tx-reopenlearn").onclick=function(){var btn=this,note=$("tx-reopenlearn-note"),lbl=btn.innerHTML;
      if(note){note.textContent="";note.style.color="var(--acc)";}
      btn.disabled=true;btn.textContent="Pokrećem…";
      function fail(m){btn.disabled=false;btn.innerHTML=lbl;if(note){note.style.color="var(--bad)";note.textContent=m;}}
      aiPost("/api/tickets/reopen/learn",{dir:dir,id:id}).then(function(d){
        if(d&&d.error){fail("Nauči nije uspelo: "+d.error);return;}
        if(!d||!d.prompt){fail("Nema prompta za učenje.");return;}
        var payload={prompt:d.prompt};if(d.cwd)payload.cwd=d.cwd;
        aiPost("/api/claude/launch",payload).then(function(r){btn.disabled=false;btn.innerHTML=lbl;
          if(note){note.style.color=(r&&r.error)?"var(--bad)":"var(--acc)";
            note.textContent=(r&&r.error)?("Pokretanje nije uspelo: "+r.error):"Claude pokrenut ✓ — predlog stiže po završetku.";}
        }).catch(function(e){fail("Pokretanje nije uspelo: "+aiErrText(e));});
      }).catch(function(e){fail("Nauči nije uspelo: "+aiErrText(e));});};
    if($("tx-showread"))$("tx-showread").onclick=function(){tkAnalyzeIds([String(t.id)],false,"#"+t.id);};
    if($("tx-reread"))$("tx-reread").onclick=function(){if(tkHasReading(t)&&!confirm("Tiket #"+t.id+" već ima sačuvan predlog upita.\nPregaziti ga novom AI analizom?"))return;tkAnalyzeIds([String(t.id)],true,"#"+t.id);};
    $("tx-addcom").onclick=function(){var ta=$("tx-newcom"),v=(ta.value||"").trim();if(!v)return;
      postTriage(dir,id,{outbox_add:v}).then(function(ok){if(ok)ta.value="";});};
    var ctxBefore=(tri.context||"");
    $("tx-save").onclick=function(){var patch={state:$("tx-state").value,priority_override:$("tx-pri").value,order:$("tx-order").value,context:$("tx-ctx").value,report:$("tx-rep").value,resolution:$("tx-res").value};
      if(patch.state==="done"&&!(patch.resolution||"").trim()&&!confirm("Stanje 'done' bez rezolucije za kupca: tiket će ostati OTVOREN na helpdesku dok je ne upišeš. Sačuvati ipak?"))return;
      postTriage(dir,id,patch).then(function(ok){$("tx-saved").textContent=ok?"saved ✓":"save failed";setTimeout(function(){$("tx-saved").textContent="";},2000);
        // a changed AI context is worth a re-read: the note is AUTHORITATIVE for the reader
        if(ok&&(patch.context||"").trim()!==ctxBefore.trim()&&(patch.context||"").trim()){ctxBefore=patch.context;
          if(confirm("Kontekst za AI je promenjen. Ponovo analizirati tiket #"+t.id+" sa ovom napomenom"+(tkHasReading(t)?" (pregazi sačuvani predlog)":"")+"?"))tkAnalyzeIds([String(t.id)],true,"#"+t.id);}});};}
  function closeTix(){$("tixmodal").classList.remove("open");tixOpen=null;}
  $("tix-x").onclick=closeTix;
  $("tix-bd").onclick=closeTix;
  $("tk-search").oninput=function(){tixQ=this.value.toLowerCase();renderTickets();};
  Array.prototype.forEach.call(document.querySelectorAll("#view-tickets th.srt"),function(th){
    th.onclick=function(){var k=th.getAttribute("data-sort");
      if(tixSort.key===k)tixSort.dir=(tixSort.dir==="asc")?"desc":"asc";
      else{tixSort.key=k;tixSort.dir=TIX_DEFDIR[k]||"asc";}
      renderTickets();};});   // renderTickets() refreshes the ▲/▼ indicator via updateSortIndicators()
  function doRescan(){var rb=$("tk-refresh");if(rb){rb.disabled=true;rb.textContent="↻ scanning…";}fetch("/api/tickets/rescan",{method:"POST"}).catch(function(){});fetchTickets();}
  $("tk-refresh").onclick=doRescan;
  $("tk-mapbtn").onclick=function(){var on=$("tk-modmap").classList.toggle("show");this.classList.toggle("act",on);if(on)renderModuleMap();};
  $("tk-allcbx").onclick=function(){var on=this.checked;Array.prototype.forEach.call($("tk-rows").querySelectorAll(".tk-rcbx"),function(c){c.checked=on;var k=c.getAttribute("data-key");if(on)tixSel[k]={dir:c.getAttribute("data-dir"),id:c.getAttribute("data-id")};else delete tixSel[k];c.closest("tr").classList.toggle("sel-row",on);});syncBulk();};
  $("tk-bulkstate").onchange=function(){var v=this.value,self=this;if(!v)return;var items=Object.keys(tixSel).map(function(k){return tixSel[k];});(function seq(i){if(i>=items.length){self.value="";return;}postTriage(items[i].dir,items[i].id,{state:v}).then(function(){seq(i+1);});})(0);};
  $("tk-clear").onclick=function(){tixSel={};syncBulk();renderTickets();};
  addEventListener("keydown",function(e){if(e.key==="Escape"&&$("tixmodal").classList.contains("open"))closeTix();});

  /* ---------- Gemini "analyze selected -> formatted prompt" (bulk-bar AI action) ---------- */
  // Reuses: aiPost() (shared error-surfacing POST), the /api/claude/launch door (same one Ask
  // Claude uses), modColor() for the per-module id tint, and the #tixmodal shell (scoped twin
  // #tkanmodal, styled via grouped selectors in app.css). index.html is off-limits, so both the
  // bulk-bar button and the result modal are injected from here.
  var tkAnBusy=false;   // in-flight guard for the shared ~15 RPM Gemini quota
  // distinct selected ticket ids, in selection order (contract: POST {ids:[...]})
  function tkSelIds(){var out=[],seen={};Object.keys(tixSel).forEach(function(k){var id=tixSel[k].id;if(id!=null&&!seen[id]){seen[id]=1;out.push(id);}});return out;}
  function tkAnModal(){var m=$("tkanmodal");if(m)return m;
    m=document.createElement("div");m.id="tkanmodal";
    m.innerHTML='<div class="bd" id="tkan-bd"></div><div class="dlg">'+
      '<div class="dh"><h3>&#128302; Predlog upita</h3><span class="tkan-sub" id="tkan-sub"></span><span class="x" id="tkan-x">&times;</span></div>'+
      '<div class="db" id="tkan-body"></div></div>';
    document.body.appendChild(m);
    $("tkan-x").onclick=tkAnClose;$("tkan-bd").onclick=tkAnClose;
    return m;}
  function tkAnOpen(){tkAnModal().classList.add("open");}
  function tkAnTitle(html){var h=document.querySelector("#tkanmodal .dh h3");if(h)h.innerHTML=html;}   // shared with the merge action
  function tkAnClose(){var m=$("tkanmodal");if(m)m.classList.remove("open");}
  function tkAnLoading(n){$("tkan-body").innerHTML='<div class="tkan-load"><span class="tkan-spin"></span>Analiziram '+n+' '+(n===1?"tiket":"tiketa")+'&hellip; (Gemini ide tiket-po-tiket, potraja)</div>';}
  function tkAnFlash(note,txt,cls){if(!note)return;note.textContent=txt;note.className="tkan-note "+(cls||"");
    if(cls)setTimeout(function(){if(note.textContent===txt){note.textContent="";note.className="tkan-note";}},2200);}
  // copy the prompt; clipboard API needs a secure context (localhost is secure), else a textarea fallback for LAN/http
  function tkAnCopy(text,note){
    function fallback(){try{var ta=document.createElement("textarea");ta.value=text;ta.style.position="fixed";ta.style.top="-9999px";ta.style.opacity="0";document.body.appendChild(ta);ta.focus();ta.select();var ok=document.execCommand("copy");document.body.removeChild(ta);tkAnFlash(note,ok?"kopirano ✓":"kopiranje nije uspelo",ok?"ok":"err");}catch(e){tkAnFlash(note,"kopiranje nije uspelo","err");}}
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(function(){tkAnFlash(note,"kopirano ✓","ok");},fallback);}else{fallback();}}
  // reuse the same door Ask Claude uses (/api/claude/launch via aiPost); pass cwd so
  // Claude opens IN the ticket's module repo (server validates it via is_known_repo)
  // `tickets` ([{module,ticket}]) is optional and MEASURES the run server-side — the reply
  // carries `work_id` when it did, and the note flashes that instead of the bare "pokrenut".
  function tkAnLaunch(prompt,cwd,btn,note,tickets){if(!prompt)return;if(btn)btn.disabled=true;
    if(note){note.textContent="pokrećem…";note.className="tkan-note";}
    var payload={prompt:prompt}; if(cwd)payload.cwd=cwd; if(Array.isArray(tickets)&&tickets.length)payload.tickets=tickets;
    aiPost("/api/claude/launch",payload).then(function(d){if(btn)btn.disabled=false;
      if(d&&d.error)tkAnFlash(note,"neuspelo: "+d.error,"err");
      else tkAnFlash(note,d&&d.work_id?"Claude pokrenut ✓ · merim vreme":"Claude pokrenut ✓","ok");
    }).catch(function(e){if(btn)btn.disabled=false;tkAnFlash(note,"neuspelo: "+aiErrText(e),"err");});}
  // a deterministic context header prepended to the launch/copy prompt so Claude (and
  // the reader) knows the ticket, module and repo — folded INTO the shown prompt so
  // "what you copy/launch == what you see". The repo also becomes the launch cwd.
  function tkAnCtx(r){r=r||{};
    var h="Tiket #"+(r.ticket_id!=null?r.ticket_id:"?")+(r.title?(" — "+r.title):"")+"\n";
    h+="Projekat / modul: "+(r.module||"?");
    if(r.repo)h+="  ·  Repo: "+r.repo;
    h+="\n(Radiš u ovom repozitorijumu na tiketu ispod — potvrdi repo/granu, pa implementiraj.)\n\n";
    return h;}
  // one card per result; an `error` entry renders the error instead of the prompt block
  function tkAnRender(results){var host=$("tkan-body");results=Array.isArray(results)?results:[];
    if($("tkan-sub"))$("tkan-sub").textContent=results.length+(results.length===1?" tiket":" tiketa");
    if(!results.length){host.innerHTML='<div class="tkan-empty">Nema rezultata.</div>';return;}
    host.innerHTML=results.map(function(r,i){r=r||{};var col=modColor(r.module||"");
      var head='<div class="tkan-h"><span class="tkan-id" style="color:'+col+'">#'+esc(r.ticket_id!=null?r.ticket_id:"?")+'</span>'+
        '<span class="tkan-dot">&middot;</span><span class="tkan-t">'+esc(r.title||"—")+'</span>'+
        '<span class="tkan-dot">&middot;</span><span class="tkan-cr">'+esc(r.creator||"—")+'</span>'+
        '<span class="tkan-dot">&middot;</span><span class="tkan-mod" style="color:'+col+';border-color:'+col+'">'+esc(r.module||"—")+'</span>'+
        // F4: profile_used is always present on a result (cached or fresh) — see
        // ticket_reader.analyze()'s contract — so this shows on every card.
        (r.profile_used!==undefined?('<span class="tkan-dot">&middot;</span><span class="tkan-cr" title="da li je profil kupca bio deo ovog upita">profil: '+(r.profile_used?"da":"ne")+'</span>'):"")+
        '</div>';
      if(r.error)return '<div class="tkan-card err">'+head+'<div class="tkan-errbox"><b>Greška</b>'+esc(r.error)+'</div></div>';
      // the server now returns full_prompt (context header + model prompt + Brain line +
      // brain rules + evidence: raw ticket, attachment listing, xlsx/docx digest) — that is
      // what gets copied/launched. Older servers only send `prompt`: fall back to the
      // client-side header + prompt so nothing regresses.
      var full=r.full_prompt||(tkAnCtx(r)+(r.prompt||""));
      var meta="";
      if(r.page)meta+='<div class="tkan-meta"><b>Stranica / ruta:</b> '+esc(r.page)+'</div>';
      var ag=Array.isArray(r.suggested_agents)?r.suggested_agents:[],sk=Array.isArray(r.suggested_skills)?r.suggested_skills:[];
      if(ag.length||sk.length)meta+='<div class="tkan-meta"><b>Brain:</b> '+esc(ag.join(", ")||"—")+' <span class="tkan-dot">&middot;</span> '+esc(sk.join(", ")||"—")+(r.complexity?(' <span class="tkan-dot">&middot;</span> '+esc(r.complexity)):"")+'</div>';
      if(r.cached)meta+='<div class="tkan-meta tkan-cached">&#128190; sačuvano '+esc(fmtAt(r.updated_at||""))+' ('+esc(r.by||"")+') — prikaz iz store-a, bez novog AI poziva</div>';
      else if(r.persist_error)meta+='<div class="tkan-meta" style="color:var(--bad)">&#9888; '+esc(r.persist_error)+'</div>';
      var atl=Array.isArray(r.attachments)?r.attachments:[];
      if(atl.length)meta+='<div class="tkan-meta"><b>Prilozi ('+atl.length+'):</b> '+atl.map(function(a){a=a||{};return esc((a.name||"?")+" ["+(a.kind||"?")+(a.status==="read"?"":", "+(a.status||"?"))+"]");}).join(", ")+'</div>';
      var oq=Array.isArray(r.open_questions)?r.open_questions:[];
      if(oq.length)meta+='<div class="tkan-meta"><b>Otvorena pitanja:</b> '+esc(oq.join(" · "))+'</div>';
      return '<div class="tkan-card">'+head+
        '<div class="lbl">Šta se traži</div><div class="tkan-need">'+esc(r.real_need||"—")+'</div>'+
        '<div class="lbl">Stil osobe</div><div class="tkan-style">'+esc(r.style_note||"—")+'</div>'+
        meta+
        '<div class="lbl">Predlog upita <span class="tkan-sub">('+full.length+' znakova — kopira/pokreće se ceo)</span></div>'+
        '<pre class="tkan-prompt" data-i="'+i+'">'+esc(full)+'</pre>'+
        '<div class="lbl">Dopuna operatera <span class="tkan-sub">(ide na kraj upita — ima prednost nad svim, poštuje se 100 %)</span></div>'+
        '<textarea class="tkan-over" data-i="'+i+'" rows="3" placeholder="npr. Uradi samo model + formu, bez liste. Ne diraj navigaciju. Pitaj me pre migracije…"></textarea>'+
        '<div class="tkan-actions"><button class="btn tkan-copy" data-i="'+i+'">&#128203; Kopiraj</button>'+
        '<button class="btn tkan-claude" data-i="'+i+'">&#8599; Otvori u Claude</button>'+
        '<button class="btn tkan-redo" data-i="'+i+'" title="Ponovo pošalje tiket AI-ju i pregazi sačuvani predlog">&#8635; Analiziraj ponovo</button>'+
        '<button class="btn tkan-withprofile" data-i="'+i+'" title="Ponovo analizira OVAJ tiket sa profilom kupca ubačenim u upit, bez obzira na globalni prekidač (jednokratno)">&#128302; sa profilom</button>'+
        '<span class="tkan-note" data-i="'+i+'"></span></div></div>';}).join("");
    Array.prototype.forEach.call(host.querySelectorAll(".tkan-redo"),function(b){b.onclick=function(){var r=results[+b.getAttribute("data-i")]||{};if(r.ticket_id==null)return;
      if(!confirm("Ponovo analizirati tiket #"+r.ticket_id+" i pregaziti sačuvani predlog?"))return;tkAnalyzeIds([String(r.ticket_id)],true,"#"+r.ticket_id);};});
    // F4: force + inject the learned profile for THIS ticket only, regardless
    // of the global toggle — the "jednokratno analiziraj sa profilom" action.
    Array.prototype.forEach.call(host.querySelectorAll(".tkan-withprofile"),function(b){b.onclick=function(){if(!confirm("Ponovo analizirati sa profilom kupca? Ovo pregazuje sačuvani predlog i troši 1 Gemini poziv."))return;var r=results[+b.getAttribute("data-i")]||{};if(r.ticket_id==null)return;
      tkAnalyzeIds([String(r.ticket_id)],{force:true,with_profile:true},"#"+r.ticket_id);};});
    // read the prompt from the rendered <pre> so what you copy/launch == what you see —
    // plus the operator's override textarea folded into the "DOPUNA OPERATERA" tail
    function preOf(i){return host.querySelector('.tkan-prompt[data-i="'+i+'"]');}
    function noteOf(i){return host.querySelector('.tkan-note[data-i="'+i+'"]');}
    function textOf(i){var pre=preOf(i);if(!pre)return "";var t=pre.textContent||"",ta=host.querySelector('.tkan-over[data-i="'+i+'"]'),ov=ta?(ta.value||"").trim():"";
      if(!ov)return t;var k=t.lastIndexOf("(nema dopune)");return k>=0?(t.slice(0,k)+ov+"\n"):(t+"\n\n=== DOPUNA OPERATERA — poslednja reč (ima prednost nad SVIM iznad; poštuje se 100 %) ===\n"+ov+"\n");}
    Array.prototype.forEach.call(host.querySelectorAll(".tkan-copy"),function(b){b.onclick=function(){var i=b.getAttribute("data-i");if(preOf(i))tkAnCopy(textOf(i),noteOf(i));};});
    Array.prototype.forEach.call(host.querySelectorAll(".tkan-claude"),function(b){b.onclick=function(){var i=b.getAttribute("data-i"),r=results[+i]||{};
      // r.module is already the file-stem module key (ticket_reader._index_tickets keys by fp.stem) — the same key openTix/postTriage use, no mapping needed
      var tk=(r.ticket_id!=null&&r.module)?[{module:r.module,ticket:String(r.ticket_id)}]:[];
      if(preOf(i))tkAnLaunch(textOf(i),r.repo,b,noteOf(i),tk);};});}
  // Run the reader over `ids`. `opts` is either the legacy boolean (force) or
  // an options object {force, with_profile} — F4's "sa profilom" card button
  // passes {force:true,with_profile:true} to force the learned profile into
  // the prompt for this call regardless of the toggle (contract: POST body
  // carries with_profile only when the caller has an opinion — true/false;
  // omitted lets the server fall back to the toggle, same as every pre-F4 caller).
  // force=false: tickets with a stored reading come back from the store (no AI
  // call, no new answer); force=true: re-read and OVERWRITE.
  function tkAnalyzeIds(ids,opts,label){
    if(tkAnBusy)return;                            // ignore duplicate clicks while a request is pending
    if(!ids||!ids.length)return;
    opts=(typeof opts==="boolean")?{force:opts}:(opts||{});
    var force=!!opts.force,withProfile=opts.with_profile;   // withProfile: true/false forces this call; undefined = toggle decides
    tkAnBusy=true;var btn=$("tk-analyze");
    if(btn){if(btn.dataset.label==null)btn.dataset.label=btn.innerHTML;btn.disabled=true;btn.innerHTML="&#128302; Analiziram…";}
    tkAnOpen();tkAnTitle("&#128302; Predlog upita"+(label?(" — "+esc(label)):""));
    if(force)tkAnLoading(ids.length);else $("tkan-body").innerHTML='<div class="tkan-load"><span class="tkan-spin"></span>Učitavam '+ids.length+' '+(ids.length===1?"tiket":"tiketa")+'&hellip; (sačuvani predlozi iz store-a, AI samo za neanalizirane)</div>';
    var payload={ids:ids,force:force};
    if(withProfile===true||withProfile===false)payload.with_profile=withProfile;
    aiPost("/api/tickets/analyze",payload).then(function(d){
      tkAnBusy=false;if(btn){btn.disabled=false;btn.innerHTML=btn.dataset.label;}
      refreshGemBudget();   // Gemini calls were just spent (or not, if every id was cached) — pill should track either way
      if(d&&d.error&&!(d.results&&d.results.length)){$("tkan-body").innerHTML='<div class="tkan-errbox"><b>Greška</b>'+esc(d.error)+'</div>';if($("tkan-sub"))$("tkan-sub").textContent="";return;}
      tkAnRender(d&&d.results);
      if(d&&d.fresh_ids&&d.fresh_ids.length)fetchTickets();   // readings were persisted -> 📖 column + modal
    }).catch(function(e){tkAnBusy=false;if(btn){btn.disabled=false;btn.innerHTML=btn.dataset.label;}
      refreshGemBudget();
      $("tkan-body").innerHTML='<div class="tkan-errbox"><b>Greška</b>'+esc(aiErrText(e))+'</div>';if($("tkan-sub"))$("tkan-sub").textContent="";});}
  // bulk-bar button: if some selected tickets already carry a stored reading, ASK
  // before spending AI calls again (OK = re-analyse + overwrite, Cancel = show stored)
  function tkAnalyze(){
    var ids=tkSelIds();if(!ids.length)return;      // ignore empty selection
    var have=[];Object.keys(tixSel).forEach(function(k){var f=tixFind(tixSel[k].dir,tixSel[k].id);if(f&&tkHasReading(f.t))have.push("#"+f.t.id);});
    var force=false;
    if(have.length){force=confirm(have.length+" od "+ids.length+" izabranih već ima sačuvan predlog upita ("+have.slice(0,8).join(", ")+(have.length>8?", …":"")+").\n\nOK = analiziraj PONOVO i pregazi sačuvano (troši AI pozive)\nCancel = prikaži sačuvane predloge, AI samo za neanalizirane");}
    tkAnalyzeIds(ids,force,"");}
  // inject the button into the bulk bar, next to the triage select (index.html is off-limits)
  (function(){var bar=$("tk-bulk");if(!bar||$("tk-analyze"))return;
    var b=document.createElement("button");b.className="btn tk-analyze";b.id="tk-analyze";
    b.title="Gemini pročita izabrane tikete i sastavi formatiran upit za svaki";
    b.innerHTML="&#128302; Analiziraj + predlog upita";b.onclick=tkAnalyze;
    var anchor=$("tk-bulkstate");
    if(anchor&&anchor.parentNode===bar)bar.insertBefore(b,anchor.nextSibling);else bar.appendChild(b);})();
  addEventListener("keydown",function(e){if(e.key==="Escape"){var m=$("tkanmodal");if(m&&m.classList.contains("open"))tkAnClose();}});

  /* ---------- AI analize — the log of every AI pass (Rescan / Analiziraj / Objedini / Reši), newest first ---------- */
  // GET /api/tickets/ailog -> {entries:[{at,action,by,modules,ticket_ids,cached_ids,failed_ids,titles,summary,extra}]}.
  // Reuses the #tkanmodal shell (title swapped, like the merge action). Clicking a ticket chip opens
  // that ticket; an `analyze` entry can re-show its stored readings (no AI call); a `merge` entry
  // shows the consolidated prompt(s) it produced.
  var TK_ACT={voice:"\uD83C\uDF99 Glasovna komanda",analyze:"\uD83D\uDD2E Predlog upita",rescan:"\u21BB Rescan (trija\u017Ea)",merge:"\uD83E\uDDE9 Objedinjeni upit",solve:"\u26A1 Re\u0161i (auto Claude)",triage:"\uD83E\uDDE0 Trija\u017Ea (Claude)",run:"\u25B6 Claude radio",estimate:"\uD83D\uDCD0 Procene (Gemini)"};
  function tkLogRender(entries){var host=$("tkan-body");entries=Array.isArray(entries)?entries:[];
    if($("tkan-sub"))$("tkan-sub").textContent=entries.length+(entries.length===1?" akcija":" akcija");
    if(!entries.length){host.innerHTML='<div class="tkan-empty">Još nema AI akcija. Rescan, Analiziraj + predlog upita, Objedini u 1 upit i Reši upisuju se ovde redom.</div>';return;}
    var mods={};allTix().forEach(function(x){mods[x.dir]=1;});
    host.innerHTML=entries.map(function(e,i){e=e||{};var ids=Array.isArray(e.ticket_ids)?e.ticket_ids:[],cached=Array.isArray(e.cached_ids)?e.cached_ids:[],failed=Array.isArray(e.failed_ids)?e.failed_ids:[],titles=e.titles||{};
      var chip=function(id,cls){var tt=titles[String(id)]||"";return '<span class="tk-lchip '+(cls||"")+'" data-id="'+esc(id)+'" title="'+esc(tt)+'">#'+esc(id)+(tt?' <i>'+esc(tt.length>34?tt.slice(0,34)+"…":tt)+'</i>':"")+'</span>';};
      var body='';
      if(ids.length&&e.action!=="estimate")body+='<div class="tk-lrow"><span class="tk-llab">'+(e.action==="analyze"?"pročitano":e.action==="rescan"?"trijažirano":"tiketi")+' ('+ids.length+')</span>'+ids.map(function(id){return chip(id,"");}).join("")+'</div>';
      if(cached.length)body+='<div class="tk-lrow"><span class="tk-llab">iz keša ('+cached.length+')</span>'+cached.map(function(id){return chip(id,"c");}).join("")+'</div>';
      if(failed.length)body+='<div class="tk-lrow"><span class="tk-llab" style="color:var(--bad)">neuspelo ('+failed.length+')</span>'+failed.map(function(id){return chip(id,"f");}).join("")+'</div>';
      // an `estimate` entry (F3 Rescan pass) carries hours per ticket in `extra` — reuse the
      // .tk-lchip chip pattern above but with the hour and the basis/factor as its title
      if(e.action==="estimate"&&e.extra){var eex=e.extra,eEst=Array.isArray(eex.estimated)?eex.estimated:[],eSkip=Array.isArray(eex.skipped)?eex.skipped:[];
        if(eEst.length)body+='<div class="tk-lrow"><span class="tk-llab">procenjeno ('+eEst.length+')</span>'+eEst.map(function(x){x=x||{};
          var tt=(x.basis||"")+(x.factor!=null?(" · faktor ×"+x.factor):"");
          return '<span class="tk-lchip est" data-id="'+esc(x.ticket||"")+'" title="'+esc(tt)+'">#'+esc(x.ticket||"?")+' · '+esc(estH(x.hours))+'h</span>';}).join("")+'</div>';
        if(eSkip.length)body+='<div class="tk-lrow"><span class="tk-llab" style="color:var(--warn,var(--acc))">preskočeno ('+eSkip.length+')</span>'+eSkip.map(function(x){x=x||{};
          var tt=(x.reason||"")+(x.detail?(" — "+x.detail):"");
          return '<span class="tk-lchip c" data-id="'+esc(x.ticket||"")+'" title="'+esc(tt)+'">#'+esc(x.ticket||"?")+'</span>';}).join("")+'</div>';}
      // a `run` entry (Claude finishing a measured session) carries files/commits/tests in
      // `extra` — reuse the same .tk-run-* marks the ticket modal's "poslednje sesije" shows
      if(e.action==="run"&&e.extra){var ex=e.extra,exT=ex.tests||{},exMark=exT.passed===true?"✓":exT.passed===false?"✗":"—",
          exCls=exT.passed===true?"ok":exT.passed===false?"bad":"",exFiles=Array.isArray(ex.files)?ex.files:[],
          exCommits=(Array.isArray(ex.commits)?ex.commits:[]).map(function(c){c=c||{};
            return '<span class="tk-run-chip" title="'+esc(c.subject||"")+'">'+esc(String(c.hash||"").slice(0,7))+'</span>';}).join("");
        body+='<div class="tk-lrow tk-run-h"><span class="tk-llab">rezultat</span>'+
          '<span class="tk-run-files" title="'+esc(exFiles.join("\n"))+'">'+exFiles.length+' fajlova</span>'+
          '<span class="tk-run-test '+exCls+'" title="'+esc(exT.last||"")+'">'+exMark+'</span>'+
          (exCommits?'<span class="tk-run-commits">'+exCommits+'</span>':'')+
          (ex.hours!=null?'<span class="tk-llab">'+esc(tkWorkDur(ex.hours,1))+'</span>':'')+'</div>';}
      var acts='';
      if(e.action==="analyze"&&ids.length)acts+='<button class="btn tk-lshow" data-i="'+i+'">&#128302; Prikaži sačuvane predloge</button>';
      if(e.action==="merge"&&e.extra&&Array.isArray(e.extra.groups)&&e.extra.groups.length)acts+='<button class="btn tk-lmerge" data-i="'+i+'">&#129513; Prikaži objedinjeni upit</button>';
      return '<div class="tkan-card tk-lcard"><div class="tkan-h"><span class="tk-lact">'+(TK_ACT[e.action]||esc(e.action||"?"))+'</span><span class="tkan-dot">&middot;</span><span class="tkan-cr">'+esc(fmtAt(e.at||""))+'</span>'+
        (e.by?'<span class="tkan-dot">&middot;</span><span class="tkan-cr">'+esc(e.by)+'</span>':"")+
        (Array.isArray(e.modules)&&e.modules.length?'<span class="tkan-dot">&middot;</span>'+e.modules.map(function(m){var c=modColor(m);return '<span class="tkan-mod" style="color:'+c+';border-color:'+c+'">'+esc(m)+'</span>';}).join(" "):"")+'</div>'+
        '<div class="tkan-need">'+esc(e.summary||"—")+'</div>'+body+(acts?'<div class="tkan-actions">'+acts+'</div>':"")+'</div>';}).join("");
    Array.prototype.forEach.call(host.querySelectorAll(".tk-lchip"),function(c){c.onclick=function(){var id=c.getAttribute("data-id"),f=null;allTix().forEach(function(x){if(String(x.t.id)===String(id))f=x;});if(f){tkAnClose();openTix(f.dir,f.t.id);}};});
    Array.prototype.forEach.call(host.querySelectorAll(".tk-lshow"),function(b){b.onclick=function(){var e=entries[+b.getAttribute("data-i")]||{};tkAnalyzeIds((e.ticket_ids||[]).concat(e.cached_ids||[]).map(String),false,"iz istorije "+fmtAt(e.at||""));};});
    Array.prototype.forEach.call(host.querySelectorAll(".tk-lmerge"),function(b){b.onclick=function(){var e=entries[+b.getAttribute("data-i")]||{};var gs=(e.extra&&e.extra.groups)||[];
      tkAnTitle("&#129513; Objedinjeni upit — iz istorije "+esc(fmtAt(e.at||"")));
      host.innerHTML=gs.map(function(g,gi){return '<div class="tkan-card"><div class="tkan-h"><span class="tkan-t">'+esc(g.repo||"(nema repoa)")+'</span><span class="tkan-dot">&middot;</span><span class="tkan-cr">'+esc((g.ticket_ids||[]).map(function(x){return "#"+x;}).join(", "))+'</span><span class="tkan-dot">&middot;</span><span class="tkan-cr">'+esc(g.source||"")+'</span></div>'+
        (g.summary?'<div class="tkan-need">'+esc(g.summary)+'</div>':"")+'<pre class="tkan-prompt" data-i="'+gi+'">'+esc(g.prompt||"")+'</pre>'+
        '<div class="tkan-actions"><button class="btn tkan-copy" data-i="'+gi+'">&#128203; Kopiraj</button><span class="tkan-note" data-i="'+gi+'"></span></div></div>';}).join("")||'<div class="tkan-empty">Upit nije sačuvan.</div>';
      Array.prototype.forEach.call(host.querySelectorAll(".tkan-copy"),function(cb){cb.onclick=function(){var i=cb.getAttribute("data-i"),pre=host.querySelector('.tkan-prompt[data-i="'+i+'"]');if(pre)tkAnCopy(pre.textContent||"",host.querySelector('.tkan-note[data-i="'+i+'"]'));};});};});}
  function tkLogOpen(){tkAnOpen();tkAnTitle("&#129504; AI analize");$("tkan-body").innerHTML='<div class="tkan-load"><span class="tkan-spin"></span>Učitavam istoriju&hellip;</div>';
    fetch("/api/tickets/ailog").then(function(r){return r.json();}).then(function(d){tkLogRender(d&&d.entries);}).catch(function(e){$("tkan-body").innerHTML='<div class="tkan-errbox"><b>Greška</b>'+esc(String(e))+'</div>';});}
  (function(){var rb=$("tk-refresh");if(!rb||$("tk-ailog"))return;
    var b=document.createElement("button");b.className="btn";b.id="tk-ailog";b.title="Istorija AI akcija nad tiketima (Rescan, Analiziraj, Objedini, Reši) — redom kako su korišćene";
    b.innerHTML="&#129504; AI analize";b.onclick=tkLogOpen;rb.parentNode.insertBefore(b,rb);})();

  /* ---------- Poslato — everything WRITTEN to the helpdesk (comments/closes/estimates), newest first ---------- */
  // GET /api/tickets/sentlog?limit=&module=&ticket= -> {entries:[{at,module,ticket,kind:comment|close|estimate,
  // preview,approved_by,http:ok|fail,error,pre_image,device}]}. writeback.py is the only writer (server.py:1936).
  // Reuses the #tkanmodal shell (tkAnOpen/tkAnTitle, like the AI log above) + the .tk-lchip ticket-open handler.
  var TK_SENT_KIND={comment:"komentar",close:"zatvaranje",estimate:"procena",create:"kreiranje",edit:"izmena"};
  var tkSentAll=[],tkSentMod=new Set(),tkSentKind=new Set();
  // Slavic count agreement (1 / 2-4 / 5+, with the 11-14 "many" exception) — one small
  // helper reused for every kind's word instead of three hand-picked strings per call site.
  function srCount(n,one,few,many){n=Math.abs(n||0);var m10=n%10,m100=n%100;
    if(m100>=11&&m100<=14)return many;if(m10===1)return one;if(m10>=2&&m10<=4)return few;return many;}
  var TK_SENT_WORDS={comment:["komentar","komentara","komentara"],close:["zatvaranje","zatvaranja","zatvaranja"],estimate:["procena","procene","procena"],create:["kreiranje","kreiranja","kreiranja"],edit:["izmena","izmene","izmena"]};
  function tkSentWord(kind,n){var w=TK_SENT_WORDS[kind]||[kind,kind,kind];return srCount(n,w[0],w[1],w[2]);}
  // the "poslato: 2 komentara · 1 zatvaranje" summary the ticket modal shows above (empty string when t.sent is all-zero)
  function tkSentSummaryText(sent){sent=sent||{};var parts=[];
    ["comment","close","estimate","create","edit"].forEach(function(k){var n=sent[k]||0;if(n>0)parts.push(n+" "+tkSentWord(k,n));});
    return parts.join(" · ");}
  function tkSentSummaryKv(t){var txt=tkSentSummaryText(t&&t.sent);if(!txt)return "";
    return '<div class="kv tk-sl-ticksum" id="tx-sentsum" title="poslato na helpdesk — klikni za detalje"><span>poslato</span><b>'+esc(txt)+'</b></div>';}

  /* ---------- measured work — the "rad" kv row + "poslednje sesije" list in the ticket modal,
     and the tiny AI-column marks in the table. Reuses srCount (declines like "procena":
     1 sesija / 2-4 sesije / 5+ sesija) and fmtAt/tsOf/esc like every other block above. ---------- */
  function tkWorkSessText(n){n=n||0;return n+" "+srCount(n,"sesija","sesije","sesija");}
  // "Xh YYm"; 0 minutes with sessions already logged reads as "—" (a session with
  // no measurable time, e.g. a run whose transcript never showed up), never "0h 00m".
  function tkWorkDur(hours,sessions){var mins=Math.round(Math.max(hours||0,0)*60);
    if(mins<=0)return sessions>0?"—":"0h 00m";
    var h=Math.floor(mins/60),m=mins%60;return h+"h "+("0"+m).slice(-2)+"m";}
  // compact form for the narrow table column: "1h05" (no space, no trailing "m")
  function tkWorkMini(hours){var mins=Math.round(Math.max(hours||0,0)*60);if(mins<=0)return "";
    var h=Math.floor(mins/60),m=mins%60;return h+"h"+("0"+m).slice(-2);}
  function tkWorkSpan(a,b){var ta=tsOf(a),tb=tsOf(b);if(ta==null||tb==null||tb<ta)return "";
    var mins=Math.round((tb-ta)/60000),h=Math.floor(mins/60),m=mins%60;
    return h?(h+"h "+("0"+m).slice(-2)+"m"):(m+"m");}
  // Open run but no hook event for 3+ minutes: the launched Claude is not reporting (hooks
  // not active in that process, or it died without SessionEnd) — say so instead of a
  // silent "u toku" that later reads as 0 h.
  function tkWorkNoEvents(w){if(!w||!w.open||!w.last_touch)return "";var t=Date.parse(w.last_touch);if(!isFinite(t))return "";
    if(Date.now()-t>3*60*1000)return ' <span class="tk-wk-noev" title="poslednji dogadjaj '+esc(fmtAt(w.last_touch))+' — hook događaji ne stižu iz te sesije (Claude ugašen bez SessionEnd? plugin hookovi?)">&#9888; bez događaja</span>';return "";}
  function tkWorkKv(work){work=work||{};if(!(work.sessions>0||work.open))return "";
    return '<div class="kv tk-wk"><span>rad</span><b>'+esc(tkWorkSessText(work.sessions))+' · '+esc(tkWorkDur(work.hours,work.sessions))+
      (work.open?' · <span class="tk-wk-live">u toku</span>'+tkWorkNoEvents(work):'')+'</b>'+
      (work.open?'<button class="btn tk-wk-end" id="tx-workend" title="označi rad na ovom tiketu kao završen">&#10003; gotovo</button><span class="tk-wk-err" id="tx-workend-err"></span>':'')+
      '</div>';}
  // one line per run, newest first (server already reversed `runs`); every field is
  // untrusted (comes off a Claude transcript), so esc()/title-esc() throughout
  function tkRunRow(r){r=r||{};var dur=(r.started&&r.ended)?tkWorkSpan(r.started,r.ended):"";
    var files=Array.isArray(r.files)?r.files:[],tests=r.tests||{},
        tmark=tests.passed===true?"✓":tests.passed===false?"✗":"—",
        tcls=tests.passed===true?"ok":tests.passed===false?"bad":"";
    var commits=(Array.isArray(r.commits)?r.commits:[]).map(function(c){c=c||{};
      return '<span class="tk-run-chip" title="'+esc(c.subject||"")+'">'+esc(String(c.hash||"").slice(0,7))+'</span>';}).join("");
    var summary=String(r.summary||"");if(summary.length>200)summary=summary.slice(0,200)+"…";
    return '<div class="tk-run-row">'+
      '<div class="tk-run-h"><span class="tk-run-at">'+esc(fmtAt(r.started||""))+'</span>'+
      (dur?' <span class="tk-run-dur">('+esc(dur)+')</span>':(r.started&&!r.ended?' <span class="tk-run-dur live">u toku</span>':''))+
      '<span class="tk-run-files" title="'+esc(files.join("\n"))+'">'+files.length+' fajlova</span>'+
      '<span class="tk-run-test '+tcls+'" title="'+esc(tests.last||"")+'">'+tmark+'</span>'+
      (commits?'<span class="tk-run-commits">'+commits+'</span>':'')+
      '</div>'+(summary?'<div class="tk-run-sum">'+esc(summary)+'</div>':'')+'</div>';}
  function tkRunsBlock(runs){runs=Array.isArray(runs)?runs:[];if(!runs.length)return "";
    return '<details class="tk-runs"><summary>poslednje sesije ('+runs.length+')</summary>'+
      '<div class="tk-runs-list">'+runs.map(tkRunRow).join("")+'</div></details>';}
  function tkSentRow(e){e=e||{};var col=modColor(e.module||"");var ok=e.http==="ok",unk=e.http==="unknown";
    var pre=(e.kind==="estimate"&&e.pre_image!=null&&e.pre_image!=="")?'<span class="tk-sl-pre">ranije: '+esc(e.pre_image)+' h</span>':"";
    return '<div class="tk-sl-row">'+
      '<span class="tk-sl-at">'+esc(fmtAt(e.at))+'</span>'+
      '<span class="tk-mod tk-sl-mod" style="color:'+col+';border:1px solid '+col+'">'+esc(e.module||"—")+'</span>'+
      '<span class="tk-lchip" data-id="'+esc(e.ticket||"")+'" data-dir="'+esc(e.module||"")+'">#'+esc(e.ticket||"?")+'</span>'+
      '<span class="tk-sl-kind">'+esc(TK_SENT_KIND[e.kind]||e.kind||"—")+'</span>'+
      '<span class="tk-sl-prev">'+esc(e.preview||"")+'</span>'+pre+
      '<span class="tk-sl-appr">'+esc(e.approved_by||"—")+'</span>'+
      '<span class="tk-sl-http'+(ok?"":unk?" unk":" fail")+'"'+(!ok&&e.error?(' title="'+esc(e.error)+(unk?" — transportna greška posle slanja: proveri na helpdesku da li je prošlo":"")+'"'):"")+'>'+(ok?"ok":unk?"nepoznato":"fail")+'</span>'+
      '</div>';}
  function tkSentRender(){var host=$("tkan-body");
    var mods=uniq(tkSentAll.map(function(e){return e.module;}));
    var kinds=["comment","close","estimate","create","edit"].filter(function(k){return tkSentAll.some(function(e){return e.kind===k;});});
    var rows=tkSentAll.filter(function(e){
      if(tkSentMod.size&&!tkSentMod.has(e.module))return false;
      if(tkSentKind.size&&!tkSentKind.has(e.kind))return false;return true;});
    if($("tkan-sub"))$("tkan-sub").textContent=rows.length+(rows.length===1?" upis":" upisa");
    var filt='<div class="tk-sl-filters">'+
      (mods.length?('<span class="tk-sl-flab">modul</span>'+mods.map(function(m){var c=modColor(m),on=tkSentMod.has(m);
        return '<span class="tk-pill tk-sl-mf'+(on?" on":"")+'" data-m="'+esc(m)+'" style="'+(on?("background:"+c+";border-color:"+c):("color:"+c+";border-color:"+c))+'">'+esc(m)+'</span>';}).join("")):"")+
      (kinds.length?('<span class="tk-sl-flab">tip</span>'+kinds.map(function(k){return '<span class="tk-pill tk-sl-kf'+(tkSentKind.has(k)?" on":"")+'" data-k="'+esc(k)+'">'+esc(TK_SENT_KIND[k])+'</span>';}).join("")):"")+
      '</div>';
    host.innerHTML=filt+(rows.length?('<div class="tk-sl-rows">'+rows.map(tkSentRow).join("")+'</div>')
      :('<div class="tkan-empty">'+((tkSentMod.size||tkSentKind.size)?"Nema poslatih upisa za izabrani filter.":"Još ništa nije poslato na helpdesk. Komentari, zatvaranja i procene koje HUD pošalje pojaviće se ovde.")+'</div>'));
    Array.prototype.forEach.call(host.querySelectorAll(".tk-sl-mf"),function(el){el.onclick=function(){var m=el.getAttribute("data-m");if(tkSentMod.has(m))tkSentMod.delete(m);else tkSentMod.add(m);tkSentRender();};});
    Array.prototype.forEach.call(host.querySelectorAll(".tk-sl-kf"),function(el){el.onclick=function(){var k=el.getAttribute("data-k");if(tkSentKind.has(k))tkSentKind.delete(k);else tkSentKind.add(k);tkSentRender();};});
    // ticket chip: same pattern as the AI log's .tk-lchip — close this modal, open that ticket
    Array.prototype.forEach.call(host.querySelectorAll(".tk-lchip"),function(c){c.onclick=function(){var id=c.getAttribute("data-id"),dir=c.getAttribute("data-dir")||"",f=null;
      allTix().forEach(function(x){if(String(x.t.id)===String(id)&&(!dir||x.dir===dir))f=f||x;});   // module+id: two modules may share an id
      if(f){tkAnClose();openTix(f.dir,f.t.id);}};});}
  function tkSentOpen(filter){tkAnOpen();tkAnTitle("&#128228; Poslato na helpdesk");
    if($("tkan-sub"))$("tkan-sub").textContent="";
    $("tkan-body").innerHTML='<div class="tkan-load"><span class="tkan-spin"></span>Učitavam&hellip;</div>';
    var qs="limit=300";
    if(filter&&filter.module)qs+="&module="+encodeURIComponent(filter.module);
    if(filter&&filter.ticket)qs+="&ticket="+encodeURIComponent(filter.ticket);
    fetch("/api/tickets/sentlog?"+qs).then(function(r){return r.json();}).then(function(d){
      tkSentAll=(d&&d.entries)||[];tkSentMod=new Set();tkSentKind=new Set();tkSentRender();
    }).catch(function(e){$("tkan-body").innerHTML='<div class="tkan-errbox"><b>Greška</b>'+esc(String(e))+'</div>';});}
  (function(){var rb=$("tk-refresh");if(!rb||$("tk-sentlog"))return;
    var b=document.createElement("button");b.className="btn";b.id="tk-sentlog";
    b.title="Sve što je HUD poslao na helpdesk (komentari, zatvaranja, procene) — redom, najnovije prvo";
    b.innerHTML="&#128228; Poslato";b.onclick=function(){tkSentOpen();};rb.parentNode.insertBefore(b,rb);})();

  /* ---------- Procene sati — ticket-modal "procena" kv row + GET /api/tickets/estimates panel ----------
     tkEstKv reads the same row.estimate the table's "h" column reads (declared with tkEstCell above);
     the panel is display-only (never writes) and reuses the #tkanmodal shell (tkAnOpen/tkAnTitle) + the
     .tk-lchip ticket-open pattern from Poslato/AI-log above. */
  // ticket-modal kv row, right under "rad" — "procena: 2.5 h (AI · zašto: …)" or "procena: 3 h (helpdesk)";
  // the div's title carries the AI basis + calibration factor too, for when the inline text is cut off
  function tkEstKv(t){var est=(t&&t.estimate)||{};var h=est.ai_h!=null?est.ai_h:est.helpdesk_h;
    if(h==null)return "";
    var isAi=est.ai_h!=null;
    var ai=(t.triage&&t.triage.ai_estimate)||{};
    var basis=String(ai.basis||"").trim();
    var label=isAi?("AI"+(basis?(" · zašto: "+basis):"")):"helpdesk";
    var full=isAi?("AI procena"+(basis?(" · zašto: "+basis):"")+(ai.factor!=null?(" · faktor ×"+ai.factor):"")):"helpdesk procena (uneta na helpdesku)";
    return '<div class="kv tk-est-kv" title="'+esc(full)+'"><span>procena</span><b'+(isAi?' style="color:var(--cy)"':'')+'>'+esc(estH(h))+' h ('+esc(label)+')</b></div>';}
  function tkEstFactor(v){return v==null?"—":("×"+estH(v));}
  function tkEstHrs(v){return v==null?"—":(estH(v)+"h");}
  function tkEstModuleCard(m){m=m||{};var col=modColor(m.module||"");
    return '<div class="tkest-mcard"><div class="tkest-mhead"><span class="tk-mod" style="color:'+col+';border:1px solid '+col+'">'+esc(m.module||"—")+'</span></div>'+
      '<div class="tkest-mrow"><span>parova</span><b>'+(m.n_pairs||0)+'</b></div>'+
      '<div class="tkest-mrow"><span>medijana stvarno/procenjeno</span><b>'+tkEstFactor(m.median_ratio)+'</b></div>'+
      '<div class="tkest-mrow"><span>sr. apsolutna greška</span><b>'+tkEstHrs(m.mean_abs_err_h)+'</b></div>'+
      '<div class="tkest-mrow"><span>AI procena</span><b>'+(m.n_ai_estimates||0)+'</b></div>'+
      '<div class="tkest-mrow"><span>čeka procenu</span><b>'+(m.n_pending||0)+'</b></div>'+
      ((m.n_unmeasured||0)?'<div class="tkest-mrow" title="procenjeni i zatvoreni tiketi bez izmerenog rada (sesija bez BRAIN_WORK_ID ili auto-zatvorena) — ne ulaze u kalibraciju"><span>bez merenja</span><b>'+(m.n_unmeasured||0)+'</b></div>':'')+'</div>';}
  // F9: avg + n (not just one rater's value) — a ticket can carry more than one
  // rating (customer + tester + admin), so "4.5/5 (2)" says both the mean and
  // how many raters it comes from; falls back to the single value when there is
  // no average yet (a pre-F9 ticket, folded to one rater in store.ticket_rating).
  function tkEstRatingText(r){if(r.rating==null)return "—";
    var v=r.rating_avg!=null?r.rating_avg:r.rating;
    return v+"/5"+(r.rating_n>1?(" ("+r.rating_n+")"):"");}
  function tkEstRowHtml(r){r=r||{};var col=modColor(r.module||"");
    return '<div class="tkest-row">'+
      '<span class="tk-mod tkest-rmod" style="color:'+col+';border:1px solid '+col+'">'+esc(r.module||"—")+'</span>'+
      '<span class="tk-lchip" data-id="'+esc(r.ticket||"")+'" data-dir="'+esc(r.module||"")+'">#'+esc(r.ticket||"?")+'</span>'+
      '<span class="tkest-scope">'+esc(r.scope||"—")+'</span>'+
      '<span class="tkest-est">'+tkEstHrs(r.estimated_h)+'</span>'+
      '<span class="tkest-act">'+tkEstHrs(r.actual_h)+'</span>'+
      '<span class="tkest-by">'+esc(r.by||"—")+'</span>'+
      '<span class="tkest-closed">'+esc(fmtAt(r.closed_at||""))+'</span>'+
      '<span class="tkest-rating">'+esc(tkEstRatingText(r))+'</span></div>';}
  function tkEstRender(d){d=d||{};var host=$("tkan-body");
    var mods=Array.isArray(d.per_module)?d.per_module:[];
    var rows=(Array.isArray(d.rows)?d.rows.slice():[]).sort(function(a,b){return cmpNum(tsOf(b.closed_at),tsOf(a.closed_at),1);});
    if($("tkan-sub"))$("tkan-sub").textContent=rows.length+(rows.length===1?" red":" redova");
    var strip=mods.length?('<div class="tkest-strip">'+mods.map(tkEstModuleCard).join("")+'</div>'):"";
    var body;
    if(rows.length){
      body='<div class="tkest-tablewrap"><div class="tkest-head-row"><span>Modul</span><span>Tiket</span><span>Scope</span><span>Procenjeno</span><span>Stvarno</span><span>Ko</span><span>Zatvoren</span><span>Ocena</span></div>'+
        '<div class="tkest-rows">'+rows.map(tkEstRowHtml).join("")+'</div></div>';
    }else{
      body='<div class="tkan-empty">'+esc(d.history_note||"Ovde se pojavljuju redovi kad tiket koji ima procenu bude zatvoren sa izmerenim radom (Claude sesijom ili ručno unetim vremenom).")+'</div>';
    }
    host.innerHTML=strip+body;
    Array.prototype.forEach.call(host.querySelectorAll(".tk-lchip"),function(c){c.onclick=function(){var id=c.getAttribute("data-id"),dir=c.getAttribute("data-dir")||"",f=null;
      allTix().forEach(function(x){if(String(x.t.id)===String(id)&&(!dir||x.dir===dir))f=f||x;});   // module+id: two modules may share an id
      if(f){tkAnClose();openTix(f.dir,f.t.id);}};});}
  function tkEstOpen(){tkAnOpen();tkAnTitle("&#128208; Procene sati");
    if($("tkan-sub"))$("tkan-sub").textContent="";
    $("tkan-body").innerHTML='<div class="tkan-load"><span class="tkan-spin"></span>Učitavam procene&hellip;</div>';
    fetch("/api/tickets/estimates").then(function(r){return r.json();}).then(function(d){tkEstRender(d);})
      .catch(function(e){$("tkan-body").innerHTML='<div class="tkan-errbox"><b>Greška</b>'+esc(String(e))+'</div>';});}
  (function(){var rb=$("tk-refresh");if(!rb||$("tk-est"))return;
    var b=document.createElement("button");b.className="btn";b.id="tk-est";
    b.title="Tačnost procena sati po modulu + istorija zatvorenih tiketa (procenjeno naspram stvarno izmerenog)";
    b.innerHTML="&#128208; Procene";b.onclick=tkEstOpen;rb.parentNode.insertBefore(b,rb);})();

  /* ---------- F4: "koristi profile kupaca" toggle (toolbar pill, next to Procene) ----------
     GET/POST /api/tickets/profiles-toggle -> {enabled}. Off: the deterministic history[]
     fold (ticket_reader._fold_into_profile) keeps running for free; the AI summarise-on-close
     call and the prompt injection on ordinary analyze are skipped — "analiziraj sa profilom"
     (tkan-withprofile above) still works as a one-off override either way. */
  var tkProfilesOn=true;   // optimistic default; corrected by the GET the moment the button exists
  function tkProfilesLabel(b){b.classList.toggle("act",tkProfilesOn);
    b.innerHTML="&#128100; profili: "+(tkProfilesOn?"uklj":"isklj");}
  function refreshTkProfilesToggle(){var b=$("tk-profiles");if(!b)return;
    fetch("/api/tickets/profiles-toggle").then(function(r){return r.json();}).then(function(d){
      if(d&&typeof d.enabled==="boolean"){tkProfilesOn=d.enabled;tkProfilesLabel(b);}
    }).catch(function(){});}
  (function(){var rb=$("tk-refresh");if(!rb||$("tk-profiles"))return;
    var b=document.createElement("button");b.className="btn";b.id="tk-profiles";
    b.title="kada je uključeno, analiza dobija profil kupca kao kontekst i pri zatvaranju tiketa Gemini osvežava profil (1 poziv); isključeno = bez dodatnih poziva";
    tkProfilesLabel(b);
    b.onclick=function(){b.disabled=true;
      aiPost("/api/tickets/profiles-toggle",{enabled:!tkProfilesOn}).then(function(d){b.disabled=false;
        if(d&&typeof d.enabled==="boolean")tkProfilesOn=d.enabled;tkProfilesLabel(b);
      }).catch(function(){b.disabled=false;});};
    rb.parentNode.insertBefore(b,rb);})();


  /* ---------- 🖼 Snimci — the before/after gallery (plan V3) ----------
     GET /api/tickets/shots -> {works:[{work_id,ticket,ticket_key,captured_at,repo,errors,
     baseline:{state,reason},progress:{side,done,total,page,at,running}|null,
     pairs:[{pair_id,page_id,url,title,state,caption,approved,decision,baseline,mode,
     shown,represented_by,drop_reason,new_page,nav:{label,path,steps,text},
     img:{before_full,after_full},regions:[{n,img:{before_crop,after_crop}}],
     regions_found}]}]} — the server hands out URLs,
     never file paths, and every image is fetched from /api/tickets/shots/img (loopback-only,
     no-store). Approve is LOCAL until THE TICKET'S OWN send button at the bottom of its group,
     which POSTs /api/tickets/shots/approve for each changed work OF THAT TICKET; the server
     writes ONE outbox draft per work (kind:"shots") through the ticket store. There is no
     global send: one button for every ticket on screen meant sending somebody else's pictures
     by pressing the only button there was.

     THE SEND SENDS. That request posts the comment to the helpdesk before it answers, and
     the answer is `send: "posted"|"failed"|"ambiguous"|"nothing"` — never "it is in the
     outbox". Delivery used to wait for a ticket close through writeback --close, so the
     operator pressed "pošalji", the panel went green and the customer got nothing (operator,
     2026-08-23). Nothing here closes a ticket or touches its status: the comment goes on an
     open ticket and a closed one alike, before a sync or after it. The button therefore
     CONFIRMS first, naming the ticket, the pairs and the pictures.
     Reuses the #tkanmodal shell (tkAnOpen/tkAnTitle)
     like AI analize / Poslato / Procene, srCount() for the Serbian plural, and esc() on every
     string that came from the server (page titles and captions are engine output, not ours).

     THE DECISION ROUND-TRIPS. approved / rejected / never-reviewed is three states here and
     three on disk (`decision`); it used to be three here and two there, so "odbaci" survived
     until the next reload and then read as never-reviewed. `approved` is still shipped and
     still read — it is the same fact in the older shape and it is the fallback for a manifest
     written before `decision` existed.

     A NEW SCREEN HAS NO "BEFORE" AND SAYS SO. `new_page` marks a page that did not exist
     before the change; the missing PRE half is explained rather than left looking like a
     failed capture, and `nav.text` — built by the server, the same lines the comment will
     carry — is previewed on the card so the operator edits the draft knowing what it says.

     ONE COLLAPSIBLE GROUP PER TICKET (<details>, the same house caret as #tixmodal .tk-runs),
     so the operator opens only the ticket they want. Grouping is by the server's `ticket_key`,
     NOT by the raw string — "VEZ05513" and "DEMO#5513" are one ticket, and the normalisation
     that decides that lives in ONE place (server `_shots_ticket_key`), never re-implemented
     here. The first group is expanded and the rest are collapsed: with one ticket — the
     from-a-ticket case — nothing needs clicking, with several nothing runs together.
     A work with no ticket is not listed at all; the server drops it, its folder stays on disk.

     THREE INDICATORS, all three asked for:
     1. WHOSE PICTURES THESE ARE — the ticket is on the group header AND on every pair card,
        both printed from the SAME group label, so the two can never disagree.
     2. WHAT THE ENGINE IS DOING — `progress`, read by the server off the PNGs the capture
        drops as it goes (there is no progress channel; see server `_shots_progress`).
        `total:0` means the plan is not known yet, and then we print a count and NO bar.
     3. HOW FAR THE REVIEW GOT — approved / rejected / UNTOUCHED is three states, not two.
        tkShSel[key] is true|false|undefined and `undefined` is "nije pregledano"; counts sit
        on every group header and on the send bar.

     WHAT IS COUNTED IS WHAT IS ON SCREEN. The engine collapses screens that share a layout
     behind one representative and drops the ones with no structural change; the server
     ships that verdict per pair (`shown`). A collapsed record is NOT a card and NOT a
     decision — it hangs, read-only with its pictures, inside the card that represents it
     (or in the work's own list when nothing represents it), and it is counted separately as
     "N sklopljeno". The panel used to render all 32 records of a work whose engine had
     collapsed 27 and print "32 nepregledano" over 5 reviewable cards. The representative's
     caption already carries "+ N druga ekrana" — nothing here restates that number.

     ↑ VRH TIKETA — from deep inside one ticket's images, back to that ticket's first pair.
     It rides in the sticky send bar, names the ticket it will return to, and appears only
     when there is somewhere to go. With two groups open the panel top is another ticket, so
     "scroll to top" would be the wrong answer.

     N ZOOMED COMPARISONS PER PAIR, one per changed REGION — one commit touching four fields
     of a form is four of them, and the panel used to be able to show exactly one. `regions` is
     rendered IN THE ORDER GIVEN (reading order down the page, so never re-sorted), each row
     labelled by POSITION only — `n/N`, never a class name or a token, which is the "ikonice i
     nebuloze" the operator already rejected. `regions_found > regions.length` prints
     "prikazano N od M izmenjenih mesta": a silent truncation reads as "that was all of it".
     `regions:[]` is the old shape — the two full pictures inline and nothing else. With
     regions the fulls move into a collapsed <details>: the crops ARE the change, the fulls are
     context, and four stacked comparisons plus two full screenshots is an unreadable card.

     RENDER DEFENSIVELY. A pair is PRE + POSLE + the screen name and its URL, and NOTHING from
     the change detection: no box, no anchor, no verdict, no percentage, and none of the
     engine's per-region crop diagnostics —
     the server stopped shipping them (`_shots_pair_public`), and a caption may be just the
     screen name. Any image variant may be absent; each missing one degrades to a designed
     .tksh-miss placeholder so a capture that failed never looks like a capture that had
     nothing to show — including a region whose BOTH sides are unreadable, which is kept as a
     numbered empty row so the positions the operator reads still match the attachment names. */
  var tkShWorks=[];          // the works exactly as the server sent them
  //: "<work_id>||<pair_id>" -> true approved | false REJECTED | absent = NOT REVIEWED YET.
  //: The third state is a state: two of them (never looked at / looked at and refused) used to
  //: render identically, so "how far have I got" was unanswerable from the panel.
  var tkShSel={};
  var tkShWas={};            // the same, as the server had it — used to send only what changed
  var tkShMode={};           // "<work_id>||<pair_id>" -> "both"|"images"|"comment"
  var tkShWasM={};           // the same, as the server had it (mode changes are changes too)
  //: the caption the operator typed, held in JS and NOT read back out of the input — a
  //: collapsed group and a progress repaint must never be able to drop what was typed
  var tkShCapV={};
  var tkShOpenG={};          // ticket group key -> expanded? (survives every re-render)
  //: ticket group key -> the last send outcome {cls,txt}. OUTSIDE the markup on purpose:
  //: the panel repaints itself (the 4s poll, and the reload the send itself triggers), and
  //: an answer that lives only in the DOM is gone before the operator reads it — which is
  //: exactly what a FAILED send must never be. Replaced by that group's next send.
  var tkShMsg={};
  var tkShFilter="";         // the ticket this gallery was opened for ("" = browse everything)
  var tkShTimer=null;        // the progress poll, cleared the moment the panel is not ours
  var TKSH_POLL_MS=4000;
  //: label + tooltip per choice; the value is the wire value the server reads as `mode`
  var TKSH_MODES=[["both","slike + opis","i opis i slike idu u komentar"],
                  ["images","samo slike","slike idu kao prilog, bez reda u opisu"],
                  ["comment","samo opis","red u opisu, bez priloženih slika"]];
  function tkShKey(w,p){return w+"||"+p;}
  // EVERY note of one group, header and footer alike — resolved by data-g, not by the
  // footer's id. The same action has two entry points and its answer has to appear at
  // whichever one the operator is looking at, including on a COLLAPSED group where the
  // header note is the only one on screen.
  function tkShNoteEls(gkey){var host=$("tkan-body");
    return host?Array.prototype.slice.call(
      host.querySelectorAll('.tksh-gnote[data-g="'+String(gkey).replace(/"/g,'\\"')+'"]')):[];}
  // KEEP `tksh-gnote` in the class list. Assigning className wholesale is how the footer
  // note has always worked — but that one is found by id, which survives it. These are
  // found by CLASS, so dropping the marker made the element unfindable the moment it was
  // first written to: the text landed once and every later lookup returned nothing.
  function tkShNoteSet(gkey,cls,txt){tkShMsg[gkey]={cls:cls,txt:txt};tkShNotePaint(gkey);}
  function tkShNotePaint(gkey){var m=tkShMsg[gkey];if(!m)return;
    tkShNoteEls(gkey).forEach(function(n){
      n.className="tkan-note tksh-gnote"+(m.cls?(" "+m.cls):"");n.textContent=m.txt;});}
  // THE ANSWER OUTLIVES THE TICKET IT IS ABOUT. A send that works takes the whole group
  // off screen - the run is finished, promoted and deleted - so the note it would have
  // been painted on no longer exists and the panel would simply empty itself without a
  // word. That is the same silence that made the operator think the button was broken,
  // arriving from the other side. Any message with nowhere to land is shown as a banner
  // at the top of the panel instead, and it already names its ticket.
  function tkShNotesPaint(){
    var host=document.getElementById("tksh-root"),orphan=[];
    Object.keys(tkShMsg).forEach(function(k){
      var els=tkShNoteEls(k);
      if(!els.length){if(tkShMsg[k].txt)orphan.push(tkShMsg[k]);return;}
      tkShNotePaint(k);});
    var old=document.getElementById("tksh-gone");
    if(old&&old.parentNode)old.parentNode.removeChild(old);
    if(!orphan.length||!host)return;
    var box=document.createElement("div");box.id="tksh-gone";box.className="tksh-gone";
    box.innerHTML=orphan.map(function(m){
      return '<div class="tkan-note '+esc(m.cls||"")+'">'+esc(m.txt)+'</div>';}).join("");
    host.insertBefore(box,host.firstChild);}
  // an unknown/absent mode is "both" — same rule the server applies, so a pair captured
  // before the choice existed keeps behaving exactly as it did
  function tkShModeOf(w,p){var m=tkShMode[tkShKey(w,p)];return (m==="images"||m==="comment")?m:"both";}
  function tkShModeLab(m){for(var i=0;i<TKSH_MODES.length;i++)if(TKSH_MODES[i][0]===m)return TKSH_MODES[i][1];return TKSH_MODES[0][1];}
  function tkShDom(w,p){return String(w+"-"+p).replace(/[^A-Za-z0-9_-]/g,"_");}
  // "ok" approved · "no" rejected · "un" never reviewed — the one place the third state is decided
  function tkShState(w,p){var v=tkShSel[tkShKey(w,p)];return v===true?"ok":(v===false?"no":"un");}
  function tkShRevFlag(st,md){
    if(st==="ok")return "&#9989; odobreno — "+esc(tkShModeLab(md));
    if(st==="no")return "&#10060; odbačeno — ne ide u komentar";
    return "&#9723; nije pregledano";}
  // the caption never comes back out of the DOM: see tkShCapV
  function tkShCap(w,p){return String(tkShCapV[tkShKey(w,p)]||"").trim().slice(0,80);}
  // WHAT IS ON SCREEN IS WHAT THE COUNTS COUNT. The engine collapses a screen whose layout
  // it already photographed elsewhere in the same work (`shown:false`); those records are
  // reachable — see tkShWorkHtml — but they are not cards, not decisions and not part of
  // "N nepregledano". A server that does not send the field means "everything is shown",
  // which is exactly what the panel did before. A collapsed pair the server reports as
  // ALREADY APPROVED stays reviewable: it rides in the draft, so hiding it would be the
  // same lie pointing the other way.
  function tkShIsShown(p){return (p&&p.shown===false)?(p.approved===true):true;}
  function tkShPairsOf(w){return ((w&&w.pairs)||[]).filter(tkShIsShown);}
  function tkShHiddenOf(w){return ((w&&w.pairs)||[]).filter(function(p){return !tkShIsShown(p);});}
  function tkShTally(pairsOf){var c={n:0,ok:0,no:0,un:0,off:0};
    pairsOf.forEach(function(w){tkShPairsOf(w).forEach(function(p){
      c.n++;c[tkShState(w.work_id,p.pair_id)]++;});
      c.off+=tkShHiddenOf(w).length;});
    return c;}
  function tkShCountHtml(c){
    return '<span class="tksh-cnt">'+c.n+' '+srCount(c.n,"par","para","parova")+' za pregled</span>'+
      '<span class="tksh-cnt st-ok">&#9989; '+c.ok+'</span>'+
      '<span class="tksh-cnt st-no">&#10060; '+c.no+'</span>'+
      '<span class="tksh-cnt st-un">&#9723; '+c.un+' nepregledano</span>'+
      // the engine's own doing, said once, in pairs — the representative's caption says it
      // again in SCREENS and the two must not be read as one number
      (c.off?'<span class="tksh-cnt" title="snimci koje je alat sklopio iza drugog ekrana ili odbacio kao nepromenjene — dostupni u kartici koja ih predstavlja, ne idu u komentar">'+
             c.off+' sklopljeno</span>':"");}
  function tkShNote(){var c=tkShTally(tkShWorks),el=$("tksh-note");if(!el)return;el.className="tkan-note";
    el.textContent=c.ok+" odobreno · "+c.no+" odbačeno · "+c.un+" nepregledano";}
  // ONE group per ticket, in the order the server sent the works (newest first). The key is
  // the server's normalised ticket_key; the LABEL is the newest work's spelling and is the
  // only ticket string rendered anywhere in the group, header and pair cards alike.
  function tkShGroups(){var order=[],by={};
    tkShWorks.forEach(function(w){
      var k=String(w.ticket_key||w.ticket||"?");
      if(!by[k]){by[k]={key:k,dom:k.replace(/[^A-Za-z0-9_-]/g,"_"),
                        label:String(w.ticket||"(bez tiketa)"),works:[]};order.push(k);}
      by[k].works.push(w);});
    return order.map(function(k){return by[k];});}
  function tkShRunning(works){var r=false;
    works.forEach(function(w){if(w.progress&&w.progress.running)r=true;});return r;}
  // a work captured with no baseline at all: the group says so on the header, so it is
  // readable while the group is collapsed. The per-pair note still explains each record.
  function tkShNoBase(works){var r=false;
    works.forEach(function(w){if(w.baseline&&w.baseline.state==="none")r=true;});return r;}
  // THE CARD THAT WOULD NOT GO AWAY. A run the operator has fully decided normally
  // promotes its pictures into the baseline and deletes its own folder — that is what
  // "ništa" and "pošalji" both end with. When promotion refuses (a page that errored on
  // the after pass, a folder with no after shots at all) the run legitimately stays, and
  // until now it stayed WITHOUT SAYING SO: identical to an untouched one, and no button
  // on screen could clear it (operator, 2026-08-28). The server answers `stay.blocked`
  // plus the reason; this is where the operator reads it and gets the way out.
  function tkShStuck(g){return (g.works||[]).filter(function(w){
    return w&&w.stay&&w.stay.blocked;});}
  function tkShStuckHtml(g){
    var st=tkShStuck(g);if(!st.length)return "";
    var seen={},why=[];
    st.forEach(function(w){var r=String((w.stay&&w.stay.reason)||"");
      if(r&&!seen[r]){seen[r]=1;why.push(r);}});
    return '<div class="tksh-stuck">'+
      '<div class="tksh-stuck-t">&#9888; '+
        (st.length>1?(st.length+" snimanja ostaju u galeriji"):"Snimanje ostaje u galeriji")+
      '</div>'+
      '<div class="tksh-stuck-w">'+esc(why.join(" · "))+'</div>'+
      '<div class="tksh-stuck-w">Odluke su sačuvane i kupcu ništa nije ostalo dužno — '+
        'ostaju samo slike na disku, koje ne mogu da postanu osnova za sledeće poređenje.</div>'+
      '<button class="btn tksh-drop" data-g="'+esc(g.key)+'"'+
        ' title="briše folder ovog snimanja sa diska; odluke i već poslati komentari ostaju netaknuti">'+
        '&#128465; Ukloni iz galerije</button></div>';}
  // The capture's own feedback. Shown while a pass is RUNNING and also when a finished folder
  // is short of its plan (a run that died half way is exactly what the operator needs to see);
  // silent for a complete capture, where captured_at already says everything.
  function tkShProgHtml(w){
    var pr=w&&w.progress;if(!pr)return "";
    var run=!!pr.running,done=+pr.done||0,tot=+pr.total||0;
    if(!run&&(!tot||done>=tot))return "";
    var pct=tot?Math.max(0,Math.min(100,Math.round(done*100/tot))):0;
    return '<div class="tksh-prog'+(run?" run":"")+'">'+
      (run?'<span class="tkan-spin"></span>':'')+
      '<b>'+(run?"snima se":"snimanje nije dovršeno")+'</b>'+
      '<span class="tksh-state">'+esc(pr.side==="before"?"PRE":"POSLE")+'</span>'+
      '<span>'+(tot?(done+"/"+tot+" ekrana")
                   :(done+" "+srCount(done,"ekran","ekrana","ekrana")+" — plan još nije poznat"))+'</span>'+
      (pr.page?'<span class="tkan-cr">'+esc(pr.page)+'</span>':"")+
      (pr.at?'<span class="tksh-when">'+esc(fmtAt(pr.at))+'</span>':"")+
      (tot?'<span class="tksh-bar" role="img" aria-label="'+done+' od '+tot+' ekrana"><i style="width:'+pct+'%"></i></span>':"")+
      '</div>';}
  // one pair's zoomed comparisons, one row per changed region, in the engine's order.
  // "" when the engine found none — then the caller renders the full pictures inline,
  // which is exactly what the panel did before regions existed.
  function tkShRegionsHtml(p,shot){
    var regs=(p&&Array.isArray(p.regions))?p.regions:[];
    if(!regs.length)return "";
    var n=regs.length,found=(typeof p.regions_found==="number")?p.regions_found:n;
    // A MISSING CROP AND A MISSING PICTURE ARE DIFFERENT THINGS. The engine emits no PRE
    // crop when it could not prove whether the element is new or merely moved — the PRE
    // picture itself is there, whole, under "cele slike ekrana". Saying "nema slike" over
    // it sends the operator looking for a screenshot that exists.
    var nocrop=((p.img||{}).before_full)?"nema isečka — cela slika je ispod":null;
    return '<div class="lbl">izmenjena mesta</div>'+
      regs.map(function(r,i){var im=(r&&r.img)||{},pos=String(r&&r.n?r.n:(i+1))+"/"+n;
        return '<div class="tksh-row tksh-crop tksh-reg">'+
          shot(im.before_crop,"PRE "+pos,nocrop)+shot(im.after_crop,"POSLE "+pos)+'</div>';}).join("")+
      // the engine capped the list (or the server did): say so, in the operator's words
      (found>n?'<div class="tksh-trunc">prikazano '+n+' od '+found+' izmenjenih mesta</div>':"");}
  // one pair -> its zoomed comparisons + the full pictures + the caption input +
  // odobri/odbaci. `tkt` is the group's ticket label: the pair says whose picture it is
  // without the group header on screen.
  // `ro` = a COLLAPSED record: same pictures, no caption box and no decision, because the
  // engine already has a picture of this screen and the counts on the header describe the
  // cards that ARE decisions. `extra` is html appended inside the card (the representative
  // hangs the records it stands for there).
  function tkShPairHtml(work,p,tkt,ro,extra){
    var k=tkShDom(work.work_id,p.pair_id),key=tkShKey(work.work_id,p.pair_id);
    var st=ro?"ro":tkShState(work.work_id,p.pair_id),md=tkShModeOf(work.work_id,p.pair_id);
    var img=p.img||{};
    // baseline==="none": the engine captured this with NO before shot at all
    // (shoot.py after --no-baseline). One picture, no comparison — and the
    // reader must not read the empty half as "nothing changed".
    var nob=(p.baseline==="none");
    // A NEW SCREEN. Also baseline "none" — there is one picture and no comparison — but the
    // REASON is different and the customer is told a different thing: it did not exist, as
    // opposed to we could not photograph it. `nav.text` is the server's own lines, the ones
    // the comment will carry.
    var np=(p.new_page===true),nav=(p&&p.nav&&typeof p.nav==="object")?p.nav:{};
    var regs=(Array.isArray(p.regions)&&p.regions.length)?p.regions.length:0;
    var why=((work.baseline&&work.baseline.reason)?String(work.baseline.reason):"");
    var shot=function(url,lab,miss){
      if(!url)return '<div class="tksh-miss">'+esc(lab)+' — '+esc(miss||"nema slike")+'</div>';
      return '<a class="tksh-shot" href="'+esc(url)+'" target="_blank" rel="noopener" title="otvori u punoj veličini">'+
        '<span class="tksh-lab">'+esc(lab)+'</span><img src="'+esc(url)+'" alt="'+esc(lab+" — "+(p.title||p.page_id||""))+'" loading="lazy"></a>';};
    return '<div class="tkan-card tksh-pair'+(ro?" tksh-ro":(st==="ok"?" on":(st==="no"?" off":"")))+'" id="tksh-card-'+k+'"'+
      (ro&&p.drop_reason?' title="'+esc(p.drop_reason)+'"':"")+'>'+
      '<div class="tkan-h"><span class="tksh-tkt">'+esc(tkt)+'</span>'+
        '<span class="tkan-t">'+esc(p.title||p.page_id||"(bez naziva)")+'</span>'+
        (p.state?'<span class="tksh-state">'+esc(p.state)+'</span>':"")+
        (p.url?'<span class="tkan-dot">&middot;</span><span class="tkan-cr">'+esc(p.url)+'</span>':"")+'</div>'+
      (np?'<div class="tksh-new"><b>&#10022; nova stranica — nije postojala pre izmene</b>'+
          '<span>ovako će pisati u komentaru kupcu (nacrt se može doraditi pre slanja):</span>'+
          '<pre>'+esc(String(nav.text||""))+'</pre>'+
          // The engine refuses to invent a click path. An empty steps line is the operator's
          // to fill in, and saying which one it is beats leaving them to notice.
          ((nav.steps&&nav.steps.length)?"":'<span class="tksh-fill">put do stranice nije mogao da se izvede iz mape — red „Do nje:“ ide prazan u komentar</span>')+
          '</div>'
        :(nob?'<div class="tksh-note">bez snimka PRE izmene — nema osnove za poređenje'+(why?' ('+esc(why)+')':"")+'</div>':""))+
      // a collapsed record has no caption box: nothing it could caption ever reaches the
      // comment, and an input that quietly does nothing is worse than no input
      (ro?"":'<input class="tksh-cap" id="tksh-cap-'+k+'" data-key="'+esc(key)+'" maxlength="80" value="'+esc(tkShCapV[key]||"")+'" placeholder="'+esc(p.caption||p.title||"izmena na ekranu")+'">')+
      // The WHOLE screen first, always visible. It is what the operator came to
      // look at; the zoomed regions below say where to look inside it. This was
      // folded behind a closed <details> whenever a pair had regions, and since
      // every shown pair now has them, the before/after view vanished exactly
      // when the zoom started working.
      (regs?('<details class="tksh-full" open><summary>cele slike ekrana &mdash; PRE i POSLE</summary>'+
             '<div class="tksh-row">'+shot(img.before_full,"PRE",np?"ova stranica nije postojala pre izmene":(nob?"nije snimljeno":null))+shot(img.after_full,"POSLE")+'</div></details>')
           :('<div class="tksh-row">'+shot(img.before_full,"PRE",np?"ova stranica nije postojala pre izmene":(nob?"nije snimljeno":null))+shot(img.after_full,"POSLE")+'</div>'))+
      tkShRegionsHtml(p,shot)+
      (extra||"")+
      (ro?'<div class="tksh-roflag">&#9723; ne ide u komentar &mdash; '+
            (p.represented_by?"isti izgled je već snimljen na ekranu koji ga predstavlja":"alat nije našao izmenu na ovom ekranu")+'</div></div>'
        :'<div class="tkan-actions tksh-acts"><button class="btn tksh-ok" data-k="'+k+'" data-w="'+esc(work.work_id)+'" data-p="'+esc(p.pair_id)+'">&#9989; odobri</button>'+
        '<button class="btn tksh-no" data-k="'+k+'" data-w="'+esc(work.work_id)+'" data-p="'+esc(p.pair_id)+'">&#10060; odbaci</button>'+
        // what an approved pair contributes; picking one approves the pair with it.
        // Lit ONLY when the pair is approved: a cyan pill is the strongest thing on the
        // card, and on a rejected — or on an untouched — pair it reads as "this one is
        // going in" when it is not.
        TKSH_MODES.map(function(m){return '<span class="tk-pill tksh-md'+((st==="ok"&&md===m[0])?" on":"")+
          '" data-k="'+k+'" data-w="'+esc(work.work_id)+'" data-p="'+esc(p.pair_id)+'" data-m="'+esc(m[0])+
          '" title="'+esc(m[2])+'">'+esc(m[1])+'</span>';}).join("")+
        '<span class="tksh-flag st-'+st+'">'+tkShRevFlag(st,md)+'</span></div></div>');}
  // one work inside its ticket group: the progress strip, the engine's errors, the pairs.
  //
  // COLLAPSED RECORDS HANG UNDER THE PAIR THAT IS THEIR PICTURE. The engine names that pair
  // by page_id (`represented_by`) and that link is followed in ONE direction only — the
  // representative's `represents` list is not shipped, so the two can never disagree.
  // Anything whose representative is not among the shown pairs (the engine dropped it for
  // its own reason, or the representative itself is not shown) lands in the work-level list
  // at the end. Nothing is ever thrown away: the engine keeps the record, the pictures and
  // the crops on purpose, and this is how the operator reaches them.
  function tkShWorkHtml(g,w,many){
    var wd=String(w.work_id||"").replace(/[^A-Za-z0-9_-]/g,"_");
    var wnb=(w.baseline&&w.baseline.state==="none");
    var shown=tkShPairsOf(w),hidden=tkShHiddenOf(w),kids={},orphans=[];
    hidden.forEach(function(p){
      var rep=String(p.represented_by||"");
      if(rep&&shown.some(function(s){return s.page_id===rep;}))(kids[rep]=kids[rep]||[]).push(p);
      else orphans.push(p);});
    var sub=function(summary,list){
      return '<details class="tksh-sub"><summary>'+summary+'</summary><div class="tksh-subb">'+
        list.map(function(p){return tkShPairHtml(w,p,g.label,true);}).join("")+'</div></details>';};
    return '<div class="tksh-work">'+
      (many?'<div class="lbl">snimanje '+esc(fmtAt(w.captured_at||""))+(w.repo?' · '+esc(w.repo):"")+
            (wnb?' <span class="tksh-state">bez snimka PRE</span>':"")+'</div>':"")+
      '<div id="tksh-prog-'+wd+'">'+tkShProgHtml(w)+'</div>'+
      ((w.errors&&w.errors.length)?'<div class="tkan-errbox">'+w.errors.map(function(e){return esc(e);}).join("<br>")+'</div>':"")+
      (shown.length?shown.map(function(p){
          var ks=kids[p.page_id]||[];
          // NO NUMBER HERE. The representative's own caption already says "+ N druga
          // ekrana"; a second count beside it would be in a different unit (records, not
          // screens) and the two would read as a contradiction.
          return tkShPairHtml(w,p,g.label,false,
            ks.length?sub("ekrani sa istim izgledom &mdash; ovaj snimak je njihova slika",ks):"");
        }).join("")
        :'<div class="tkan-empty">'+(hidden.length
            ?'Nema šta da se pregleda: svaki snimak ovog rada je sklopljen iza drugog ekrana ili je bez izmene.'
            :'Nijedan par nije snimljen za ovaj rad.')+'</div>')+
      // "nije izdvojio za pregled" and not "bez izmene": this list holds BOTH the records
      // the engine dropped as structurally unchanged AND the ones whose representative is
      // itself not shown. Each card says which it is; the summary must not claim one reason
      // for both.
      (orphans.length?sub(orphans.length+' '+srCount(orphans.length,"snimak","snimka","snimaka")+
                          ' &mdash; alat ih nije izdvojio za pregled, ne idu u komentar',orphans):"")+
      '</div>';}
  function tkShRender(){
    var host=$("tkan-body");if(!host)return;
    if(!tkShWorks.length){host.innerHTML='<div id="tksh-root"><div class="tkan-empty">'+
      (tkShFilter?('Za tiket <b>'+esc(tkShFilter)+'</b> nema snimaka.'):'Nema snimaka.')+
      '<br>Parovi se pojave pošto <b>shoot.py after</b> odradi snimanje na tiketu — vizuelna izmena (šablon/CSS/JS) se snimi pre izmene i posle nje.'+
      '<br>Snimak bez <b>--ticket</b> se ne prikazuje ovde (ostaje na disku, ali ne pripada nijednom komentaru).</div></div>';
      // The empty gallery is the NORMAL end of a successful send, so the answer has to
      // survive into it - this branch used to return before anything could be painted.
      tkShNotesPaint();return;}
    var groups=tkShGroups();
    host.innerHTML='<div id="tksh-root">'+groups.map(function(g,i){
      // first group expanded, the rest collapsed — until the operator says otherwise, and
      // then that choice survives every progress repaint
      var open=(g.key in tkShOpenG)?!!tkShOpenG[g.key]:(i===0);
      var c=tkShTally(g.works),newest=g.works[0]||{};
      return '<details class="tksh-grp" data-g="'+esc(g.key)+'"'+(open?" open":"")+'>'+
        '<summary title="'+esc(g.label)+' — klikni da otvoriš/zatvoriš snimke ovog tiketa">'+
          '<span class="tksh-tkt">'+esc(g.label)+'</span>'+
          tkShCountHtml(c)+
          (newest.captured_at?'<span class="tksh-when">'+esc(fmtAt(newest.captured_at))+'</span>':"")+
          (newest.repo?'<span class="tksh-when">'+esc(newest.repo)+'</span>':"")+
          (tkShNoBase(g.works)?'<span class="tksh-state">bez snimka PRE</span>':"")+
          '<span class="tksh-live" id="tksh-live-'+g.dom+'">'+
            (tkShRunning(g.works)?'<span class="tkan-spin"></span>snima se':"")+'</span>'+
          // BOTH ACTIONS ON THE HEADER TOO, because a collapsed group had none: deciding a
          // ticket meant expanding it first even when the operator already knew the answer.
          // Same handlers as the pair at the bottom of the group — one implementation, two
          // places to reach it. Buttons inside a <summary> toggle the <details> on click, so
          // the handler stops the event; see the wiring below.
          '<span class="tksh-hact">'+
            // The outcome has to be readable HERE too. It used to be written only into the
            // note at the bottom of the group — which is inside the <details> body, so on a
            // COLLAPSED group the answer ("nothing changed", "the run stayed and why") was
            // rendered into a hidden element and the button looked dead.
            '<span class="tkan-note tksh-gnote" data-g="'+esc(g.key)+'"></span>'+
            '<button class="btn tksh-go" data-g="'+esc(g.key)+'"'+
              ' title="odobreni parovi ovog tiketa idu u nacrt komentara na '+esc(g.label)+'">'+
              '&#128228; pošalji</button>'+
            '<button class="btn tksh-nogo" data-g="'+esc(g.key)+'"'+
              ' title="nijedna slika ovog tiketa ne ide u komentar — sve se označavaju kao odbačene">'+
              '&#128683; ništa</button>'+
          '</span>'+
        '</summary>'+
        '<div class="tksh-gb">'+g.works.map(function(w){
          return tkShWorkHtml(g,w,g.works.length>1);}).join("")+
          // EACH TICKET SENDS ITSELF, from the bottom of its own group. One button for
          // everything on screen meant the only control there was could ship another
          // customer's pictures; and with the groups collapsed it was impossible to tell
          // whose. The button names its ticket, and it posts that ticket's works only.
          tkShStuckHtml(g)+
          '<div class="tksh-gsend">'+
            '<span class="tkan-note tksh-gnote" id="tksh-note-'+g.dom+'" data-g="'+esc(g.key)+'"></span>'+
            '<button class="btn tksh-nogo" data-g="'+esc(g.key)+'"'+
              ' title="nijedna slika ovog tiketa ne ide u komentar — sve se označavaju kao odbačene">'+
              '&#128683; Ništa iz ovog tiketa u komentar</button>'+
            '<button class="btn tksh-go" id="tksh-go-'+g.dom+'" data-g="'+esc(g.key)+'"'+
              ' title="odobreni parovi ovog tiketa idu u nacrt komentara na '+esc(g.label)+'">'+
              '&#128228; Pošalji u komentar tiketa <span class="tksh-tkt">'+esc(g.label)+'</span></button>'+
          '</div>'+
        '</div>'+
      '</details>';}).join("")+
      // The strip is the LAST child of #tksh-root and sticks to the bottom of #tkan-body,
      // which is the element that scrolls (.db, overflow-y:auto) and is therefore a valid
      // sticky ancestor. It carries the jump-back control and the overall tally — the two
      // things that must be readable however far down the operator is. NOT a send button:
      // sending belongs to one ticket and lives inside that ticket's group.
      '<div class="tksh-foot">'+
        '<button class="btn tksh-top" id="tksh-top" title="vrati se na prvi snimak tiketa koji trenutno gledaš">'+
          '&#8593; vrh tiketa <span class="tksh-tkt" id="tksh-toptkt"></span></button>'+
        '<span class="tkan-note" id="tksh-note"></span></div></div>';
    tkShNote();
    // one repaint for both controls: approve/reject flips the card, the flag and the group
    // counts; a mode pill flips the pill row too — and picking a mode approves the pair,
    // because choosing what a pair contributes IS approving it (the operator's own wording)
    var paint=function(w,pid,dom){var st=tkShState(w,pid),card=document.getElementById("tksh-card-"+dom);
      if(card){card.classList.toggle("on",st==="ok");card.classList.toggle("off",st==="no");
        var f=card.querySelector(".tksh-flag");
        if(f){f.className="tksh-flag st-"+st;f.innerHTML=tkShRevFlag(st,tkShModeOf(w,pid));}
        Array.prototype.forEach.call(card.querySelectorAll(".tksh-md"),function(el){
          el.classList.toggle("on",st==="ok"&&el.getAttribute("data-m")===tkShModeOf(w,pid));});}
      tkShPaintCounts();tkShNote();};
    Array.prototype.forEach.call(host.querySelectorAll(".tksh-ok,.tksh-no"),function(b){
      b.onclick=function(){var w=b.getAttribute("data-w"),pid=b.getAttribute("data-p");
        tkShSel[tkShKey(w,pid)]=b.classList.contains("tksh-ok");
        paint(w,pid,b.getAttribute("data-k"));
        tkShSave(w);};});
    Array.prototype.forEach.call(host.querySelectorAll(".tksh-md"),function(el){
      el.onclick=function(){var w=el.getAttribute("data-w"),pid=el.getAttribute("data-p");
        tkShMode[tkShKey(w,pid)]=el.getAttribute("data-m");
        tkShSel[tkShKey(w,pid)]=true;
        paint(w,pid,el.getAttribute("data-k"));
        tkShSave(w);};});
    Array.prototype.forEach.call(host.querySelectorAll(".tksh-cap"),function(el){
      el.oninput=function(){var ck=el.getAttribute("data-key");
        tkShCapV[ck]=String(el.value||"");
        // The caption rides with the approved pair, so it is part of the
        // decision. Debounced — one request per keystroke would be absurd.
        tkShSave(String(ck).split("||")[0],900);};});
    Array.prototype.forEach.call(host.querySelectorAll(".tksh-grp"),function(el){
      el.addEventListener("toggle",function(){tkShOpenG[el.getAttribute("data-g")]=el.open;
        tkShSyncTop();});});
    // A <button> inside a <summary> still toggles its <details>: the click bubbles and the
    // browser's default action fires on the summary. Without stopping BOTH, pressing
    // "pošalji" on a collapsed group would send AND expand it, and on an open one would
    // send AND collapse it — the panel would appear to jump on its own.
    var noToggle=function(ev){if(ev){ev.preventDefault();ev.stopPropagation();}};
    // CONFIRMED BEFORE IT LEAVES. One click now puts a comment on a real customer's
    // ticket — it is not a draft any more and there is no unsend — so the dialog names
    // the ticket, the pairs and the pictures, and says the status is not touched (the
    // operator's first question about this button). Counted from the SERVER's att_n,
    // never from a picture count re-derived here; the attachment policy is one line in
    // server.py and a second implementation would promise the wrong number.
    // Nothing is approved -> nothing goes out -> no dialog: the "ništa" button has its
    // own, and asking twice teaches people to click through.
    Array.prototype.forEach.call(host.querySelectorAll(".tksh-go"),function(b){
      b.onclick=function(ev){noToggle(ev);
        var key=b.getAttribute("data-g"),grp=null;
        tkShGroups().forEach(function(g){if(g.key===key)grp=g;});
        if(!grp)return;
        var n=0,imgs=0;
        grp.works.forEach(function(w){(w.pairs||[]).forEach(function(p){
          if(tkShSel[tkShKey(w.work_id,p.pair_id)]!==true)return;
          n++;
          if(tkShModeOf(w.work_id,p.pair_id)!=="comment")imgs+=(p.att_n||0);});});
        if(n&&!window.confirm("Komentar ide na tiket "+grp.label+" ODMAH.\n\n"+
                              n+" "+srCount(n,"par","para","parova")+", "+
                              imgs+" "+srCount(imgs,"slika","slike","slika")+".\n\n"+
                              "Slanje se ne može povući. Status tiketa se ne menja."))return;
        tkShSend(key);};});
    // NOTHING FROM THIS TICKET. Marks every pair of the group REJECTED and then posts, so
    // the refusal is persisted rather than left as an unsaved click — an unsent decision is
    // the bug we just fixed in the other direction. Zero approved pairs makes the server
    // drop the draft, so the comment gets nothing, which is the whole point.
    // Confirmed first: it decides a whole ticket at once, and after it the run counts as
    // finished and leaves the gallery.
    Array.prototype.forEach.call(host.querySelectorAll(".tksh-nogo"),function(b){
      b.onclick=function(ev){noToggle(ev);
        var key=b.getAttribute("data-g"),grp=null;
        tkShGroups().forEach(function(g){if(g.key===key)grp=g;});
        if(!grp)return;
        // COUNT first, mutate only after the operator says yes. Marking them and then
        // asking would leave every pair rejected on "Otkaži" — a cancel that changed the
        // thing it was cancelling.
        var n=0;
        grp.works.forEach(function(w){n+=(w.pairs||[]).length;});
        if(!window.confirm("Nijedna slika tiketa "+grp.label+" ne ide u komentar?\n\n"+
                           n+" "+srCount(n,"par","para","parova")+
                           " se označava kao odbačeno. Slike ostaju kao osnova za "+
                           "sledeće poređenje."))return;
        grp.works.forEach(function(w){(w.pairs||[]).forEach(function(p){
          tkShSel[tkShKey(w.work_id,p.pair_id)]=false;});});
        tkShSend(key);};});
    // REMOVE A RUN THE SWEEP CANNOT REMOVE. Only ever offered for a work the server
    // itself marked `stay.blocked` — decided, owing the customer nothing, and refused by
    // promotion — and the server checks that again before it deletes anything. The
    // confirm names what is lost, because this one really does throw pictures away.
    Array.prototype.forEach.call(host.querySelectorAll(".tksh-drop"),function(b){
      b.onclick=function(ev){noToggle(ev);
        var key=b.getAttribute("data-g"),grp=null;
        tkShGroups().forEach(function(g){if(g.key===key)grp=g;});
        if(!grp)return;
        var st=tkShStuck(grp);if(!st.length)return;
        if(!window.confirm("Ukloniti "+st.length+" "+srCount(st.length,"snimanje","snimanja","snimanja")+
                           " tiketa "+grp.label+" iz galerije?\n\n"+
                           "Slike se brišu sa diska i NE postaju osnova za sledeće poređenje.\n"+
                           "Odluke i već poslati komentari ostaju netaknuti."))return;
        b.disabled=true;tkShNoteSet(key,"","uklanjam…");
        var done=0,gone=0,fail="",lost="";
        var step=function(){
          if(done>=st.length){
            b.disabled=false;
            tkShNoteSet(key,fail?"err":"ok",
              fail?("nije uklonjeno: "+fail)
                 :(gone+" "+srCount(gone,"snimanje","snimanja","snimanja")+" uklonjeno iz galerije"+
                   (lost?(" · "+lost):"")));
            fetch("/api/tickets/shots"+(tkShFilter?("?ticket="+encodeURIComponent(tkShFilter)):"")).then(function(r){return r.json();}).then(function(d){
              if(!tkShAlive())return;
              tkShAdopt((d&&Array.isArray(d.works))?d.works:[]);tkShRender();}).catch(function(){});
            return;}
          var w=st[done++];
          aiPost("/api/tickets/shots/discard",{work_id:w.work_id}).then(function(d){
            if(d&&d.error)fail=String(d.why||d.error);
            else{if(d&&d.deleted)gone++;if(d&&d.reason)lost=String(d.reason);}
            step();}).catch(function(e){fail=aiErrText(e);done=st.length;step();});};
        step();};});
    // ↑ BACK TO THE START OF THE TICKET BEING READ — not the top of the panel, which with
    // two groups open is somebody else's ticket. The target is the group whose start has
    // scrolled off the top and which still fills it; the button prints that group's ticket
    // so the operator never has to guess where it lands, and hides itself when the start is
    // already on screen (a control that does nothing is worse than no control).
    // `.onscroll =` and not addEventListener: this runs again on every re-render, and the
    // scroller (#tkan-body) outlives the markup inside it.
    host.onscroll=tkShSyncTop;
    if($("tksh-top"))$("tksh-top").onclick=function(){
      var g=tkShTopTarget;if(!g)return;
      host.scrollTop+=g.getBoundingClientRect().top-host.getBoundingClientRect().top;
      tkShSyncTop();};
    // The last send's answer, re-applied over the fresh markup: this render is usually
    // the one the SEND itself triggered, and painting it before the reload lands means
    // painting it onto elements that are about to be thrown away.
    tkShNotesPaint();
    tkShSyncTop();}
  var tkShTopTarget=null;      // the .tksh-grp the jump-back button currently points at
  function tkShSyncTop(){
    var host=$("tkan-body"),btn=$("tksh-top");if(!host||!btn)return;
    var top=host.getBoundingClientRect().top,hit=null;
    Array.prototype.forEach.call(host.querySelectorAll(".tksh-grp"),function(el){
      var r=el.getBoundingClientRect();
      // scrolled meaningfully past its start, and still the group under the top edge
      if(r.top<top-24&&r.bottom>top+40)hit=el;});
    tkShTopTarget=hit;
    btn.classList.toggle("on",!!hit);
    var lab=hit&&hit.querySelector(".tksh-tkt"),out=$("tksh-toptkt");
    if(out)out.textContent=lab?lab.textContent:"";}
  // group-header counts only — cheap enough to run on every click, and it must run there:
  // a header is the only place the counts are visible while its group is collapsed
  function tkShPaintCounts(){var host=$("tkan-body");if(!host)return;
    tkShGroups().forEach(function(g){
      var el=host.querySelector('.tksh-grp[data-g="'+g.key.replace(/"/g,'\\"')+'"] summary');
      if(!el)return;
      var spans=el.querySelectorAll(".tksh-cnt");if(!spans.length)return;
      var tmp=document.createElement("span");tmp.innerHTML=tkShCountHtml(tkShTally(g.works));
      var fresh=tmp.querySelectorAll(".tksh-cnt");
      for(var i=0;i<spans.length&&i<fresh.length;i++)spans[i].innerHTML=fresh[i].innerHTML;});}
  // ONE TICKET'S SEND. POST one approve per CHANGED work OF THAT TICKET: a work whose
  // selection still matches what the server already had is left alone (no manifest rewrite,
  // no rev bump on its ticket), and no other ticket on screen is touched at all.
  /* ---------- persist ONE work's decisions, without sending anything ----------
     The review used to live only in this browser: decide every pair, close the
     panel without pressing send, and the whole review was gone (operator,
     2026-08-28: "kada odaberem odobri/odbaci na svim parovima i ne pošaljem već
     izađem, obriše se ono što sam uradio, ne čuva se odabir").
     POSTs /api/tickets/shots/decide, which writes the same three-state
     `decision` the send path writes and touches NO outbox — deciding is not a
     promise to send, and a draft written here would make the send button look
     already pressed. Debounced per work so a burst of clicks is one request. */
  var tkShSaveT={};
  function tkShDecisionJob(w){
    var pairs=[],reject=[],drop=[];
    (w.pairs||[]).forEach(function(p){
      var key=tkShKey(w.work_id,p.pair_id),sel=tkShSel[key];
      if(sel===true)pairs.push({pair_id:p.pair_id,
                                caption:tkShCap(w.work_id,p.pair_id),
                                mode:tkShModeOf(w.work_id,p.pair_id)});
      else if(sel===false)reject.push(p.pair_id);
      else drop.push(p.pair_id);});
    return {work_id:w.work_id,ticket:w.ticket,pairs:pairs,reject:reject,drop:drop};}
  function tkShSave(workId,delay){
    if(!workId)return;
    var w=null;
    (tkShWorks||[]).forEach(function(x){if(String(x.work_id)===String(workId))w=x;});
    if(!w)return;
    if(tkShSaveT[workId])clearTimeout(tkShSaveT[workId]);
    tkShSaveT[workId]=setTimeout(function(){
      delete tkShSaveT[workId];
      aiPost("/api/tickets/shots/decide",tkShDecisionJob(w)).then(function(d){
        // aiPost RESOLVES on an HTTP error — it never rejects, it hands back
        // {error:"HTTP 404"}. Checking only .catch() meant a 404 (an agent view
        // running the previous build) counted as a save: the decision was
        // marked persisted, nothing was on disk, and the send button then said
        // "komentar je već otišao" about a comment that never existed.
        if(d&&d.error){
          tkShNoteSet(tkShGroupKeyOf(w),"err",
                      "odabir NIJE sačuvan ("+d.error+") — ako si upravo ažurirao "+
                      "brain, restartuj agent view");
          return;}
        // The server now agrees with the screen, so a later repaint must not
        // read these back as "never reviewed".
        (w.pairs||[]).forEach(function(p){var k=tkShKey(w.work_id,p.pair_id);
          tkShWas[k]=tkShSel[k];tkShWasM[k]=tkShModeOf(w.work_id,p.pair_id);});
      }).catch(function(e){
        // Never silent: an unsaved decision that LOOKS saved is the bug.
        tkShNoteSet(tkShGroupKeyOf(w),"err",
                    "odabir NIJE sačuvan ("+aiErrText(e)+") — proveri agent view");});
    },delay||0);}
  function tkShGroupKeyOf(w){var key="";
    tkShGroups().forEach(function(g){g.works.forEach(function(x){
      if(String(x.work_id)===String(w.work_id))key=g.key;});});
    return key;}

  function tkShSend(gkey){
    var groups=tkShGroups(),g=null;
    groups.forEach(function(x){if(x.key===gkey)g=x;});
    if(!g)return;
    // EVERY button and EVERY note of this group, header and footer alike — resolved by
    // data-g, not by the footer's id. The same action now has two entry points and its
    // answer has to appear at whichever one the operator is looking at.
    var q=function(sel){var host=$("tkan-body");
      return host?Array.prototype.slice.call(
        host.querySelectorAll(sel+'[data-g="'+gkey.replace(/"/g,'\\"')+'"]')):[];};
    var btns=q(".tksh-go").concat(q(".tksh-nogo"));
    var note={set:function(cls,txt){tkShNoteSet(gkey,cls,txt);}};
    var jobs=[];
    g.works.forEach(function(w){
      var pairs=[],reject=[],drop=[],changed=false;
      // THREE STATES, ALL THREE ON THE WIRE. Approved goes in `pairs`, an explicit "odbaci"
      // in `reject` (the server persists it, so a reload still shows it), and never-reviewed
      // in `drop` — which clears any decision the manifest held. They are NOT the same
      // thing and sending them down one list is what lost every rejection.
      // EVERY pair of the work is walked, collapsed ones included.
      (w.pairs||[]).forEach(function(p){var key=tkShKey(w.work_id,p.pair_id),sel=tkShSel[key],
          on=(sel===true),md=tkShModeOf(w.work_id,p.pair_id);
        if(on)pairs.push({pair_id:p.pair_id,caption:tkShCap(w.work_id,p.pair_id),mode:md});
        else if(sel===false)reject.push(p.pair_id);
        else drop.push(p.pair_id);
        // a pair already approved whose MODE changed is a change too, or switching
        // "both" to "samo slike" would silently send nothing
        if(sel!==tkShWas[key]||(on&&md!==(tkShWasM[key]||"both")))changed=true;});
      // SEND WHAT IS OWED, NOT WHAT IS APPROVED. This used to fire whenever any pair was
      // approved, which under a draft-until-close model just rewrote the draft — now it
      // would put a SECOND copy of the same comment on the customer's ticket, and a posted
      // comment cannot be edited or withdrawn. So: the decisions changed, or the server
      // says this work still has something undelivered (a send that failed or never ran).
      // SEND WHEN THE CUSTOMER IS OWED SOMETHING — three independent reasons,
      // and the third is not optional now that decisions are saved as they are
      // clicked. `changed` compares against what the SERVER last said, so it is
      // false the moment a decision has been persisted; `undelivered` counts
      // pending drafts and is 0 when none was ever written. A work with
      // approved pairs that has never been drafted (`ever_sent` false) is owed
      // a comment by both of those measures and was refused by both.
      if(changed||(w.undelivered||0)||(pairs.length&&!w.ever_sent))
        jobs.push({work_id:w.work_id,ticket:w.ticket,
                   pairs:pairs,reject:reject,drop:drop});});
    if(!jobs.length){
      // WHY nothing happened, not just THAT nothing happened. The operator pressed
      // send on a ticket they had not reviewed, got "nema izmena" into a hidden
      // element, and read the silence as a broken button. Untouched and
      // already-sent are different situations and get different sentences.
      //
      // THREE, not two. "komentar je već otišao" was printed for ANY group whose
      // decisions matched the server, INCLUDING one where every pair is rejected and
      // therefore no comment was ever written — a false statement about the customer's
      // ticket, right where the operator was already asking why the card is still on
      // screen. A group that is all-rejected gets its own sentence, and when the run is
      // also stuck the reason rides along instead of being left to the strip above.
      var seen=0,tot=0,okN=0;
      g.works.forEach(function(w){(w.pairs||[]).forEach(function(p){tot++;
        var s=tkShSel[tkShKey(w.work_id,p.pair_id)];
        if(s!==undefined)seen++;
        if(s===true)okN++;});});
      var stuck=tkShStuck(g),tail=stuck.length?(" · "+String((stuck[0].stay&&stuck[0].stay.reason)||"")):"";
      if(!seen)note.set("err","nijedan par nije pregledan — odobri ili odbaci "+tot+" "+
                        srCount(tot,"par","para","parova")+", ili klikni „ništa“");
      else if(okN){
        // "Već je otišlo" is a claim about the CUSTOMER'S TICKET and must never
        // be guessed. It holds only when this group really has a drafted
        // comment behind it; a group that was approved but never drafted lands
        // here too, and telling the operator it had been sent is how #43417
        // ended with an empty ticket and a deleted run.
        var everN=0;
        g.works.forEach(function(w){if(w.ever_sent)everN++;});
        if(everN)note.set("err","nema izmena od poslednjeg slanja — komentar je već otišao");
        else note.set("err","odobreno, ali komentar nije nikada poslat — ako je "+
                            "dugme bez efekta, restartuj agent view i probaj ponovo");}
      else note.set(stuck.length?"warn":"ok",
                    "sve je odbačeno — nijedan komentar nije ni poslat"+tail);
      return;}
    btns.forEach(function(b){b.disabled=true;});
    note.set("","šaljem na helpdesk…");
    var done=0,pairsIn=0,gone=0,left="",fail="",sent=0,amb="",bad="",resumed=0;
    var step=function(){
      if(done>=jobs.length){
        btns.forEach(function(b){b.disabled=false;});
        // WHAT THE HELPDESK SAID, not what the outbox holds. The button used to answer
        // "šalje se pri zatvaranju tiketa" the moment a draft was written, and nothing
        // ever went — the operator read a green note over a comment that did not exist.
        // Three outcomes and three sentences: an AMBIGUOUS send (the request left, the
        // answer never came) is neither a success nor a failure and must never be
        // painted as either — nothing is re-sent on its own, so the operator is the
        // one who looks.
        var cls="ok",txt="";
        if(fail){cls="err";txt="greška: "+fail;}
        else if(amb){cls="warn";
          txt="nejasan ishod: "+amb+" — zahtev je otišao, odgovor nije stigao. Proveri komentare na tiketu "+
              g.label+"; ništa se ne šalje ponovo samo od sebe.";}
        else if(bad){cls="err";
          txt="komentar NIJE poslat: "+bad+" — snimci ostaju u galeriji, možeš probati ponovo";}
        else if(sent){txt="poslato na "+g.label+" — "+sent+" "+tkSentWord("comment",sent)+
              (pairsIn?(", "+pairsIn+" "+srCount(pairsIn,"par","para","parova")):"")+
              (resumed?" (nastavak ranije započetog slanja)":"");}
        else{txt="ništa nije poslato — nacrt uklonjen";}
        note.set(cls,txt
              // a finished run leaves the gallery: its pictures became the baseline and the
              // folder is gone. Said out loud — a card that vanishes without a word reads
              // as a bug.
              +(gone?(" · "+gone+" "+srCount(gone,"snimanje","snimanja","snimanja")+" završeno: slike su prešle u baseline, folder uklonjen"):"")
              +(left?(" · "+left):""));
        fetch("/api/tickets").then(function(r){return r.json();}).then(function(d){TIX=d||{projects:[]};renderTickets();}).catch(function(){});
        // the listing may have lost a work (finalized) — repaint from the server, keeping
        // every local decision (tkShAdopt only adopts pairs it has never seen)
        fetch("/api/tickets/shots"+(tkShFilter?("?ticket="+encodeURIComponent(tkShFilter)):"")).then(function(r){return r.json();}).then(function(d){
          if(!tkShAlive())return;
          tkShAdopt((d&&Array.isArray(d.works))?d.works:[]);tkShRender();}).catch(function(){});
        return;}
      var j=jobs[done++];
      aiPost("/api/tickets/shots/approve",j).then(function(d){
        if(d&&d.error)fail=String(d.error);else pairsIn+=(d&&d.pairs)||0;
        // The send outcome of THIS work, straight from the server: it posted the
        // comment inside the same request and knows whether the helpdesk took it.
        if(d&&d.send==="posted"){sent+=(d.comments||0);if(d.resumed)resumed++;}
        else if(d&&d.send==="ambiguous")amb=String(d.send_error||"nepoznat ishod");
        else if(d&&d.send==="failed")bad=String(d.send_error||"nepoznata greška");
        ((d&&d.finalized)||[]).forEach(function(f){
          if(f&&f.deleted)gone++;else if(f&&f.reason)left=String(f.reason);});
        (j.pairs||[]).forEach(function(p){var key=tkShKey(j.work_id,p.pair_id);
          tkShWas[key]=true;tkShWasM[key]=p.mode||"both";});
        (j.reject||[]).forEach(function(p){tkShWas[tkShKey(j.work_id,p)]=false;});
        (j.drop||[]).forEach(function(p){tkShWas[tkShKey(j.work_id,p)]=undefined;});
        step();}).catch(function(e){fail=aiErrText(e);done=jobs.length;step();});};
    step();}
  // Adopt a fresh listing WITHOUT losing the operator's work: a pair we have never seen takes
  // the server's approved/mode state, a pair we already know keeps the local decision — a poll
  // that landed between a click and the send button must not undo the click.
  function tkShAdopt(works){
    tkShWorks=works;
    works.forEach(function(w){(w.pairs||[]).forEach(function(p){
      var key=tkShKey(w.work_id,p.pair_id);
      if(key in tkShWas)return;
      // THREE STATES OFF DISK, not two. `decision` is the persisted answer and
      // `approved` is the same fact in the older shape — a manifest written before the
      // third state existed still reads exactly as it did. `undefined` is a value here:
      // assigning it still makes `key in tkShWas` true, which is what "we have seen this
      // pair and the server says nobody reviewed it" has to mean.
      var st=(p.decision==="rejected")?false
             :((p.decision==="approved"||p.approved)?true:undefined);
      tkShWas[key]=st;
      tkShWasM[key]=(p.mode==="images"||p.mode==="comment")?p.mode:"both";
      tkShMode[key]=tkShWasM[key];
      if(st!==undefined)tkShSel[key]=st;});});}
  function tkShPairSig(works){var out=[];
    (works||[]).forEach(function(w){(w.pairs||[]).forEach(function(p){
      out.push(w.work_id+"||"+p.pair_id);});});
    return out.join(",");}
  // #tkanmodal is SHARED (AI analize, Poslato, Procene, Snimci). The poll is ours only while
  // our own root is still in it — otherwise a tick would repaint somebody else's panel.
  function tkShAlive(){var m=$("tkanmodal");
    return !!(m&&m.classList.contains("open")&&document.getElementById("tksh-root"));}
  function tkShStop(){if(tkShTimer){clearInterval(tkShTimer);tkShTimer=null;}}
  function tkShStart(){tkShStop();tkShTimer=setInterval(tkShTick,TKSH_POLL_MS);}
  function tkShTick(){
    if(!tkShAlive()){tkShStop();return;}
    fetch("/api/tickets/shots"+(tkShFilter?("?ticket="+encodeURIComponent(tkShFilter)):"")).then(function(r){return r.json();}).then(function(d){
      if(!tkShAlive())return;
      var works=(d&&Array.isArray(d.works))?d.works:[];
      if(tkShPairSig(works)===tkShPairSig(tkShWorks)){
        // same pairs — only the capture moved. Patch the strips in place; a full re-render
        // here would blur whatever input has focus for no reason at all.
        var host=$("tkan-body");
        tkShWorks=works;
        works.forEach(function(w){
          var el=document.getElementById("tksh-prog-"+String(w.work_id||"").replace(/[^A-Za-z0-9_-]/g,"_"));
          if(el)el.innerHTML=tkShProgHtml(w);});
        tkShGroups().forEach(function(g){var el=host&&document.getElementById("tksh-live-"+g.dom);
          if(el)el.innerHTML=tkShRunning(g.works)?'<span class="tkan-spin"></span>snima se':"";});
        return;}
      // the pair set changed: the capture finished and wrote its manifest. Re-render — but
      // never while the operator is typing a caption; the next tick is 4s away.
      var ae=document.activeElement;
      if(ae&&/^(INPUT|TEXTAREA)$/.test(ae.tagName||"")&&$("tkan-body")&&$("tkan-body").contains(ae))return;
      tkShAdopt(works);tkShRender();
    }).catch(function(){});}
  // filter: {ticket:"<mod>#<id>"} from the ticket modal — that ticket's work only.
  // No filter = the browse-everything toolbar button.
  function tkShOpen(filter){tkAnOpen();
    tkShFilter=(filter&&filter.ticket)?String(filter.ticket):"";
    tkAnTitle("&#128444;&#65039; Snimci pre/posle"+(tkShFilter?(" — "+esc(tkShFilter)):""));
    if($("tkan-sub"))$("tkan-sub").textContent=tkShFilter?"samo ovaj tiket":"";
    $("tkan-body").innerHTML='<div class="tkan-load"><span class="tkan-spin"></span>Učitavam snimke&hellip;</div>';
    tkShStop();
    // A FINISHED RUN LEAVES BEFORE THE LIST IS DRAWN. Everything decided and everything
    // delivered: its pictures become the repo's baseline and its folder goes, so the
    // gallery holds the tickets still being worked and not every ticket ever done. The
    // server does the deciding; this is a POST because it promotes and deletes, and it
    // never blocks the listing — a failure here just leaves the run visible.
    aiPost("/api/tickets/shots/finalize",{}).catch(function(){}).then(tkShLoad);}
  // the first listing of a fresh gallery: every local decision is dropped on purpose here
  // (this is a NEW review), which is exactly what tkShAdopt needs to take the server's
  // approved/rejected state as its starting point
  function tkShLoad(){
    fetch("/api/tickets/shots"+(tkShFilter?("?ticket="+encodeURIComponent(tkShFilter)):"")).then(function(r){return r.json();}).then(function(d){
      tkShWorks=[];tkShSel={};tkShWas={};tkShMode={};tkShWasM={};tkShCapV={};tkShOpenG={};tkShMsg={};
      tkShAdopt((d&&Array.isArray(d.works))?d.works:[]);
      tkShRender();
      if(d&&d.error&&$("tkan-body"))$("tkan-body").insertAdjacentHTML("afterbegin",'<div class="tkan-errbox">'+esc(d.error)+'</div>');
      tkShStart();
    }).catch(function(e){$("tkan-body").innerHTML='<div class="tkan-errbox"><b>Greška</b>'+esc(aiErrText(e))+'</div>';});}
  (function(){var rb=$("tk-refresh");if(!rb||$("tk-shots"))return;
    var b=document.createElement("button");b.className="btn";b.id="tk-shots";
    b.title="Snimci ekrana pre i posle vizuelne izmene, SVI tiketi — pregled, opis i odobravanje; odobreno se šalje kao komentar na tiket, odmah, bez zatvaranja tiketa. Za jedan tiket: red „snimci“ u samom tiketu.";
    b.innerHTML="&#128444;&#65039; Snimci";b.onclick=function(){tkShOpen();};rb.parentNode.insertBefore(b,rb);})();
  // ticket-modal line under the "poslato" strip: how many pairs ride in this
  // ticket's shots drafts, and how much of them actually reached the customer.
  // A draft with more pictures than one comment holds is delivered as SEVERAL
  // (server-side `outbox[].chunks`, one entry per comment); counting only
  // pair_count then claims the customer has pictures that are still sitting here.
  function tkShotsPairs(t){var o={pairs:0,sent:0,want:0};
    ((t&&Array.isArray(t.outbox))?t.outbox:[]).forEach(function(d){
      if(!d||d.kind!=="shots")return;
      o.pairs+=(typeof d.pair_count==="number"?d.pair_count:0);
      var ch=Array.isArray(d.chunks)?d.chunks:[];
      o.want+=ch.length;
      ch.forEach(function(c){if(c&&c.posted)o.sent++;});});
    return o;}
  // ALWAYS rendered: this row is the ticket's own way into the gallery (the toolbar
  // button browses every ticket, which for one change is 168 pairs). Clickable via the
  // .tk-sl-ticksum affordance the "poslato" row above it already uses; wired in openTix,
  // which is where the module+id live.
  function tkShotsKv(t){var p=tkShotsPairs(t),n=p.pairs,
      part=(p.want&&p.sent<p.want)?(' — poslato '+p.sent+'/'+p.want+' komentara'):'';
    return '<div class="kv tk-sl-ticksum tk-sh-ticksum" id="tx-shots" title="snimci pre/posle za OVAJ tiket — klikni za pregled, opis i odobravanje"><span>snimci</span><b>'+
      (n?(n+" "+srCount(n,"par","para","parova")+' u komentaru'+part):'pregledaj parove')+'</b></div>';}

  // ticket-modal "ocena" rows, right under "customer" — F9: server's `rating`
  // block ({value,avg,n,items:[{role,rater,rating,comment,at}]}, see the
  // /api/tickets contract) lists every rater who scored the ticket (a closed
  // ticket can be rated by the customer AND, independently, a tester or an
  // admin); nothing when the ticket carries no rating yet (value:null — not
  // rated, or a pre-F9 helpdesk with only ONE rater, folded into one item).
  function tkRatingRow(it){it=it||{};if(it.rating==null)return "";
    var role=String(it.role||"?"),cls=tixRole(role==="customer"?"customer":"other");
    var txt=it.rating+"/5"+(it.rater?(" — "+it.rater):"")+(it.comment?(" ("+it.comment+")"):"");
    return '<div class="kv" title="'+esc(fmtAt(it.at||""))+'"><span>ocena <span class="tix-role r-'+
      cls+'">'+esc(role)+'</span></span><b>'+esc(txt)+'</b></div>';}
  function tkRatingKv(t){var rt=t&&t.rating;if(!rt||rt.value==null)return "";
    var items=Array.isArray(rt.items)?rt.items:[];
    var avg=(rt.n>1&&rt.avg!=null)
      ?('<div class="kv" title="prosek '+rt.n+' ocena"><span>ocena (prosek)</span><b>'+esc(rt.avg+"/5")+'</b></div>')
      :"";
    return avg+items.map(tkRatingRow).join("");}

  // ticket-modal "profil kupca" kv row, right under "customer" — server's small
  // read-only view of profiles.json (creator_profile.has/sample_count/habits/
  // ai_summary_at, see the /api/tickets contract); nothing when the creator has
  // no learned profile yet (has:false — a brand new or never-analysed customer).
  function tkCreatorProfileKv(t){var cp=t&&t.creator_profile;if(!cp||!cp.has)return "";
    var habits=(Array.isArray(cp.habits)?cp.habits:[]).slice(0,3);
    var txt=(cp.sample_count||0)+" tiketa"+(habits.length?(" · navike: "+habits.join(" · ")):"");
    return '<div class="kv" title="'+esc(fmtAt(cp.ai_summary_at||""))+'"><span>profil kupca</span><b>'+esc(txt)+'</b></div>';}

  /* ---------- Gemini budget pill (tk-head, next to #tk-sub) ---------- */
  // GET /api/gemini/usage -> {used,cap,day,over,per_key,keys,per_model:{model:{used,cap,over}},reset_at,min_interval,forecast:{pending_reads,calls_per_ticket,calls_needed,fits}}.
  function tkGemModelName(m){return String(m||"").replace(/^gemini-/,"").replace(/-latest$/,"");}
  function refreshGemBudget(){var pill=$("tk-gembudget");if(!pill)return;
    fetch("/api/gemini/usage").then(function(r){return r.json();}).then(function(d){d=d||{};
      var per=d.per_model||{},models=Object.keys(per);
      if(!models.length){pill.textContent="Gemini · —";pill.title="";pill.className="tk-sl-gem";return;}
      var over=false;
      var parts=models.map(function(m){var v=per[m]||{};if(v.over)over=true;
        return tkGemModelName(m)+" "+(v.used||0)+"/"+(v.cap||0)+(v.over?" ⚠":"");});
      var resetTxt="";
      if(d.reset_at){var rd=new Date(d.reset_at);if(!isNaN(rd.getTime()))resetTxt=" · reset "+("0"+rd.getHours()).slice(-2)+":"+("0"+rd.getMinutes()).slice(-2);}
      var fcTxt=(d.forecast&&d.forecast.fits===false)?(" · ne staje: "+(d.forecast.calls_needed||d.forecast.pending_reads||0)+" tiketa"):"";
      pill.textContent="Gemini danas · "+parts.join(" · ")+resetTxt+fcTxt;
      pill.className="tk-sl-gem"+(over?" bad":"");
      var tp=[];
      if(d.per_key)tp.push("po ključu: "+Object.keys(d.per_key).map(function(k){return k+"="+d.per_key[k];}).join(", "));
      if(d.min_interval!=null)tp.push("min razmak "+d.min_interval+"s");
      pill.title=tp.join(" · ");
    }).catch(function(){pill.textContent="Gemini · —";pill.title="";pill.className="tk-sl-gem";});}
  (function(){var sub=$("tk-sub");if(!sub||$("tk-gembudget"))return;
    var pill=document.createElement("span");pill.id="tk-gembudget";pill.className="tk-sl-gem";pill.textContent="Gemini · —";
    sub.parentNode.insertBefore(pill,sub.nextSibling);})();
  setInterval(function(){if(mode==="tickets")refreshGemBudget();},60000);

  /* ---------- Objedinjeni upit — analyze selected -> ONE consolidated prompt per repo ---------- */
  // POST /api/tickets/merge {ids} -> {groups:[{repo,modules,ticket_ids,tickets,prompt,source,merge_error}]}.
  // Display ONLY: nothing is launched or written by the call. Reuses the #tkanmodal shell,
  // tkAnCopy/tkAnLaunch (the operator may still copy the prompt, or open Claude in the repo
  // interactively — never auto mode from here). Rendered from tickets.js like tk-analyze.
  function tkMgLoading(n){$("tkan-body").innerHTML='<div class="tkan-load"><span class="tkan-spin"></span>Analiziram '+n+' '+(n===1?"tiket":"tiketa")+' i sastavljam objedinjeni upit&hellip; (Gemini: jedan poziv po tiketu + jedan po repou, potraja)</div>';}
  function tkMgRender(d){var host=$("tkan-body");var groups=(d&&Array.isArray(d.groups))?d.groups:[];
    var total=groups.reduce(function(a,g){return a+((g.ticket_ids||[]).length);},0);
    if($("tkan-sub"))$("tkan-sub").textContent=total+(total===1?" tiket":" tiketa")+" · "+groups.length+(groups.length===1?" upit":" upita");
    var extra="";
    if(d&&d.unknown&&d.unknown.length)extra+='<div class="tkan-errbox"><b>Nepoznati tiketi</b>'+esc(d.unknown.join(", "))+'</div>';
    if(d&&d.skipped)extra+='<div class="tkan-errbox"><b>Preskočeno</b>'+esc(d.skipped+" preko limita od "+d.cap+" po pozivu — selektuj manje.")+'</div>';
    if(!groups.length){host.innerHTML=extra+'<div class="tkan-empty">Nema ničega za objedinjavanje.</div>';return;}
    host.innerHTML=extra+groups.map(function(g,i){g=g||{};var mods=(g.modules||[]),col=modColor(mods[0]||"");
      var head='<div class="tkan-h"><span class="tkan-mod" style="color:'+col+';border-color:'+col+'">'+esc(mods.join(", ")||"—")+'</span>'+
        '<span class="tkan-dot">&middot;</span><span class="tkan-t">'+esc(g.repo||"repo nije mapiran")+'</span>'+
        '<span class="tkan-dot">&middot;</span><span class="tkan-cr">'+(g.ticket_ids||[]).length+' tiketa</span>'+
        (g.source==="fallback"?'<span class="tkan-dot">&middot;</span><span class="tkan-cr" title="'+esc(g.merge_error||"")+'">bez AI spajanja (fallback)</span>':"")+'</div>';
      var list='<div class="lbl">Tiketi</div><div class="tkan-need">'+(g.tickets||[]).map(function(t){
        return '<span class="tkan-id" style="color:'+modColor(t.module||"")+'">#'+esc(t.ticket_id)+'</span> '+esc(t.title||"—")+(t.error?' <span style="color:var(--bad)">('+esc(t.error)+')</span>':(t.real_need?' — '+esc(t.real_need):""));}).join("<br>")+'</div>';
      var sum=g.summary?'<div class="lbl">O čemu je paket</div><div class="tkan-style">'+esc(g.summary)+(g.order_note?"<br>"+esc(g.order_note):"")+'</div>':"";
      return '<div class="tkan-card'+(g.source==="fallback"?" err":"")+'">'+head+list+sum+
        '<div class="lbl">Objedinjeni upit</div>'+
        '<pre class="tkan-prompt tkmg-prompt" data-i="'+i+'">'+esc(g.prompt||"")+'</pre>'+
        '<div class="tkan-actions"><button class="btn tkan-copy tkmg-copy" data-i="'+i+'">&#128203; Kopiraj</button>'+
        '<button class="btn tkan-claude tkmg-claude" data-i="'+i+'"'+(g.repo?"":" disabled title=\"repo nije mapiran\"")+'>&#8599; Otvori u Claude (u repou)</button>'+
        '<span class="tkan-note" data-i="'+i+'"></span></div></div>';}).join("");
    function preOf(i){return host.querySelector('.tkmg-prompt[data-i="'+i+'"]');}
    function noteOf(i){return host.querySelector('.tkan-note[data-i="'+i+'"]');}
    Array.prototype.forEach.call(host.querySelectorAll(".tkmg-copy"),function(b){b.onclick=function(){var i=b.getAttribute("data-i"),pre=preOf(i);if(pre)tkAnCopy(pre.textContent||"",noteOf(i));};});
    Array.prototype.forEach.call(host.querySelectorAll(".tkmg-claude"),function(b){b.onclick=function(){var i=b.getAttribute("data-i"),pre=preOf(i),g=groups[+i]||{};
      // every ticket the group covers — g.tickets[].module is the file-stem key (from ticket_merger, same field ticket_reader sets)
      var tk=(g.tickets||[]).map(function(gt){gt=gt||{};return {module:gt.module,ticket:String(gt.ticket_id)};}).filter(function(x){return x.module&&x.ticket&&x.ticket!=="undefined";});
      if(pre)tkAnLaunch(pre.textContent||"",g.repo,b,noteOf(i),tk);};});}
  function tkMerge(){
    if(tkAnBusy)return;                            // shares the in-flight guard with analyze (same quota)
    var ids=tkSelIds();if(!ids.length)return;
    tkAnBusy=true;var btn=$("tk-merge");
    if(btn){if(btn.dataset.label==null)btn.dataset.label=btn.innerHTML;btn.disabled=true;btn.innerHTML="&#129513; Objedinjujem…";}
    tkAnOpen();tkAnTitle("&#129513; Objedinjeni upit");if($("tkan-sub"))$("tkan-sub").textContent="";
    tkMgLoading(ids.length);
    aiPost("/api/tickets/merge",{ids:ids}).then(function(d){
      tkAnBusy=false;if(btn){btn.disabled=false;btn.innerHTML=btn.dataset.label;}
      refreshGemBudget();
      if(d&&d.error&&!(d.groups&&d.groups.length)){$("tkan-body").innerHTML='<div class="tkan-errbox"><b>Greška</b>'+esc(d.error)+'</div>';return;}
      tkMgRender(d);
    }).catch(function(e){tkAnBusy=false;if(btn){btn.disabled=false;btn.innerHTML=btn.dataset.label;}
      refreshGemBudget();
      $("tkan-body").innerHTML='<div class="tkan-errbox"><b>Greška</b>'+esc(aiErrText(e))+'</div>';});}
  (function(){var bar=$("tk-bulk");if(!bar||$("tk-merge"))return;
    var b=document.createElement("button");b.className="btn tk-merge";b.id="tk-merge";
    b.title="Gemini pročita izabrane tikete i sastavi JEDAN objedinjeni upit po repou — samo prikaz, ništa se ne pokreće";
    b.innerHTML="&#129513; Objedini u 1 upit";b.onclick=tkMerge;
    var anchor=$("tk-analyze")||$("tk-bulkstate");
    if(anchor&&anchor.parentNode===bar)bar.insertBefore(b,anchor.nextSibling);else bar.appendChild(b);})();

  /* ---------- Reši tiket — batch auto-solve selected tickets (bulk-bar action) ---------- */
  // POST /api/tickets/solve {ids, note?} -> ONE auto Claude per REPO that resolves + commits +
  // PUSHES with no review (the user authorised this for trivial tickets). A CONFIRM step precedes
  // the POST — an accident guard, not a diff review. Injected like tk-analyze; #tksomodal reuses the
  // #tixmodal shell (grouped in app.css). Contract: {ok, launched:[{repo,modules,ticket_ids}], skipped:[{...,reason}]}.
  var tkSoBusy=false;   // in-flight guard
  function tkSoModal(){var m=$("tksomodal");if(m)return m;
    m=document.createElement("div");m.id="tksomodal";
    m.innerHTML='<div class="bd" id="tkso-bd"></div><div class="dlg">'+
      '<div class="dh"><h3>&#9889; Reši selektovane tikete</h3><span class="tkso-sub" id="tkso-sub"></span><span class="x" id="tkso-x">&times;</span></div>'+
      '<div class="db" id="tkso-body"></div></div>';
    document.body.appendChild(m);
    $("tkso-x").onclick=tkSoClose;$("tkso-bd").onclick=tkSoClose;
    return m;}
  function tkSoOpen(){tkSoModal().classList.add("open");}
  function tkSoClose(){var m=$("tksomodal");if(m)m.classList.remove("open");}
  // confirm step: summarise the batch + an optional operator conclusion, THEN dispatch on click
  function tkSoConfirm(ids){
    tkSoOpen();
    if($("tkso-sub"))$("tkso-sub").textContent=ids.length+(ids.length===1?" tiket":" tiketa");
    $("tkso-body").innerHTML='<div class="tkso-warn"><div class="tkso-warn-h">&#9888; Automatski rad bez pregleda</div>'+
      '<div class="tkso-warn-b">Pokreće se po jedan Claude terminal za <b>svaki repozitorijum</b> izabranih tiketa, u <b>auto modu</b>: '+
      'reši, <b>commit</b> i <b>push</b> &mdash; bez pregleda izmena. Namenjeno samo trivijalnim tiketima.</div></div>'+
      '<div class="tkso-field"><label for="tkso-note">Moj zaključak (opciono &mdash; jači od Gemini analize)</label>'+
      '<textarea id="tkso-note" rows="3" placeholder="npr. samo promeni tekst na stranici X u ..."></textarea></div>'+
      '<div class="tkso-actions"><button class="btn" id="tkso-cancel">Otkaži</button>'+
      '<button class="btn tkso-go" id="tkso-go">&#9889; Reši, commit &amp; push ('+ids.length+')</button>'+
      '<span class="tkso-note" id="tkso-flash"></span></div>';
    $("tkso-cancel").onclick=tkSoClose;
    $("tkso-go").onclick=function(){var el=$("tkso-note");tkSoDispatch(ids,(el&&el.value)||"");};
  }
  function tkSoLoading(n){$("tkso-body").innerHTML='<div class="tkso-load"><span class="tkso-spin"></span>Pokrećem terminale za '+n+' '+(n===1?"tiket":"tiketa")+'&hellip;</div>';}
  function tkSoDispatch(ids,note){
    if(tkSoBusy)return;tkSoBusy=true;                 // guard: one dispatch at a time
    var go=$("tkso-go");if(go)go.disabled=true;
    tkSoLoading(ids.length);
    aiPost("/api/tickets/solve",{ids:ids,note:note}).then(function(d){
      tkSoBusy=false;
      if(d&&d.error&&!(d.launched||d.skipped)){$("tkso-body").innerHTML='<div class="tkso-errbox"><b>Greška</b>'+esc(d.error)+'</div>';return;}
      tkSoRender(d);
    }).catch(function(e){tkSoBusy=false;$("tkso-body").innerHTML='<div class="tkso-errbox"><b>Greška</b>'+esc(aiErrText(e))+'</div>';});
  }
  function tkSoRender(d){d=d||{};var launched=Array.isArray(d.launched)?d.launched:[],skipped=Array.isArray(d.skipped)?d.skipped:[];var html="";
    if(launched.length)html+='<div class="lbl">Pokrenuto ('+launched.length+')</div>'+launched.map(function(r){r=r||{};
      var mods=(r.modules||[]).join(", "),n=(r.ticket_ids||[]).length;
      return '<div class="tkso-row ok"><span class="tkso-ic">&#9889;</span><div><div class="tkso-repo">'+esc(r.repo||"—")+'</div>'+
        '<div class="tkso-meta">'+esc(mods)+' &middot; '+n+' '+(n===1?"tiket":"tiketa")+' &middot; auto commit+push</div></div></div>';}).join("");
    if(skipped.length)html+='<div class="lbl">Preskočeno ('+skipped.length+')</div>'+skipped.map(function(s){s=s||{};
      var who=s.repo||s.module||(s.ticket_id!=null?("#"+s.ticket_id):"—");
      return '<div class="tkso-row skip"><span class="tkso-ic">&#9888;</span><div><div class="tkso-repo">'+esc(who)+'</div>'+
        '<div class="tkso-meta">'+esc(s.reason||"—")+'</div></div></div>';}).join("");
    if(!launched.length&&!skipped.length)html='<div class="tkso-empty">Ništa nije pokrenuto.</div>';
    $("tkso-body").innerHTML=html;
    if($("tkso-sub"))$("tkso-sub").textContent=launched.length+" pokrenuto";}
  function tkSolve(){
    if(tkSoBusy)return;                               // ignore while a dispatch is pending
    var ids=tkSelIds();if(!ids.length)return;         // ignore empty selection
    tkSoConfirm(ids);
  }
  // inject the button into the bulk bar, next to the analyze action (index.html is off-limits)
  (function(){var bar=$("tk-bulk");if(!bar||$("tk-solve"))return;
    var b=document.createElement("button");b.className="btn tk-solve";b.id="tk-solve";
    b.title="Pokreni Claude po repozitorijumu da automatski reši, commit-uje i push-uje izabrane tikete";
    b.innerHTML="&#9889; Reši selektovane";b.onclick=tkSolve;
    var anchor=$("tk-analyze")||$("tk-bulkstate");
    if(anchor&&anchor.parentNode===bar)bar.insertBefore(b,anchor.nextSibling);else bar.appendChild(b);})();
  addEventListener("keydown",function(e){if(e.key==="Escape"){var m=$("tksomodal");if(m&&m.classList.contains("open"))tkSoClose();}});

  /* ---------- "Vraćeni / Dopune" lane (PLAN_REOPENED.md Phase 5) ----------
     A ticket the operator closed can be REOPENED on the helpdesk (status → Active +
     a "dopuna" comment). server.py's tickets_data() now carries three additive,
     .get-tolerant keys per ticket: `rounds` (the durable ledger), `reopened` (the
     current-reopen marker) and `reopen` (the AI working block —
     predlog/suggested_branch/confidence/legitimate/questions/suggested_reply). Such
     a ticket renders ONLY here, never as a table row (renderTickets excludes it via
     the ONE membership helper tixIsReopenOpen). ALL helpdesk-origin text (titles,
     comment bodies, resolutions, predlog, suggested_reply, questions) is UNTRUSTED
     and rendered through esc()/safeUrl(), exactly like tixCommentsHtml — no innerHTML
     with raw ticket text. The lane container is injected from JS (index.html is
     off-limits); all visual design lives in app.css (frontend-junior owns the
     .reopen-* classes). .reopen-round1 (a generic max-height collapsible) is reused
     for BOTH the round-1 history reveal AND the Gemini predlog reveal. */
  var reopenUI={};          // key "dir|id" -> transient card UI {r1,predlogOpen,editorOpen,editText,note,sending}
  var reopenFocusKey=null;  // set when an editor opens; renderReopenLane re-focuses that card's textarea once
  function reopenKey(dir,id){return dir+"|"+id;}
  function reopenState(key){return reopenUI[key]||(reopenUI[key]={});}
  // THE membership helper — mirrors rounds.is_reopened_open: a lane card iff the
  // `reopened` marker is set (its `at` present; an empty {} default is NOT reopened)
  // AND the current round has no closed_at. One helper so the table exclusion and the
  // lane can never disagree.
  function tixCurrentRound(t){var r=t&&t.rounds;return (Array.isArray(r)&&r.length)?r[r.length-1]:null;}
  function tixIsReopenOpen(t){var rm=t&&t.reopened;if(!(rm&&rm.at))return false;var cur=tixCurrentRound(t);return !!(cur&&!cur.closed_at);}
  function tixReopenList(){return allTix().filter(function(x){return tixIsReopenOpen(x.t);});}
  function tixNormBranch(v){var s=String(v||"").toUpperCase();return (s==="A"||s==="B"||s==="C")?s:"";}
  // AUTHORITATIVE branch = rounds[cur].branch (written ONLY by rounds.record_verdict);
  // reopen.branch mirrors it; reopen.suggested_branch is Gemini's ADVISORY guess.
  function tixReopenBranch(t){var cur=tixCurrentRound(t)||{};return tixNormBranch(cur.branch)||tixNormBranch((t.reopen||{}).branch);}
  function tixReopenSuggested(t){return tixNormBranch((t.reopen||{}).suggested_branch);}
  // the dopuna comments = the SERVER-provided window. `row.dopuna_ids` (a list of
  // comment ids, [] when not an open reopen round) is now the ONE source of truth —
  // computed server-side via reopen_ai._dopuna_comments, the SAME window the prompts
  // use, with the ledger's precise tz-aware parser AND its trigger-id fallback baked
  // in. The client no longer re-derives the window from timestamps: the old tsOf pass
  // (prev-round close anchor + trigger-id fallback) drifted from the backend's tz
  // handling — THIS is that drift fix. We only RESOLVE the ids against this ticket's
  // comments, preserving the server's order. A null id (a comment that lacks one) is
  // SKIPPED; an id with no matching comment is skipped; empty dopuna_ids -> [] -> no
  // highlight (tixDopunaHtml renders its designed empty note), never a fall-back to a
  // client recomputation.
  function tixDopunaComments(t){
    var ids=Array.isArray(t.dopuna_ids)?t.dopuna_ids:[];
    if(!ids.length)return [];
    var byId={};(Array.isArray(t.comments)?t.comments:[]).forEach(function(c){if(c&&typeof c==="object"&&c.id!=null)byId[c.id]=c;});
    var out=[];ids.forEach(function(cid){if(cid==null)return;var c=byId[cid];if(c)out.push(c);});
    return out;}
  // dopuna comment(s) — the card's focus. Every field is untrusted helpdesk text.
  function tixDopunaHtml(t){var list=tixDopunaComments(t);
    if(!list.length)return '<div class="reopen-dopuna">'+esc("Nije prepoznat dopunski komentar — status je vraćen na Aktivan bez teksta.")+'</div>';
    return list.map(function(c){var role=tixRole(c.author_role),atts=tixAttsHtml(c.attachments);
      return '<div class="reopen-dopuna"><b>'+esc(c.author||"—")+'</b> '+esc(role)+' · '+esc(fmtAt(c.at))+
        '<div>'+esc(c.body||"")+'</div>'+(atts?'<div class="tix-cmt-att">'+atts+'</div>':'')+'</div>';}).join("");}
  // round-1 history — a collapsed .reopen-round1 revealed by its own toggle. The
  // toggle+block sit in a plain wrapper div so the app.css caret combinators (~ / :has)
  // scope to THIS pair and never rotate when the sibling predlog block opens.
  function tixRound1Html(t,open){var r=(Array.isArray(t.rounds)&&t.rounds.length)?t.rounds[0]:null;
    var closed=r?fmtDMY(r.closed_at):"",res=r?String(r.resolution||"").trim():"",by=r?String(r.closed_by||"").trim():"";
    var inner='<b>Zatvoren:</b> '+(closed?esc(closed):"—")+(by?' · <b>ko:</b> '+esc(by):"")+
      '<div><b>Rešenje poslato kupcu (krug 1):</b></div><div>'+(res?esc(res):"(nije zabeleženo)")+'</div>';
    return '<div><button class="reopen-round1__toggle" data-act="r1">Krug 1 — prvo rešenje</button>'+
      '<div class="reopen-round1'+(open?' reopen-round1--open':'')+'" data-role="round1">'+inner+'</div></div>';}
  // the Gemini predlog reveal (reuses .reopen-round1; toggled by the Predlog action button)
  function tixPredlogHtml(t,open){var rp=t.reopen||{},pred=String(rp.predlog||"").trim(),
      sug=tixReopenSuggested(t),conf=String(rp.confidence||"").trim(),reply=String(rp.suggested_reply||"").trim();
    var inner;
    if(pred){inner=esc(pred)+
      (sug?'<div><b>Predložena grana (savet):</b> '+esc(sug)+(conf?' · pouzdanost '+esc(conf):'')+'</div>':'')+
      (reply?'<div><b>Predlog odgovora:</b></div><div>'+esc(reply)+'</div>':'');}
    else{inner=esc("Nema predloga — generiše se u punom Rescan-u (Gemini).");}
    return '<div class="reopen-round1'+(open?' reopen-round1--open':'')+'" data-role="predlog">'+inner+'</div>';}
  // one card. Buttons are branch-aware: the action matching the DECIDED branch (A/B/C)
  // is the primary; before a verdict exists, Klasifikuj is primary.
  function tixReopenCardHtml(x){var t=x.t,dir=x.dir,id=String(t.id),key=reopenKey(dir,id),col=modColor(x.mod),st=reopenState(key);
    var decided=tixReopenBranch(t),sug=tixReopenSuggested(t),badge,badgeCls;
    if(decided){badge=decided;badgeCls="reopen-branch--"+decided.toLowerCase();}
    else if(sug){badge=sug+"?";badgeCls="reopen-branch--"+sug.toLowerCase();}   // advisory: colour by suggested branch, "?" marks it unconfirmed
    else{badge="—";badgeCls="reopen-branch--none";}
    var rp=t.reopen||{},conf=String(rp.confidence||"").trim(),questions=Array.isArray(rp.questions)?rp.questions:[];
    var showAsk=(conf.toLowerCase()==="low")||questions.length>0;
    var prim=decided||"classify";
    // Klasifikuj carries a title now: it no longer starts anything, it SHOWS the
    // prompt — and if a branch is already picked, the prompt obeys that branch.
    var BTITLE={classify:(decided
      ? ("Sastavi upit za granu "+decided+" — prikaz, ništa se ne pokreće; upit traži od Claude-a da granu potvrdi, proveri prijavu i pošalje odgovor tek uz tvoju potvrdu")
      : "Sastavi upit koji klasifikuje (A/B/C), proveri prijavu i pošalje odgovor tek uz tvoju potvrdu — samo prikaz, ništa se ne pokreće")};
    function b(act,label){return '<button class="reopen-btn'+(prim===act?" reopen-btn--primary":"")+'" data-act="'+act+'"'+
      (BTITLE[act]?(' title="'+esc(BTITLE[act])+'"'):"")+'>'+label+'</button>';}
    var override='<select class="reopen-btn" data-act="override" title="Ručno postavi/izmeni granu (A/B/C)">'+
      '<option value=""'+(decided?"":" selected")+'>grana ▾</option>'+
      ["A","B","C"].map(function(v){return '<option value="'+v+'"'+(decided===v?" selected":"")+'>'+v+'</option>';}).join("")+'</select>';
    var editor="";
    if(st.editorOpen){editor='<div class="reopen-actions" data-role="editorbox">'+
      '<textarea class="tk-mm-noteta" data-role="editta" rows="4" placeholder="Odgovor kupcu — proveri PRE slanja (ide na helpdesk kao komentar, NE zatvara tiket)">'+esc(st.editText||"")+'</textarea>'+
      '<button class="reopen-btn reopen-btn--primary" data-act="send">Pošalji (bez zatvaranja)</button>'+
      '<button class="reopen-btn" data-act="canceledit">Otkaži</button></div>';}
    var head='<div class="reopen-card__head"><span class="reopen-card__title" data-act="open">'+
      '<span style="color:'+col+'">#'+esc(id)+'</span> · '+esc(t.title||"—")+'</span>'+
      '<span class="reopen-branch '+badgeCls+'">'+esc(badge)+'</span>'+
      (conf?'<span class="reopen-confidence">pouzdanost <b>'+esc(conf)+'</b></span>':'')+'</div>';
    var actions='<div class="reopen-actions">'+
      '<button class="reopen-btn" data-act="predlog">Predlog</button>'+
      b("classify","Klasifikuj")+b("A","A: Odgovori bez zatvaranja")+b("B","B: Reši")+b("C","C: Ne mogu da reprodukujem")+
      (showAsk?'<button class="reopen-btn" data-act="ask">Pitaj</button>':'')+
      override+'<span data-role="note">'+esc(st.note||"")+'</span></div>';
    return '<div class="reopen-card" data-dir="'+esc(dir)+'" data-id="'+esc(id)+'" data-key="'+esc(key)+'">'+
      head+tixRound1Html(t,st.r1)+tixDopunaHtml(t)+tixPredlogHtml(t,st.predlogOpen)+editor+actions+'</div>';}
  function renderReopenLane(){var lane=$("reopen-lane");if(!lane)return;
    var list=tixReopenList();
    if(!list.length){lane.style.display="none";lane.innerHTML="";return;}
    lane.style.display="";   // reveal (app.css sets #reopen-lane{display:flex})
    list.sort(function(a,b){return cmpNum(tsOf((a.t.reopened||{}).at),tsOf((b.t.reopened||{}).at),-1);});   // newest reopen first
    lane.innerHTML='<h3>Vraćeni / Dopune <span>('+list.length+')</span></h3>'+list.map(tixReopenCardHtml).join("");
    Array.prototype.forEach.call(lane.querySelectorAll(".reopen-card"),wireReopenCard);
    if(reopenFocusKey){var fc=null;Array.prototype.forEach.call(lane.querySelectorAll(".reopen-card"),function(c){if(c.getAttribute("data-key")===reopenFocusKey)fc=c;});
      if(fc){var ta=fc.querySelector('[data-role="editta"]');if(ta){try{ta.focus();var v=ta.value;ta.value="";ta.value=v;}catch(e){}}}
      reopenFocusKey=null;}}
  function wireReopenCard(card){var dir=card.getAttribute("data-dir"),id=card.getAttribute("data-id"),key=card.getAttribute("data-key"),st=reopenState(key);
    Array.prototype.forEach.call(card.querySelectorAll("[data-act]"),function(el){var act=el.getAttribute("data-act");
      if(el.tagName==="SELECT"){el.onchange=function(){var v=el.value;if(v==="A"||v==="B"||v==="C")tixReopenVerdict(dir,id,v,el);};return;}
      el.onclick=function(e){e.stopPropagation();tixReopenAct(act,dir,id,key,el,card);};});
    var ta=card.querySelector('[data-role="editta"]');if(ta)ta.oninput=function(){st.editText=ta.value;};}   // hold typed value in state (collapsible-safe; read from state at submit, never the DOM)
  // update just the note span in one card, no full re-render (keeps focus / a disabled button)
  function setReopenNote(key){var st=reopenUI[key];if(!st)return;var lane=$("reopen-lane");if(!lane)return;
    Array.prototype.forEach.call(lane.querySelectorAll(".reopen-card"),function(c){if(c.getAttribute("data-key")!==key)return;
      var n=c.querySelector('[data-role="note"]');if(n)n.textContent=st.note||"";});}
  function tixReopenAct(act,dir,id,key,el,card){var st=reopenState(key);
    if(act==="open"||act==="B"){openTix(dir,id);return;}   // B = the existing done+resolution path in the detail modal; close_out re-closes it round-aware
    if(act==="r1"){var rb=card.querySelector('[data-role="round1"]');if(rb){st.r1=!st.r1;rb.classList.toggle("reopen-round1--open",st.r1);}return;}
    if(act==="predlog"){var pb=card.querySelector('[data-role="predlog"]');if(pb){st.predlogOpen=!st.predlogOpen;pb.classList.toggle("reopen-round1--open",st.predlogOpen);}return;}
    if(act==="classify"){tixReopenClassify(dir,id,key,el);return;}
    if(act==="A"){var rpa=((tixFind(dir,id)||{t:{}}).t.reopen)||{};tixOpenEditor(dir,id,key,String(rpa.suggested_reply||"").trim()||"Poštovani,\n\n");return;}
    if(act==="C"){tixOpenEditor(dir,id,key,tixTemplateCantReproduce(dir,id));return;}
    if(act==="ask"){tixOpenEditor(dir,id,key,tixTemplateQuestions(dir,id));return;}
    if(act==="send"){tixReopenSend(dir,id,key);return;}
    if(act==="canceledit"){st.editorOpen=false;st.note="";renderReopenLane();return;}}
  function tixOpenEditor(dir,id,key,seed){var st=reopenState(key);st.editorOpen=true;st.editText=seed||"";st.note="";reopenFocusKey=key;renderReopenLane();}
  function tixTemplateCantReproduce(dir,id){var rp=((tixFind(dir,id)||{t:{}}).t.reopen)||{},qs=Array.isArray(rp.questions)?rp.questions:[];
    var s="Poštovani,\n\nNažalost, trenutno ne možemo da reprodukujemo opisani problem.";
    if(qs.length){s+=" Da bismo mogli da pomognemo, molimo vas da nam odgovorite na sledeće:\n";qs.forEach(function(q){s+="\n- "+String(q);});}
    else{s+=" Molimo vas da nam pošaljete više detalja: tačne korake, stranicu/ekran i vreme kada se problem javlja.";}
    return s+"\n\nHvala.";}
  function tixTemplateQuestions(dir,id){var rp=((tixFind(dir,id)||{t:{}}).t.reopen)||{},qs=Array.isArray(rp.questions)?rp.questions:[];
    var s="Poštovani,\n\nDa bismo nastavili, potrebno nam je nekoliko podataka:\n";
    if(qs.length)qs.forEach(function(q){s+="\n- "+String(q);});else s+="\n- (dodajte pitanja)";
    return s+"\n\nHvala.";}
  // Klasifikuj: build the adjudication prompt server-side and SHOW IT. It used to
  // pipe straight into /api/claude/launch, so one click opened a terminal and the
  // operator never saw the prompt at all ("treba da se predlog upita da dobijem a ne
  // da se otvori terminal", 2026-08-28). Same two-step every other prompt in this
  // panel takes: render it in the shared #tkanmodal with Kopiraj, and leave the
  // launch as a SECOND, deliberate click. The prompt carries the whole job now —
  // triage, verify the report, then comment-without-closing or work-and-close —
  // and it asks the operator before anything reaches the customer.
  function tixReopenClassify(dir,id,key,btn){var st=reopenState(key);if(btn)btn.disabled=true;
    st.note="Sastavljam upit…";setReopenNote(key);
    aiPost("/api/tickets/reopen/classify",{dir:dir,id:id}).then(function(d){
      if(btn)btn.disabled=false;
      if(d&&d.error){st.note="Upit nije sastavljen: "+d.error;setReopenNote(key);return;}
      if(!d||!d.prompt){st.note="Nema upita za ovaj tiket.";setReopenNote(key);return;}
      st.note="Upit je otvoren u prozoru — kopiraj ga ili ga pokreni.";setReopenNote(key);
      tixReopenPromptModal(dir,id,d);
    }).catch(function(e){if(btn)btn.disabled=false;st.note="Upit nije sastavljen: "+aiErrText(e);setReopenNote(key);});}
  // The prompt preview itself. One card, same markup as the merge modal's so the
  // copy/launch behaviour is literally the same helpers (tkAnCopy / tkAnLaunch) and
  // cannot drift; `tickets` is passed so a launch is MEASURED against this ticket.
  function tixReopenPromptModal(dir,id,d){
    var f=tixFind(dir,id),t=(f&&f.t)||{},mod=(f&&f.mod)||"",col=modColor(mod),
        branch=tixReopenBranch(t),
        // the folder NAME, like the merge modal shows — the absolute cwd is what the
        // launch uses, not what the operator needs to read off a header
        repo=String(d.cwd||"").split(/[\\/]/).filter(Boolean).pop()||"";
    tkAnOpen();tkAnTitle("&#9878;&#65039; Upit za vraćen tiket");
    if($("tkan-sub"))$("tkan-sub").textContent="#"+id+(branch?(" · grana "+branch):" · grana nije odlučena");
    $("tkan-body").innerHTML='<div class="tkan-card">'+
      '<div class="tkan-h"><span class="tkan-mod" style="color:'+col+';border-color:'+col+'">'+esc(mod||"—")+'</span>'+
        '<span class="tkan-dot">&middot;</span><span class="tkan-id" style="color:'+col+'">#'+esc(id)+'</span>'+
        '<span class="tkan-dot">&middot;</span><span class="tkan-t">'+esc(t.title||"—")+'</span>'+
        (repo?('<span class="tkan-dot">&middot;</span><span class="tkan-cr">'+esc(repo)+'</span>'):"")+'</div>'+
      // Said before the prompt, because it is the thing that changes what the operator
      // expects: this session may post to the customer, after asking.
      '<div class="tkan-style">'+esc(branch
        ? ("Grana "+branch+" je već odlučena — upit je usmeren na nju i traži od Claude-a da je "
           + "potvrdi, ne da klasifikuje ponovo.")
        : "Grana još nije odlučena — upit prvo klasifikuje (A / B / C), pa nastavlja.")+
        " "+esc("Claude proverava prijavu pa šalje odgovor kupcu — ali tek uz tvoju potvrdu: prvo ti pokaže tačan tekst koji ide na helpdesk.")+'</div>'+
      '<div class="lbl">Upit</div>'+
      '<pre class="tkan-prompt" id="tkrp-prompt"></pre>'+
      '<div class="tkan-actions"><button class="btn tkan-copy" id="tkrp-copy">&#128203; Kopiraj</button>'+
      '<button class="btn tkan-claude" id="tkrp-claude"'+(d.cwd?"":' disabled title="repo nije mapiran"')+'>&#8599; Otvori u Claude (u repou)</button>'+
      '<span class="tkan-note" id="tkrp-note"></span></div></div>';
    // textContent, never innerHTML: the prompt embeds the customer's own dopuna text
    var pre=$("tkrp-prompt");if(pre)pre.textContent=String(d.prompt||"");
    var note=$("tkrp-note");
    if($("tkrp-copy"))$("tkrp-copy").onclick=function(){tkAnCopy(pre?(pre.textContent||""):"",note);};
    if($("tkrp-claude"))$("tkrp-claude").onclick=function(){
      tkAnLaunch(pre?(pre.textContent||""):"",d.cwd,this,note,[{module:mod,ticket:String(id)}]);};}
  // A / C / Pitaj: seed an EDITABLE draft (reuse the outbox mechanism: postTriage
  // outbox_add), then post THAT draft comment-only via /reopen/reply. Never closes.
  function tixReopenSend(dir,id,key){var st=reopenUI[key];if(!st||!st.editorOpen||st.sending)return;
    var text=String(st.editText||"").trim();
    if(!text){st.note="Prazan odgovor — unesite tekst.";setReopenNote(key);return;}
    st.sending=true;st.note="Čuvam nacrt…";setReopenNote(key);
    var f=tixFind(dir,id),before={};((f&&f.t.outbox)||[]).forEach(function(d){if(d&&d.id)before[d.id]=1;});
    postTriage(dir,id,{outbox_add:text}).then(function(ok){
      if(!ok){st.sending=false;st.note="Nacrt nije sačuvan — osveži pa probaj ponovo.";setReopenNote(key);return;}
      var f2=tixFind(dir,id),box=(f2&&f2.t.outbox)||[],draft=null,i,j;
      for(i=box.length-1;i>=0;i--){if(box[i]&&box[i].id&&!before[box[i].id]){draft=box[i];break;}}   // the draft we just added = the id not present before
      if(!draft)for(j=box.length-1;j>=0;j--){if(box[j]&&box[j].id&&!box[j].posted){draft=box[j];break;}}
      if(!draft){st.sending=false;st.note="Ne mogu da pronađem nacrt za slanje.";setReopenNote(key);return;}
      st.note="Šaljem na helpdesk…";setReopenNote(key);
      aiPost("/api/tickets/reopen/reply",{dir:dir,id:id,draft_id:draft.id}).then(function(d){st.sending=false;
        var rep=(d&&d.report)||{},did=draft.id;
        if(d&&d.error&&!d.report){st.note="Slanje nije uspelo: "+d.error;setReopenNote(key);return;}
        if((rep.posted||[]).indexOf(did)>=0){st.editorOpen=false;st.editText="";st.note="Poslato kupcu ✓ (tiket nije zatvoren).";}
        else if((rep.ambiguous||[]).indexOf(did)>=0){st.note="⚠ Neizvesno — možda je već poslato; proveri na helpdesku.";}
        else{var er="";(rep.comments||[]).forEach(function(c){if(c&&c.draft===did&&c.error)er=String(c.error);});st.note="✗ Nije poslato"+(er?(": "+er):"")+" (nacrt ostaje u outbox-u).";}
        fetchTickets();   // re-pull -> renderTickets -> renderReopenLane (shows the note; drops the editor when posted)
      }).catch(function(e){st.sending=false;st.note="Slanje nije uspelo: "+aiErrText(e);setReopenNote(key);});
    });}
  // manual branch set/override -> POST /reopen/verdict (rounds.record_verdict is the ONE ledger-branch writer)
  function tixReopenVerdict(dir,id,branch,sel){var key=reopenKey(dir,id),st=reopenState(key);if(sel)sel.disabled=true;st.note="Postavljam granu "+branch+"…";setReopenNote(key);
    aiPost("/api/tickets/reopen/verdict",{dir:dir,id:id,verdict:{branch:branch}}).then(function(d){if(sel)sel.disabled=false;
      if(d&&d.error){st.note="Grana nije postavljena: "+d.error;setReopenNote(key);return;}
      st.note="Grana postavljena: "+branch+" ✓";fetchTickets();
    }).catch(function(e){if(sel)sel.disabled=false;st.note="Grana nije postavljena: "+aiErrText(e);setReopenNote(key);});}
  // the ticket-modal rounds-history block (openTix): round 1 (first close) + each reopen
  // round with its branch/status. Reuses the modal's own .lbl/.kv (scoped to #tixmodal);
  // every field is untrusted helpdesk/ledger text -> esc().
  function tkRoundsBlock(t){var r=t&&t.rounds;if(!(Array.isArray(r)&&r.length))return "";
    var rows=r.map(function(rd,i){rd=rd||{};
      if(i===0)return '<div class="kv"><span>krug 1 (prvo zatvaranje)</span><b>'+(rd.closed_at?esc(fmtDMY(rd.closed_at)):"—")+
        (rd.closed_by?' · '+esc(rd.closed_by):"")+(rd.resolution?' — '+esc(String(rd.resolution)):"")+'</b></div>';
      var br=tixNormBranch(rd.branch)||"—",trig=rd.trigger||{},status=rd.closed_at?('zatvoren '+esc(fmtDMY(rd.closed_at))):'otvoren';
      return '<div class="kv"><span>krug '+(i+1)+' (vraćen)</span><b>vraćen '+(rd.reopened_at?esc(fmtDMY(rd.reopened_at)):"—")+
        ' · grana '+esc(br)+' · '+status+(trig.author?' · dopuna: '+esc(trig.author):"")+
        (rd.resolution?' — '+esc(String(rd.resolution)):"")+'</b></div>';}).join("");
    // Phase 7 — "Analiziraj i predloži (nauči)": a POST-COMPLETION action, so it is
    // offered whenever the ledger carries a reopen round (any round with reopened_at,
    // OPEN or CLOSED) — unlike the lane's Klasifikuj, which is reopen-open only. The
    // button carries no dir/id; openTix wires it (dir/id in scope there) to the SAME
    // two-step Klasifikuj uses: /api/tickets/reopen/learn -> the EXISTING
    // /api/claude/launch door. Consult-only/propose-only is enforced by the launched
    // learning_prompt, not this button. Inline-styled row (no app.css touch), matching
    // the modal's other button rows.
    var learn=r.some(function(rd){return rd&&rd.reopened_at;})
      ?('<div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-top:.45rem">'+
        '<button class="btn" id="tx-reopenlearn" title="Pokreni Claude: analiza ovog vraćanja + PREDLOG izmena skilova/dokumentacije (konsultacija — ne menja ništa bez tvoje potvrde)">&#129504; Analiziraj i predloži (nauči)</button>'+
        '<span id="tx-reopenlearn-note" style="font-size:.72rem;align-self:center"></span></div>')
      :"";
    return '<div class="lbl">Krugovi (vraćanja)</div>'+rows+learn;}
  // inject the lane container ABOVE the table (index.html is off-limits). Starts hidden;
  // renderReopenLane reveals it only when there is at least one reopened-open ticket.
  (function(){if($("reopen-lane"))return;var tw=document.querySelector("#view-tickets .tk-tablewrap");if(!tw||!tw.parentNode)return;
    var lane=document.createElement("div");lane.id="reopen-lane";lane.style.display="none";
    tw.parentNode.insertBefore(lane,tw);})();

