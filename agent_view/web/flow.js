  /* ---------- FLOW view (buildFlow: one request end to end — prompt, agents, tools, goal) ---------- */
  function buildFlow(){var host=$("flow-body");var s=sessions[selected];
    if(!s){host.innerHTML='<div class="fempty">No active session. The Flow view shows one request end to end: your prompt at the top, the agents &amp; tools working in the middle, the goal at the bottom.</div>';return;}
    var agents=Object.keys(s.agents).map(function(k){return s.agents[k];}).sort(function(a,b){return (a.ts||0)-(b.ts||0);});
    var anyRun=agents.some(function(a){return a.state==="run"||a.state==="ask";});
    var lanes=agents.map(function(a){var col=a.color||colorFor(a.group);var lbl={run:"running",ask:"awaiting",done:"done",error:"error",idle:"idle"}[a.state]||a.state;
      return '<div class="lane '+(a.state==="run"?"run":"")+'" style="--lc:'+col+'"><div class="h"><span class="dot"></span><span class="n">'+esc(a.name)+'</span><span class="st">'+esc(lbl)+'</span></div>'+(a.tool?'<div class="fbead" style="--bc:#38e6ff"><span class="tk">'+TOOLTYPES[toolType(a.tool)].k+'</span>'+esc(a.tool)+'</div>':'')+'</div>';}).join("");
    var rt=(s.tools||[]).slice(-10);
    var toolLane='<div class="lane" style="--lc:#38e6ff"><div class="h"><span class="dot"></span><span class="n">session tools</span><span class="st">'+rt.length+'</span></div>'+
      rt.map(function(t){var T=TOOLTYPES[toolType(t.tool)];return '<div class="fbead" style="--bc:'+T.c+'"><span class="tk">'+T.k+'</span>'+esc(t.tool)+'</div>';}).join("")+'</div>';
    var todos=s.todos||[];var mdone=todos.filter(function(t){return t.status==="completed";}).length;
    var miles=todos.length?('<div class="fstem"></div><div class="fmiles"><div class="fmh">plan &middot; '+mdone+' / '+todos.length+' milestones done</div>'+
      todos.map(function(t){var cls=t.status==="completed"?"m-done":(t.status==="in_progress"?"m-run":"m-pend");
        return '<div class="mrow '+cls+'"><span class="mk"></span><span class="mc">'+esc(t.content)+'</span></div>';}).join("")+'</div>'):'';
    var pills=order.map(function(id,i){var ss=sessions[id];if(!ss)return "";var live=(Date.now()/1000-ss.last)<8;var cw=baseCwd(ss);
      return '<div class="fpill'+(id===selected?" sel":"")+(live?" live":"")+'" data-sid="'+esc(id)+'">Session '+(i+1)+(cw?" &middot; "+esc(cw):"")+'</div>';}).join("");
    var hasPrompt=!!(s.prompt&&String(s.prompt).trim());
    var promptBox=hasPrompt
      ? '<div class="fprompt clamp click" id="flow-prompt"><div class="l">your prompt</div><div class="q">'+esc(s.prompt)+'</div><div class="more">click for full prompt &#8594;</div></div>'
      : '<div class="fprompt"><div class="l">your prompt</div><div class="q">— no prompt captured this session —</div></div>';
    host.innerHTML='<div class="fwrap">'+
      '<div class="fpills">'+pills+'</div>'+
      promptBox+
      miles+
      '<div class="fstem"></div><div class="flanes">'+(lanes||'<div class="fempty">no agents yet</div>')+toolLane+'</div><div class="fstem"></div>'+
      '<div class="fgoal"><div class="l">goal</div><div class="q">'+esc(hasPrompt?s.prompt:"the session objective")+' &middot; '+(todos.length?(mdone+" / "+todos.length+" milestones"):(anyRun?"in progress":"idle"))+'</div></div></div>';
    Array.prototype.forEach.call(host.querySelectorAll(".fpill"),function(el){el.onclick=function(){selected=el.getAttribute("data-sid");buildFlow();};});
    var fp=$("flow-prompt");if(fp)fp.onclick=function(){openPrompt(s.prompt||"");};}

