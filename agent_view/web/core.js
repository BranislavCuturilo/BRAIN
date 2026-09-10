  "use strict";
  var ICONS={
    coordination:'<circle cx="8" cy="8" r="2.2"/><path d="M8 5.8V2M8 10.2v3.8M5.9 6.9 3 4M10.1 6.9 13 4"/>',
    reading:'<path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z"/><circle cx="8" cy="8" r="2"/>',
    consultants:'<path d="M2 3.5h12v7H6l-3 2.5v-2.5H2Z"/><path d="M5 6h6M5 8h4"/>',
    review:'<path d="M8 1.8 13 3.5v4C13 11 8 14 8 14S3 11 3 7.5v-4Z"/><path d="M5.8 7.7 7.3 9.3 10.3 6"/>',
    backend:'<ellipse cx="8" cy="3.8" rx="5" ry="1.8"/><path d="M3 3.8v8.4C3 13.2 5.2 14 8 14s5-.8 5-1.8V3.8M3 8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8"/>',
    views:'<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M2 6h12M4.3 4.5h.01M6 4.5h.01"/>',
    front:'<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M2.5 6.5h11M6.5 6.5v7"/>',
    slices:'<path d="M8 2 14 5 8 8 2 5Z"/><path d="M2 8l6 3 6-3M2 11l6 3 6-3"/>',
    operations:'<circle cx="8" cy="8" r="2.3"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4"/>',
    brain:'<path d="M4 12 3 13M4.5 11.5 11 5l2 2-6.5 6.5-2.8.8Z"/><path d="M9.5 3.5 11 2l3 3-1.5 1.5Z"/>'
  };
  var GRP_SLUG={"Coordination":"coordination","Reading & analysis":"reading","Consultants (advise, never edit)":"consultants",
    "Review & quality":"review","Backend":"backend","Views & CRUD":"views","Front end":"front","Whole slices":"slices",
    "Operations":"operations","The brain itself":"brain"};
  var SLUG_COL={coordination:"#a78bfa",reading:"#38bdf8",consultants:"#2dd4bf",review:"#e11d48",backend:"#f59e0b",
    views:"#3b82f6",front:"#ec4899",slices:"#22c55e",operations:"#64748b",brain:"#a855f7"};
  function slugOf(group){return GRP_SLUG[group]||"coordination";}
  function colorFor(group,fallback){return SLUG_COL[slugOf(group)]||fallback||"#5f7a95";}
  function iconSlug(slug,cls){return '<svg class="'+(cls||"a-ico")+'" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'+(ICONS[slug]||ICONS.coordination)+'</svg>';}
  function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
  function hhmmss(ts){return new Date(ts*1000).toTimeString().slice(0,8);}
  function hexA(hex,a){var h=(hex||"#5f7a95").replace("#","");var n=parseInt(h.length===3?h.replace(/(.)/g,"$1$1"):h,16);return "rgba("+(n>>16&255)+","+(n>>8&255)+","+(n&255)+","+a+")";}
  function fmtTok(n){n=n||0;return n>=1e6?(n/1e6).toFixed(1)+"M":n>=1000?(n/1000).toFixed(1)+"k":String(n);}
  // colour arrives on an untrusted event and lands in style="…"; only a plain
  // hex is ever kept, so it can't break out of the attribute (XSS guard).
  function safeCol(c){return (typeof c==="string"&&/^#[0-9a-fA-F]{3,8}$/.test(c))?c:"#5f7a95";}
  // POS holds dragged node positions; hud stores SCREEN coords, all stores WORLD
  // coords, so the key is namespaced by mode or the two would be misread.
  function posKey(sid,name){return mode+"::"+sid+"::"+name;}
  // tool name -> a short glyph code + colour, grouped by type
  var TOOLTYPES={terminal:{k:"SH",c:"#4ade80",l:"Bash / PowerShell"},web:{k:"WEB",c:"#38bdf8",l:"WebSearch / Fetch"},file:{k:"RD",c:"#a78bfa",l:"Read"},edit:{k:"ED",c:"#fbbf24",l:"Edit / Write"},search:{k:"GR",c:"#2dd4bf",l:"Grep / Glob"},git:{k:"GIT",c:"#f472b6",l:"git"},spawn:{k:"TASK",c:"#e11d48",l:"Task (spawn)"},other:{k:"·",c:"#5f7a95",l:"other"}};
  var TORDER=["terminal","web","file","edit","search","git","spawn"];
  function toolType(n){n=(n||"").toLowerCase();
    if(n==="bash"||n==="powershell")return "terminal";
    if(n.indexOf("web")===0)return "web";
    if(n==="read"||n==="notebookread")return "file";
    if(n==="edit"||n==="write"||n==="multiedit"||n==="notebookedit")return "edit";
    if(n==="grep"||n==="glob")return "search";
    if(n==="task"||n==="agent")return "spawn";
    if(n.indexOf("git")>=0)return "git";
    return "other";}
  var toolSparks=[];   // transient blooms at the core when the selected session runs a tool
  var toolBoxes=[];    // hitboxes for the tool chips drawn this frame (hover/click)
  var STATE_LABEL={run:"running",done:"done",ask:"awaiting reply",error:"error",idle:"idle"};

  var $=function(id){return document.getElementById(id);};
  var sessions={},order=[],selected=null;
  var soundOn=localStorage.getItem("av_sound")!=="0";
  var voiceOn=localStorage.getItem("av_voice")==="1";
  var mode=localStorage.getItem("av_mode")||"hud";
  var META=null, METAMAP={};

  var $conn=$("conn"),$sstat=$("sstat"),$sound=$("sound"),$voice=$("voice");


  /* ---------- meta (dashboard data) ---------- */
  function fetchMeta(){
    fetch("/api/meta").then(function(r){return r.json();}).then(function(d){
      META=d;METAMAP={};(d.agents||[]).forEach(function(a){METAMAP[a.name]=a;});
      renderAmbient();if(mode==="dash")buildDashboard();
    }).catch(function(){});
  }
  function renderAmbient(){
    if(!META||!META.kpis)return;var k=META.kpis;
    $("am-agents").textContent=k.agents;$("am-hours").textContent=k.hours;
    $("am-out").textContent=fmtTok(k.outputTokens);$("am-cache").textContent=k.cachePct+"%";
  }


  /* ---------- sound ---------- */
  var lastPlay=0;
  function playFor(phase,race){if(!soundOn)return;var now=Date.now();if(now-lastPlay<180)return;lastPlay=now;
    try{var a=new Audio("/sound?phase="+encodeURIComponent(phase)+"&race="+encodeURIComponent(race||""));a.volume=0.4;a.play().catch(function(){});}catch(e){}}


  /* ---------- voice ---------- */
  // getVoices() can be empty until the async voiceschanged fires; keep one shared,
  // refreshed cache so both event-narration (en) and mail read-aloud (sr) can pick from it.
  var canSpeak=("speechSynthesis" in window),lastSpoke=0,speechVoices=[];
  function refreshVoices(){if(!canSpeak)return;try{speechVoices=speechSynthesis.getVoices()||[];}catch(e){}}
  function pickVoice(prefix){for(var i=0;i<speechVoices.length;i++){if(new RegExp("^"+prefix,"i").test(speechVoices[i].lang||""))return speechVoices[i];}return null;}
  function speak(text){if(!voiceOn||!canSpeak)return;var now=Date.now();
    if(now-lastSpoke<600||(speechSynthesis.speaking&&speechSynthesis.pending))return;lastSpoke=now;
    try{var u=new SpeechSynthesisUtterance(text);u.rate=1.06;u.pitch=1;u.volume=0.9;u.lang="en-US";
      var pref=pickVoice("en");if(pref)u.voice=pref;
      speechSynthesis.speak(u);}catch(e){}}
  function narrate(agent,phase){var s=(agent||"agent").split(":").pop().replace(/-/g," ");
    var line={start:s+" started",done:s+" done",ask:s+" needs approval",error:s+" error",turn:"cycle complete"}[phase];
    if(line)speak(line);}
  // Mail read-aloud voice picker: persisted in localStorage("mailVoice") by voiceURI (name fallback).
  // Empty selection => auto-pick a Serbian voice in mailReadAloud, so the picker never breaks the default.
  function voiceId(v){return v?(v.voiceURI||v.name):"";}
  function chosenVoice(){var id=localStorage.getItem("mailVoice")||"";if(!id)return null;
    for(var i=0;i<speechVoices.length;i++)if(voiceId(speechVoices[i])===id)return speechVoices[i];return null;}
  function refreshVoiceSelect(){var sel=$("mail-voicesel");if(!sel)return;
    var saved=localStorage.getItem("mailVoice")||"",vs=speechVoices||[];
    sel.innerHTML='<option value="">Auto (Serbian if available)</option>'+vs.map(function(v){var id=voiceId(v);
      return '<option value="'+esc(id)+'"'+(id===saved?" selected":"")+'>'+esc((v.name||"voice")+" ("+(v.lang||"?")+")")+'</option>';}).join("");
    if(saved)sel.value=saved;   // keep the choice even if its voice hasn't loaded yet
    var hasSr=vs.some(function(v){return /^sr/i.test(v.lang||"")||/serbian|srpski/i.test(v.name||"");});
    var hint=$("mail-voicehint");
    if(hint){var show=vs.length&&!hasSr;hint.style.display=show?"":"none";
      hint.textContent=show?"No Serbian voice found — add one in Windows Settings > Time & Language > Speech.":"";}}


  /* ---------- shared: agent metadata lookup (used by the HUD tooltip/inspector AND the dashboard doc reader) ---------- */
  function metaOf(name){return METAMAP[name]||null;}

  /* ---------- common ---------- */
  function renderAll(){var n=order.length;if(!selected&&n)selected=order[0];
    $("empty").style.display=n?"none":"";$sstat.textContent=n+" "+(n===1?"session":"sessions");
    renderTabs();renderPanel();renderHud();if(mode==="flow")buildFlow();}
  function removeSession(id){delete sessions[id];order=order.filter(function(x){return x!==id;});
    Object.keys(POS).forEach(function(k){if(k.split("::")[1]===id)delete POS[k];});
    if(selected===id)selected=order[0]||null;renderAll();}
  function ensure(id,cwd){if(!sessions[id]){sessions[id]={id:id,cwd:cwd||"",agents:{},events:[],tools:[],prompt:"",counts:{start:0,done:0,ask:0,error:0},last:Date.now()/1000};order.push(id);}return sessions[id];}
  function applyEvent(sid,cwd,e,counts){var s=ensure(sid,cwd);if(cwd)s.cwd=cwd;s.last=Date.now()/1000;if(counts)s.counts=counts;
    s.agents[e.agent]={name:e.agent,state:e.state,group:e.group,color:safeCol(e.color),tool:e.tool,label:e.label,ts:e.ts,race:e.race};
    if(e.state==="run")s._pulse=1;   // cluster pulse in HUD·All when a session goes active
    s.events.push(e);if(s.events.length>200)s.events=s.events.slice(-200);
    if(mode==="hud"&&e.state==="run"&&sid===selected){packets.push({sid:sid,name:e.agent,col:safeCol(e.color),t:0});
      // handoff (inferred): if another agent finished in the last ~2.5s, pulse from it to this one
      var recent=null,rt=0,s2=sessions[sid];Object.keys(s2.agents).forEach(function(k){var ag=s2.agents[k];if(k!==e.agent&&ag.state==="done"&&ag.ts&&(Date.now()/1000-ag.ts)<2.5&&ag.ts>rt){recent=k;rt=ag.ts;}});
      if(recent)packets.push({sid:sid,name:e.agent,from:recent,col:safeCol(e.color),t:0});}
    playFor(e.phase,e.race);narrate(e.agent,e.phase);}
  function loadSnapshot(list){sessions={};order=[];(list||[]).forEach(function(s){sessions[s.id]={id:s.id,cwd:s.cwd,agents:{},events:s.events||[],tools:s.tools||[],prompt:s.prompt||"",todos:s.todos||[],counts:s.counts||{},last:s.last};
    (s.agents||[]).forEach(function(a){a.color=safeCol(a.color);sessions[s.id].agents[a.name]=a;});order.push(s.id);});
    if(order.length&&!sessions[selected])selected=order[0];renderAll();}


  /* ---------- SSE ---------- */
  function connect(){var es=new EventSource("/stream");
    es.onopen=function(){$conn.classList.add("on");};es.onerror=function(){$conn.classList.remove("on");};
    es.onmessage=function(m){var msg;try{msg=JSON.parse(m.data);}catch(e){return;}
      if(msg.type==="snapshot"){loadSnapshot(msg.sessions);return;}
      if(msg.type==="drop"){removeSession(msg.session);return;}
      if(msg.type==="tool"){var st=sessions[msg.session];if(st){st.tools=st.tools||[];st.tools.push({tool:msg.tool,ts:msg.ts});if(st.tools.length>40)st.tools=st.tools.slice(-40);if(msg.session===selected&&mode==="hud")toolSparks.push({tool:msg.tool,t:0});if(mode==="flow"&&msg.session===selected)buildFlow();}if(typeof onGameTool==="function")onGameTool(msg);return;}
      if(msg.type==="prompt"){var sp=sessions[msg.session];if(sp){sp.prompt=msg.prompt;if(mode==="flow"&&msg.session===selected)buildFlow();}return;}
      if(msg.type==="todos"){var std=sessions[msg.session];if(std){std.todos=msg.todos||[];if(mode==="flow"&&msg.session===selected)buildFlow();}return;}
      if(msg.type==="ticket-triage"){applyTriage(msg.dir,msg.id,msg.triage,msg.rev,msg.outbox);return;}
      if(msg.type==="rescan"){var rb=$("tk-refresh");if(rb){if(msg.status==="start"||msg.status==="sync"){rb.disabled=true;rb.textContent=msg.status==="sync"?"↻ helpdesk sync…":"↻ scanning…";}else if(msg.status==="synced"||msg.status==="sync-skipped"){rb.disabled=true;rb.textContent="↻ triage…";rb.title=msg.status==="synced"?("helpdesk: +"+msg.added+" new, ~"+msg.updated+" updated, closed here "+msg.closed_here+", closed on helpdesk "+msg.closed_helpdesk+((msg.needs_resolution||[]).length?(" · BEZ REZOLUCIJE ZA KUPCA (ostaju otvoreni): "+msg.needs_resolution.join(", ")):"")+(msg.failed?", FAILED "+msg.failed:"")):("helpdesk sync skipped: "+(msg.reason||"")+" — set HELPDESK_URL/HELPDESK_TOKEN");fetchTickets();}else if(msg.status==="estimated"){rb.disabled=true;rb.textContent="↻ procene…";rb.title="procene: "+(msg.n||0)+" poslato · "+(msg.skipped||0)+" preskočeno";}else if(msg.status==="estimate-skipped"){rb.disabled=true;rb.textContent="↻ procene…";rb.title="procene preskočene (nema pristupa helpdesku)";}else if(msg.status==="busy"){rb.disabled=true;rb.textContent="↻ već radi…";rb.title="rescan je već u toku — sačekaj da završi";}else{rb.disabled=false;rb.textContent="↻ rescan";if(msg.status==="done"){rb.title="poslednji rescan: Gemini trijaža "+(msg.analysed||0)+" tiketa"+(msg.failed?", "+msg.failed+" neuspelo":"")+" — detalji u 🧠 AI analize";fetchTickets();}}}return;}
      if(msg.type==="module-repo"){applyRepo(msg.dir,msg.repo);return;}
      if(msg.type==="mail-new"){onMailNew(msg);return;}
      if(msg.type==="testrun"){if(typeof applyTestRun==="function")applyTestRun(msg.run);return;}
      if(msg.type==="game"){if(typeof onGameState==="function")onGameState(msg);return;}
      if(msg.type==="event"){applyEvent(msg.session,msg.cwd,msg.event,msg.counts);renderAll();}};}


  /* ---------- full-prompt modal (flow prompt + dashboard per-request) ---------- */
  function openPrompt(text){var body=$("prompt-body");if(!body)return;body.textContent=String(text==null?"":text);$("promptmodal").classList.add("open");}
  function closePrompt(){$("promptmodal").classList.remove("open");}
  $("prompt-x").onclick=closePrompt;$("prompt-bd").onclick=closePrompt;

  /* ---------- shared: generic fetch-with-normalised-error helper (used by GIT and PRODUCTION) ---------- */
  function gitFetch(url){return fetch(url).then(function(r){
      if(r.status===403)return {forbidden:true};
      if(!r.ok)return {error:"HTTP "+r.status};
      return r.json().then(function(d){return {data:d};},function(){return {error:"bad response"};});
    },function(){return {error:"server not reachable"};});}

  /* ---------- shared: floating HUD tooltip (used by GIT + PRODUCTION scenes & 2D charts) ----------
     ONE reusable element, created once (lazily, so document.body exists) and appended to <body>.
     hudTip.show(clientX,clientY,html) positions near the cursor and FLIPS at the viewport edges to
     stay on-screen; hudTip.hide() hides it. Styled from the HUD tokens via `.hud-tip` in app.css;
     pointer-events:none so it never eats a hover/click, no animation so it is reduced-motion-safe.
     This is deliberately SEPARATE from sessions.js's #tip (the HUD graph's node-metadata tooltip,
     which has its own inner structure and inlined edge logic bound to node data) — see craft-reuse:
     "exists but wrong for you → write the new one next to it and say why". */
  var hudTip=(function(){var el=null;
    function ensure(){if(el)return el;el=document.createElement("div");el.className="hud-tip";
      el.setAttribute("aria-hidden","true");(document.body||document.documentElement).appendChild(el);return el;}
    return {
      show:function(cx,cy,html){var t=ensure();t.innerHTML=html;t.style.display="block";
        var tw=t.offsetWidth,th=t.offsetHeight,x=cx+16,y=cy+16;
        if(x+tw>innerWidth-10)x=cx-tw-16;if(x<8)x=8;      // flip left near the right edge
        if(y+th>innerHeight-10)y=cy-th-16;if(y<8)y=8;     // flip up near the bottom edge
        t.style.left=x+"px";t.style.top=y+"px";},
      hide:function(){if(el)el.style.display="none";}
    };})();
  // Nearest-hit picker over a scene's per-frame hit array (screen coords). Each entry is
  // {x,y,r,data}; returns the closest entry whose centre is within its own radius, else null.
  function hudPick(arr,mx,my){var best=null,bd=Infinity;if(!arr)return null;
    for(var i=0;i<arr.length;i++){var p=arr[i],dx=mx-p.x,dy=my-p.y,d=dx*dx+dy*dy;
      if(d<=p.r*p.r&&d<bd){bd=d;best=p;}}return best;}
  // Wire hover tooltips on a CANVAS scene. `getArr` returns the scene's live hit array (rebuilt each
  // frame during draw). Cursor→canvas mapping is a plain rect subtract with NO devicePixelRatio
  // factor: the scenes draw in CSS-pixel space (ctx.setTransform(DPR,…) bakes DPR into the backing
  // store, geometry uses getBoundingClientRect().width/height = CSS px), so the stored p.x/p.y are
  // already CSS px and match e.clientX-rect.left. (Divide by DPR only if a scene ever drew in device px.)
  function hudTipCanvas(cv,getArr,fmt){if(!cv)return;
    cv.addEventListener("mousemove",function(e){var r=cv.getBoundingClientRect();
      var h=hudPick(getArr(),e.clientX-r.left,e.clientY-r.top);
      if(h){cv.style.cursor="pointer";hudTip.show(e.clientX,e.clientY,fmt(h.data));}
      else{cv.style.cursor="";hudTip.hide();}});
    cv.addEventListener("mouseleave",function(){hudTip.hide();});}
  // Wire hover tooltips on a 2D SVG/DOM chart CONTAINER by delegation, so it survives innerHTML
  // rebuilds. Each hoverable child carries a `data-tip` attribute whose value is a small HTML string
  // (build it with hudTipAttr so it round-trips the attribute layer). Wire-once via a marker flag.
  function hudTipDelegate(host){if(!host||host.__hudTipWired)return;host.__hudTipWired=true;
    host.addEventListener("mousemove",function(e){var t=e.target&&e.target.closest?e.target.closest("[data-tip]"):null;
      if(t&&host.contains(t))hudTip.show(e.clientX,e.clientY,t.getAttribute("data-tip"));else hudTip.hide();});
    host.addEventListener("mouseleave",function(){hudTip.hide();});}
  // Build a data-tip attribute value from an already-built (values-esc'd) tooltip HTML string:
  // esc() the whole thing so it is attribute-safe; the browser decodes it once on read, and the
  // values inside were esc'd when the html was built, so untrusted content still renders inert.
  function hudTipAttr(html){return ' data-tip="'+esc(html)+'"';}

  /* ---------- shared: date/time formatting (used by TICKETS and MAIL) ---------- */
  // ISO date -> ms epoch, or null for empty/unparseable (tolerant of bad input)
  function tsOf(s){if(!s)return null;var d=new Date(s);return isNaN(d.getTime())?null:d.getTime();}
  // ISO timestamp -> locale string; falls back to the raw value (the caller esc()s it)
  function fmtAt(s){if(!s)return "—";var d=new Date(s);return isNaN(d.getTime())?String(s):d.toLocaleString();}

  /* ---------- shared: generic AI-call + non-blocking loader (used by MAIL and Ask Claude) ---------- */
  // one POST helper that ALWAYS surfaces the real server error (incl. HTTP status),
  // so a failure reads "Suggest failed: HTTP 429", never a generic word.
  function aiPost(url,body){
    return fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})})
      .then(function(r){return r.json().catch(function(){return {};}).then(function(d){d=d||{};if(!r.ok&&!d.error)d.error="HTTP "+r.status;return d;});});}
  function aiErrText(e){return (e&&e.message)?e.message:"network error";}
  // non-blocking AI loader — one shared component every AI call drives (start/stop by id, supports concurrency)
  var aiOps={},aiOpSeq=0;
  function aiLoaderStart(name){var id=++aiOpSeq;aiOps[id]=name||"AI";renderAiLoader();return id;}
  function aiLoaderStop(id){delete aiOps[id];renderAiLoader();}
  function renderAiLoader(){var el=$("ai-loader");if(!el)return;var ks=Object.keys(aiOps);
    el.classList.toggle("on",ks.length>0);var host=$("ai-loader-ops");
    if(host)host.innerHTML=ks.map(function(k){return '<div class="ail-op">'+esc(aiOps[k])+'</div>';}).join("");}

  /* A refusal the operator must not miss. Longer default for an error than for
     a confirmation: an error is read, a confirmation is glanced at. */
  var _hudToastT=null;
  function hudToast(msg,isErr,ms){
    var t=document.getElementById("hud-toast"); if(!t) return;
    t.textContent=msg; t.className="hud-toast on"+(isErr?" err":"");
    if(_hudToastT) clearTimeout(_hudToastT);
    _hudToastT=setTimeout(function(){t.className="hud-toast";}, ms||(isErr?9000:3000));
  }
