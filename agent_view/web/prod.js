  /* ---------- PRODUCTION view (live VPS: system metrics + Docker containers) ---------- */
  // State machine mirrors gitRender: 403 (loopback-only) / not-configured / error / ok.
  // Reuses gitFetch(url) (403 → {forbidden:true} before .json()) — same loopback shape.
  var prodOverview=null,prodConfigured=null,prodForbidden=false,prodErr=null,prodOverviewErr=null;
  // bytes → human (binary/1024). The app only had fmtTok (token counts), no byte formatter.
  function fmtBytes(n){n=+n;if(!isFinite(n)||n<0)return "—";
    if(n<1024)return Math.round(n)+" B";
    var u=["KB","MB","GB","TB","PB"],i=-1;
    do{n/=1024;i++;}while(n>=1024&&i<u.length-1);
    return (n>=100?n.toFixed(0):n.toFixed(1))+" "+u[i];}
  function prodGet(o,path){var p=String(path).split("."),x=o;for(var i=0;i<p.length;i++){if(x==null)return undefined;x=x[p[i]];}return x;}
  function prodPick(o,paths){for(var i=0;i<paths.length;i++){var v=prodGet(o,paths[i]);if(v!==undefined&&v!==null)return v;}return undefined;}
  function prodNum(v){v=+v;return isFinite(v)?v:0;}
  function prodIsUp(c){return String((c&&c.status)||"").toLowerCase()==="running";}
  // Usage ring: HIGH = BAD (red). Deliberately the INVERSE of gitRing (CI pass rate,
  // where high = good/green) — kept as its own function so the two colour semantics
  // never merge and silently invert somewhere.
  function prodUsageCol(pct){pct=+pct||0;return pct>=85?"#fb7185":pct>=70?"#fbbf24":"#38e6ff";}
  function prodRing(pct,size){size=size||112;pct=Math.max(0,Math.min(100,+pct||0));
    var r=(size/2)-9,c=2*Math.PI*r,off=c*(1-pct/100),cx=size/2,col=prodUsageCol(pct);
    return '<svg viewBox="0 0 '+size+' '+size+'" width="'+size+'" height="'+size+'" class="ps-ringsvg">'+
      '<circle cx="'+cx+'" cy="'+cx+'" r="'+r+'" fill="none" stroke="rgba(56,189,248,.14)" stroke-width="8"/>'+
      '<circle cx="'+cx+'" cy="'+cx+'" r="'+r+'" fill="none" stroke="'+col+'" stroke-width="8" stroke-linecap="round" stroke-dasharray="'+c.toFixed(1)+'" stroke-dashoffset="'+off.toFixed(1)+'" transform="rotate(-90 '+cx+' '+cx+')" style="transition:stroke-dashoffset .5s"/>'+
      '<text x="'+cx+'" y="'+(cx+size*0.09).toFixed(1)+'" text-anchor="middle" font-size="'+(size*0.24).toFixed(0)+'" font-weight="700" fill="'+col+'">'+Math.round(pct)+'<tspan font-size="'+(size*0.12).toFixed(0)+'">%</tspan></text></svg>';}
  // Preload: render the last-known overview (system+containers+health) INSTANTLY on
  // tab open from localStorage, then let the live fetch refresh it.
  function prodSaveSnap(){try{if(prodOverview)localStorage.setItem("av_prod_snap",JSON.stringify(prodOverview));}catch(e){}}
  function prodLoadSnap(){try{var d=JSON.parse(localStorage.getItem("av_prod_snap")||"null");return (d&&typeof d==="object")?d:null;}catch(e){return null;}}
  function prodInit(){
    if(!prodOverview){var s=prodLoadSnap();if(s){prodOverview=s;prodConfigured=true;prodRender();}}
    prodFetchStatus();prodFetchOverview();}
  function prodFetchStatus(){gitFetch("/api/prod/status").then(function(res){
      if(res.forbidden){prodForbidden=true;prodRender();return;}
      if(res.error)return;                                 // status probe failed → let overview drive
      prodForbidden=false;prodConfigured=!!(res.data&&res.data.configured);prodRender();});}
  function prodFetchOverview(silent){gitFetch("/api/prod/overview").then(function(res){
      if(res.forbidden){prodForbidden=true;prodRender();return;}
      prodForbidden=false;
      if(res.error){prodErr=res.error;prodRender();return;}
      prodErr=null;var d=res.data||{};
      if(d.ok===false){prodOverview=null;if(d.configured!==undefined)prodConfigured=!!d.configured;
        prodOverviewErr=d.error||"Monitor error";prodRender();return;}
      prodOverviewErr=null;prodConfigured=true;prodOverview=d;prodSaveSnap();prodHistPush(d);prodRender();});}
  // top-level state machine: 403 / not-configured / monitor-error / loading / ok
  function prodRender(){
    var note=$("prod-note"),shell=$("prod-shell");if(!note||!shell)return;
    function showNote(cls,html){note.style.display="";note.className="git-note"+(cls?" "+cls:"");note.innerHTML=html;shell.style.display="none";var s=$("prod-sub");if(s)s.innerHTML="";}
    if(prodForbidden)return showNote("desktop",'<b>Production view is desktop-only</b><span>These metrics answer only on the loopback interface (this machine). Open the Live Agent View directly on the host &mdash; a non-loopback device gets a 403.</span>');
    if(prodConfigured===false)return showNote("",'<b>Monitor not configured</b><span>Dodaj monitor: {url, token} u agent_view.config.json (deljeni config) ili MONITOR_URL/MONITOR_TOKEN u okruženje.</span>');
    if(prodOverviewErr)return showNote("err",'<b>Monitor error</b><span>'+esc(prodOverviewErr)+'</span>');
    if(prodErr&&!prodOverview)return showNote("err",'<b>Could not reach the monitor</b><span>'+esc(prodErr)+'</span>');
    if(!prodOverview){note.style.display="";note.className="git-note";note.innerHTML='<div class="prod-loading">Loading live metrics&hellip;</div>';shell.style.display="none";return;}
    note.style.display="none";shell.style.display="";
    var sys=prodOverview.system||{};
    prodRenderSystem(sys);
    prodRenderNet(prodGet(sys,"network")||{});
    prodRenderHealth(prodOverview.health||[]);
    prodRenderContainers(prodOverview.containers||[]);
    prodRenderSub(prodOverview);
    prodRenderAlerts(prodOverview);         // "what needs attention" — derived from the current overview
    prodRenderResHist();                    // CPU/MEM/DISK trend area charts (history buffer)
    prodRenderLatency(prodOverview.health||[]);   // per-app latency sparkline + uptime ribbon (history buffer)
    prodReactorSync();prodReactorStart();   // rebuild orbiting nodes from the latest data; loop keeps running
    if(prodVisitsOpen)prodGlobeStart();     // re-arm the globe loop on tab re-entry if traffic was left open
  }
  function prodRenderSub(ov){var s=$("prod-sub");if(!s)return;
    var sys=ov.system||{},host=prodPick(sys,["hostname","host","system.hostname","node"]);
    var cs=ov.containers||[],up=cs.filter(prodIsUp).length,bits=[];
    if(host)bits.push('<b>'+esc(String(host))+'</b>');
    bits.push('<b>'+cs.length+'</b> container'+(cs.length===1?"":"s"));
    bits.push('<b>'+up+'</b> up');
    s.innerHTML=bits.join(" &middot; ");}
  // ONE implementation of each system percentage, shared by the rings, the history
  // buffers and the alerts feed — so all three read identical values (craft-reuse).
  function prodCpuPct(sys){return prodNum(prodPick(sys,["cpu.percent","cpu_percent","cpu.usage"]));}
  function prodMemPct(sys){return prodNum(prodPick(sys,["memory.virtual.percent","memory.percent","mem.percent","memory.used_percent"]));}
  function prodDiskInfo(sys){var usage=prodGet(sys,"disk.usage");if(!usage||typeof usage!=="object")usage=prodGet(sys,"disk")||{};
    var mount=(usage["/"]!==undefined)?"/":Object.keys(usage)[0];
    var root=(mount!=null?usage[mount]:null)||{};
    var p=prodPick(root,["percent","used_percent"]);
    return {mount:mount,root:root,pct:prodNum(p!=null?p:prodPick(sys,["disk.percent"]))};}
  function prodRenderSystem(sys){
    var host=$("prod-system");if(!host)return;var cards=[];
    // CPU — ring + core count + load average + a per-core bar row
    var cpuPct=prodCpuPct(sys);
    var cores=prodPick(sys,["cpu.count","cpu.count_logical","cpu_count"]);
    var load=prodPick(sys,["cpu.load_avg","load_avg","loadavg","cpu.loadavg"]);
    var per=prodPick(sys,["cpu.per_cpu","per_cpu","cpu.percpu","cpu.per_core"]);
    var cpuDetail=(cores!=null)?('<div class="ps-detail"><b>'+prodNum(cores)+'</b> cores</div>'):'';
    var loadStr=Array.isArray(load)?load.slice(0,3).map(function(x){return prodNum(x).toFixed(2);}).join("  "):"";
    var loadHtml=loadStr?('<div class="ps-load">load <b>'+loadStr+'</b></div>'):'';
    var perHtml="";
    if(Array.isArray(per)&&per.length)perHtml='<div class="ps-cpus" title="per-core load">'+per.map(function(v){var p=Math.max(0,Math.min(100,prodNum(v)));return '<div class="ps-cpu" style="height:'+Math.max(8,p)+'%;background:'+prodUsageCol(p)+'"></div>';}).join("")+'</div>';
    cards.push('<div class="ps-card"><div class="ps-lab">CPU</div><div class="ps-ringwrap">'+prodRing(cpuPct)+'</div>'+cpuDetail+loadHtml+perHtml+'</div>');
    // Memory — ring + used/total humanized
    var memPct=prodMemPct(sys);
    var memUsed=prodPick(sys,["memory.virtual.used","memory.used","mem.used"]);
    var memTotal=prodPick(sys,["memory.virtual.total","memory.total","mem.total"]);
    var memDetail=(memUsed!=null&&memTotal!=null)?('<b>'+fmtBytes(memUsed)+'</b> / '+fmtBytes(memTotal)):'';
    cards.push('<div class="ps-card"><div class="ps-lab">Memory</div><div class="ps-ringwrap">'+prodRing(memPct)+'</div><div class="ps-detail">'+memDetail+'</div><div class="ps-sub">used / total</div></div>');
    // Disk — usage keyed by mount; pick "/" or the first mount defensively
    var di=prodDiskInfo(sys),mount=di.mount,root=di.root,diskPct=di.pct;
    var diskUsed=prodPick(root,["used"]),diskTotal=prodPick(root,["total"]);
    var diskDetail=(diskUsed!=null&&diskTotal!=null)?('<b>'+fmtBytes(diskUsed)+'</b> / '+fmtBytes(diskTotal)):'';
    cards.push('<div class="ps-card"><div class="ps-lab">Disk'+(mount!=null?(' &middot; '+esc(String(mount))):'')+'</div><div class="ps-ringwrap">'+prodRing(diskPct)+'</div><div class="ps-detail">'+diskDetail+'</div><div class="ps-sub">used / total</div></div>');
    host.innerHTML=cards.join("");
  }
  // network is defensive: keys vary, so walk (max 1 level deep) and humanize byte-ish keys
  function prodNetCards(net){var out=[];
    function hk(k){return String(k).replace(/[_.]/g," ").replace(/\b\w/g,function(c){return c.toUpperCase();});}
    // only humanize keys that are actually byte counters (bytes_sent, rx_bytes, …);
    // packet/error/drop/connection counts contain sent/recv too but must stay raw counts.
    function bytesy(k){return /byte/i.test(String(k));}
    function walk(prefix,obj,depth){Object.keys(obj||{}).forEach(function(k){var v=obj[k];if(v==null)return;
      var lab=prefix?prefix+" · "+hk(k):hk(k);
      if(typeof v==="number")out.push({label:lab,val:(bytesy(k)||bytesy(prefix))?fmtBytes(v):String(v)});
      else if(typeof v==="string")out.push({label:lab,val:v});
      else if(typeof v==="boolean")out.push({label:lab,val:v?"yes":"no"});
      else if(typeof v==="object"&&depth<1)walk(hk(k),v,depth+1);});}
    walk("",net,0);return out.slice(0,8);}   // keep the band scannable
  function prodRenderNet(net){var host=$("prod-net");if(!host)return;
    host.innerHTML=prodNetCards(net).map(function(c){
      return '<div class="ps-stat"><span class="k" title="'+esc(c.label)+'">'+esc(c.label)+'</span><span class="v" title="'+esc(c.val)+'">'+esc(c.val)+'</span></div>';}).join("");}
  // ports may arrive as a string, an array of objects, or a docker dict — normalize compactly
  function prodPort1(e){if(e==null)return "";if(typeof e==="string")return e;
    if(typeof e==="object"){var pub=e.PublicPort||e.public||e.HostPort,priv=e.PrivatePort||e.private,typ=e.Type||e.type||"";
      if(pub&&priv)return pub+"→"+priv+(typ?"/"+typ:"");
      if(priv)return String(priv)+(typ?"/"+typ:"");
      if(pub)return String(pub)+(typ?"/"+typ:"");}
    return String(e);}
  function prodPorts(p){if(!p)return "";
    if(typeof p==="string")return p;
    var out=[];
    if(Array.isArray(p))out=p.map(prodPort1).filter(Boolean);
    else if(typeof p==="object")Object.keys(p).forEach(function(k){var v=p[k];
      if(Array.isArray(v)&&v.length)v.forEach(function(b){out.push((b&&b.HostPort?b.HostPort+"→":"")+k);});
      else out.push(k);});
    else out=[String(p)];
    var seen={},uniq=[];out.forEach(function(x){if(x&&!seen[x]){seen[x]=1;uniq.push(x);}});
    return uniq.join(", ");}
  function prodRenderContainers(list){
    var head=$("prod-cont-head"),host=$("prod-containers");if(!host)return;
    list=Array.isArray(list)?list:[];
    var up=list.filter(prodIsUp).length,down=list.length-up;
    if(head){head.className="prod-cont-head"+(down>0?" alert":"");
      head.innerHTML='Containers <span class="pc-sum"><span class="up">'+up+' up</span> / <span class="down">'+down+' down</span></span>';}
    if(!list.length){host.innerHTML='<div class="prod-cont-empty">No containers reported by the monitor.</div>';return;}
    // down-first, then by name — a stopped container lands at the top and stays scannable
    var sorted=list.slice().sort(function(a,b){var ua=prodIsUp(a)?1:0,ub=prodIsUp(b)?1:0;
      if(ua!==ub)return ua-ub;return String(a.name||"").localeCompare(String(b.name||""));});
    host.innerHTML='<div class="prod-grid">'+sorted.map(function(c){
      var upC=prodIsUp(c),st=String(c.status||(upC?"running":"down")),ports=prodPorts(c.ports);
      return '<div class="pcont '+(upC?"up":"down")+'" title="'+esc(String(c.id||""))+'">'+
        '<div class="pc-top"><span class="pc-name">'+esc(c.name||"(unnamed)")+'</span>'+
          '<span class="pc-status '+(upC?"up":"down")+'">'+esc(st)+'</span></div>'+
        '<div class="pc-image" title="'+esc(String(c.image||""))+'">'+esc(c.image||"—")+'</div>'+
        '<div class="pc-ports'+(ports?"":" none")+'">'+(ports?esc(ports):"no published ports")+'</div>'+
      '</div>';}).join("")+'</div>';
  }
  $("prod-refresh").onclick=function(){prodFetchStatus();prodFetchOverview();};

  /* ---------- APP HEALTH panel (overview.health: monitored APPS, distinct from containers) ---------- */
  // Distinct from containers: an app is an HTTP endpoint we probe (status up/down + response_ms),
  // not a docker process. Down-first, down pill red + pulsing glow, latency amber >1000ms / red on down.
  function prodHealthUp(h){return String((h&&h.status)||"").toLowerCase()==="up";}
  function prodMsCls(h){if(!prodHealthUp(h))return "bad";var ms=+(h&&h.response_ms);return (isFinite(ms)&&ms>1000)?"warn":"";}
  function prodMsText(h){var ms=h&&h.response_ms;
    if(ms==null||!isFinite(+ms))return prodHealthUp(h)?"—":"timeout";
    return Math.round(+ms)+" ms";}
  function prodRenderHealth(list){
    var head=$("prod-health-head"),host=$("prod-health");if(!host)return;
    list=Array.isArray(list)?list:[];
    if(!list.length){if(head){head.style.display="none";head.innerHTML="";}host.innerHTML="";return;}   // hide gracefully
    var up=list.filter(prodHealthUp).length,down=list.length-up;
    if(head){head.style.display="";head.className="prod-cont-head"+(down>0?" alert":"");
      head.innerHTML='App health <span class="pc-sum"><span class="up">'+up+' up</span> / <span class="down">'+down+' down</span></span>';}
    // down-first, then by name — a failing app lands top-left and stays scannable (mirrors the container grid)
    var sorted=list.slice().sort(function(a,b){var ua=prodHealthUp(a)?1:0,ub=prodHealthUp(b)?1:0;
      if(ua!==ub)return ua-ub;return String(a.name||a.app_id||"").localeCompare(String(b.name||b.app_id||""));});
    host.innerHTML=sorted.map(function(h){
      var upC=prodHealthUp(h),name=h.name||h.app_id||"(unnamed)";
      var http=(h.http_status!=null&&h.http_status!=="")?('<span class="phz-http">HTTP '+esc(String(h.http_status))+'</span>'):'';
      return '<div class="phz '+(upC?"up":"down")+'">'+
        '<div class="phz-main"><span class="phz-name" title="'+esc(String(h.url||name))+'">'+esc(name)+'</span>'+
          (h.url?('<span class="phz-url">'+esc(String(h.url))+'</span>'):'')+'</div>'+
        '<div class="phz-right"><span class="pc-status '+(upC?"up":"down")+'">'+(upC?"up":"down")+'</span>'+   /* reuse the container status pill */
          '<div class="phz-meta">'+http+'<span class="phz-ms '+prodMsCls(h)+'">'+esc(prodMsText(h))+'</span></div></div>'+
      '</div>';}).join("");
  }

  /* ---------- 2D ANALYTICS (client-side history, SVG/DOM — no libs, no backend) ----------
     Small ring buffers accumulated on each SUCCESSFUL overview poll, persisted to
     localStorage("av_prod_hist") and hydrated on load (so sparklines aren't empty on
     first open — mirrors the av_prod_snap preload). Rendered on the existing poll; no rAF. */
  var PROD_HIST_APP=40, PROD_HIST_RES=60, PROD_HIST_APPS_MAX=60;
  var prodHist={apps:{},res:[]};   // apps: {app_id:[{ms,up}…40]}  res:[{cpu,mem,disk}…60]
  function prodHistSave(){try{localStorage.setItem("av_prod_hist",JSON.stringify(prodHist));}catch(e){}}
  function prodHistLoad(){try{var d=JSON.parse(localStorage.getItem("av_prod_hist")||"null");if(!d||typeof d!=="object")return;
    var apps=(d.apps&&typeof d.apps==="object")?d.apps:{},res=Array.isArray(d.res)?d.res:[],clean={};
    Object.keys(apps).slice(0,PROD_HIST_APPS_MAX).forEach(function(k){var b=Array.isArray(apps[k])?apps[k]:[];
      clean[k]=b.slice(-PROD_HIST_APP).map(function(s){s=s||{};var ms=+s.ms;return {ms:isFinite(ms)?ms:null,up:!!s.up,t:(typeof s.t==="number"?s.t:null)};});});
    prodHist.apps=clean;
    prodHist.res=res.slice(-PROD_HIST_RES).map(function(s){s=s||{};return {cpu:prodNum(s.cpu),mem:prodNum(s.mem),disk:prodNum(s.disk),t:(typeof s.t==="number"?s.t:null)};});
  }catch(e){}}
  prodHistLoad();   // hydrate on load
  // append one sample per app + one resource sample; trim to the caps; persist. Called on poll success.
  function prodHistPush(ov){ov=ov||{};var now=Date.now();
    var h=Array.isArray(ov.health)?ov.health:[],seen={};
    h.forEach(function(x){var id=String((x&&(x.app_id||x.name))||"");if(!id)return;seen[id]=1;
      var buf=prodHist.apps[id]||(prodHist.apps[id]=[]),ms=+(x&&x.response_ms),up=String((x&&x.status)||"").toLowerCase()==="up";
      buf.push({ms:isFinite(ms)?ms:null,up:up,t:now});
      if(buf.length>PROD_HIST_APP)buf.splice(0,buf.length-PROD_HIST_APP);});
    if(Object.keys(prodHist.apps).length>PROD_HIST_APPS_MAX)   // bound churn: drop keys absent from this poll
      Object.keys(prodHist.apps).forEach(function(k){if(!seen[k])delete prodHist.apps[k];});
    var sys=ov.system||{};
    prodHist.res.push({cpu:prodCpuPct(sys),mem:prodMemPct(sys),disk:prodDiskInfo(sys).pct,t:now});
    if(prodHist.res.length>PROD_HIST_RES)prodHist.res.splice(0,prodHist.res.length-PROD_HIST_RES);
    prodHistSave();}

  /* --- Panel 1: per-app latency sparkline + uptime ribbon (history-driven) --- */
  // Colours from the resolved tokens in PRC (theme-safe): red=down/timeout, amber>1000ms, else cyan.
  function prodSampleCol(s){if(!s||!s.up||s.ms==null||!isFinite(+s.ms))return PRC.bad;return (+s.ms>1000)?PRC.warn:PRC.cy;}
  // sample wall-clock label for hover (buffers gained a `t` field; older persisted samples lack it → em dash)
  function prodWhen(t){return (typeof t==="number"&&isFinite(t))?new Date(t).toLocaleTimeString():"—";}
  function prodSparkSvg(buf){var W=200,H=34,pad=3,n=buf.length;
    if(!n)return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none"><text x="'+(W/2)+'" y="'+(H/2+3)+'" text-anchor="middle" fill="'+PRC.dim+'" font-size="9" font-family="'+PMONO+'">no samples yet</text></svg>';
    var vals=buf.map(function(s){return (!s.up||s.ms==null||!isFinite(+s.ms))?null:Math.max(0,+s.ms);});
    var mx=1000;vals.forEach(function(v){if(v!=null&&v>mx)mx=v;});   // scale so the 1000ms guide is always on-chart
    function X(i){return n<=1?W/2:pad+(W-2*pad)*(i/(n-1));}
    function Y(v){if(v==null)v=mx;var t=Math.max(0,Math.min(1,v/mx));return pad+(H-2*pad)*(1-t);}
    var out='<line x1="0" y1="'+Y(1000).toFixed(1)+'" x2="'+W+'" y2="'+Y(1000).toFixed(1)+'" stroke="'+PRC.warn+'" stroke-opacity=".18" stroke-width="1" stroke-dasharray="3 3"/>';
    for(var i=1;i<n;i++)out+='<line x1="'+X(i-1).toFixed(1)+'" y1="'+Y(vals[i-1]).toFixed(1)+'" x2="'+X(i).toFixed(1)+'" y2="'+Y(vals[i]).toFixed(1)+'" stroke="'+prodSampleCol(buf[i])+'" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>';
    out+='<circle cx="'+X(n-1).toFixed(1)+'" cy="'+Y(vals[n-1]).toFixed(1)+'" r="2.4" fill="'+prodSampleCol(buf[n-1])+'"/>';
    // transparent per-sample hover bands (on top; pointer-events:all → each column tooltips its sample)
    var bw=(n>1?((W-2*pad)/(n-1)):W);
    for(var j=0;j<n;j++){var s=buf[j],bx=(n>1?X(j)-bw/2:0);
      var msTxt=(!s.up||s.ms==null||!isFinite(+s.ms))?(s.up?"—":"down/timeout"):(Math.round(+s.ms)+" ms");
      var cls=(!s.up)?"down":((s.ms!=null&&isFinite(+s.ms)&&+s.ms>1000)?"warn":"up");
      var htip='<div class="htr"><span>time</span><b>'+esc(prodWhen(s.t))+'</b></div>'+
        '<div class="htr"><span>latency</span><span class="'+cls+'">'+esc(msTxt)+'</span></div>';
      out+='<rect x="'+Math.max(0,bx).toFixed(1)+'" y="0" width="'+bw.toFixed(1)+'" height="'+H+'" fill="rgba(0,0,0,0)" pointer-events="all"'+hudTipAttr(htip)+'/>';}
    return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none">'+out+'</svg>';}
  function prodRibbonSvg(buf){var W=200,H=8,n=buf.length;
    if(!n)return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none"><rect x="0" y="0" width="'+W+'" height="'+H+'" fill="'+PRC.dim+'" fill-opacity=".15"/></svg>';
    var bw=W/n,out='';
    for(var i=0;i<n;i++){var s=buf[i],up=s.up;
      var htip='<div class="htr"><span>state</span><span class="'+(up?"up":"down")+'">'+(up?"up":"down")+'</span></div>'+
        '<div class="htr"><span>time</span><b>'+esc(prodWhen(s.t))+'</b></div>';
      out+='<rect x="'+(i*bw).toFixed(2)+'" y="0" width="'+Math.max(0.4,bw-0.35).toFixed(2)+'" height="'+H+'" fill="'+(up?PRC.acc:PRC.bad)+'" fill-opacity="'+(up?".5":"1")+'"'+hudTipAttr(htip)+'/>';}
    return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none">'+out+'</svg>';}
  function prodUptimePct(buf){if(!buf||!buf.length)return null;var u=0;buf.forEach(function(s){if(s.up)u++;});return u/buf.length*100;}
  function prodUptimeCls(p){return p==null?"":p>=99?"good":p>=95?"mid":"low";}
  function prodRenderLatency(list){var head=$("prod-lat-head"),host=$("prod-lat");if(!host)return;
    list=Array.isArray(list)?list:[];
    if(!list.length){if(head){head.style.display="none";head.innerHTML="";}host.innerHTML="";return;}   // hide gracefully (mirrors health)
    if(head){head.style.display="";head.className="prod-cont-head";head.innerHTML='Latency &amp; uptime <span class="pc-sum">last '+PROD_HIST_APP+' samples</span>';}
    var sorted=list.slice().sort(function(a,b){var ua=prodHealthUp(a)?1:0,ub=prodHealthUp(b)?1:0;   // down-first, like health/containers
      if(ua!==ub)return ua-ub;return String(a.name||a.app_id||"").localeCompare(String(b.name||b.app_id||""));});
    host.innerHTML=sorted.map(function(h){
      var id=String((h.app_id||h.name)||""),buf=prodHist.apps[id]||[],name=h.name||h.app_id||"(unnamed)",upC=prodHealthUp(h);
      var up=prodUptimePct(buf),upTxt=(up==null)?"—":(up>=99.95?"100":up.toFixed(1))+"% up";
      return '<div class="plat '+(upC?"up":"down")+'">'+
        '<div class="plat-top"><span class="plat-name" title="'+esc(String(h.url||name))+'">'+esc(name)+'</span>'+
          '<span class="phz-ms '+prodMsCls(h)+'">'+esc(prodMsText(h))+'</span></div>'+   /* reuse the health latency pill */
        '<div class="plat-spark">'+prodSparkSvg(buf)+'</div>'+
        '<div class="plat-rib" title="up/down over the last '+buf.length+' samples">'+prodRibbonSvg(buf)+'</div>'+
        '<div class="plat-foot"><span class="plat-up '+prodUptimeCls(up)+'">'+upTxt+'</span><span class="plat-cnt">'+buf.length+'/'+PROD_HIST_APP+'</span></div>'+
      '</div>';}).join("");
    hudTipDelegate(host);}   // one delegated wiring covers every app's sparkline + ribbon

  /* --- Panel 2: CPU / MEM / DISK trend area charts (history-driven) --- */
  // usage colour REUSES prodUsageCol (same buckets as the rings): >=85 red, >=70 amber, else cyan.
  function prodAreaSvg(vals,col,tips){var W=200,H=46,pad=2,n=vals.length;if(!n)return '';
    function X(i){return n<=1?W:pad+(W-2*pad)*(i/(n-1));}
    function Y(v){v=Math.max(0,Math.min(100,+v||0));return pad+(H-2*pad)*(1-v/100);}
    var pts=[];for(var i=0;i<n;i++)pts.push([X(i),Y(vals[i])]);
    if(n===1)pts=[[0,Y(vals[0])],[W,Y(vals[0])]];   // single sample → flat line across
    var line=pts.map(function(p,i){return (i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1);}).join(' ');
    var first=pts[0],last=pts[pts.length-1];
    var area='M'+first[0].toFixed(1)+' '+H+' '+pts.map(function(p){return 'L'+p[0].toFixed(1)+' '+p[1].toFixed(1);}).join(' ')+' L'+last[0].toFixed(1)+' '+H+' Z';
    // optional transparent per-sample hover bands (% + time), same pattern as the sparkline
    var bands='';if(tips&&tips.length===n){var bw=(n>1?((W-2*pad)/(n-1)):W);
      for(var j=0;j<n;j++){var tp=tips[j],bx=(n>1?X(j)-bw/2:0);
        var htip='<div class="htr"><span>time</span><b>'+esc(prodWhen(tp.t))+'</b></div>'+
          '<div class="htr"><span>usage</span><b>'+Math.round(tp.v)+'%</b></div>';
        bands+='<rect x="'+Math.max(0,bx).toFixed(1)+'" y="0" width="'+bw.toFixed(1)+'" height="'+H+'" fill="rgba(0,0,0,0)" pointer-events="all"'+hudTipAttr(htip)+'/>';}}
    return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none">'+
      '<line x1="0" y1="'+Y(70).toFixed(1)+'" x2="'+W+'" y2="'+Y(70).toFixed(1)+'" stroke="'+PRC.warn+'" stroke-opacity=".14" stroke-width="1" stroke-dasharray="3 3"/>'+
      '<line x1="0" y1="'+Y(85).toFixed(1)+'" x2="'+W+'" y2="'+Y(85).toFixed(1)+'" stroke="'+PRC.bad+'" stroke-opacity=".16" stroke-width="1" stroke-dasharray="3 3"/>'+
      '<path d="'+area+'" fill="'+col+'" fill-opacity=".16"/>'+
      '<path d="'+line+'" fill="none" stroke="'+col+'" stroke-width="1.6" stroke-linejoin="round"/>'+bands+'</svg>';}
  function prodRenderResHist(){var host=$("prod-reshist");if(!host)return;var res=prodHist.res||[];
    if(!res.length){host.innerHTML='<div class="prh-empty">Collecting resource history&hellip; the trend fills as the monitor polls.</div>';return;}
    host.innerHTML=[["CPU","cpu"],["Memory","mem"],["Disk","disk"]].map(function(s){
      var vals=res.map(function(r){return prodNum(r[s[1]]);}),cur=vals[vals.length-1],col=prodUsageCol(cur);
      var tips=res.map(function(r){return {v:prodNum(r[s[1]]),t:r.t};});
      var lo=Math.round(Math.min.apply(null,vals)),hi=Math.round(Math.max.apply(null,vals));
      return '<div class="prh-card"><div class="prh-top"><span class="prh-lab">'+s[0]+'</span>'+
        '<span class="prh-cur" style="color:'+col+'">'+Math.round(cur)+'%</span></div>'+
        '<div class="prh-chart">'+prodAreaSvg(vals,col,tips)+'</div>'+
        '<div class="prh-foot">'+vals.length+' samples &middot; '+lo+'&ndash;'+hi+'% range</div></div>';}).join("");
    hudTipDelegate(host);}

  /* --- Panel 3: "What needs attention" — DERIVED from the current overview each render --- */
  function prodBuildAlerts(ov){ov=ov||{};var out=[];
    (ov.health||[]).forEach(function(h){if(!prodHealthUp(h))
      out.push({sev:"bad",tag:"app",msg:esc(String(h.name||h.app_id||"app"))+" is down"+(h.http_status?" (HTTP "+esc(String(h.http_status))+")":"")});});
    (ov.containers||[]).forEach(function(c){if(!prodIsUp(c))
      out.push({sev:"bad",tag:"container",msg:esc(String(c.name||"(unnamed)"))+" container is "+esc(String(c.status||"stopped"))});});
    var sys=ov.system||{},di=prodDiskInfo(sys),dp=di.pct,mnt=esc(String(di.mount||"/"));
    if(dp>=85)out.push({sev:"bad",tag:"disk",msg:"Disk "+mnt+" at "+Math.round(dp)+"% &mdash; nearly full"});
    else if(dp>=70)out.push({sev:"warn",tag:"disk",msg:"Disk "+mnt+" at "+Math.round(dp)+"%"});
    var cpu=prodCpuPct(sys);if(cpu>=90)out.push({sev:"warn",tag:"cpu",msg:"CPU load at "+Math.round(cpu)+"%"});
    (ov.health||[]).forEach(function(h){if(prodHealthUp(h)){var ms=+h.response_ms;if(isFinite(ms)&&ms>1000)
      out.push({sev:"warn",tag:"latency",msg:esc(String(h.name||h.app_id||"app"))+" is slow &mdash; "+Math.round(ms)+" ms"});}});
    return out.map(function(a,i){a._i=i;return a;}).sort(function(a,b){   // bad first, stable within severity
      var sa=a.sev==="bad"?0:1,sb=b.sev==="bad"?0:1;return sa!==sb?sa-sb:a._i-b._i;});}
  function prodRenderAlerts(ov){var head=$("prod-alerts-head"),host=$("prod-alerts");if(!host)return;
    var al=prodBuildAlerts(ov);
    if(head)head.innerHTML='What needs attention'+(al.length?' <span class="pc-sum">'+al.length+'</span>':'');
    if(!al.length){host.innerHTML='<div class="pa-clear"><span class="pa-dot" style="background:'+PRC.acc+'"></span>All clear &mdash; every app up, resources nominal.</div>';return;}
    host.innerHTML='<div class="pa-list">'+al.map(function(a){
      return '<div class="pa-row '+a.sev+'"><span class="pa-dot"></span><span class="pa-msg">'+a.msg+'</span><span class="pa-tag">'+esc(a.tag)+'</span></div>';}).join("")+'</div>';}

  /* ---------- TRAFFIC (visits) — ON DEMAND: a heavier query, never on the 8s poll ---------- */
  var prodVisits=null,prodVisitsErr=null,prodVisitsForbidden=false,prodVisitsBusy=false;
  var prodVisitsOpen=false,prodVisitsSite="",prodVisitsLoaded=false;
  function prodInt(n){n=+n;return isFinite(n)?n.toLocaleString("en-US"):"—";}   // grouped, tabular-nums in CSS
  function prodCodeCls(code){code=+code;return code>=500?"bad":code>=400?"warn":(code>=200&&code<300)?"ok":"mut";}
  function prodCodeKind(code){code=+code;return code>=500?"server error":code>=400?"client error":code>=300?"redirect":code>=200?"success":"";}
  function prodTsec(title,inner){return '<div class="prod-tsec"><div class="prod-tsec-h">'+esc(title)+'</div>'+inner+'</div>';}
  function prodHitRows(list,keys){return list.map(function(r){   // keys = candidate label fields, first defined wins (name|country|path…)
      var lbl="";for(var i=0;i<keys.length;i++){if(r[keys[i]]!=null){lbl=String(r[keys[i]]);break;}}
      return '<div class="prod-trow"><span class="lbl" title="'+esc(lbl)+'">'+esc(lbl||"—")+'</span><span class="hits">'+prodInt(r.hits)+'</span></div>';
    }).join("");}
  // site selector: "main" (no param) + every app/container name we already know about
  function prodBuildSiteOptions(){
    var sel=$("prod-traffic-site");if(!sel)return;var ov=prodOverview||{},seen={},order=[];
    function add(n){n=String(n==null?"":n).trim();if(!n||seen[n])return;seen[n]=1;order.push(n);}
    (ov.health||[]).forEach(function(h){add(h&&(h.name||h.app_id));});
    (ov.containers||[]).forEach(function(c){add(c&&c.name);});
    var opts=['<option value="">main (all sites)</option>'];
    order.forEach(function(n){opts.push('<option value="'+esc(n)+'">'+esc(n)+'</option>');});
    sel.innerHTML=opts.join("");sel.value=prodVisitsSite;   // preserve the current pick across rebuilds
  }
  function prodTrafficToggle(){
    prodVisitsOpen=!prodVisitsOpen;
    var tgl=$("prod-traffic-toggle"),body=$("prod-traffic-body"),wrap=$("prod-traffic-sitewrap"),ref=$("prod-traffic-refresh"),globe=$("prod-globe-wrap");
    if(tgl){tgl.classList.toggle("open",prodVisitsOpen);tgl.setAttribute("aria-expanded",prodVisitsOpen?"true":"false");
      tgl.innerHTML=(prodVisitsOpen?"&#9662;":"&#9656;")+" Traffic";}
    if(wrap)wrap.style.display=prodVisitsOpen?"":"none";
    if(ref)ref.style.display=prodVisitsOpen?"":"none";
    if(body)body.style.display=prodVisitsOpen?"":"none";
    if(globe)globe.style.display=prodVisitsOpen?"":"none";
    if(prodVisitsOpen){prodBuildSiteOptions();prodGlobeStart();if(!prodVisitsLoaded)prodFetchVisits();else prodRenderVisits();}
    else prodGlobeStop();   // stop the globe rAF when the panel closes
  }
  function prodFetchVisits(){
    prodVisitsBusy=true;prodVisitsErr=null;prodVisitsForbidden=false;prodRenderVisits();
    var url="/api/prod/visits"+(prodVisitsSite?("?site="+encodeURIComponent(prodVisitsSite)):"");
    gitFetch(url).then(function(res){   // reuse gitFetch: 403 → {forbidden:true} before .json()
      prodVisitsBusy=false;prodVisitsLoaded=true;
      if(res.forbidden){prodVisitsForbidden=true;prodVisits=null;prodRenderVisits();return;}
      if(res.error){prodVisitsErr=res.error;prodVisits=null;prodRenderVisits();return;}
      var d=res.data||{};
      if(d.ok===false){prodVisitsErr=d.error||"Traffic stats unavailable";prodVisits=null;prodRenderVisits();return;}
      prodVisits=d;prodVisitsErr=null;prodRenderVisits();
    });
  }
  function prodRenderVisits(){
    var body=$("prod-traffic-body");if(!body)return;
    if(prodVisitsForbidden){body.innerHTML='<div class="git-note desktop"><b>Traffic is desktop-only</b><span>These stats answer only on the loopback interface (this machine). Open the Live Agent View on the host &mdash; a non-loopback device gets a 403.</span></div>';return;}
    if(prodVisitsErr){body.innerHTML='<div class="git-note err"><b>Traffic unavailable</b><span>'+esc(prodVisitsErr)+'</span></div>';return;}
    if(!prodVisits){body.innerHTML='<div class="prod-loading">Loading traffic&hellip;</div>';return;}
    var v=prodVisits,g=v.general||{};
    var cards=[["Unique visitors",g.unique_visitors,0],["Total requests",g.total_requests,0],
      ["Valid requests",g.valid_requests,0],["Failed requests",g.failed_requests,1]];
    var html='<div class="prod-tstats">'+cards.map(function(c){
      var bad=c[2]&&(+c[1]>0);
      return '<div class="ps-stat'+(bad?" bad":"")+'"><span class="k">'+esc(c[0])+'</span><span class="v">'+prodInt(c[1])+'</span></div>';
    }).join("")+'</div>';
    var secs=[];
    var pages=Array.isArray(v.top_pages)?v.top_pages.slice(0,15):[];
    secs.push(prodTsec("Top pages",pages.length?prodHitRows(pages,["path","page","url"]):'<div class="prod-tempty">No page data.</div>'));
    if(Array.isArray(v.referrers)&&v.referrers.length)
      secs.push(prodTsec("Referrers",prodHitRows(v.referrers.slice(0,15),["referrer","name"])));
    var codes=Array.isArray(v.status_codes)?v.status_codes:[];
    secs.push(prodTsec("Status codes",codes.length?codes.map(function(c){   // 2xx green / 4xx amber / 5xx red
      return '<div class="prod-trow"><span class="prod-code '+prodCodeCls(c.code)+'">'+esc(String(c.code))+'</span>'+
        '<span class="lbl">'+esc(prodCodeKind(c.code))+'</span><span class="hits">'+prodInt(c.hits)+'</span></div>';
    }).join(""):'<div class="prod-tempty">No status data.</div>'));
    if(Array.isArray(v.countries)&&v.countries.length)
      secs.push(prodTsec("Countries",prodHitRows(v.countries.slice(0,15),["country","name"])));
    html+='<div class="prod-tgrid">'+secs.join("")+'</div>';
    body.innerHTML=html;
    if(prodVisitsOpen)prodGlobeStart();   // refresh globe caption / static frame with the new country data
  }
  $("prod-traffic-toggle").onclick=prodTrafficToggle;
  $("prod-traffic-site").onchange=function(){prodVisitsSite=this.value;prodFetchVisits();};
  $("prod-traffic-refresh").onclick=function(){prodFetchVisits();};

  /* ================= LIVE 3D VISUALS (canvas 2D, no libs) =================
     Two perspective-projected scenes bound to the SAME real data the panels use.
     Gating discipline copied from the HUD `draw`: each rAF frame checks `mode`
     (and hidden / traffic-open) at the TOP and BAILS — it does not reschedule —
     so nothing runs on a hidden tab or another view. Reduced-motion draws ONE
     static frame and never loops. DPR + resize handled like the HUD canvas. */

  // Colours pulled from the CSS tokens (so light theme / rebrand carry through) —
  // no hex literals baked into the drawing. hexA() adds alpha to a token hex.
  var PRC={cy:"#38e6ff",warn:"#fbbf24",bad:"#fb7185",acc:"#4ade80",violet:"#a78bfa",dim:"#37506a",mut:"#5f7a95",fg:"#cfe8ff"};
  function prodTokens(){try{var cs=getComputedStyle(document.documentElement);
    function g(n,f){var v=(cs.getPropertyValue(n)||"").trim();return /^#[0-9a-fA-F]{3,8}$/.test(v)?v:f;}
    PRC.cy=g("--cy",PRC.cy);PRC.warn=g("--warn",PRC.warn);PRC.bad=g("--bad",PRC.bad);PRC.acc=g("--acc",PRC.acc);
    PRC.violet=g("--violet",PRC.violet);PRC.dim=g("--hud-dim",PRC.dim);PRC.mut=g("--hud-mut",PRC.mut);PRC.fg=g("--hud-fg",PRC.fg);
  }catch(e){}}
  prodTokens();
  var PMONO="ui-monospace,Menlo,monospace";
  function prodReduced(){return !!(window.matchMedia&&window.matchMedia("(prefers-reduced-motion:reduce)").matches);}

  /* ---------- VPS REACTOR: core = the VPS, orbiting nodes = monitored apps ---------- */
  var prodReactorCV=$("prod-reactor"),prodReactorCtx=prodReactorCV?prodReactorCV.getContext("2d"):null;
  var reW=0,reH=0,reRotY=0,reT=0,prodReactorRunning=false,prodReactorRAF=0;
  var prodReactorNodes=[],prodReactorSlot={};   // slot: stable {ang,lat} per node key, so refresh doesn't reshuffle
  var prodReactorHit=[];   // per-frame projected {x,y,r,data:node} for hover hit-testing (hudTip)
  // Rebuild the node set from the latest overview. health first; containers as fallback.
  function prodReactorSync(){
    var ov=prodOverview||{},list=[],src="";
    var h=Array.isArray(ov.health)?ov.health:[];
    if(h.length){src="health";
      list=h.map(function(x){var up=String((x&&x.status)||"").toLowerCase()==="up";var ms=+(x&&x.response_ms);
        return {key:"h:"+String(x.app_id||x.name||""),label:String(x.name||x.app_id||"app"),up:up,ms:isFinite(ms)?ms:NaN,fixed:false};});
    }else{src="containers";
      var c=Array.isArray(ov.containers)?ov.containers:[];
      list=c.map(function(x){var up=String((x&&x.status)||"").toLowerCase()==="running";
        return {key:"c:"+String(x.name||""),label:String(x.name||"container"),up:up,ms:NaN,fixed:true};});}
    var keys={};list.forEach(function(n){keys[n.key]=1;});
    Object.keys(prodReactorSlot).forEach(function(k){if(!keys[k])delete prodReactorSlot[k];});   // drop stale
    var n=list.length||1;
    list.forEach(function(nd,i){
      if(!prodReactorSlot[nd.key])prodReactorSlot[nd.key]={ang:(i/n)*Math.PI*2,lat:((i+0.5)/n-0.5)*0.9};
      nd.ang=prodReactorSlot[nd.key].ang;nd.lat=prodReactorSlot[nd.key].lat;});
    prodReactorNodes=list;
    var down=list.filter(function(nd){return !nd.up;}).length;
    var sub=$("prod-reactor-sub");
    if(sub)sub.innerHTML=list.length?(list.length+" "+(src==="health"?"app":"container")+(list.length===1?"":"s")+" &middot; "+(down?('<span style="color:'+PRC.bad+'">'+down+" down</span>"):"all up")):"no monitored nodes";
  }
  function prodNodeCol(nd){if(!nd.up)return PRC.bad;if(isFinite(+nd.ms)&&+nd.ms>1000)return PRC.warn;return PRC.cy;}
  function prodOrbitR(nd,rMin,rMax){if(nd.fixed)return (rMin+rMax)/2;      // containers: fixed radius
    if(!nd.up)return rMax;                                                 // down/timeout sits at the outer edge
    var m=+nd.ms;if(!isFinite(m))return (rMin+rMax)/2;
    m=Math.max(0,Math.min(1500,m));return rMin+(rMax-rMin)*(m/1500);}      // fast=close, slow=far
  function prodReactorResize(){var cv=prodReactorCV;if(!cv||!prodReactorCtx)return;var r=cv.getBoundingClientRect();
    reW=r.width;reH=r.height;var w=Math.max(1,Math.round(reW*DPR)),h=Math.max(1,Math.round(reH*DPR));
    if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;}
    prodReactorCtx.setTransform(DPR,0,0,DPR,0,0);}
  function prodReactorDraw(){
    prodReactorHit.length=0;   // rebuilt this frame; empty on any early bail
    var ctx=prodReactorCtx;if(!ctx)return;var W=reW,H=reH;if(W<2||H<2)return;
    ctx.clearRect(0,0,W,H);
    var cx=W/2,cy=H/2,R0=Math.min(W,H),FOV=R0*2.4,rotX=0.5;
    function proj(x,y,z){var cA=Math.cos(reRotY),sA=Math.sin(reRotY);
      var X=x*cA-z*sA,Z=x*sA+z*cA;var cB=Math.cos(rotX),sB=Math.sin(rotX);
      var Y2=y*cB-Z*sB,Z2=y*sB+Z*cB;var d=FOV/(FOV+Z2);return {x:cx+X*d,y:cy+Y2*d,z:Z2,d:d};}
    var rMin=R0*0.16,rMax=R0*0.33,core=proj(0,0,0);
    var pts=prodReactorNodes.map(function(nd){var r=prodOrbitR(nd,rMin,rMax),a=nd.ang,lat=nd.lat;
      var p=proj(r*Math.cos(a)*Math.cos(lat),r*Math.sin(lat),r*Math.sin(a)*Math.cos(lat));return {nd:nd,p:p};});
    var far2near=pts.slice().sort(function(a,b){return b.p.z-a.p.z;});   // draw far first
    // beams core -> node
    far2near.forEach(function(o){var col=prodNodeCol(o.nd);ctx.beginPath();ctx.moveTo(core.x,core.y);ctx.lineTo(o.p.x,o.p.y);
      ctx.strokeStyle=hexA(col,0.10+0.16*o.p.d);ctx.lineWidth=1;ctx.stroke();});
    // reactor core = the VPS
    var gp=0.6+0.4*Math.sin(reT*3.0),coreR=R0*0.05;
    var grd=ctx.createRadialGradient(core.x,core.y,0,core.x,core.y,coreR*5);
    grd.addColorStop(0,hexA(PRC.cy,0.5));grd.addColorStop(0.4,hexA(PRC.cy,0.12));grd.addColorStop(1,hexA(PRC.cy,0));
    ctx.fillStyle=grd;ctx.beginPath();ctx.arc(core.x,core.y,coreR*5,0,7);ctx.fill();
    ctx.beginPath();ctx.arc(core.x,core.y,coreR*1.7,0,7);ctx.strokeStyle=hexA(PRC.cy,0.3+0.2*gp);ctx.lineWidth=1.3;ctx.stroke();
    ctx.beginPath();ctx.arc(core.x,core.y,coreR*(0.95+0.08*gp),0,7);ctx.fillStyle=hexA(PRC.fg,0.95);
    ctx.shadowBlur=22;ctx.shadowColor=PRC.cy;ctx.fill();ctx.shadowBlur=0;
    ctx.font="bold 9px "+PMONO;ctx.fillStyle=hexA(PRC.cy,0.92);ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText("VPS",core.x,core.y);
    // nodes far -> near
    far2near.forEach(function(o){var nd=o.nd,p=o.p,col=prodNodeCol(nd),down=!nd.up,rad=(down?5.5:4.5)*(0.7+0.5*p.d);
      prodReactorHit.push({x:p.x,y:p.y,r:Math.max(10,rad+6),data:nd});
      if(down){var b=0.5+0.5*Math.sin(reT*18);ctx.shadowBlur=10+16*b;}else{ctx.shadowBlur=8;}ctx.shadowColor=col;
      ctx.beginPath();ctx.arc(p.x,p.y,rad,0,7);ctx.fillStyle=col;ctx.fill();ctx.shadowBlur=0;
      ctx.beginPath();ctx.arc(p.x,p.y,rad+4,0,7);ctx.strokeStyle=hexA(col,0.3*p.d);ctx.lineWidth=1;ctx.stroke();
      ctx.font="10px "+PMONO;ctx.fillStyle=hexA(down?col:PRC.fg,0.5+0.4*p.d);ctx.textAlign="center";ctx.textBaseline="top";
      ctx.fillText(nd.label,p.x,p.y+rad+3);});
  }
  function prodReactorFrame(){
    if(mode!=="prod"||document.hidden){prodReactorRunning=false;prodReactorRAF=0;return;}   // bail — no reschedule
    reRotY+=0.0035;reT+=0.0038;                    // slowed ~35% from the preview (0.00524 rot / 0.006 t)
    prodReactorDraw();prodReactorRAF=requestAnimationFrame(prodReactorFrame);}
  function prodReactorStart(){
    if(!prodReactorCV||mode!=="prod")return;
    prodReactorResize();
    if(prodReduced()){prodReactorDraw();return;}   // reduced motion: one static frame, no loop
    if(prodReactorRunning)return;                  // already looping — keep it smooth, don't restart
    prodReactorRunning=true;prodReactorRAF=requestAnimationFrame(prodReactorFrame);}

  /* ---------- HOLO GLOBE: visit origins as arcs from home (Serbia) ---------- */
  // Compact lat/lon table (~60 common origins, lowercased keys + aliases). Unknown → skipped.
  var PROD_GEO={
    "serbia":[44,20],"rs":[44,20],"montenegro":[42.7,19.3],"me":[42.7,19.3],"croatia":[45.1,15.2],"hr":[45.1,15.2],
    "bosnia and herzegovina":[44,18],"bosnia":[44,18],"ba":[44,18],"north macedonia":[41.6,21.7],"macedonia":[41.6,21.7],"mk":[41.6,21.7],
    "slovenia":[46.1,14.8],"si":[46.1,14.8],"albania":[41,20],"al":[41,20],
    "germany":[51,10],"de":[51,10],"austria":[47.5,14.5],"at":[47.5,14.5],"france":[46,2],"fr":[46,2],
    "united kingdom":[54,-2],"uk":[54,-2],"great britain":[54,-2],"gb":[54,-2],"netherlands":[52.3,5.5],"nl":[52.3,5.5],
    "italy":[42.8,12.8],"it":[42.8,12.8],"spain":[40,-4],"es":[40,-4],"portugal":[39.5,-8],"pt":[39.5,-8],
    "switzerland":[46.8,8.2],"ch":[46.8,8.2],"belgium":[50.6,4.6],"be":[50.6,4.6],"ireland":[53,-8],"ie":[53,-8],
    "sweden":[62,15],"se":[62,15],"norway":[62,10],"no":[62,10],"denmark":[56,10],"dk":[56,10],"finland":[64,26],"fi":[64,26],
    "poland":[52,19],"pl":[52,19],"czechia":[49.8,15.5],"czech republic":[49.8,15.5],"cz":[49.8,15.5],
    "slovakia":[48.7,19.7],"sk":[48.7,19.7],"hungary":[47,20],"hu":[47,20],"romania":[46,25],"ro":[46,25],
    "bulgaria":[42.7,25.5],"bg":[42.7,25.5],"greece":[39,22],"gr":[39,22],"turkey":[39,35],"tr":[39,35],
    "ukraine":[49,32],"ua":[49,32],"russia":[61,90],"russian federation":[61,90],"ru":[61,90],"belarus":[53,28],"by":[53,28],
    "moldova":[47,28],"md":[47,28],"lithuania":[55,24],"lt":[55,24],"latvia":[57,25],"lv":[57,25],"estonia":[59,26],"ee":[59,26],
    "luxembourg":[49.8,6.1],"cyprus":[35,33],"malta":[35.9,14.4],
    "united states":[38,-97],"united states of america":[38,-97],"usa":[38,-97],"us":[38,-97],
    "canada":[56,-106],"ca":[56,-106],"mexico":[23,-102],"mx":[23,-102],"brazil":[-10,-52],"br":[-10,-52],
    "argentina":[-34,-64],"ar":[-34,-64],"china":[35,105],"cn":[35,105],"japan":[36,138],"jp":[36,138],
    "south korea":[36.5,128],"korea, republic of":[36.5,128],"republic of korea":[36.5,128],"kr":[36.5,128],
    "india":[22,79],"in":[22,79],"indonesia":[-2,118],"id":[-2,118],"singapore":[1.3,103.8],"sg":[1.3,103.8],
    "thailand":[15,101],"th":[15,101],"vietnam":[16,106],"vn":[16,106],"philippines":[13,122],"ph":[13,122],
    "australia":[-25,133],"au":[-25,133],"new zealand":[-41,174],"nz":[-41,174],"south africa":[-29,24],"za":[-29,24],
    "egypt":[26,30],"eg":[26,30],"nigeria":[10,8],"ng":[10,8],"israel":[31,35],"il":[31,35],
    "saudi arabia":[24,45],"sa":[24,45],"united arab emirates":[24,54],"uae":[24,54],"ae":[24,54],"iran":[32,53],"ir":[32,53]};
  var PROD_BOT={"russia":1,"russian federation":1,"ru":1,"china":1,"cn":1,"vietnam":1,"vn":1,"iran":1,"ir":1,"north korea":1,"kp":1};
  var PROD_HOME=[44,20];   // Serbia
  var prodGlobeCV=$("prod-globe"),prodGlobeCtx=prodGlobeCV?prodGlobeCV.getContext("2d"):null;
  var glW=0,glH=0,glRotY=0,prodGlobeRunning=false,prodGlobeRAF=0;
  var prodGlobeHit=[];   // per-frame projected {x,y,r,data} for front-facing origins + home (hudTip)
  function prodGlobeResize(){var cv=prodGlobeCV;if(!cv||!prodGlobeCtx)return;var r=cv.getBoundingClientRect();
    glW=r.width;glH=r.height;var w=Math.max(1,Math.round(glW*DPR)),h=Math.max(1,Math.round(glH*DPR));
    if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;}
    prodGlobeCtx.setTransform(DPR,0,0,DPR,0,0);}
  function prodGlobeCaption(){var cap=$("prod-globe-cap");if(!cap)return;
    var cs=(prodVisits&&Array.isArray(prodVisits.countries))?prodVisits.countries:[];
    var mapped=0;cs.forEach(function(c){if(PROD_GEO[String(c.country||c.name||"").toLowerCase().trim()])mapped++;});
    cap.innerHTML="&#127760; Visitor origins"+(cs.length?(" &middot; "+mapped+"/"+cs.length+" located"):"");}
  function prodGlobeDraw(){
    prodGlobeHit.length=0;   // rebuilt this frame; empty on any early bail
    var ctx=prodGlobeCtx;if(!ctx)return;var W=glW,H=glH;if(W<2||H<2)return;
    ctx.clearRect(0,0,W,H);
    var cx=W/2,cy=H/2,R=Math.min(W,H)*0.36,FOV=R*3.4,rotX=0.42;
    function proj(x,y,z){var cA=Math.cos(glRotY),sA=Math.sin(glRotY);
      var X=x*cA-z*sA,Z=x*sA+z*cA;var cB=Math.cos(rotX),sB=Math.sin(rotX);
      var Y2=y*cB-Z*sB,Z2=y*sB+Z*cB;var d=FOV/(FOV+Z2);return {x:cx+X*d,y:cy+Y2*d,z:Z2,d:d};}
    function sph(lat,lon){var la=lat*Math.PI/180,lo=lon*Math.PI/180;
      return {x:R*Math.cos(la)*Math.cos(lo),y:R*Math.sin(la),z:R*Math.cos(la)*Math.sin(lo)};}
    // limb + fill
    var lg=ctx.createRadialGradient(cx-R*0.3,cy-R*0.3,R*0.1,cx,cy,R);
    lg.addColorStop(0,hexA(PRC.violet,0.10));lg.addColorStop(1,hexA(PRC.cy,0.02));
    ctx.fillStyle=lg;ctx.beginPath();ctx.arc(cx,cy,R,0,7);ctx.fill();
    ctx.beginPath();ctx.arc(cx,cy,R,0,7);ctx.strokeStyle=hexA(PRC.violet,0.28);ctx.lineWidth=1;ctx.stroke();
    // wireframe — FRONT FACE ONLY (z>0). parallels + meridians.
    ctx.strokeStyle=hexA(PRC.violet,0.16);ctx.lineWidth=1;
    var la2,lo2,pv,started;
    for(la2=-60;la2<=60;la2+=30){started=false;ctx.beginPath();
      for(lo2=0;lo2<=360;lo2+=6){var s=sph(la2,lo2),p=proj(s.x,s.y,s.z);
        if(p.z>0){if(started)ctx.lineTo(p.x,p.y);else{ctx.moveTo(p.x,p.y);started=true;}}else started=false;}
      ctx.stroke();}
    for(lo2=0;lo2<360;lo2+=30){started=false;ctx.beginPath();
      for(la2=-90;la2<=90;la2+=6){var s2=sph(la2,lo2),p2=proj(s2.x,s2.y,s2.z);
        if(p2.z>0){if(started)ctx.lineTo(p2.x,p2.y);else{ctx.moveTo(p2.x,p2.y);started=true;}}else started=false;}
      ctx.stroke();}
    // arcs + points for located countries
    function unit(lat,lon){var la=lat*Math.PI/180,lo=lon*Math.PI/180;return [Math.cos(la)*Math.cos(lo),Math.sin(la),Math.cos(la)*Math.sin(lo)];}
    function drawArc(dst,col){var a=unit(PROD_HOME[0],PROD_HOME[1]),b=unit(dst[0],dst[1]);
      var dot=Math.max(-1,Math.min(1,a[0]*b[0]+a[1]*b[1]+a[2]*b[2])),om=Math.acos(dot),so=Math.sin(om);
      ctx.beginPath();var moved=false;
      for(var t=0;t<=1.0001;t+=1/28){var s1,s2;
        if(om<1e-3||so<1e-6){s1=1-t;s2=t;}else{s1=Math.sin((1-t)*om)/so;s2=Math.sin(t*om)/so;}
        var vx=a[0]*s1+b[0]*s2,vy=a[1]*s1+b[1]*s2,vz=a[2]*s1+b[2]*s2;
        var lift=R*(1+0.35*Math.sin(Math.PI*t)),p=proj(vx*lift,vy*lift,vz*lift);
        if(p.z>0){if(moved)ctx.lineTo(p.x,p.y);else{ctx.moveTo(p.x,p.y);moved=true;}}else moved=false;}
      ctx.strokeStyle=col;ctx.lineWidth=1.2;ctx.stroke();}
    var cs=(prodVisits&&Array.isArray(prodVisits.countries))?prodVisits.countries:[];
    var maxHits=1;cs.forEach(function(c){var h=+c.hits;if(isFinite(h)&&h>maxHits)maxHits=h;});
    cs.forEach(function(c){var key=String(c.country||c.name||"").toLowerCase().trim(),ll=PROD_GEO[key];if(!ll)return;
      var bot=!!PROD_BOT[key],ac=bot?hexA(PRC.bad,0.5):hexA(PRC.cy,0.4);
      drawArc(ll,ac);
      var s3=sph(ll[0],ll[1]),p3=proj(s3.x,s3.y,s3.z);
      if(p3.z>0){var hits=+c.hits||0,rad=2+4*Math.sqrt(hits/maxHits),col=bot?PRC.bad:PRC.cy;
        ctx.shadowBlur=10;ctx.shadowColor=col;ctx.beginPath();ctx.arc(p3.x,p3.y,rad,0,7);ctx.fillStyle=col;ctx.fill();ctx.shadowBlur=0;
        prodGlobeHit.push({x:p3.x,y:p3.y,r:Math.max(9,rad+5),data:{name:c.country||c.name||key,hits:hits,bot:bot}});}});
    // home marker
    var hs=sph(PROD_HOME[0],PROD_HOME[1]),hp=proj(hs.x,hs.y,hs.z);
    if(hp.z>0){var hb=0.5+0.5*Math.sin(glRotY*4);ctx.beginPath();ctx.arc(hp.x,hp.y,3.4,0,7);ctx.fillStyle=PRC.violet;
      ctx.shadowBlur=12;ctx.shadowColor=PRC.violet;ctx.fill();ctx.shadowBlur=0;
      ctx.beginPath();ctx.arc(hp.x,hp.y,5+hb*4,0,7);ctx.strokeStyle=hexA(PRC.violet,(1-hb)*0.7);ctx.lineWidth=1.3;ctx.stroke();
      prodGlobeHit.push({x:hp.x,y:hp.y,r:10,data:{name:"Serbia",home:true}});}
  }
  function prodGlobeFrame(){
    if(mode!=="prod"||document.hidden||!prodVisitsOpen){prodGlobeRunning=false;prodGlobeRAF=0;return;}   // bail — no reschedule
    glRotY+=0.0026;                                // slowed ~35% from the preview (0.004)
    prodGlobeDraw();prodGlobeRAF=requestAnimationFrame(prodGlobeFrame);}
  function prodGlobeStart(){
    if(!prodGlobeCV||mode!=="prod"||!prodVisitsOpen)return;
    prodGlobeResize();prodGlobeCaption();
    if(prodReduced()){prodGlobeDraw();return;}     // reduced motion: one static frame, no loop
    if(prodGlobeRunning)return;
    prodGlobeRunning=true;prodGlobeRAF=requestAnimationFrame(prodGlobeFrame);}
  function prodGlobeStop(){prodGlobeRunning=false;if(prodGlobeRAF)cancelAnimationFrame(prodGlobeRAF);prodGlobeRAF=0;}

  // restart loops when the tab becomes visible again (they self-bail when hidden)
  document.addEventListener("visibilitychange",function(){if(document.hidden||mode!=="prod")return;
    prodReactorStart();if(prodVisitsOpen)prodGlobeStart();});
  // resize both prod canvases (only meaningful while on the Production view)
  addEventListener("resize",function(){if(mode!=="prod")return;
    if(prodReactorCV){prodReactorResize();if(!prodReactorRunning)prodReactorDraw();}
    if(prodVisitsOpen&&prodGlobeCV){prodGlobeResize();if(!prodGlobeRunning)prodGlobeDraw();}});

  /* ---- hover tooltips for the two prod canvas scenes (shared core.js hudTip; read the per-frame
       hit arrays the draws populate — passive readers, they never touch the rAF loops) ---- */
  function prodReactorTipHtml(nd){
    var h='<span class="htk">'+esc(nd.label||"")+'</span>'+
      '<div class="htr"><span>status</span><span class="'+(nd.up?"up":"down")+'">'+(nd.up?"up":"down")+'</span></div>';
    var ms=+nd.ms;
    if(isFinite(ms)){h+=(ms>1000)?('<div class="htr"><span>latency</span><span class="warn">'+Math.round(ms)+' ms</span></div>')
      :('<div class="htr"><span>latency</span><b>'+Math.round(ms)+' ms</b></div>');}
    else if(!nd.fixed)h+='<div class="htr"><span>latency</span><b>'+(nd.up?"—":"timeout")+'</b></div>';
    return h;}
  function prodGlobeTipHtml(d){
    if(d.home)return '<span class="htk">'+esc(d.name)+'</span><div class="htsub">home &middot; origin</div>';
    return '<span class="htk">'+esc(String(d.name))+(d.bot?' <span class="down">bot</span>':"")+'</span>'+
      '<div class="htr"><span>visits</span><b>'+prodInt(d.hits)+'</b></div>';}
  hudTipCanvas(prodReactorCV,function(){return prodReactorHit;},prodReactorTipHtml);
  hudTipCanvas(prodGlobeCV,function(){return prodGlobeHit;},prodGlobeTipHtml);

