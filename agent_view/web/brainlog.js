  /* ---------- BRAIN LOG view (changelog of skill/agent updates from /api/brainlog) ---------- */
  var BLOG=[],blogQ="";
  // date+time -> epoch ms for a robust newest-first sort; falls back to string compare on unparseable input.
  function blTs(e){var d=Date.parse((String(e&&e.date||"")+" "+String(e&&e.time||"")).trim());return isNaN(d)?null:d;}
  function blKind(k){k=String(k||"").toLowerCase();return k==="agent"?"agent":"skill";}
  function fetchBrainLog(){
    fetch("/api/brainlog").then(function(r){return r.json();}).then(function(d){
      BLOG=(d&&d.entries)||[];
      $("bl-sub").innerHTML=(d&&d.error)?('<b style="color:#fb7185">'+esc(d.error)+'</b>'):('<b>'+BLOG.length+'</b> update'+(BLOG.length===1?"":"s"));
      renderBrainLog();
    }).catch(function(){BLOG=[];$("bl-sub").textContent="server not reachable";renderBrainLog();});}
  function renderBrainLog(){
    var rows=BLOG.filter(function(e){return blogQ?String(e.skill||"").toLowerCase().indexOf(blogQ)>=0:true;});
    rows=rows.slice().sort(function(a,b){var x=blTs(a),y=blTs(b);
      if(x!=null&&y!=null&&x!==y)return y-x;   // newest first
      var xs=String(a.date||"")+" "+String(a.time||""),ys=String(b.date||"")+" "+String(b.time||"");return xs<ys?1:xs>ys?-1:0;});
    $("bl-rows").innerHTML=rows.map(function(e){var kind=blKind(e.kind);
      return '<tr title="'+esc((e.path||"")+(e.hash?(" · "+e.hash):""))+'">'+
        '<td class="bl-date">'+esc(e.date||"—")+'</td>'+
        '<td class="bl-time">'+esc(e.time||"")+'</td>'+
        '<td class="bl-skill"><span class="bl-kind k-'+kind+'">'+esc(e.kind||"skill")+'</span><b>'+esc(e.skill||"—")+'</b></td>'+
        '<td class="bl-desc">'+esc(e.subject||"")+'</td></tr>';}).join("");
    var empty=$("bl-empty");if(!empty)return;
    if(!rows.length){empty.style.display="block";empty.textContent=BLOG.length?('No updates match "'+blogQ+'".'):"No brain updates yet.";}
    else empty.style.display="none";}
  $("bl-search").oninput=function(){blogQ=this.value.toLowerCase();renderBrainLog();};
  $("bl-refresh").onclick=fetchBrainLog;

