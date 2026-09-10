  /* ---------- TABLE renderers ---------- */
  function renderTabs(){$("tabs").innerHTML="";
    order.forEach(function(id,i){var s=sessions[id];if(!s)return;var live=(Date.now()/1000-s.last)<8;
      var t=document.createElement("div");t.className="tab"+(id===selected?" sel":"")+(live?" live":"");
      var cw=s.cwd?s.cwd.replace(/[\\/]+$/,"").split(/[\\/]/).pop():"";
      t.innerHTML='<span class="tdot"></span>Session '+(i+1)+(cw?' <span class="cw">'+esc(cw)+'</span>':'')+'<span class="x" title="remove">&times;</span>';
      t.onclick=function(e){if(e.target.className==="x"){removeSession(id);return;}selected=id;renderAll();};
      $("tabs").appendChild(t);});}
  function iconForGroup(group){return iconSlug(slugOf(group),"a-ico");}
  function agentRow(a){var st=a.state||"idle";var sub=a.tool?("tool: "+a.tool):(a.label||"");
    var d=document.createElement("div");d.className="agent "+st;d.style.setProperty("--gc",a.color||"var(--faint)");
    d.innerHTML=iconForGroup(a.group)+'<div><div class="a-top"><span class="a-name">'+esc(a.name)+'</span>'+
      (a.group?'<span class="a-grp">'+esc(a.group)+'</span>':'')+'<span class="wave"><i></i><i></i><i></i><i></i><i></i><i></i></span></div>'+
      (sub?'<div class="a-sub">'+esc(sub)+'</div>':'')+'</div><div class="a-state"><span class="sdot"></span>'+esc(STATE_LABEL[st]||st)+'</div>';
    return d;}
  function renderPanel(){if(!selected||!sessions[selected]){$("panel").innerHTML="";return;}
    var s=sessions[selected];var agents=Object.keys(s.agents).map(function(k){return s.agents[k];}).sort(function(a,b){return (b.ts||0)-(a.ts||0);});
    var c=s.counts||{};var wrap=document.createElement("div");
    var head=document.createElement("div");head.className="sess-head";
    head.innerHTML='<span class="path">'+esc(s.cwd||"—")+'</span><span class="metrics">'+
      '<span class="metric run">running <b>'+agents.filter(function(a){return a.state==="run"||a.state==="ask";}).length+'</b></span>'+
      '<span class="metric">start <b>'+(c.start||0)+'</b></span><span class="metric">done <b>'+(c.done||0)+'</b></span>'+
      '<span class="metric ask">asks <b>'+(c.ask||0)+'</b></span><span class="metric err">errors <b>'+(c.error||0)+'</b></span></span>';
    wrap.appendChild(head);
    var stage=document.createElement("div");stage.className="stage";
    if(!agents.length)stage.innerHTML='<div class="empty" style="padding:2rem">no agents yet</div>';
    else agents.forEach(function(a){var el=agentRow(a);el.style.cursor="pointer";el.title="click for inspector";el.onclick=function(){openInspector(a.name);};stage.appendChild(el);});
    wrap.appendChild(stage);
    var log=document.createElement("div");log.className="log";
    var rows=(s.events||[]).slice(-40).reverse().map(function(e){return '<div class="lr"><span class="t">'+hhmmss(e.ts)+'</span>'+
      '<span class="ag" style="--lc:'+(e.color||"var(--fg)")+'">'+esc(e.agent)+'</span><span class="ph-'+esc(e.phase)+'">'+esc(e.phase)+(e.tool?(' · '+esc(e.tool)):'')+'</span></div>';}).join("");
    log.innerHTML='<h3>Events</h3><div class="rows">'+(rows||'<div class="lr">—</div>')+'</div>';wrap.appendChild(log);
    $("panel").innerHTML="";$("panel").appendChild(wrap);}

