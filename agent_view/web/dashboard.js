  /* ---------- DASHBOARD view ---------- */
  var READ=[["costs a lot, never used","the description is not triggering — rewrite it, or delete the thing"],
    ["used a lot, expensive per call","split what it preloads into references/"],
    ["used a lot, scores weak","almost always too general to act on — sharpen or split it"],
    ["scores retire","delete it; a rule proven unhelpful is worse than a missing one"],
    ["unproven for months","it is not being triggered — the description is the problem"]];
  function chipList(names){return (names&&names.length)?names.map(function(n){return '<span class="mchip">'+esc(n)+'</span>';}).join(""):'<span class="zero">&mdash;</span>';}
  function sortT(th){var t=th.closest("table"),b=t.tBodies[0];
    var idx=Array.prototype.indexOf.call(th.parentNode.children,th);
    var dir=(t.getAttribute("data-c")==idx&&t.getAttribute("data-d")!=="desc")?"desc":"asc";
    function val(r){var c=r.cells[idx];if(!c)return "";var dv=c.getAttribute("data-v");
      var x=dv!==null?dv:c.textContent.trim();var s=String(x).replace(/[, ]/g,"");
      var n=/^[-+]?\d+(\.\d+)?$/.test(s)?parseFloat(s):NaN;return isNaN(n)?s.toLowerCase():n;}
    Array.prototype.slice.call(b.rows).sort(function(a,z){var p=val(a),q=val(z);return (p<q?-1:p>q?1:0)*(dir==="asc"?1:-1);}).forEach(function(r){b.appendChild(r);});
    t.setAttribute("data-c",idx);t.setAttribute("data-d",dir);
    Array.prototype.forEach.call(t.tHead.rows[0].cells,function(c){c.removeAttribute("data-sorted");});
    th.setAttribute("data-sorted",dir);}
  function buildDashboard(){var host=$("dash-body");
    if(!META){host.innerHTML='<div class="dloading">Loading dashboard data&hellip;</div>';return;}
    if(META.error){host.innerHTML='<div class="dloading">Could not load: '+esc(META.error)+'</div>';return;}
    var du=$("dupd");if(du)du.textContent=META.generatedAt?("· updated "+new Date(META.generatedAt*1000).toLocaleTimeString()+" · auto-refresh 60s"):"";
    var k=META.kpis||{};
    var kpis=[[k.agents,"agents"],[k.skills,"skills"],[k.references,"references"],[fmtTok(k.alwaysTok),"tok always in context"],
      [k.seen,"seen in a transcript"],[k.hours,"hours worked"],[k.minutesToday,"minutes today"],[fmtTok(k.outputTokens),"output tokens"],[k.cachePct+"%","cache hit"]];
    var html='<div class="kpis">'+kpis.map(function(x){return '<div class="kcard"><b>'+esc(x[0])+'</b><span>'+esc(x[1])+'</span></div>';}).join("")+'</div>';
    // per request
    html+='<div class="dsec">Per request</div><div style="overflow-x:auto"><table class="dtable"><thead><tr><th>When</th><th>You asked</th><th class="ta">Agents</th><th>Which</th><th class="ta">Skills</th><th>Which</th></tr></thead><tbody>';
    (META.perRequest||[]).forEach(function(p,pi){html+='<tr><td style="color:var(--hud-dim)">'+esc(p.when)+'</td><td><div class="pq pq-click" data-pi="'+pi+'">'+esc(p.prompt)+'</div></td>'+
      '<td class="ta"><b class="'+(p.agents.length?"hitn":"zero")+'">'+p.agents.length+'</b></td><td>'+chipList(p.agents)+'</td>'+
      '<td class="ta"><b class="'+(p.skills.length?"hitn":"zero")+'">'+p.skills.length+'</b></td><td>'+chipList(p.skills)+'</td></tr>';});
    html+='</tbody></table></div>';
    // agents
    var maxCost=Math.max.apply(null,(META.agents||[]).map(function(a){return a.cost;}).concat([1]));
    html+='<div class="dsec">Agents</div><div style="overflow-x:auto"><table class="dtable"><thead><tr><th>Agent</th><th>Group</th><th>Grade</th><th>Model</th><th class="ta">Used</th><th>Last</th><th class="ta">Cost / call</th><th class="ta">Score</th><th>Preloads</th></tr></thead><tbody>';
    (META.agents||[]).forEach(function(a){var col=colorFor(a.group);
      html+='<tr class="drow" data-kind="agent" data-name="'+esc(a.name)+'" style="--rc:'+col+'"><td class="nm"><div class="dnm">'+iconSlug(slugOf(a.group),"di")+'<b>'+esc(a.name)+'</b></div></td>'+
        '<td>'+esc(a.group||"—")+'</td><td><span class="gr gr-'+esc(a.grade)+'">'+esc(a.grade)+'</span></td><td>'+esc(a.model)+'</td>'+
        '<td class="ta">'+a.used+'</td><td style="color:var(--hud-dim)">'+esc(a.last||"—")+'</td>'+
        '<td class="ta" data-v="'+a.cost+'"><span class="cbar"><i style="width:'+Math.round(a.cost/maxCost*100)+'%"></i></span>'+fmtTok(a.cost)+'</td>'+
        '<td class="ta" data-v="'+(parseFloat(a.score)||0)+'"><span class="pband p-'+esc(a.scoreBand||"unproven")+'">'+esc(a.scoreBand||"unproven")+(a.score?(" "+esc(a.score)):"")+'</span></td><td>'+chipList(a.pre)+'</td></tr>';});
    html+='</tbody></table></div>';
    // skills
    var maxBody=Math.max.apply(null,(META.skills||[]).map(function(s){return s.body;}).concat([1]));
    html+='<div class="dsec">Skills</div><div style="overflow-x:auto"><table class="dtable"><thead><tr><th>Skill</th><th class="ta">Invoked</th><th class="ta">Via preload</th><th>Last</th><th class="ta">Tok on load</th><th class="ta">Refs</th><th class="ta">Preloaded by</th><th class="ta">Score</th></tr></thead><tbody>';
    (META.skills||[]).forEach(function(s){html+='<tr class="drow" data-kind="skill" data-name="'+esc(s.name)+'" style="--rc:#38bdf8"><td class="nm"><div class="dnm"><b>/brain:'+esc(s.name)+'</b></div></td>'+
      '<td class="ta">'+s.used+'</td><td class="ta">'+(s.preload||0)+'</td><td style="color:var(--hud-dim)">'+esc(s.last||"—")+'</td>'+
      '<td class="ta" data-v="'+s.body+'"><span class="cbar"><i style="width:'+Math.round(s.body/maxBody*100)+'%"></i></span>'+fmtTok(Math.round(s.body/4))+'</td>'+
      '<td class="ta">'+s.refs+'</td><td class="ta">'+s.by+'</td><td class="ta" data-v="'+(parseFloat(s.score)||0)+'"><span class="pband p-'+esc(s.scoreBand||"unproven")+'">'+esc(s.scoreBand||"unproven")+(s.score?(" "+esc(s.score)):"")+'</span></td></tr>';});
    html+='</tbody></table></div>';
    // cold
    var cold=(META.cold&&META.cold.agents||[]).concat(META.cold&&META.cold.skills||[]);
    html+='<div class="dsec">Not seen in any transcript</div><div class="dchips">'+(cold.length?cold.map(function(n){return '<span class="dchip">'+esc(n)+'</span>';}).join(""):'<span class="zero">everything has been seen</span>')+'</div>';
    // reading it
    html+='<div class="dsec">Reading it</div><dl class="dread">'+READ.map(function(r){return '<dt>'+esc(r[0])+'</dt><dd>'+esc(r[1])+'</dd>';}).join("")+'</dl>';
    html+='<div class="dnote">Same data as <code>dashboard.html</code> &mdash; folded into the live view, shown only when you ask for it.</div>';
    host.innerHTML=html;
    Array.prototype.forEach.call(host.querySelectorAll("table th"),function(th){th.onclick=function(e){e.stopPropagation();sortT(th);};});
    Array.prototype.forEach.call(host.querySelectorAll("tr.drow"),function(tr){tr.onclick=function(){openDoc(tr.getAttribute("data-kind"),tr.getAttribute("data-name"),tr.parentNode);};});
    Array.prototype.forEach.call(host.querySelectorAll(".pq-click"),function(el){el.style.cursor="pointer";el.title="click for full prompt";
      el.onclick=function(e){e.stopPropagation();var p=(META.perRequest||[])[parseInt(el.getAttribute("data-pi"),10)];if(p)openPrompt(p.prompt);};});}

  /* ---------- doc reader modal ---------- */
  var docCache={},docRows=[],docIdx=0;
  function skillMetaOf(name){var a=(META&&META.skills)||[];for(var i=0;i<a.length;i++)if(a[i].name===name)return a[i];return null;}
  function openDoc(kind,name,tbody){
    docRows=Array.prototype.slice.call(tbody.querySelectorAll("tr.drow")).map(function(tr){return {kind:tr.getAttribute("data-kind"),name:tr.getAttribute("data-name")};});
    docIdx=0;for(var i=0;i<docRows.length;i++)if(docRows[i].kind===kind&&docRows[i].name===name){docIdx=i;break;}
    $("docmodal").classList.add("open");showDoc();}
  function closeDoc(){$("docmodal").classList.remove("open");}
  function stepDoc(d){var n=docIdx+d;if(n<0||n>=docRows.length)return;docIdx=n;var mb=$("doc-body");mb.style.opacity="0";setTimeout(function(){showDoc();mb.style.opacity="1";},120);}
  function showDoc(){var r=docRows[docIdx];var am=(r.kind==="agent")?metaOf(r.name):null;var sm=(r.kind==="skill")?skillMetaOf(r.name):null;
    var col=am?colorFor(am.group):"#38bdf8";
    $("docmodal").querySelector(".ddlg").style.setProperty("--mc",col);
    $("doc-ico").innerHTML='<span style="color:'+col+'">'+iconSlug(am?slugOf(am.group):"reading","")+'</span>';
    $("doc-tt").textContent=(r.kind==="agent")?r.name:("/brain:"+r.name);$("doc-tt").style.color=col;
    $("doc-sub").textContent=am?((am.group||"")+" · "+(am.model||"")):(sm?(sm.refs+" refs · score "+(sm.score||"—")):r.kind);
    $("doc-pos").textContent=(docIdx+1)+" / "+docRows.length;
    $("doc-prev").disabled=(docIdx===0);$("doc-next").disabled=(docIdx===docRows.length-1);
    var mb=$("doc-body"),key=r.kind+":"+r.name;
    if(docCache[key]!==undefined){mb.innerHTML=mdRender(docCache[key]);return;}
    mb.innerHTML='<div class="mload">Loading brief&hellip;</div>';
    fetch("/api/doc?kind="+encodeURIComponent(r.kind)+"&name="+encodeURIComponent(r.name)).then(function(x){return x.json();}).then(function(d){
      var cur=docRows[docIdx];if(!cur||cur.kind+":"+cur.name!==key)return; // stepped away before it loaded
      if(d.error){mb.innerHTML='<div class="mload">Couldn\'t read this file.</div>';return;}
      docCache[key]=d.markdown||"";mb.innerHTML=mdRender(docCache[key]);
    }).catch(function(){mb.innerHTML='<div class="mload">Couldn\'t read this file.</div>';});}
  function mdInline(s){s=esc(s);s=s.replace(/`([^`]+)`/g,'<code>$1</code>');s=s.replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');return s;}
  function mdRender(src){src=String(src);var fm=src.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n/);if(fm)src=src.slice(fm[0].length);
    var lines=src.split("\n"),out=[],i=0;
    function list(tag,items){out.push("<"+tag+">"+items.map(function(x){return "<li>"+mdInline(x)+"</li>";}).join("")+"</"+tag+">");}
    while(i<lines.length){var ln=lines[i];
      if(/^```/.test(ln)){var buf=[];i++;while(i<lines.length&&!/^```/.test(lines[i])){buf.push(lines[i]);i++;}i++;out.push('<pre><code>'+esc(buf.join("\n"))+'</code></pre>');continue;}
      if(/^### /.test(ln)){out.push("<h3>"+mdInline(ln.slice(4))+"</h3>");i++;continue;}
      if(/^## /.test(ln)){out.push("<h2>"+mdInline(ln.slice(3))+"</h2>");i++;continue;}
      if(/^# /.test(ln)){out.push("<h1>"+mdInline(ln.slice(2))+"</h1>");i++;continue;}
      if(/^> /.test(ln)){var q=[];while(i<lines.length&&/^> /.test(lines[i])){q.push(lines[i].slice(2));i++;}out.push("<blockquote>"+mdInline(q.join(" "))+"</blockquote>");continue;}
      if(/^\|/.test(ln)){var rows=[];while(i<lines.length&&/^\|/.test(lines[i])){rows.push(lines[i]);i++;}
        var bd=rows.filter(function(rr){return !/^\|[\s|:-]+\|?\s*$/.test(rr);});var h="<table>";
        bd.forEach(function(rr,ri){var cs=rr.split("|").slice(1,-1).map(function(c){return c.trim();});h+="<tr>"+cs.map(function(c){return ri===0?"<th>"+mdInline(c)+"</th>":"<td>"+mdInline(c)+"</td>";}).join("")+"</tr>";});
        h+="</table>";out.push(h);continue;}
      if(/^[-*] /.test(ln)){var it=[];while(i<lines.length&&/^[-*] /.test(lines[i])){it.push(lines[i].slice(2));i++;}list("ul",it);continue;}
      if(/^\d+\. /.test(ln)){var it2=[];while(i<lines.length&&/^\d+\. /.test(lines[i])){it2.push(lines[i].replace(/^\d+\.\s/,""));i++;}list("ol",it2);continue;}
      if(ln.trim()===""){i++;continue;}
      out.push("<p>"+mdInline(ln)+"</p>");i++;}
    return out.join("");}
  $("doc-x").onclick=closeDoc;$("doc-bd").onclick=closeDoc;
  $("doc-prev").onclick=function(){stepDoc(-1);};$("doc-next").onclick=function(){stepDoc(1);};
  addEventListener("keydown",function(e){if(!$("docmodal").classList.contains("open"))return;
    if(e.key==="Escape")closeDoc();else if(e.key==="ArrowLeft")stepDoc(-1);else if(e.key==="ArrowRight")stepDoc(1);});

