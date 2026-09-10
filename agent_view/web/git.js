  /* ---------- GIT view (repo DAG + metrics + branches/Actions/PRs from /api/git/*) ---------- */
  var gitRepos=[],gitSel=null,gitRepo=null,gitGithub=null,gitForbidden=false,gitReposErr=null;
  // sidebar hide/collapse (persisted): hidden → left sidebar removed, repo list in a bottom strip.
  var gitSideHidden=(function(){try{return localStorage.getItem("gitSideHidden")==="1";}catch(e){return false;}})();
  var gitStripOpen=false;          // the bottom repo strip's body expanded (only meaningful while hidden)
  // Pinned ("watched") repos persist in localStorage as a JSON array of paths. The live 4s poll
  // fetches git status ONLY for pinned ∪ selected (the server also adds any active-session repo),
  // so 25 repos don't all run git every 4 seconds. localStorage is the single source of truth for
  // pins — the server's `pinned` field is informational and never overrides the local set.
  var gitPins=(function(){try{var a=JSON.parse(localStorage.getItem("gitPins")||"[]");var m={};
    (Array.isArray(a)?a:[]).forEach(function(p){if(p)m[p]=true;});return m;}catch(e){return {};}})();
  function gitSavePins(){try{localStorage.setItem("gitPins",JSON.stringify(Object.keys(gitPins)));}catch(e){}}
  function gitIsPinned(p){return !!gitPins[p];}
  function gitTogglePin(p){if(gitPins[p])delete gitPins[p];else gitPins[p]=true;gitSavePins();gitRenderSidebar();}
  // watch set = pinned ∪ selected; each path url-encoded, joined by literal commas so the server can
  // split(",") then decode each. The selected repo is always watched so it stays fresh.
  function gitWatchList(){var m={};Object.keys(gitPins).forEach(function(p){m[p]=true;});if(gitSel)m[gitSel]=true;return Object.keys(m);}
  function gitWatchParam(){return gitWatchList().map(encodeURIComponent).join(",");}
  // a repo row carries git status only when active/watched; unwatched poll rows have base fields only
  function gitRepoHasStatus(r){return !!r&&(r.branch!==undefined||r.dirty!==undefined||r.conflicts!==undefined);}
  // merge a cheap watch-poll (watched=fresh status, unwatched=base fields only) into the cached full
  // list BY PATH: for a repo the poll did NOT refresh, keep its last-known status so it never blanks.
  function gitMergeRepos(cached,incoming){
    var prev={};cached.forEach(function(r){prev[r.path]=r;});
    return incoming.map(function(r){
      if(gitRepoHasStatus(r))return r;                    // fresh status from the poll → take it
      var old=prev[r.path];if(!old)return r;              // brand-new repo we have no cache for
      var merged={};for(var k in old)merged[k]=old[k];    // keep cached status fields (branch/dirty/…)
      ["path","name","active","pinned"].forEach(function(f){if(r[f]!==undefined)merged[f]=r[f];});
      return merged;                                      // base fields fresh, status kept from cache
    });
  }
  // lane palette — same HUD hues used elsewhere in this file (hex is the house style in JS here)
  var GIT_LANES=["#38e6ff","#a78bfa","#4ade80","#fbbf24","#f472b6","#38bdf8","#2dd4bf","#fb7185"];
  function gitLaneCol(c){c=(+c||0);return GIT_LANES[((c%GIT_LANES.length)+GIT_LANES.length)%GIT_LANES.length];}
  var GIT_BRANCH_SVG='<svg class="gi" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="4" cy="4" r="1.6"/><circle cx="4" cy="12" r="1.6"/><circle cx="12" cy="6" r="1.6"/><path d="M4 5.6v4.8M4 8h4a2 2 0 0 0 2-2V7.6"/></svg>';
  // one fetch helper: 403 (loopback-only endpoint hit from a non-loopback device) is a
  // distinct state from any other error, so callers can show the "desktop-only" note.

  // ONCE per tab-open: full=1 pulls status for ALL repos so the initial picture is complete.
  // Preload: show the last-known repo list (with its recorded status) INSTANTLY on
  // tab open from localStorage, then let the live fetch refresh it — opening the tab
  // is never an empty "loading" flash; live updating starts only once it's open.
  function gitSaveSnap(){try{localStorage.setItem("av_git_snap",JSON.stringify(gitRepos));}catch(e){}}
  function gitLoadSnap(){try{var a=JSON.parse(localStorage.getItem("av_git_snap")||"[]");return Array.isArray(a)?a:[];}catch(e){return [];}}
  function gitInit(){
    if(!gitRepos.length){var s=gitLoadSnap();if(s.length){gitRepos=s;
      if(!gitSel){var a=gitRepos.filter(function(r){return r.active;})[0]||gitRepos[0];gitSel=a?a.path:null;}
      gitRender();}}
    gitApplySideState();                             // apply the persisted sidebar-hidden state on open
    gitFetchHeatAll();                               // aggregate heatmap (all repos), independent of selection
    gitFetchRepos(false,true);if(gitSel){gitFetchRepo(gitSel,true);gitFetchGithub(gitSel,true);gitFetchInsights(gitSel);}}

  // full=true → /api/git/repos?full=1 (all repos WITH status; tab-open only).
  // full=false → /api/git/repos?watch=<pinned∪selected> (cheap: status only for watched+active; the
  //   4s poll and the manual refresh use this). Unwatched rows come back base-only and are merged so
  //   they keep their last-known status.
  function gitFetchRepos(silent,full){
    var url=full?"/api/git/repos?full=1":("/api/git/repos?watch="+gitWatchParam());
    gitFetch(url).then(function(res){
      if(res.forbidden){gitForbidden=true;gitRender();return;}
      gitForbidden=false;
      if(res.error){gitReposErr=res.error;gitRender();return;}
      gitReposErr=null;
      var incoming=(res.data&&res.data.repos)||[];
      gitRepos=full?incoming:gitMergeRepos(gitRepos,incoming);
      gitSaveSnap();                                   // record for instant preload on next open
      // keep the current selection if it still exists; otherwise pick the ACTIVE repo, else the first
      if(!gitSel||!gitRepos.some(function(r){return r.path===gitSel;})){
        var act=gitRepos.filter(function(r){return r.active;})[0]||gitRepos[0];
        gitSel=act?act.path:null;gitRepo=null;gitGithub=null;
        if(gitSel){gitFetchRepo(gitSel,false);gitFetchGithub(gitSel,false);gitFetchInsights(gitSel);}
      }
      if(silent&&gitRepos.length){gitRenderSidebar();}   // live glow refresh only, don't disturb detail
      else gitRender();
    });
  }
  function gitFetchRepo(path,silent){
    gitFetch("/api/git/repo?path="+encodeURIComponent(path)).then(function(res){
      if(path!==gitSel)return;   // selection moved on while this was in flight
      if(res.forbidden){gitForbidden=true;gitRender();return;}
      if(res.error){gitRepo={__error:res.error};gitRenderDetail();return;}
      gitRepo=res.data||{};gitRenderDetail();
    });
  }
  function gitFetchGithub(path,silent){
    gitFetch("/api/git/github?path="+encodeURIComponent(path)).then(function(res){
      if(path!==gitSel)return;
      if(res.forbidden){gitForbidden=true;gitRender();return;}
      if(res.error){gitGithub={__error:res.error};gitRenderPanels();gitRenderMetrics();return;}
      gitGithub=res.data||{};gitRenderPanels();gitRenderMetrics();   // CI ring + PR count live in the metrics band
    });
  }

  // top-level state machine: desktop-only 403 / repos error / no repos / normal
  function gitRender(){
    var note=$("git-note"),shell=$("git-shell");if(!note||!shell)return;
    if(gitForbidden){note.style.display="";shell.style.display="none";note.className="git-note desktop";
      note.innerHTML='<b>Git view is desktop-only</b><span>The Git endpoints answer only on the loopback interface (this machine). Open the Live Agent View directly on the host to use it.</span>';
      $("git-sub").innerHTML="";return;}
    if(gitReposErr&&!gitRepos.length){note.style.display="";shell.style.display="none";note.className="git-note err";
      note.innerHTML='<b>Could not load repositories</b><span>'+esc(gitReposErr)+'</span>';$("git-sub").innerHTML="";return;}
    if(!gitRepos.length){note.style.display="";shell.style.display="none";note.className="git-note";
      note.innerHTML='<b>No repos found</b><span>Add <code>git_roots</code> or <code>git_repos</code> to the config.</span>';$("git-sub").innerHTML="";return;}
    note.style.display="none";shell.style.display="";
    gitRenderSidebar();gitRenderMetrics();gitRenderGraph();gitRenderPanels();gitRenderSub();gitRenderInsights();
  }
  function gitRenderSub(){var s=$("git-sub");if(!s)return;
    if(!gitRepo||gitRepo.__error){s.innerHTML="";return;}
    s.innerHTML='<b>'+esc(gitRepo.name||"")+'</b> &middot; '+esc(gitRepo.branch||"")+
      (gitRepo.head?(' &middot; '+esc(String(gitRepo.head).slice(0,8))):"")+
      ' &middot; '+((gitRepo.commits||[]).length)+' commits';}
  // one repo card — status fields are absent for unwatched poll rows; after a merge they're kept
  // from cache, but a brand-new repo seen only in a cheap poll has none yet — show no chip then.
  function gitRepoCardHtml(r){
    var pinned=gitIsPinned(r.path);
    var cls="git-repo"+(r.path===gitSel?" sel":"")+(r.active?" active":"")+(pinned?" pinned":"");
    var chip=!gitRepoHasStatus(r)?""
      :(r.conflicts>0)?'<span class="gr-chip conflict">conflict</span>'
      :(r.dirty?'<span class="gr-chip dirty">dirty</span>':'<span class="gr-chip clean">clean</span>');
    var ab=(r.ahead||r.behind)?('<span class="gr-ab"><span class="up">&#8593;'+(+r.ahead||0)+'</span> <span class="down">&#8595;'+(+r.behind||0)+'</span></span>'):"";
    var pin='<button class="gr-pin'+(pinned?" on":"")+'" data-pin="'+esc(r.path)+'" type="button" aria-pressed="'+(pinned?"true":"false")+'" title="'+(pinned?"Watching live &mdash; click to stop":"Watch live (refresh every 4s)")+'">'+(pinned?"★":"☆")+'</button>';
    return '<div class="'+cls+'" data-path="'+esc(r.path)+'" title="'+esc(r.path)+'">'+
      '<div class="gr-top">'+(r.active?'<span class="gr-active-dot"></span>':"")+
        '<span class="gr-name">'+esc(r.name||r.path)+'</span>'+(r.active?'<span class="gr-activetag">ACTIVE</span>':"")+pin+'</div>'+
      '<div class="gr-branch">'+GIT_BRANCH_SVG+esc(r.branch||"—")+'</div>'+
      '<div class="gr-meta">'+chip+ab+'</div>'+
    '</div>';
  }
  // wire pin toggles + repo-select on ONE host (the sidebar OR the collapsed bottom strip — both
  // render the same cards, so selecting a repo works from either).
  function gitWireRepoHost(host){
    Array.prototype.forEach.call(host.querySelectorAll(".gr-pin"),function(el){
      el.onclick=function(ev){ev.stopPropagation();gitTogglePin(el.getAttribute("data-pin"));};   // watch without selecting
    });
    Array.prototype.forEach.call(host.querySelectorAll(".git-repo"),function(el){
      el.onclick=function(){var p=el.getAttribute("data-path");if(p===gitSel)return;
        gitSel=p;gitRepo=null;gitGithub=null;
        gitRenderSidebar();gitRenderMetrics();gitRenderGraph();gitRenderPanels();gitRenderSub();gitRenderInsights();
        gitFetchRepo(p,false);gitFetchGithub(p,false);gitFetchInsights(p);};
    });
  }
  function gitRenderSidebar(){
    var html=gitRepos.map(gitRepoCardHtml).join("");
    ["git-repos","git-repos-strip"].forEach(function(id){var h=$(id);if(!h)return;h.innerHTML=html;gitWireRepoHost(h);});
    var c1=$("git-repocount"),c2=$("git-repocount-strip");
    if(c1)c1.textContent=gitRepos.length;if(c2)c2.textContent=gitRepos.length;
  }
  // sidebar hide/collapse — when hidden the left sidebar is removed (insights get the full width)
  // and the repo list moves into the collapsible strip at the very bottom. State persists in LS.
  function gitApplySideState(){
    var shell=$("git-shell");if(shell)shell.classList.toggle("side-hidden",gitSideHidden);
    var sbody=$("git-strip-body"),stog=$("git-strip-toggle"),open=gitSideHidden&&gitStripOpen;
    if(sbody)sbody.style.display=open?"":"none";
    if(stog){stog.setAttribute("aria-expanded",open?"true":"false");
      stog.innerHTML=(open?"&#9662;":"&#9656;")+" Repositories";}
    // the main column just changed width → re-fit the three canvases (the heatmap is CSS-driven and reflows itself)
    if(mode==="git"){
      if(gitOrbitCV){gitOrbitResize();if(!gitOrbitRunning)gitOrbitDraw();}
      if(gitTerrCV){gitTerrainResize();if(!gitTerrRunning)gitTerrainDraw();}
      if(gitT2CV){gitTerrain2Resize();if(!gitT2Running)gitTerrain2Draw();}
    }
  }
  // ---- metrics band: commits7d + sparkline, CI ring (github only), churn, PRs, branches ----
  function gitSparkline(arr){
    arr=(arr||[]).map(function(n){return +n||0;});if(arr.length<2)return "";
    var w=120,h=30,pad=2,max=Math.max.apply(null,arr),min=Math.min.apply(null,arr),span=(max-min)||1;
    var pts=arr.map(function(v,i){var x=pad+i*((w-2*pad)/(arr.length-1));var y=h-pad-((v-min)/span)*(h-2*pad);return x.toFixed(1)+","+y.toFixed(1);});
    return '<svg class="gm-spark" viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" width="100%" height="30">'+
      '<path d="M'+pts[0]+' L'+pts.join(" ")+' L'+(w-pad)+','+(h-pad)+' L'+pad+','+(h-pad)+' Z" fill="rgba(56,230,255,.12)"/>'+
      '<polyline points="'+pts.join(" ")+'" fill="none" stroke="#38e6ff" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/></svg>';}
  function gitRing(pct){
    pct=Math.max(0,Math.min(100,+pct||0));var r=22,c=2*Math.PI*r,off=c*(1-pct/100);
    var col=pct>=80?"#4ade80":pct>=50?"#fbbf24":"#fb7185";
    return '<svg viewBox="0 0 60 60" width="60" height="60" class="gm-ring">'+
      '<circle cx="30" cy="30" r="'+r+'" fill="none" stroke="rgba(56,189,248,.14)" stroke-width="6"/>'+
      '<circle cx="30" cy="30" r="'+r+'" fill="none" stroke="'+col+'" stroke-width="6" stroke-linecap="round" stroke-dasharray="'+c.toFixed(1)+'" stroke-dashoffset="'+off.toFixed(1)+'" transform="rotate(-90 30 30)"/>'+
      '<text x="30" y="34" text-anchor="middle" font-size="13" fill="'+col+'">'+Math.round(pct)+'%</text></svg>';}
  function gitGithubReady(){return gitGithub&&!gitGithub.__error&&gitGithub.enabled!==false;}
  function gitRenderMetrics(){
    var host=$("git-metrics");if(!host)return;
    if(!gitRepo||gitRepo.__error||!gitRepo.metrics){host.innerHTML="";return;}
    var m=gitRepo.metrics,cards=[];
    cards.push('<div class="gm-card"><div class="gm-lab">Commits &middot; 7d</div><div class="gm-val">'+(+m.commits7d||0)+'</div>'+gitSparkline(m.daily14)+'<div class="gm-sub">14-day activity</div></div>');
    if(gitGithubReady()&&(gitGithub.runs||[]).length){
      var runs=gitGithub.runs,done=runs.filter(function(r){var s=String(r.status||"").toLowerCase();return s==="success"||s==="failure";});
      var ok=runs.filter(function(r){return String(r.status||"").toLowerCase()==="success";}).length;
      var pct=done.length?(ok/done.length*100):0;
      cards.push('<div class="gm-card gm-ci"><div class="gm-lab">CI pass rate</div><div class="gm-ringwrap">'+gitRing(pct)+'</div><div class="gm-sub">'+ok+'/'+done.length+' passing</div></div>');
    }
    cards.push('<div class="gm-card"><div class="gm-lab">Churn &middot; 7d</div><div class="gm-val"><span class="gm-add">+'+(+m.churnAdd||0)+'</span> <span class="gm-del">&#8722;'+(+m.churnDel||0)+'</span></div><div class="gm-sub">lines changed</div></div>');
    if(gitGithubReady())cards.push('<div class="gm-card"><div class="gm-lab">Open PRs</div><div class="gm-val">'+((gitGithub.prs||[]).length)+'</div><div class="gm-sub">pull requests</div></div>');
    cards.push('<div class="gm-card"><div class="gm-lab">Branches</div><div class="gm-val">'+(+m.branches||(gitRepo.branches||[]).length||0)+'</div><div class="gm-sub">'+(+m.contributors||0)+' contributors</div></div>');
    host.innerHTML=cards.join("");
  }
  // ---- commit graph: render-ready col/row/parents from the backend, drawn as SVG ----
  function gitRenderGraph(){
    var host=$("git-graph-scroll");if(!host)return;
    if(!gitRepo){host.innerHTML='<div class="git-loading">Loading repository&hellip;</div>';return;}
    if(gitRepo.__error){host.innerHTML='<div class="git-note-inline">Could not load repo: '+esc(gitRepo.__error)+'</div>';return;}
    var commits=gitRepo.commits||[];
    if(!commits.length){host.innerHTML='<div class="git-empty">No commits in this repository.</div>';return;}
    var cols=commits.map(function(c){return +c.col||0;}),rows=commits.map(function(c){return +c.row||0;});
    var maxCol=(gitRepo.maxCol!=null)?gitRepo.maxCol:Math.max.apply(null,cols);
    var maxRow=Math.max.apply(null,rows);
    var LANE_W=24,ROW_H=34,X0=22,Y0=26,NODE_R=6;
    function lx(c){return X0+(+c||0)*LANE_W;}
    function ry(r){return Y0+(+r||0)*ROW_H;}
    var byHash={};commits.forEach(function(c){byHash[c.hash]=c;});
    var headCol=null;commits.forEach(function(c){if(c.isHead)headCol=+c.col||0;});
    var graphW=lx(maxCol)+LANE_W,labelX=graphW+14,H=ry(maxRow)+Y0;
    var maxChars=0;commits.forEach(function(c){var s=(c.short||"")+"  "+(c.subject||"")+"  "+((c.refs||[]).join(" "));if(s.length>maxChars)maxChars=s.length;});
    var W=Math.max(labelX+maxChars*7.1+20,graphW+240);
    var svg="";
    // lane rails per column (faint); the active branch lane is brighter + flowing-dash
    for(var col=0;col<=maxCol;col++){var active=(col===headCol);
      svg+='<line x1="'+lx(col)+'" y1="'+(Y0-6)+'" x2="'+lx(col)+'" y2="'+(H-Y0+6)+'" stroke="'+gitLaneCol(col)+'" stroke-width="'+(active?2:1.4)+'" opacity="'+(active?0.5:0.14)+'"'+(active?' class="git-lane-active"':"")+'/>';}
    // edges: each commit down to each parent (curved across lanes), coloured by the parent lane
    commits.forEach(function(c){var x1=lx(c.col),y1=ry(c.row);
      (c.parents||[]).forEach(function(p){var pcol=(p.col!=null)?p.col:(byHash[p.hash]?byHash[p.hash].col:c.col);
        var pc=byHash[p.hash],prow=pc?pc.row:((+c.row||0)+1);var x2=lx(pcol),y2=ry(prow),col=gitLaneCol(pcol);
        if(x1===x2)svg+='<path d="M'+x1+' '+y1+' L'+x2+' '+y2+'" stroke="'+col+'" stroke-width="2" fill="none" opacity=".85"/>';
        else{var my=(y1+y2)/2;svg+='<path d="M'+x1+' '+y1+' C'+x1+' '+my+' '+x2+' '+my+' '+x2+' '+y2+'" stroke="'+col+'" stroke-width="2" fill="none" opacity=".85"/>';}});});
    // nodes + labels (short hash + refs + subject); HEAD gets a pulsing halo
    commits.forEach(function(c){var x=lx(c.col),y=ry(c.row),col=gitLaneCol(c.col);
      if(c.isHead)svg+='<circle cx="'+x+'" cy="'+y+'" r="'+(NODE_R+3)+'" fill="none" stroke="'+col+'" stroke-width="1.5" class="git-head-halo"/>';
      svg+='<circle cx="'+x+'" cy="'+y+'" r="'+NODE_R+'" fill="'+col+'" stroke="#05080d" stroke-width="1.5"'+(c.isHead?' class="git-node-head"':"")+'/>';
      var lbl='<text x="'+labelX+'" y="'+(y+4)+'"><tspan class="git-lbl-hash" fill="#38e6ff">'+esc(c.short||"")+'</tspan> ';
      (c.refs||[]).forEach(function(rf){lbl+='<tspan fill="#a78bfa">'+esc(rf)+'</tspan> ';});
      lbl+='<tspan fill="#cfe8ff">'+esc(c.subject||"")+'</tspan></text>';
      svg+=lbl;});
    host.innerHTML='<svg id="git-graph" width="'+Math.ceil(W)+'" height="'+Math.ceil(H)+'" viewBox="0 0 '+Math.ceil(W)+' '+Math.ceil(H)+'">'+svg+'</svg>';
    var gh=$("git-graph-h");if(gh)gh.innerHTML=GIT_BRANCH_SVG+' Commit graph &middot; '+esc(gitRepo.branch||"");
  }
  // ---- right panels: branches / Actions / PRs / conflicts ----
  function gitRenderPanels(){var host=$("git-panels");if(!host)return;
    host.innerHTML=gitBranchesPanel()+gitActionsPanel()+gitPrsPanel()+gitConflictsPanel();}
  function gitBranchesPanel(){
    var b=(gitRepo&&!gitRepo.__error&&gitRepo.branches)||[];
    var body=(!gitRepo||gitRepo.__error)?'<div class="gp-empty">&mdash;</div>':(b.length?b.map(function(x){
      var ab=(x.ahead||x.behind)?('<span class="gb-ab">&#8593;'+(+x.ahead||0)+' &#8595;'+(+x.behind||0)+'</span>'):"";
      return '<div class="gb-row'+(x.current?" cur":"")+'"><span class="gb-name">'+(x.current?"&#9679; ":"")+esc(x.name||"")+'</span>'+ab+'</div>';
    }).join(""):'<div class="gp-empty">No branches</div>');
    return '<div class="git-panel"><div class="gp-h">'+GIT_BRANCH_SVG+' Branches<span class="cnt">'+b.length+'</span></div><div class="gp-body">'+body+'</div></div>';}
  function gitActionsPanel(){
    var g=gitGithub,inner,cnt="";
    if(!g)inner='<div class="gp-note">Loading&hellip;</div>';
    else if(g.__error)inner='<div class="gp-note">'+esc(g.__error)+'</div>';
    else if(g.enabled===false)inner='<div class="gp-note">'+esc(g.reason||"GitHub not connected")+'</div>';
    else{var runs=g.runs||[];cnt='<span class="cnt">'+runs.length+'</span>';
      inner=runs.length?runs.map(function(r){var st=String(r.status||"").toLowerCase();
        var ico=st==="success"?'<span class="ga-ico success">&#10003;</span>'
          :st==="failure"?'<span class="ga-ico failure">&#10007;</span>'
          :'<span class="ga-ico in_progress"><span class="git-spin"></span></span>';
        return '<div class="ga-row">'+ico+'<span class="ga-name">'+esc(r.name||"")+'</span><span class="ga-when">'+esc(r.when||"")+'</span></div>';
      }).join(""):'<div class="gp-empty">No recent runs</div>';}
    return '<div class="git-panel"><div class="gp-h">&#9881; GitHub Actions'+cnt+'</div><div class="gp-body">'+inner+'</div></div>';}
  function gitPrsPanel(){
    var g=gitGithub,inner,cnt="";
    if(!g)inner='<div class="gp-note">Loading&hellip;</div>';
    else if(g.__error)inner='<div class="gp-note">'+esc(g.__error)+'</div>';
    else if(g.enabled===false)inner='<div class="gp-note">'+esc(g.reason||"GitHub not connected")+'</div>';
    else{var prs=g.prs||[];cnt='<span class="cnt">'+prs.length+'</span>';
      inner=prs.length?prs.map(function(p){var ck=String(p.checks||"").toLowerCase();
        var badge=ck?('<span class="gpr-checks '+(ck==="success"||ck==="failure"?ck:"pending")+'">'+esc(p.checks)+'</span>'):"";
        return '<div class="gpr-row"><div class="gpr-top"><span class="gpr-num">#'+esc(String(p.number==null?"":p.number))+'</span><span class="gpr-title">'+esc(p.title||"")+'</span></div>'+
          '<div class="gpr-meta"><span class="gpr-add">+'+(+p.additions||0)+'</span><span class="gpr-del">&#8722;'+(+p.deletions||0)+'</span>'+badge+'</div></div>';
      }).join(""):'<div class="gp-empty">No open pull requests</div>';}
    return '<div class="git-panel"><div class="gp-h">&#9903; Pull requests'+cnt+'</div><div class="gp-body">'+inner+'</div></div>';}
  function gitConflictsPanel(){
    var confl=gitRepo&&!gitRepo.__error&&gitRepo.status&&gitRepo.status.conflicts;
    var arr=Array.isArray(confl)?confl:[],n=Array.isArray(confl)?confl.length:(+confl||0),inner;
    if(!gitRepo||gitRepo.__error)inner='<div class="gp-empty">&mdash;</div>';
    else if(!n)inner='<div class="gc-clean">&#10003; Working tree clean &mdash; no conflicts</div>';
    else inner=arr.length?arr.map(function(f){return '<div class="gc-row">&#9888; '+esc(String(f))+'</div>';}).join("")
      :'<div class="gc-row">&#9888; '+n+' conflicted file'+(n===1?"":"s")+'</div>';
    return '<div class="git-panel"><div class="gp-h">&#9888; Conflicts'+(n?'<span class="cnt">'+n+'</span>':"")+'</div><div class="gp-body">'+inner+'</div></div>';}
  function gitRenderDetail(){gitRenderMetrics();gitRenderGraph();gitRenderPanels();gitRenderSub();gitRenderInsights();}
  $("git-refresh").onclick=function(){gitFetchHeatAll();gitFetchRepos(true);if(gitSel){gitFetchRepo(gitSel,false);gitFetchGithub(gitSel,false);gitFetchInsights(gitSel);}};

  /* ============= GIT INSIGHTS: 3D orbital + 3D terrain + 2D analytics panels =============
     Insights come from a SEPARATE ~60s server-cached endpoint. Fetched only on SELECT and
     manual refresh — never on the 4s repos poll. Keyed by repo path: undefined = not fetched
     yet (Loading), an object with empty arrays = a quiet repo (empty state), {__error} = failed.
     The two canvases reuse the Production reactor/globe machinery (PRC tokens, hexA, DPR,
     prodReduced) and copy its gating: every rAF frame bails at the TOP when the view is not
     git / the tab is hidden / the section is collapsed, and never reschedules from that state. */
  var gitInsights={};              // path -> {heatmap,contributors,churn,hotspots,filetypes} | {__error}
  var gitInsOpen=true;             // Insights section expanded; canvases only loop while open
  // The commit-heatmap panel is an AGGREGATE across ALL known repos, independent of the
  // selected repo — fetched in gitInit + on manual refresh (never on repo-select or the 4s
  // poll). Shape: {repos:[{name,color,path}], days:{'YYYY-MM-DD':{'<repo>':count,...}}}.
  var gitHeatAll=null;             // null=not fetched (Loading); {__error} = failed
  function gitFetchHeatAll(){
    gitFetch("/api/git/heatmap-all").then(function(res){
      if(res.forbidden){gitForbidden=true;gitRender();return;}
      if(res.error){gitHeatAll={__error:res.error};gitRenderHeatmap();return;}
      gitHeatAll=res.data||{};gitRenderHeatmap();
    });
  }
  function gitInsData(){return gitSel?gitInsights[gitSel]:null;}
  function gitFetchInsights(path){
    if(!path)return;
    gitFetch("/api/git/insights?path="+encodeURIComponent(path)).then(function(res){
      if(path!==gitSel)return;                       // selection moved on while this was in flight
      if(res.forbidden){gitForbidden=true;gitRender();return;}
      if(res.error){gitInsights[path]={__error:res.error};gitRenderInsights();return;}
      gitInsights[path]=res.data||{};
      gitRenderInsights();
    });
  }
  // shared weekday-aligned calendar builder (used by BOTH the 2D heatmap and the 3D terrain).
  // Columns = weeks (a new column starts on Sunday); rows 0..6 = Sun..Sat; missing days = null.
  function gitCalYmd(dt){return dt.getFullYear()+"-"+("0"+(dt.getMonth()+1)).slice(-2)+"-"+("0"+dt.getDate()).slice(-2);}
  // GitHub-style DENSE calendar: a fixed trailing window of full weeks ending today,
  // EVERY day present (count 0 when no commit), columns = Sunday-started weeks, rows =
  // weekday 0..6. The heatmap feed is SPARSE (commit-days only, with gaps), so we must
  // densify here by walking the dates — otherwise empty weeks vanish and days from
  // different weeks collapse into one column, which is what broke the layout before.
  // the bare trailing-window GRID: columns = Sunday-started weeks, each an array of
  // 7 ISO date strings (or null for a slot outside the window). Shared by the 2D
  // heatmap, the aggregate heatmap and both 3D terrains, so the calendar geometry
  // is defined in exactly one place.
  function gitCalGrid(){
    var WEEKS=26;
    var today=new Date();today.setHours(0,0,0,0);
    var dt=new Date(today);dt.setDate(dt.getDate()-(WEEKS*7-1));
    dt.setDate(dt.getDate()-dt.getDay());                      // back up to the Sunday
    var cols=[],cur=null;
    while(dt<=today){
      var wd=dt.getDay();
      if(wd===0||!cur){cur=[null,null,null,null,null,null,null];cols.push(cur);}
      cur[wd]=gitCalYmd(dt);
      dt.setDate(dt.getDate()+1);
    }
    return cols;
  }
  // map a SPARSE per-repo [{date,count}] feed onto the shared grid → columns of
  // {date,count}|null (kept for the two 3D terrains, which read .date/.count).
  function gitCalWeeks(hm){
    var map={};(hm||[]).forEach(function(d){map[String(d.date)]=+d.count||0;});
    return gitCalGrid().map(function(col){return col.map(function(iso){
      return iso?{date:iso,count:map[iso]||0}:null;});});
  }
  function gitInsCaption(){
    var sub=$("git-ins-sub");if(!sub)return;
    if(!gitSel){sub.innerHTML="";return;}
    var d=gitInsData();
    if(d&&d.__error){sub.innerHTML='<span style="color:'+PRC.bad+'">'+esc(d.__error)+'</span>';return;}
    if(!d){sub.innerHTML="loading&hellip;";return;}
    var commits=(gitRepo&&!gitRepo.__error&&gitRepo.commits)?gitRepo.commits.length:0;
    var contrib=(d.contributors||[]).length,hs=(d.hotspots||[]).length;
    sub.innerHTML=commits+" commits &middot; "+contrib+" contributor"+(contrib===1?"":"s")+" &middot; "+hs+" hotspot"+(hs===1?"":"s");
  }
  // master render — each source (commits / insights) independently empty-states if absent
  function gitRenderInsights(){
    gitInsCaption();
    gitOrbitSync();gitOrbitStart();                // orbital reads gitRepo.commits
    gitTerrainSync();gitTerrainStart();            // terrain (shipped quad-mesh) reads insights.heatmap
    gitTerrain2Sync();gitTerrain2Start();          // relief field (original preview) reads the SAME heatmap
    gitRenderHeatmap();gitRenderContributors();gitRenderFiletypes();gitRenderChurn();gitRenderHotspots();
  }
  // collapse toggle: frames self-bail while collapsed; re-arm both loops on expand
  (function(){var t=$("git-ins-toggle");if(!t)return;
    t.onclick=function(){gitInsOpen=!gitInsOpen;
      var wrap=$("git-insights");if(wrap)wrap.classList.toggle("collapsed",!gitInsOpen);
      t.setAttribute("aria-expanded",gitInsOpen?"true":"false");
      t.innerHTML=(gitInsOpen?"&#9662;":"&#9656;")+" Insights";
      if(gitInsOpen){gitOrbitSync();gitOrbitStart();gitTerrainSync();gitTerrainStart();gitTerrain2Sync();gitTerrain2Start();}};})();
  // secondary (commit-graph DAG + branch/PR panels) collapse — purely cosmetic, no rAF involved
  (function(){var t=$("git-graph-toggle");if(!t)return;var open=true;
    t.onclick=function(){open=!open;var wrap=$("git-secondary");if(wrap)wrap.classList.toggle("collapsed",!open);
      t.setAttribute("aria-expanded",open?"true":"false");
      t.innerHTML=(open?"&#9662;":"&#9656;")+" Commit graph &amp; branches";};})();
  // sidebar hide / show-sidebar / bottom-strip collapse
  (function(){var hide=$("git-side-hide"),show=$("git-side-show"),strip=$("git-strip-toggle");
    function setHidden(h){gitSideHidden=h;try{localStorage.setItem("gitSideHidden",h?"1":"0");}catch(e){}
      if(h)gitStripOpen=true;                         // reveal the strip expanded the moment you hide
      gitApplySideState();}
    if(hide)hide.onclick=function(){setHidden(true);};
    if(show)show.onclick=function(){setHidden(false);};
    if(strip)strip.onclick=function(){gitStripOpen=!gitStripOpen;gitApplySideState();};})();

  /* ---- 2D: commit heatmap (flat GitHub-style calendar grid, cyan intensity) ---- */
  function gitRenderHeatmap(){
    var host=$("git-heatmap"),cnt=$("git-heat-cnt");if(!host)return;
    var d=gitHeatAll;                                // AGGREGATE across all repos, not the selected one
    if(!d){host.innerHTML='<div class="gin-empty">Loading&hellip;</div>';if(cnt)cnt.textContent="";return;}
    if(d.__error){host.innerHTML='<div class="gin-empty">'+esc(d.__error)+'</div>';if(cnt)cnt.textContent="";return;}
    var days=d.days||{},repoList=d.repos||[];
    var colOf={};repoList.forEach(function(r){colOf[r.name]=r.color;});   // repo → its assigned hue
    // per-repo totals over the window drive the legend + header count
    var totals={},grand=0;
    for(var iso0 in days){var b0=days[iso0];for(var k0 in b0){totals[k0]=(totals[k0]||0)+b0[k0];grand+=b0[k0];}}
    var active=repoList.filter(function(r){return (totals[r.name]||0)>0;})
      .sort(function(a,b){return (totals[b.name]||0)-(totals[a.name]||0);});
    if(!grand){host.innerHTML='<div class="gin-empty">No recent activity across any repo</div>';if(cnt)cnt.textContent="";return;}
    if(cnt)cnt.textContent=grand+" commits · "+active.length+" repo"+(active.length===1?"":"s");
    // dense trailing-window calendar; per day: INTENSITY = total commits, HUE = the dominant repo
    var cols=gitCalGrid(),max=1;
    cols.forEach(function(col){col.forEach(function(iso){if(iso){var br=days[iso];if(br){var t=0;for(var k in br)t+=br[k];if(t>max)max=t;}}});});
    var MON=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    var months="",prevM=-1;
    cols.forEach(function(col){var first=null;for(var i=0;i<7;i++){if(col[i]){first=col[i];break;}}
      var m=first?(+first.slice(5,7)-1):-1;
      months+='<span>'+((m>=0&&m!==prevM)?MON[m]:"")+'</span>';if(m>=0)prevM=m;});
    var cells="";
    cols.forEach(function(col){
      for(var r=0;r<7;r++){var iso=col[r];
        if(!iso){cells+='<div class="gin-cell empty"></div>';continue;}
        var br=days[iso]||{},entries=[],total=0;
        for(var rk in br){entries.push([rk,br[rk]]);total+=br[rk];}
        entries.sort(function(a,b){return b[1]-a[1];});           // dominant repo first
        var hue=entries.length?(colOf[entries[0][0]]||"#38e6ff"):"#38e6ff";
        var bg=total?hexA(hue,(0.18+0.82*(total/max))):'rgba(56,189,248,.06)';
        var htip='<span class="htk">'+esc(iso)+'</span>';
        entries.slice(0,10).forEach(function(e){
          htip+='<div class="htr"><span><i style="display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;vertical-align:-1px;background:'+esc(colOf[e[0]]||"#38e6ff")+'"></i>'+esc(e[0])+'</span><b>'+e[1]+'</b></div>';});
        if(entries.length>10)htip+='<div class="htr"><span>+'+(entries.length-10)+' more</span><b></b></div>';
        htip+='<div class="htr htr-tot"><span>total</span><b>'+total+'</b></div>';
        cells+='<div class="gin-cell"'+hudTipAttr(htip)+' style="background:'+bg+'"></div>';}
    });
    // N stretchy columns (minmax(0,1fr)) → the calendar fills the panel edge-to-edge; the months
    // header shares the SAME template so its labels stay aligned with the columns below.
    var gt='grid-template-columns:repeat('+cols.length+',minmax(0,1fr))';
    var legend=active.map(function(r){
      var htip='<span class="htk">'+esc(r.name)+'</span><div class="htr"><span>commits</span><b>'+(totals[r.name]||0)+'</b></div>';
      return '<span class="gin-rlg"'+hudTipAttr(htip)+'><i style="background:'+esc(r.color)+'"></i>'+esc(r.name)+' <b>'+(totals[r.name]||0)+'</b></span>';}).join("");
    var html='<div class="gin-hm"><div class="gin-hm-scroll">'+
      '<div class="gin-hm-months" style="'+gt+'">'+months+'</div>'+
      '<div class="gin-hm-main"><div class="gin-hm-wd">'+
        '<span></span><span>Mon</span><span></span><span>Wed</span><span></span><span>Fri</span><span></span></div>'+
      '<div class="gin-cal" style="'+gt+'">'+cells+'</div></div></div>'+
      '<div class="gin-repolegend">'+legend+'</div>'+
      '<div class="gin-callegend">less'+
      [0.12,0.35,0.6,0.85,1].map(function(a){return '<i style="background:rgba(120,140,160,'+a+')"></i>';}).join("")+'more</div></div>';
    host.innerHTML=html;hudTipDelegate(host);
  }
  /* ---- 2D: contributors bar leaderboard ---- */
  function gitRenderContributors(){
    var host=$("git-contributors"),cnt=$("git-contrib-cnt");if(!host)return;
    var d=gitInsData();
    if(!d){host.innerHTML='<div class="gin-empty">Loading&hellip;</div>';if(cnt)cnt.textContent="";return;}
    var cs=((d.contributors)||[]).slice().sort(function(a,b){return (+b.commits||0)-(+a.commits||0);});
    if(!cs.length){host.innerHTML='<div class="gin-empty">No contributors</div>';if(cnt)cnt.textContent="";return;}
    if(cnt)cnt.textContent=cs.length;
    var max=Math.max.apply(null,cs.map(function(c){return +c.commits||0;}))||1;
    host.innerHTML=cs.slice(0,12).map(function(c){var v=+c.commits||0,pct=Math.max(3,v/max*100);
      var htip='<span class="htk">'+esc(c.name||"—")+'</span><div class="htr"><span>commits</span><b>'+v+'</b></div>';
      return '<div class="gin-bar"'+hudTipAttr(htip)+'><span class="gin-bar-name">'+esc(c.name||"—")+'</span>'+
        '<span class="gin-bar-track"><span class="gin-bar-fill" style="width:'+pct.toFixed(1)+'%"></span></span>'+
        '<span class="gin-bar-val">'+v+'</span></div>';}).join("");
    hudTipDelegate(host);
  }
  /* ---- 2D: code churn (SVG area chart, +add green above / -del red below the zero line) ---- */
  function gitRenderChurn(){
    var host=$("git-churn");if(!host)return;
    var d=gitInsData();
    if(!d){host.innerHTML='<div class="gin-empty">Loading&hellip;</div>';return;}
    var ch=(d.churn)||[];
    if(!ch.length){host.innerHTML='<div class="gin-empty">No recent activity</div>';return;}
    var W=320,H=120,pad=8,mid=H/2,n=ch.length,max=1;
    ch.forEach(function(w){max=Math.max(max,+w.add||0,+w.del||0);});
    function x(i){return n>1?pad+i*((W-2*pad)/(n-1)):W/2;}
    var addPts=ch.map(function(w,i){return x(i)+","+(mid-(+w.add||0)/max*(mid-pad)).toFixed(1);});
    var delPts=ch.map(function(w,i){return x(i)+","+(mid+(+w.del||0)/max*(mid-pad)).toFixed(1);});
    var endX=(n>1?(W-pad):W/2);
    var addArea='M'+pad+','+mid+' L'+addPts.join(" L")+' L'+endX+','+mid+' Z';
    var delArea='M'+pad+','+mid+' L'+delPts.join(" L")+' L'+endX+','+mid+' Z';
    var tAdd=ch.reduce(function(s,w){return s+(+w.add||0);},0),tDel=ch.reduce(function(s,w){return s+(+w.del||0);},0);
    // transparent per-week hover bands (drawn last, so on top; pointer-events:all captures the whole column)
    var bw=(n>1?((W-2*pad)/(n-1)):W),bands='';
    for(var bi=0;bi<n;bi++){var wk=ch[bi],bx=x(bi)-bw/2;
      var htip='<span class="htk">'+esc(String(wk.week||("week "+(bi+1))))+'</span>'+
        '<div class="htr"><span>added</span><span class="up">+'+(+wk.add||0)+'</span></div>'+
        '<div class="htr"><span>removed</span><span class="down">&#8722;'+(+wk.del||0)+'</span></div>';
      bands+='<rect x="'+Math.max(0,bx).toFixed(1)+'" y="0" width="'+bw.toFixed(1)+'" height="'+H+'" fill="rgba(0,0,0,0)" pointer-events="all"'+hudTipAttr(htip)+'/>';}
    host.innerHTML='<svg class="gin-svg" viewBox="0 0 '+W+' '+H+'">'+
      '<line x1="'+pad+'" y1="'+mid+'" x2="'+(W-pad)+'" y2="'+mid+'" stroke="rgba(95,122,149,.4)" stroke-width="1" stroke-dasharray="3 3"/>'+
      '<path d="'+addArea+'" fill="rgba(74,222,128,.18)" stroke="#4ade80" stroke-width="1.4"/>'+
      '<path d="'+delArea+'" fill="rgba(251,113,133,.16)" stroke="'+PRC.bad+'" stroke-width="1.4"/>'+
      bands+'</svg>'+
      '<div class="gin-legend"><span><span class="sw" style="background:#4ade80"></span>+'+tAdd+' added</span>'+
      '<span><span class="sw" style="background:'+PRC.bad+'"></span>&#8722;'+tDel+' removed</span>'+
      '<span style="margin-left:auto;color:var(--hud-dim)">'+n+' week'+(n===1?"":"s")+'</span></div>';
    hudTipDelegate(host);
  }
  /* ---- 2D: hotspots treemap (recursive bisection along the longer axis; size = changes) ---- */
  function gitTreemapLayout(items,x,y,w,h,out){
    if(!items.length)return;
    if(items.length===1){out.push({x:x,y:y,w:w,h:h,it:items[0]});return;}
    var total=items.reduce(function(s,i){return s+i.value;},0),half=total/2,acc=0,i=0;
    for(;i<items.length-1;i++){if(acc+items[i].value>half&&i>0)break;acc+=items[i].value;}
    var a=items.slice(0,i),b=items.slice(i),af=(a.reduce(function(s,it){return s+it.value;},0))/(total||1);
    if(w>=h){var wa=w*af;gitTreemapLayout(a,x,y,wa,h,out);gitTreemapLayout(b,x+wa,y,w-wa,h,out);}
    else{var ha=h*af;gitTreemapLayout(a,x,y,w,ha,out);gitTreemapLayout(b,x,y+ha,w,h-ha,out);}
  }
  function gitRenderHotspots(){
    var host=$("git-hotspots"),cnt=$("git-hot-cnt");if(!host)return;
    var d=gitInsData();
    if(!d){host.innerHTML='<div class="gin-empty">Loading&hellip;</div>';if(cnt)cnt.textContent="";return;}
    var hs=((d.hotspots)||[]).map(function(x){return {path:String(x.path||""),value:+x.changes||0};})
      .filter(function(x){return x.value>0;}).sort(function(a,b){return b.value-a.value;}).slice(0,24);
    if(!hs.length){host.innerHTML='<div class="gin-empty">No recent file changes</div>';if(cnt)cnt.textContent="";return;}
    if(cnt)cnt.textContent=hs.length;
    var out=[];gitTreemapLayout(hs,0,0,100,100,out);var max=hs[0].value||1;
    host.innerHTML='<div class="gin-tree">'+out.map(function(o){var it=o.it,base=it.path.split("/").pop()||it.path;
      var a=(0.2+0.6*(it.value/max)).toFixed(3),big=(o.w>16&&o.h>16);
      var htip='<span class="htk">'+esc(base)+'</span><div class="htsub">'+esc(it.path)+'</div>'+
        '<div class="htr"><span>changes</span><b>'+it.value+'</b></div>';
      return '<div class="gin-tile"'+hudTipAttr(htip)+' '+
        'style="left:'+o.x.toFixed(2)+'%;top:'+o.y.toFixed(2)+'%;width:'+o.w.toFixed(2)+'%;height:'+o.h.toFixed(2)+'%;background:rgba(56,230,255,'+a+')">'+
        (big?('<span class="gin-tile-name">'+esc(base)+'</span><span class="gin-tile-val">'+it.value+' &Delta;</span>'):"")+'</div>';
    }).join("")+'</div>';
    hudTipDelegate(host);
  }
  /* ---- 2D: file types donut (SVG, dasharray-on-circle segments; lane palette) ---- */
  function gitRenderFiletypes(){
    var host=$("git-filetypes"),cnt=$("git-ft-cnt");if(!host)return;
    var d=gitInsData();
    if(!d){host.innerHTML='<div class="gin-empty">Loading&hellip;</div>';if(cnt)cnt.textContent="";return;}
    var ft=((d.filetypes)||[]).slice().sort(function(a,b){return (+b.count||0)-(+a.count||0);});
    if(!ft.length){host.innerHTML='<div class="gin-empty">No files</div>';if(cnt)cnt.textContent="";return;}
    if(cnt)cnt.textContent=ft.length;
    var total=ft.reduce(function(s,x){return s+(+x.count||0);},0)||1,top=ft.slice(0,8);
    var shown=top.reduce(function(s,x){return s+(+x.count||0);},0);
    if(shown<total)top=top.concat([{ext:"other",count:total-shown}]);
    var R=52,cx=64,cy=64,T=16,C=2*Math.PI*R,acc=0;
    // dasharray segments have fill:none, so only the painted STROKE arc captures hover (SVG
    // visiblePainted) — each circle is hoverable over exactly its own arc, not the whole ring.
    function ftTip(x){var pct=Math.round((+x.count||0)/total*100);
      return '<span class="htk">'+esc(x.ext||"?")+'</span><div class="htr"><span>files</span><b>'+(+x.count||0)+'</b></div>'+
        '<div class="htr"><span>share</span><b>'+pct+'%</b></div>';}
    var segs=top.map(function(x,i){var len=((+x.count||0)/total)*C,col=gitLaneCol(i);
      var s='<circle cx="'+cx+'" cy="'+cy+'" r="'+R+'" fill="none" stroke="'+col+'" stroke-width="'+T+'" '+
        'stroke-dasharray="'+len.toFixed(2)+' '+(C-len).toFixed(2)+'" stroke-dashoffset="'+(-acc).toFixed(2)+'" transform="rotate(-90 '+cx+' '+cy+')"'+hudTipAttr(ftTip(x))+'/>';
      acc+=len;return s;}).join("");
    host.innerHTML='<div class="gin-donutwrap"><svg viewBox="0 0 128 128" width="128" height="128" style="flex:none">'+segs+
      '<text x="64" y="61" text-anchor="middle" font-size="20" font-weight="700" fill="'+PRC.fg+'">'+total+'</text>'+
      '<text x="64" y="78" text-anchor="middle" font-size="9" fill="'+PRC.dim+'" letter-spacing="1">FILES</text></svg>'+
      '<div class="gin-legend">'+top.map(function(x,i){var pct=Math.round((+x.count||0)/total*100);
        return '<span'+hudTipAttr(ftTip(x))+'><span class="sw" style="background:'+gitLaneCol(i)+'"></span>'+esc(x.ext||"?")+' &middot; '+(+x.count||0)+' ('+pct+'%)</span>';
      }).join("")+'</div></div>';
    hudTipDelegate(host);
  }

  /* ---------- 3D REPO ORBITAL: core = the repo, commits orbit in tilted rings by lane ----------
     Same perspective machinery as the Production reactor (proj/rotY/rotX/core). Rings are grouped
     by commit lane (col); the HEAD node is brightest with a pulsing halo. A persistent slot map
     keyed by commit hash keeps angular positions stable across data refreshes (no reshuffle). */
  var gitOrbitCV=$("git-orbit"),gitOrbitCtx=gitOrbitCV?gitOrbitCV.getContext("2d"):null;
  var goW=0,goH=0,goRotY=0,goT=0,gitOrbitRunning=false,gitOrbitRAF=0;
  var gitOrbitNodes=[],gitOrbitRings=1,gitOrbitSlot={};   // slot: stable {ang} per commit hash
  var gitOrbitHit=[];   // per-frame projected {x,y,r,data:node} for hover hit-testing (hudTip)
  function gitOrbitHasData(){return !!(gitRepo&&!gitRepo.__error&&(gitRepo.commits||[]).length);}
  function gitOrbitSync(){
    var commits=(gitRepo&&!gitRepo.__error&&gitRepo.commits)?gitRepo.commits:[];
    var recent=commits.slice().sort(function(a,b){return (+a.row||0)-(+b.row||0);}).slice(0,60);   // newest 60
    var colset={};recent.forEach(function(c){colset[+c.col||0]=1;});
    var ringCols=Object.keys(colset).map(Number).sort(function(a,b){return a-b;});
    var ringIdx={};ringCols.forEach(function(c,i){ringIdx[c]=i;});
    var rings=ringCols.length||1;
    var perRingCount={},perRingSeen={};
    recent.forEach(function(c){var col=+c.col||0;perRingCount[col]=(perRingCount[col]||0)+1;});
    var keys={};
    gitOrbitNodes=recent.map(function(c){var col=+c.col||0,k=String(c.hash||c.short||"");
      keys[k]=1;var seen=(perRingSeen[col]=(perRingSeen[col]||0)+1)-1,grp=perRingCount[col]||1;
      if(!gitOrbitSlot[k])gitOrbitSlot[k]={ang:(seen/grp)*Math.PI*2};
      return {key:k,col:col,ring:ringIdx[col],isHead:!!c.isHead,label:String(c.short||"").slice(0,7),ang:gitOrbitSlot[k].ang,
        short:String(c.short||""),subject:String(c.subject||""),author:String(c.author||""),date:String(c.date||"")};});
    Object.keys(gitOrbitSlot).forEach(function(k){if(!keys[k])delete gitOrbitSlot[k];});   // prune stale slots
    gitOrbitRings=rings;
    var cap=$("git-orbit-cap");
    if(cap){var name=(gitRepo&&gitRepo.name)?gitRepo.name:"";
      cap.innerHTML=gitOrbitNodes.length?(esc(name)+" &middot; "+gitOrbitNodes.length+" commits &middot; "+rings+" lane"+(rings===1?"":"s")):"no commits";}
  }
  function gitOrbitResize(){var cv=gitOrbitCV;if(!cv||!gitOrbitCtx)return;var r=cv.getBoundingClientRect();
    goW=r.width;goH=r.height;var w=Math.max(1,Math.round(goW*DPR)),h=Math.max(1,Math.round(goH*DPR));
    if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;}gitOrbitCtx.setTransform(DPR,0,0,DPR,0,0);}
  function gitOrbitDraw(){
    gitOrbitHit.length=0;   // rebuilt this frame; stays empty on any early bail so no stale hits
    var ctx=gitOrbitCtx;if(!ctx)return;var W=goW,H=goH;if(W<2||H<2)return;
    ctx.clearRect(0,0,W,H);
    var cx=W/2,cy=H/2,R0=Math.min(W,H),FOV=R0*2.4,rotX=0.52,rings=Math.max(1,gitOrbitRings);
    function proj(x,y,z){var cA=Math.cos(goRotY),sA=Math.sin(goRotY);
      var X=x*cA-z*sA,Z=x*sA+z*cA;var cB=Math.cos(rotX),sB=Math.sin(rotX);
      var Y2=y*cB-Z*sB,Z2=y*sB+Z*cB;var dd=FOV/(FOV+Z2);return {x:cx+X*dd,y:cy+Y2*dd,z:Z2,d:dd};}
    var rMin=R0*0.17,rMax=R0*0.40;
    function ringR(ri){return rings>1?(rMin+(rMax-rMin)*(ri/(rings-1))):(rMin+rMax)/2;}
    function ringTilt(ri){return (ri-(rings-1)/2)*0.5;}
    function ptOf(ri,ang){var rr=ringR(ri),tl=ringTilt(ri),lx=rr*Math.cos(ang),lz=rr*Math.sin(ang);
      return proj(lx,-lz*Math.sin(tl),lz*Math.cos(tl));}
    var core=proj(0,0,0);
    // faint ring guides, one per lane
    for(var g=0;g<rings;g++){var gn=null;for(var j=0;j<gitOrbitNodes.length;j++){if(gitOrbitNodes[j].ring===g){gn=gitOrbitNodes[j];break;}}
      var col=gitLaneCol(gn?gn.col:g);ctx.beginPath();var moved=false;
      for(var a=0;a<=Math.PI*2+0.01;a+=Math.PI/40){var p=ptOf(g,a);if(moved)ctx.lineTo(p.x,p.y);else{ctx.moveTo(p.x,p.y);moved=true;}}
      ctx.closePath();ctx.strokeStyle=hexA(col,0.12);ctx.lineWidth=1;ctx.stroke();}
    var pts=gitOrbitNodes.map(function(nd){return {nd:nd,p:ptOf(nd.ring,nd.ang)};});
    var far2near=pts.slice().sort(function(a,b){return b.p.z-a.p.z;});
    // beams core -> node
    far2near.forEach(function(o){var col=gitLaneCol(o.nd.col);ctx.beginPath();ctx.moveTo(core.x,core.y);ctx.lineTo(o.p.x,o.p.y);
      ctx.strokeStyle=hexA(col,0.05+0.12*o.p.d);ctx.lineWidth=1;ctx.stroke();});
    // core = the repo
    var gp=0.6+0.4*Math.sin(goT*3.0),coreR=R0*0.05;
    var grd=ctx.createRadialGradient(core.x,core.y,0,core.x,core.y,coreR*5);
    grd.addColorStop(0,hexA(PRC.cy,0.5));grd.addColorStop(0.4,hexA(PRC.cy,0.12));grd.addColorStop(1,hexA(PRC.cy,0));
    ctx.fillStyle=grd;ctx.beginPath();ctx.arc(core.x,core.y,coreR*5,0,7);ctx.fill();
    ctx.beginPath();ctx.arc(core.x,core.y,coreR*1.7,0,7);ctx.strokeStyle=hexA(PRC.cy,0.3+0.2*gp);ctx.lineWidth=1.3;ctx.stroke();
    ctx.beginPath();ctx.arc(core.x,core.y,coreR*(0.95+0.08*gp),0,7);ctx.fillStyle=hexA(PRC.fg,0.95);
    ctx.shadowBlur=22;ctx.shadowColor=PRC.cy;ctx.fill();ctx.shadowBlur=0;
    ctx.font="bold 8px "+PMONO;ctx.fillStyle=hexA(PRC.cy,0.9);ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText("REPO",core.x,core.y);
    // nodes far -> near; HEAD brightest + pulsing halo
    far2near.forEach(function(o){var nd=o.nd,p=o.p,col=gitLaneCol(nd.col),head=nd.isHead,rad=(head?6:4)*(0.7+0.5*p.d);
      gitOrbitHit.push({x:p.x,y:p.y,r:Math.max(9,rad+5),data:nd});
      if(head){var b=0.5+0.5*Math.sin(goT*6);ctx.shadowBlur=12+14*b;ctx.shadowColor=col;
        ctx.beginPath();ctx.arc(p.x,p.y,rad+5+b*3,0,7);ctx.strokeStyle=hexA(col,(1-b)*0.7);ctx.lineWidth=1.4;ctx.stroke();}
      else{ctx.shadowBlur=7;ctx.shadowColor=col;}
      ctx.beginPath();ctx.arc(p.x,p.y,rad,0,7);ctx.fillStyle=head?hexA(PRC.fg,0.96):col;ctx.fill();ctx.shadowBlur=0;
      if(head){ctx.beginPath();ctx.arc(p.x,p.y,rad+3,0,7);ctx.strokeStyle=col;ctx.lineWidth=1.4;ctx.stroke();}
      if(head||p.d>1.02){ctx.font="9px "+PMONO;ctx.fillStyle=hexA(head?PRC.cy:PRC.fg,head?0.95:0.32+0.42*p.d);
        ctx.textAlign="center";ctx.textBaseline="top";ctx.fillText(nd.label,p.x,p.y+rad+3);}});
  }
  function gitOrbitFrame(){
    if(mode!=="git"||document.hidden||!gitInsOpen){gitOrbitRunning=false;gitOrbitRAF=0;return;}   // bail — no reschedule
    goRotY+=0.0035;goT+=0.0038;                    // slowed like the reactor
    gitOrbitDraw();gitOrbitRAF=requestAnimationFrame(gitOrbitFrame);}
  function gitOrbitStart(){
    if(!gitOrbitCV||mode!=="git"||!gitInsOpen)return;
    gitOrbitResize();
    if(prodReduced()||!gitOrbitHasData()){gitOrbitDraw();return;}   // reduced motion OR no commits: one static frame
    if(gitOrbitRunning)return;
    gitOrbitRunning=true;gitOrbitRAF=requestAnimationFrame(gitOrbitFrame);}

  /* ---------- 3D COMMIT TERRAIN: the daily heatmap as a rotating relief (higher/brighter=more) ---------- */
  var gitTerrCV=$("git-terrain"),gitTerrCtx=gitTerrCV?gitTerrCV.getContext("2d"):null;
  var gtW=0,gtH=0,gtRotY=0.6,gitTerrRunning=false,gitTerrRAF=0;
  var gitTerrGrid=[],gitTerrCols=0,gitTerrMax=1;
  var gitTerrMeta=[];   // parallel to gitTerrGrid: the {date,count} day object (or null) per cell, for hover
  var gitTerrHit=[];    // per-frame projected {x,y,r,data} vertices for hover hit-testing (hudTip)
  function gitTerrHasData(){return gitTerrCols>0&&gitTerrMax>0;}
  function gitTerrainSync(){
    var d=gitInsData(),hm=(d&&!d.__error&&d.heatmap)?d.heatmap:[];
    var cols=gitCalWeeks(hm);if(cols.length>26)cols=cols.slice(cols.length-26);   // last ~26 weeks
    var grid=[],meta=[],max=1,total=0;
    cols.forEach(function(col){var row=[],mrow=[];for(var r=0;r<7;r++){var day=col[r];var v=day?day.count:0;if(v>max)max=v;total+=v;row.push(v);mrow.push(day||null);}grid.push(row);meta.push(mrow);});
    gitTerrGrid=grid;gitTerrMeta=meta;gitTerrCols=grid.length;gitTerrMax=max;
    var cap=$("git-terrain-cap");
    if(cap)cap.innerHTML=gitTerrCols?(gitTerrCols+" week"+(gitTerrCols===1?"":"s")+" &middot; peak "+max+"/day"):"no recent activity";
  }
  function gitTerrainResize(){var cv=gitTerrCV;if(!cv||!gitTerrCtx)return;var r=cv.getBoundingClientRect();
    gtW=r.width;gtH=r.height;var w=Math.max(1,Math.round(gtW*DPR)),h=Math.max(1,Math.round(gtH*DPR));
    if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;}gitTerrCtx.setTransform(DPR,0,0,DPR,0,0);}
  function gitTerrainDraw(){
    gitTerrHit.length=0;   // rebuilt this frame; empty on any early bail
    var ctx=gitTerrCtx;if(!ctx)return;var W=gtW,H=gtH;if(W<2||H<2)return;
    ctx.clearRect(0,0,W,H);
    var cx=W/2,cy=H/2+H*0.12,R0=Math.min(W,H),FOV=R0*2.6,rotX=0.92;
    function proj(x,y,z){var cA=Math.cos(gtRotY),sA=Math.sin(gtRotY);
      var X=x*cA-z*sA,Z=x*sA+z*cA;var cB=Math.cos(rotX),sB=Math.sin(rotX);
      var Y2=y*cB-Z*sB,Z2=y*sB+Z*cB;var dd=FOV/(FOV+Z2);return {x:cx+X*dd,y:cy+Y2*dd,z:Z2,d:dd};}
    var cols=gitTerrCols,rows=7;
    if(!cols){var span0=R0*0.5;ctx.strokeStyle=hexA(PRC.dim,0.4);ctx.lineWidth=1;   // empty: faint flat grid
      for(var i0=-2;i0<=2;i0++){var pA=proj(-span0,0,i0/2*span0),pB=proj(span0,0,i0/2*span0);
        ctx.beginPath();ctx.moveTo(pA.x,pA.y);ctx.lineTo(pB.x,pB.y);ctx.stroke();}return;}
    var spanX=R0*0.62,spanZ=R0*0.44,hMax=R0*0.30;
    function pt(c,r){var x=(cols>1?(c/(cols-1)-0.5):0)*spanX*2,z=(r/6-0.5)*spanZ*2;
      var v=gitTerrGrid[c]?gitTerrGrid[c][r]:0;return proj(x,-(v/gitTerrMax)*hMax,z);}
    var quads=[];
    for(var c=0;c<cols-1;c++)for(var r=0;r<rows-1;r++){
      var v=(gitTerrGrid[c][r]+gitTerrGrid[c+1][r]+gitTerrGrid[c][r+1]+gitTerrGrid[c+1][r+1])/4;
      var a=pt(c,r),b=pt(c+1,r),cc=pt(c+1,r+1),dd=pt(c,r+1);
      quads.push({a:a,b:b,c:cc,d:dd,v:v,z:(a.z+b.z+cc.z+dd.z)/4});}
    quads.sort(function(p,q){return q.z-p.z;});   // far -> near
    quads.forEach(function(Q){var inten=Q.v/gitTerrMax;
      ctx.beginPath();ctx.moveTo(Q.a.x,Q.a.y);ctx.lineTo(Q.b.x,Q.b.y);ctx.lineTo(Q.c.x,Q.c.y);ctx.lineTo(Q.d.x,Q.d.y);ctx.closePath();
      ctx.fillStyle=hexA(PRC.cy,0.06+0.55*inten);ctx.fill();
      ctx.strokeStyle=hexA(PRC.cy,0.12+0.35*inten);ctx.lineWidth=1;ctx.stroke();});
    for(var c2=0;c2<cols;c2++)for(var r2=0;r2<rows;r2++){var v2=gitTerrGrid[c2][r2];   // glow the peaks
      if(v2>0&&v2>=gitTerrMax*0.75){var p=pt(c2,r2);ctx.beginPath();ctx.arc(p.x,p.y,2.2,0,7);
        ctx.fillStyle=hexA(PRC.fg,0.9);ctx.shadowBlur=8;ctx.shadowColor=PRC.cy;ctx.fill();ctx.shadowBlur=0;}}
    for(var hc=0;hc<cols;hc++)for(var hr=0;hr<rows;hr++){var hp=pt(hc,hr);   // every vertex → a hover hit point
      var day=(gitTerrMeta[hc]&&gitTerrMeta[hc][hr])||null;
      gitTerrHit.push({x:hp.x,y:hp.y,r:9,data:{day:day,count:(gitTerrGrid[hc]&&gitTerrGrid[hc][hr])||0}});}
  }
  function gitTerrainFrame(){
    if(mode!=="git"||document.hidden||!gitInsOpen){gitTerrRunning=false;gitTerrRAF=0;return;}   // bail — no reschedule
    gtRotY+=0.0032;                                // slowed
    gitTerrainDraw();gitTerrRAF=requestAnimationFrame(gitTerrainFrame);}
  function gitTerrainStart(){
    if(!gitTerrCV||mode!=="git"||!gitInsOpen)return;
    gitTerrainResize();
    if(prodReduced()||!gitTerrHasData()){gitTerrainDraw();return;}   // reduced motion OR quiet repo: one static frame
    if(gitTerrRunning)return;
    gitTerrRunning=true;gitTerrRAF=requestAnimationFrame(gitTerrainFrame);}

  /* ---------- 3D COMMIT RELIEF (the ORIGINALLY-PROPOSED preview terrain, kept ALONGSIDE gitTerrain) ----------
     A 20x7 heightfield of glowing vertical columns rising from a slowly rotating, tilted plane
     (rotY + oscillating rotX), cyan-by-height, z-sorted back-to-front, peaks glow. Driven by the SAME
     insights heatmap the shipped terrain uses (counts -> column heights); degrades to a calm procedural
     idle field when the repo has no recent activity. Same gating/lifecycle as the other git scenes;
     the perspective (rotY/rotX/proj) is inlined per-scene, matching how every other scene in this file
     builds its own projection (there is no shared helper). Cadence copies the shipped ~35% slowdown. */
  var gitT2CV=$("git-terrain2"),gitT2Ctx=gitT2CV?gitT2CV.getContext("2d"):null;
  var g2W=0,g2H=0,g2T=0,gitT2Running=false,gitT2RAF=0;
  var gitT2Grid=[],gitT2Cols=20,gitT2Rows=7,gitT2Max=1,gitT2Idle=false;
  var gitT2Meta=null;   // {date,count} day per cell when real data (null in idle field), for hover
  var gitT2Hit=[];      // per-frame projected {x,y,r,data} column tops for hover hit-testing (hudTip)
  // calm, low-amplitude seeded heightfield so the scene stays alive when there is no commit data
  function gitT2IdleField(cols,rows){var seed=1;function rnd(){seed=(seed*9301+49297)%233280;return seed/233280;}
    var g=[];for(var c=0;c<cols;c++){var row=[];for(var r=0;r<rows;r++){row.push(Math.max(0,rnd()*rnd()*0.9-0.08));}g.push(row);}return g;}
  function gitTerrain2Sync(){
    var d=gitInsData(),hm=(d&&!d.__error&&d.heatmap)?d.heatmap:[];
    var COLS=20,ROWS=7,weeks=gitCalWeeks(hm);
    var use=weeks.slice(Math.max(0,weeks.length-COLS)),pad=COLS-use.length;   // last 20 weeks, left-padded
    var grid=[],meta=[],max=1,total=0;
    for(var c=0;c<COLS;c++){var col=(c>=pad)?use[c-pad]:null,row=[],mrow=[];
      for(var r=0;r<ROWS;r++){var day=(col&&col[r])?col[r]:null,v=day?(+day.count||0):0;if(v>max)max=v;total+=v;row.push(v);mrow.push(day);}
      grid.push(row);meta.push(mrow);}
    if(total>0){gitT2Grid=grid;gitT2Max=max;gitT2Idle=false;gitT2Meta=meta;}
    else{gitT2Grid=gitT2IdleField(COLS,ROWS);gitT2Max=1;gitT2Idle=true;gitT2Meta=null;}
    gitT2Cols=COLS;gitT2Rows=ROWS;
    var cap=$("git-terrain2-cap");
    if(cap)cap.innerHTML=gitT2Idle?"idle field &middot; no recent activity":(total+" commits &middot; peak "+max+"/day");
  }
  function gitTerrain2Resize(){var cv=gitT2CV;if(!cv||!gitT2Ctx)return;var r=cv.getBoundingClientRect();
    g2W=r.width;g2H=r.height;var w=Math.max(1,Math.round(g2W*DPR)),h=Math.max(1,Math.round(g2H*DPR));
    if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;}gitT2Ctx.setTransform(DPR,0,0,DPR,0,0);}
  function gitTerrain2Draw(){
    gitT2Hit.length=0;   // rebuilt this frame; empty on any early bail (and in the idle field)
    var ctx=gitT2Ctx;if(!ctx)return;var W=g2W,H=g2H;if(W<2||H<2)return;
    ctx.clearRect(0,0,W,H);
    var cx=W/2,cy=H*0.60,R0=Math.min(W,H),fov=R0*2.7,t=g2T;
    function rotY(p,a){var c=Math.cos(a),s=Math.sin(a);return {x:p.x*c-p.z*s,y:p.y,z:p.x*s+p.z*c};}
    function rotX(p,a){var c=Math.cos(a),s=Math.sin(a);return {x:p.x,y:p.y*c-p.z*s,z:p.y*s+p.z*c};}
    function proj(p){var s=fov/(fov+p.z);return {x:cx+p.x*s,y:cy+p.y*s,s:s,z:p.z};}
    var cols=gitT2Cols,rows=gitT2Rows,sp=R0*0.085,hMax=R0*0.42,ay=Math.sin(t*0.5)*0.15+0.5,ry=t*0.25,cells=[];
    for(var x=0;x<cols;x++)for(var z=0;z<rows;z++){
      var raw=(gitT2Grid[x]&&gitT2Grid[x][z]!=null)?gitT2Grid[x][z]:0,v=Math.max(0,Math.min(1,raw/gitT2Max));
      var hgt=v*hMax,wx=(x-cols/2)*sp,wz=(z-rows/2)*sp;
      var b=rotX(rotY({x:wx,y:0,z:wz},ry),ay),to=rotX(rotY({x:wx,y:-hgt,z:wz},ry),ay);
      var Tp=proj(to);cells.push({b:proj(b),t:Tp,v:v});
      if(!gitT2Idle){var day=(gitT2Meta&&gitT2Meta[x]&&gitT2Meta[x][z])||null;   // real data only (idle = no hover)
        gitT2Hit.push({x:Tp.x,y:Tp.y,r:8,data:{day:day,count:(gitT2Grid[x]&&gitT2Grid[x][z])||0}});}}
    cells.sort(function(p,q){return q.b.z-p.b.z;});   // far -> near
    cells.forEach(function(c){var v=c.v,col=v<0.28?hexA(PRC.cy,0.30):v<0.7?hexA(PRC.cy,0.62):hexA(PRC.fg,0.95);
      ctx.strokeStyle=col;ctx.lineWidth=Math.max(1,3*c.t.s);ctx.globalAlpha=Math.max(0.25,c.t.s);
      ctx.beginPath();ctx.moveTo(c.b.x,c.b.y);ctx.lineTo(c.t.x,c.t.y);ctx.stroke();
      ctx.fillStyle=col;ctx.shadowColor=PRC.cy;ctx.shadowBlur=v>0.7?8*c.t.s:0;
      ctx.beginPath();ctx.arc(c.t.x,c.t.y,Math.max(1.2,2.2*c.t.s),0,7);ctx.fill();ctx.shadowBlur=0;});
    ctx.globalAlpha=1;
  }
  function gitTerrain2Frame(){
    if(mode!=="git"||document.hidden||!gitInsOpen){gitT2Running=false;gitT2RAF=0;return;}   // bail — no reschedule
    g2T+=0.0026;                                   // ~35% slower than the preview's raw 0.004, matching the shipped scenes
    gitTerrain2Draw();gitT2RAF=requestAnimationFrame(gitTerrain2Frame);}
  function gitTerrain2Start(){
    if(!gitT2CV||mode!=="git"||!gitInsOpen)return;
    gitTerrain2Resize();
    if(prodReduced()){gitTerrain2Draw();return;}   // reduced motion: one static frame (the idle field still animates otherwise)
    if(gitT2Running)return;
    gitT2Running=true;gitT2RAF=requestAnimationFrame(gitTerrain2Frame);}

  // re-arm all three git canvases when the tab returns to view (they self-bail when hidden/away)
  document.addEventListener("visibilitychange",function(){if(document.hidden||mode!=="git")return;
    gitOrbitStart();gitTerrainStart();gitTerrain2Start();});
  // resize all three git canvases (only meaningful while on the Git view)
  addEventListener("resize",function(){if(mode!=="git")return;
    if(gitOrbitCV){gitOrbitResize();if(!gitOrbitRunning)gitOrbitDraw();}
    if(gitTerrCV){gitTerrainResize();if(!gitTerrRunning)gitTerrainDraw();}
    if(gitT2CV){gitTerrain2Resize();if(!gitT2Running)gitTerrain2Draw();}});

  /* ---- hover tooltips for the three git canvas scenes (shared core.js hudTip; read the per-frame
       hit arrays the draws populate — a passive reader, it never touches the rAF loops) ---- */
  function gitOrbitTipHtml(nd){
    var h='<span class="htk">'+esc(nd.short||nd.label||"")+(nd.isHead?' <span class="warn">HEAD</span>':"")+'</span>';
    if(nd.author)h+='<div class="htr"><span>author</span><b>'+esc(nd.author)+'</b></div>';
    if(nd.date)h+='<div class="htr"><span>date</span><b>'+esc(nd.date)+'</b></div>';
    if(nd.subject)h+='<div class="htsub">'+esc(nd.subject)+'</div>';
    return h;}
  function gitCellTipHtml(d){var day=d.day;
    return '<span class="htk">'+(day?esc(String(day.date)):"—")+'</span>'+
      '<div class="htr"><span>commits</span><b>'+(d.count||0)+'</b></div>';}
  hudTipCanvas(gitOrbitCV,function(){return gitOrbitHit;},gitOrbitTipHtml);
  hudTipCanvas(gitTerrCV,function(){return gitTerrHit;},gitCellTipHtml);
  hudTipCanvas(gitT2CV,function(){return gitT2Hit;},gitCellTipHtml);

