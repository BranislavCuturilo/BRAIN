  /* ---------- HUD canvas + interactions ---------- */
  var cv=$("graph"),ctx=cv.getContext("2d"),W,H,DPR=Math.min(2,window.devicePixelRatio||1);
  function resize(){var r=cv.getBoundingClientRect();W=r.width;H=r.height;cv.width=W*DPR;cv.height=H*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);}
  addEventListener("resize",resize);
  var POS={};                 // key sid|name -> {manual, x, y}  (world coords in all-mode, screen in hud)
  var packets=[];             // hud-mode only: {sid,name,col,t}
  var cam={x:0,y:0,z:0.8};    // HUD·All camera (world -> screen)
  function center(){return {cx:W/2,cy:H/2,R:Math.min(W,H)*0.32};}
  function gridCols(){return Math.ceil(Math.sqrt(Math.max(1,order.length)));}
  function clusterWorld(idx){var cols=gridCols(),rows=Math.ceil(order.length/cols);var col=idx%cols,row=Math.floor(idx/cols);return {x:col*320-(cols*320)/2+160,y:row*320-(rows*320)/2+160};}
  function w2s(wx,wy){return {x:(wx-cam.x)*cam.z+W/2,y:(wy-cam.y)*cam.z+H/2};}
  // one node list with SCREEN positions for the current mode
  function nodesForDraw(){var out=[];
    if(mode==="all"){order.forEach(function(sid,si){var s=sessions[sid];if(!s)return;var c=clusterWorld(si),cs=w2s(c.x,c.y);var sessLive=(Date.now()/1000-s.last)<8;var names=Object.keys(s.agents);
      names.forEach(function(name,i){var key=posKey(sid,name);var st=POS[key]||(POS[key]={manual:false,x:0,y:0});var p;
        if(st.manual){p=w2s(st.x,st.y);}else{var ang=-Math.PI/2+i/Math.max(1,names.length)*Math.PI*2;p=w2s(c.x+Math.cos(ang)*90,c.y+Math.sin(ang)*90);}
        out.push({sid:sid,name:name,live:s.agents[name],sessLive:sessLive,col:s.agents[name].color||"#5f7a95",x:p.x,y:p.y,coreX:cs.x,coreY:cs.y});});});
    }else{var s=sessions[selected];if(!s)return out;var c=center();var names=Object.keys(s.agents);
      names.forEach(function(name,i){var key=posKey(selected,name);var st=POS[key]||(POS[key]={manual:false,x:0,y:0});var p;
        if(st.manual){p={x:st.x,y:st.y};}else{var ang=-Math.PI/2+i/Math.max(1,names.length)*Math.PI*2;p={x:c.cx+Math.cos(ang)*c.R,y:c.cy+Math.sin(ang)*c.R};}
        out.push({sid:selected,name:name,live:s.agents[name],sessLive:true,col:s.agents[name].color||"#5f7a95",x:p.x,y:p.y,coreX:c.cx,coreY:c.cy});});}
    return out;}
  function hit(mx,my){var ns=nodesForDraw();var rr=(mode==="all")?11:16;for(var i=ns.length-1;i>=0;i--){var dx=mx-ns[i].x,dy=my-ns[i].y;if(dx*dx+dy*dy<=rr*rr)return ns[i];}return null;}
  // in HUD·All, which session's central core is under the cursor (for drill-in)
  function hitCore(mx,my){if(mode!=="all")return null;for(var i=0;i<order.length;i++){var s=sessions[order[i]];if(!s)continue;var c=clusterWorld(i),p=w2s(c.x,c.y),r=26*cam.z;var dx=mx-p.x,dy=my-p.y;if(dx*dx+dy*dy<=r*r)return order[i];}return null;}
  function drawHud(now){var c=center();var ns=nodesForDraw();
    ns.forEach(function(nd){var active=(nd.live.state==="run"||nd.live.state==="ask");ctx.beginPath();ctx.moveTo(c.cx,c.cy);ctx.lineTo(nd.x,nd.y);ctx.strokeStyle=active?hexA(nd.col,.55):"rgba(90,122,149,.16)";ctx.lineWidth=active?1.6:1;ctx.stroke();});
    // concurrency web: faint pulsing ties between agents running at the same time
    var run=ns.filter(function(nd){return nd.live.state==="run"||nd.live.state==="ask";});
    for(var a=0;a<run.length;a++)for(var b=a+1;b<run.length;b++){ctx.beginPath();ctx.moveTo(run[a].x,run[a].y);ctx.lineTo(run[b].x,run[b].y);ctx.strokeStyle=hexA("#38e6ff",.06+0.05*(0.5+0.5*Math.sin(now/450)));ctx.lineWidth=1;ctx.stroke();}
    function nodeByName(nm){for(var j=0;j<ns.length;j++)if(ns[j].name===nm)return ns[j];return null;}
    for(var i=packets.length-1;i>=0;i--){var pk=packets[i];pk.t+=0.022;if(pk.t>=1){packets.splice(i,1);continue;}var t=nodeByName(pk.name);if(!t){packets.splice(i,1);continue;}
      var fx=c.cx,fy=c.cy;if(pk.from){var f=nodeByName(pk.from);if(f){fx=f.x;fy=f.y;}}
      var x=fx+(t.x-fx)*pk.t,y=fy+(t.y-fy)*pk.t;ctx.beginPath();ctx.arc(x,y,3.2,0,7);ctx.fillStyle=hexA(pk.col,1);ctx.shadowBlur=12;ctx.shadowColor=pk.col;ctx.fill();ctx.shadowBlur=0;}
    ns.forEach(function(nd){var st=nd.live.state;var active=(st==="run"||st==="ask");var hov=(nd.name===hoveredName||nd.name===inspectedName);
      if(st==="ask"){var ph=(now/700)%1;ctx.beginPath();ctx.arc(nd.x,nd.y,10+ph*60,0,7);ctx.strokeStyle=hexA("#fbbf24",(1-ph)*.45);ctx.lineWidth=1.5;ctx.stroke();}
      ctx.beginPath();ctx.arc(nd.x,nd.y,active?13:9,0,7);ctx.fillStyle=hexA(nd.col,active?.16:.07);ctx.fill();
      if(hov){ctx.beginPath();ctx.arc(nd.x,nd.y,16,0,7);ctx.strokeStyle=hexA(nd.col,.9);ctx.lineWidth=1.5;ctx.stroke();}
      ctx.beginPath();ctx.arc(nd.x,nd.y,active?6:4.5,0,7);ctx.fillStyle=st==="idle"?"#37506a":nd.col;ctx.shadowBlur=active?12:(hov?10:0);ctx.shadowColor=nd.col;ctx.fill();ctx.shadowBlur=0;
      if(st==="ask"){ctx.beginPath();ctx.arc(nd.x,nd.y,10+Math.sin(now/200)*2,0,7);ctx.strokeStyle=hexA("#fbbf24",.8);ctx.lineWidth=1.4;ctx.stroke();}
      ctx.font=(hov?"bold ":"")+"13px ui-monospace,Menlo,monospace";ctx.fillStyle=active||hov?"#cfe8ff":"#5f7a95";ctx.textAlign=nd.x<c.cx?"right":"left";ctx.textBaseline="middle";ctx.fillText(nd.name,nd.x+(nd.x<c.cx?-14:14),nd.y);});
    drawSessionTools(now,c);}
  function roundRectP(x,y,w,h,r){ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath();}
  function toolChip(x,y,tt,alpha){var T=TOOLTYPES[tt];ctx.globalAlpha=alpha;ctx.fillStyle=hexA(T.c,.16);roundRectP(x-14,y-8,28,16,4);ctx.fill();ctx.strokeStyle=hexA(T.c,.5);ctx.lineWidth=1;ctx.stroke();ctx.fillStyle=T.c;ctx.font="bold 8px ui-monospace,Menlo,monospace";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText(T.k,x,y);ctx.globalAlpha=1;}
  function drawSessionTools(now,c){toolBoxes.length=0;var s=sessions[selected];if(!s)return;var tools=s.tools||[];
    // ring of the most recent tools around the core (newest brightest)
    var ring=tools.slice(-9).reverse(),rr=c.R*0.44;
    ring.forEach(function(t,i){var ang=-Math.PI/2+i/9*Math.PI*2,x=c.cx+Math.cos(ang)*rr,y=c.cy+Math.sin(ang)*rr;toolChip(x,y,toolType(t.tool),Math.max(.16,1-i*0.1));toolBoxes.push({x:x,y:y,tool:t.tool,ts:t.ts});});
    // sparks: expanding ring at the core when a tool just fired
    for(var i=toolSparks.length-1;i>=0;i--){var sp=toolSparks[i];sp.t+=0.045;if(sp.t>=1){toolSparks.splice(i,1);continue;}var T=TOOLTYPES[toolType(sp.tool)];
      ctx.beginPath();ctx.arc(c.cx,c.cy,c.R*0.30+sp.t*40,0,7);ctx.strokeStyle=hexA(T.c,(1-sp.t)*.7);ctx.lineWidth=2;ctx.stroke();}
    // tape: recent tools as a row along the bottom
    var tape=tools.slice(-8),tx=c.cx-(tape.length-1)*20,ty=H-40;
    if(tape.length){ctx.fillStyle="#37506a";ctx.font="8px ui-monospace,Menlo,monospace";ctx.textAlign="center";ctx.textBaseline="bottom";ctx.fillText("recent tools · hover / click a chip",c.cx,ty-14);
      tape.forEach(function(t,i){var x=tx+i*40;toolChip(x,ty,toolType(t.tool),i===tape.length-1?1:.55);toolBoxes.push({x:x,y:ty,tool:t.tool,ts:t.ts});});}}
  function hitTool(mx,my){for(var i=toolBoxes.length-1;i>=0;i--){var b=toolBoxes[i];if(Math.abs(mx-b.x)<=15&&Math.abs(my-b.y)<=9)return b;}return null;}
  function showToolTip(b,mx,my){var ty=toolType(b.tool),T=TOOLTYPES[ty],s=sessions[selected];var n=(s&&s.tools||[]).filter(function(t){return toolType(t.tool)===ty;}).length;
    tip.style.setProperty("--tc",T.c);
    tip.innerHTML='<div class="th" style="color:'+T.c+'"><b>'+esc(b.tool)+'</b></div><div class="grp">'+esc(T.l)+' &middot; '+T.k+'</div>'+
      '<div class="row"><span>type</span><b>'+ty+'</b></div><div class="row"><span>this session</span><b>'+n+'&times;</b></div>'+
      (b.ts?'<div class="row"><span>at</span><b>'+hhmmss(b.ts)+'</b></div>':'')+
      '<div style="margin-top:.3rem;color:var(--hud-dim);font-size:.62rem">click for tool detail &rarr;</div>';
    tip.style.display="block";var tw=tip.offsetWidth,th=tip.offsetHeight,x=mx+16,y=my+16;if(x+tw>innerWidth-10)x=mx-tw-16;if(y+th>innerHeight-10)y=my-th-16;tip.style.left=x+"px";tip.style.top=y+"px";}
  function openToolInspector(toolName){var s=sessions[selected];if(!s)return;var ty=toolType(toolName),T=TOOLTYPES[ty];
    insp.style.setProperty("--ic",T.c);inspectedName=null;
    var uses=(s.tools||[]).filter(function(t){return t.tool===toolName;});
    var typeTotal=(s.tools||[]).filter(function(t){return toolType(t.tool)===ty;}).length;
    inspBody.innerHTML='<div class="ih" style="color:'+T.c+'"><span class="nm">'+esc(toolName)+'</span><span class="pill">'+T.k+'</span></div>'+
      '<div class="grp">'+esc(T.l)+' &middot; tool</div>'+
      '<div class="badgerow"><div class="mini"><b>'+uses.length+'</b><span>this tool</span></div><div class="mini"><b>'+typeTotal+'</b><span>'+T.k+' type</span></div></div>'+
      '<div class="sec">Recent uses this session</div>'+(uses.length?uses.slice(-20).reverse().map(function(t){return '<div class="ev"><span style="color:var(--hud-dim)">'+hhmmss(t.ts)+'</span><span class="ph-done">tool</span><span style="color:var(--hud-dim)">'+esc(toolName)+'</span></div>';}).join(""):'<div class="stat" style="color:var(--hud-dim)">no uses recorded</div>');
    insp.classList.add("open");}
  function baseCwd(s){return s&&s.cwd?s.cwd.replace(/[\\/]+$/,"").split(/[\\/]/).pop():"";}
  function drawAll(now){
    // session<->session ties: clusters sharing a repo/cwd get a dashed pulsing thread
    for(var a=0;a<order.length;a++)for(var b=a+1;b<order.length;b++){var sa=sessions[order[a]],sb=sessions[order[b]];if(!sa||!sb)continue;var ca=baseCwd(sa);if(!ca||ca!==baseCwd(sb))continue;
      var pa=w2s(clusterWorld(a).x,clusterWorld(a).y),pb=w2s(clusterWorld(b).x,clusterWorld(b).y);
      ctx.setLineDash([6,6]);ctx.lineDashOffset=-(now/40)%12;ctx.beginPath();ctx.moveTo(pa.x,pa.y);ctx.lineTo(pb.x,pb.y);ctx.strokeStyle=hexA("#38e6ff",.22+0.1*Math.sin(now/500));ctx.lineWidth=1.4;ctx.stroke();ctx.setLineDash([]);
      ctx.fillStyle=hexA("#38e6ff",.6);ctx.font=(9*Math.max(.85,cam.z))+"px ui-monospace,Menlo,monospace";ctx.textAlign="center";ctx.textBaseline="bottom";ctx.fillText("same repo",(pa.x+pb.x)/2,(pa.y+pb.y)/2-4);}
    order.forEach(function(sid,si){var s=sessions[sid];if(!s)return;var c=clusterWorld(si),cs=w2s(c.x,c.y);var live=(Date.now()/1000-s.last)<8;var coreR=26*cam.z;
      if(s._pulse>0){ctx.beginPath();ctx.arc(cs.x,cs.y,coreR+s._pulse*44,0,7);ctx.strokeStyle=hexA("#4ade80",s._pulse*.65);ctx.lineWidth=2;ctx.stroke();s._pulse=Math.max(0,s._pulse-0.02);}
      ctx.beginPath();ctx.arc(cs.x,cs.y,coreR,0,7);ctx.fillStyle=live?hexA("#38e6ff",.14):hexA("#37506a",.12);ctx.fill();
      ctx.beginPath();ctx.arc(cs.x,cs.y,coreR*.5,0,7);ctx.fillStyle=live?"#9fe9ff":"#37506a";if(live){ctx.shadowBlur=16;ctx.shadowColor="#38e6ff";}ctx.fill();ctx.shadowBlur=0;
      var run=Object.keys(s.agents).filter(function(k){var st=s.agents[k].state;return st==="run"||st==="ask";}).length;
      var cw=s.cwd?s.cwd.replace(/[\\/]+$/,"").split(/[\\/]/).pop():"";
      ctx.globalAlpha=live?1:.55;ctx.textAlign="center";ctx.textBaseline="top";
      ctx.font=(11*Math.max(.85,cam.z))+"px ui-monospace,Menlo,monospace";ctx.fillStyle=live?"#cfe8ff":"#5f7a95";ctx.fillText("Session "+(si+1)+(cw?" · "+cw:""),cs.x,cs.y+coreR+6);
      ctx.font=(9*Math.max(.85,cam.z))+"px ui-monospace,Menlo,monospace";ctx.fillStyle="#37506a";ctx.fillText(Object.keys(s.agents).length+" agents · "+run+" running",cs.x,cs.y+coreR+6+13*Math.max(.85,cam.z));
      ctx.globalAlpha=1;});
    var ns=nodesForDraw();
    ns.forEach(function(nd){var active=(nd.live.state==="run"||nd.live.state==="ask");ctx.beginPath();ctx.moveTo(nd.coreX,nd.coreY);ctx.lineTo(nd.x,nd.y);ctx.strokeStyle=active?hexA(nd.col,nd.sessLive?.5:.22):"rgba(90,122,149,.12)";ctx.lineWidth=1;ctx.stroke();});
    var z=Math.max(.7,cam.z);   // scale nodes with zoom so a zoomed-out galaxy isn't cluttered (matches the preview)
    ns.forEach(function(nd){var st=nd.live.state;var active=(st==="run"||st==="ask");var hov=(nd.sid===hoveredSid&&nd.name===hoveredName)||(nd.sid===selected&&nd.name===inspectedName);var r=(active?7:5)*z;ctx.globalAlpha=nd.sessLive?1:.45;
      ctx.beginPath();ctx.arc(nd.x,nd.y,r,0,7);ctx.fillStyle=st==="idle"?"#37506a":nd.col;if(active||hov){ctx.shadowBlur=10;ctx.shadowColor=nd.col;}ctx.fill();ctx.shadowBlur=0;
      if(hov){ctx.beginPath();ctx.arc(nd.x,nd.y,r+6,0,7);ctx.strokeStyle=hexA(nd.col,.9);ctx.lineWidth=1.5;ctx.stroke();}
      if(st==="ask"){ctx.beginPath();ctx.arc(nd.x,nd.y,r+4+Math.sin(now/200)*2,0,7);ctx.strokeStyle=hexA("#fbbf24",.8);ctx.lineWidth=1.3;ctx.stroke();}
      ctx.globalAlpha=1;});
    drawMinimap();drawEdgeArrows();}
  function draw(now){ctx.clearRect(0,0,W,H);if(mode==="all")drawAll(now);else if(mode==="hud"||mode==="dash")drawHud(now);requestAnimationFrame(draw);}
  function drawMinimap(){var mc=$("mini");if(!mc)return;var g=mc.getContext("2d"),w=160,h=120;if(mc.width!==w*DPR){mc.width=w*DPR;mc.height=h*DPR;g.setTransform(DPR,0,0,DPR,0,0);}g.clearRect(0,0,w,h);
    var cols=gridCols(),rows=Math.ceil(order.length/cols),gw=(cols*320)||320,gh=(rows*320)||320,pad=10,s=Math.min((w-2*pad)/gw,(h-2*pad)/gh);
    function m(wx,wy){return {x:w/2+wx*s,y:h/2+wy*s};}
    order.forEach(function(sid,i){var se=sessions[sid];if(!se)return;var c=clusterWorld(i),p=m(c.x,c.y),live=(Date.now()/1000-se.last)<8;g.beginPath();g.arc(p.x,p.y,4,0,7);g.fillStyle=live?"#38e6ff":"#37506a";g.fill();});
    var tl=m(cam.x-(W/2)/cam.z,cam.y-(H/2)/cam.z),br=m(cam.x+(W/2)/cam.z,cam.y+(H/2)/cam.z);g.strokeStyle="rgba(56,230,255,.8)";g.lineWidth=1;g.strokeRect(tl.x,tl.y,br.x-tl.x,br.y-tl.y);}
  function drawEdgeArrows(){var n=0;order.forEach(function(sid,i){var se=sessions[sid];if(!se||(Date.now()/1000-se.last)>=8)return;var c=clusterWorld(i),p=w2s(c.x,c.y);if(p.x>=-20&&p.x<=W+20&&p.y>=-20&&p.y<=H+20)return;if(n>=6)return;n++;
    var ang=Math.atan2(p.y-H/2,p.x-W/2),rad=Math.min(W,H)/2-40,ex=W/2+Math.cos(ang)*rad,ey=H/2+Math.sin(ang)*rad;
    ctx.save();ctx.translate(ex,ey);ctx.rotate(ang);ctx.fillStyle="rgba(56,230,255,.9)";ctx.beginPath();ctx.moveTo(8,0);ctx.lineTo(-6,-6);ctx.lineTo(-6,6);ctx.closePath();ctx.fill();ctx.restore();
    ctx.fillStyle="#38e6ff";ctx.font="10px ui-monospace,Menlo,monospace";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText(""+(i+1),W/2+Math.cos(ang)*(rad-16),H/2+Math.sin(ang)*(rad-16));});}
  function zoomAt(mx,my,f){var wx=cam.x+(mx-W/2)/cam.z,wy=cam.y+(my-H/2)/cam.z;cam.z=Math.max(0.35,Math.min(2.5,cam.z*f));cam.x=wx-(mx-W/2)/cam.z;cam.y=wy-(my-H/2)/cam.z;updateZoom();}
  function recenter(){cam.x=0;cam.y=0;cam.z=0.8;updateZoom();}
  function updateZoom(){var z=$("zread");if(z)z.textContent=Math.round(cam.z*100)+"%";}

  var hoveredName=null,hoveredSid=null,inspectedName=null,dragName=null,dragSid=null,dragMoved=false,downAt=null,panning=false,panStart=null,didPan=false;
  var tip=$("tip");
  function showTip(nd,mx,my){var st=nd.live.state;var m=metaOf(nd.name);
    var lbl={run:'<span class="live">running</span>',ask:'<span class="liveask">awaiting reply</span>',done:"done",error:"error",idle:"idle"}[st]||st;
    tip.style.setProperty("--tc",nd.col);
    var rows='<div class="th" style="color:'+nd.col+'">'+iconSlug(slugOf(nd.live.group),"a-ico")+'<b>'+esc(nd.name)+'</b></div>'+
      '<div class="grp">'+esc(nd.live.group||"—")+'</div>'+
      '<div class="row"><span>state</span><b>'+lbl+'</b></div>';
    if(nd.live.tool)rows+='<div class="row"><span>tool</span><b>'+esc(nd.live.tool)+'</b></div>';
    if(m){rows+='<div class="row"><span>grade</span><b>'+esc(m.grade)+'</b></div>'+
      '<div class="row"><span>used</span><b>'+m.used+'&times;</b></div>'+
      '<div class="row"><span>cost/call</span><b>~'+fmtTok(m.cost)+'</b></div>'+
      (m.score?'<div class="row"><span>score</span><b>'+esc(m.score)+'</b></div>':'');}
    rows+='<div style="margin-top:.35rem;color:var(--hud-dim);font-size:.62rem">click for full inspector &rarr;</div>';
    tip.innerHTML=rows;tip.style.display="block";
    var tw=tip.offsetWidth,th=tip.offsetHeight,x=mx+16,y=my+16;
    if(x+tw>innerWidth-10)x=mx-tw-16;if(y+th>innerHeight-10)y=my-th-16;tip.style.left=x+"px";tip.style.top=y+"px";}
  function hideTip(){tip.style.display="none";}
  function interactive(){return mode==="hud"||mode==="all";}
  cv.addEventListener("mousemove",function(e){if(!interactive())return;var mx=e.clientX,my=e.clientY;
    if(dragName){var dx=mx-downAt.x,dy=my-downAt.y;if(dragMoved||dx*dx+dy*dy>16){dragMoved=true;var k=posKey(dragSid,dragName);var st=POS[k]||(POS[k]={});st.manual=true;if(mode==="all"){st.x=cam.x+(mx-W/2)/cam.z;st.y=cam.y+(my-H/2)/cam.z;}else{st.x=mx;st.y=my;}}return;}
    if(panning){didPan=true;cam.x=panStart.cx-(mx-panStart.x)/cam.z;cam.y=panStart.cy-(my-panStart.y)/cam.z;return;}
    if(mode==="hud"){var tb=hitTool(mx,my);if(tb){hoveredName=null;hoveredSid=null;cv.style.cursor="pointer";showToolTip(tb,mx,my);return;}}
    var nd=hit(mx,my);hoveredName=nd?nd.name:null;hoveredSid=nd?nd.sid:null;cv.style.cursor=nd?"grab":(mode==="all"?(hitCore(mx,my)?"pointer":"grab"):"default");
    if(nd)showTip(nd,mx,my);else hideTip();});
  cv.addEventListener("mousedown",function(e){if(!interactive())return;didPan=false;dragMoved=false;var nd=hit(e.clientX,e.clientY);
    if(nd){dragName=nd.name;dragSid=nd.sid;dragMoved=false;downAt={x:e.clientX,y:e.clientY};cv.style.cursor="grabbing";hideTip();}
    else if(mode==="all"){panning=true;panStart={x:e.clientX,y:e.clientY,cx:cam.x,cy:cam.y};cv.style.cursor="grabbing";}});
  addEventListener("mouseup",function(){if(dragName){if(!dragMoved){if(mode==="all")selected=dragSid;openInspector(dragName);}cv.style.cursor="grab";dragName=null;return;}if(panning){panning=false;cv.style.cursor="grab";}});
  cv.addEventListener("dblclick",function(e){if(mode==="all"){recenter();return;}if(mode!=="hud")return;var nd=hit(e.clientX,e.clientY);if(nd){var k=posKey(nd.sid,nd.name);if(POS[k])POS[k].manual=false;}});
  cv.addEventListener("wheel",function(e){if(mode!=="all")return;e.preventDefault();zoomAt(e.clientX,e.clientY,e.deltaY<0?1.1:1/1.1);},{passive:false});
  // right-click the central core in single-session HUD -> back to HUD·All (and never the browser menu on the canvas)
  cv.addEventListener("contextmenu",function(e){e.preventDefault();if(mode==="hud"){var c=center();var dx=e.clientX-c.cx,dy=e.clientY-c.cy;if(dx*dx+dy*dy<=(c.R*0.34)*(c.R*0.34))setMode("all");}});
  addEventListener("keydown",function(e){if(mode!=="all")return;if($("docmodal").classList.contains("open"))return;var n=40/cam.z;
    if(e.key==="ArrowLeft")cam.x-=n;else if(e.key==="ArrowRight")cam.x+=n;else if(e.key==="ArrowUp")cam.y-=n;else if(e.key==="ArrowDown")cam.y+=n;
    else if(e.key==="+"||e.key==="=")zoomAt(W/2,H/2,1.1);else if(e.key==="-")zoomAt(W/2,H/2,1/1.1);else if(e.key==="0"||e.key==="Home")recenter();else return;e.preventDefault();});
  $("zin").onclick=function(){zoomAt(W/2,H/2,1.1);};$("zout").onclick=function(){zoomAt(W/2,H/2,1/1.1);};$("rc").onclick=recenter;
  $("mini").addEventListener("click",function(e){var r=$("mini").getBoundingClientRect(),w=160,h=120;var cols=gridCols(),rows=Math.ceil(order.length/cols),gw=(cols*320)||320,gh=(rows*320)||320,pad=10,s=Math.min((w-2*pad)/gw,(h-2*pad)/gh);cam.x=((e.clientX-r.left)-w/2)/s;cam.y=((e.clientY-r.top)-h/2)/s;});

  /* ---------- inspector ---------- */
  var insp=$("insp"),inspBody=$("insp-body");
  function closeInsp(){insp.classList.remove("open");inspectedName=null;}
  $("insp-x").onclick=closeInsp;
  addEventListener("keydown",function(e){if(e.key==="Escape"&&insp.classList.contains("open"))closeInsp();});
  cv.addEventListener("click",function(e){if(mode!=="hud"&&mode!=="all")return;if(dragName||dragMoved)return;if(didPan){didPan=false;return;}
    if(mode==="hud"){var tb=hitTool(e.clientX,e.clientY);if(tb){openToolInspector(tb.tool);return;}}
    var nd=hit(e.clientX,e.clientY);if(nd)return;
    if(mode==="all"){var cs=hitCore(e.clientX,e.clientY);if(cs){selected=cs;setMode("hud");return;}}
    if(insp.classList.contains("open"))closeInsp();});
  function openInspector(name){var s=sessions[selected];if(!s)return;var live=s.agents[name]||{state:"idle",group:""};var m=metaOf(name);
    var col=live.color||(m?colorFor(m.group):"#5f7a95");
    var lbl=STATE_LABEL[live.state]||live.state||"idle";
    insp.style.setProperty("--ic",col);inspectedName=name;
    var hist=(s.events||[]).filter(function(e){return e.agent===name;}).slice(-30).reverse();
    var html='<div class="ih" style="color:'+col+'">'+iconSlug(slugOf(live.group||(m&&m.group)),"a-ico")+'<span class="nm">'+esc(name)+'</span>'+
      (m?'<span class="pill">'+esc(m.grade)+'</span>':'')+'</div>'+
      '<div class="grp">'+esc((live.group||(m&&m.group))||"—")+(m?(' &middot; '+esc(m.model)):'')+'</div>';
    if(m)html+='<div class="badgerow"><div class="mini"><b>'+m.used+'</b><span>used</span></div>'+
      '<div class="mini"><b>~'+fmtTok(m.cost)+'</b><span>tok/call</span></div>'+
      '<div class="mini"><b>'+(m.score||"—")+'</b><span>score</span></div></div>';
    html+='<div class="sec">Now</div><div class="stat"><span>state</span><b style="color:'+col+'">'+esc(lbl)+'</b></div>';
    if(live.tool)html+='<div class="stat"><span>current tool</span><b>'+esc(live.tool)+'</b></div>';
    if(m&&m.last)html+='<div class="stat"><span>last active (all time)</span><b>'+esc(m.last)+'</b></div>';
    if(m&&m.desc)html+='<div class="sec">What it does</div><div class="desc">'+esc(m.desc)+'</div>';
    if(m)html+='<div class="sec">Preloaded skills</div><div class="chips">'+(m.pre&&m.pre.length?m.pre.map(function(p){return '<span class="chip">'+esc(p)+'</span>';}).join(""):'<span class="chip" style="color:var(--hud-dim)">none</span>')+'</div>';
    html+='<div class="sec">History this session</div>'+(hist.length?hist.map(function(e){return '<div class="ev"><span style="color:var(--hud-dim)">'+hhmmss(e.ts)+'</span><span class="ph-'+esc(e.phase)+'">'+esc(e.phase)+'</span><span style="color:var(--hud-dim)">'+(e.tool?esc(e.tool):'')+'</span></div>';}).join(""):'<div class="stat" style="color:var(--hud-dim)">no events yet</div>');
    inspBody.innerHTML=html;insp.classList.add("open");}

  /* ---------- HUD panels ---------- */
  function renderHud(){var s=sessions[selected];$("hud-empty").style.display=s?"none":"";
    var ht=$("hud-tabs");ht.innerHTML="";
    order.forEach(function(id,i){var ss=sessions[id];if(!ss)return;var live=(Date.now()/1000-ss.last)<8;
      var d=document.createElement("div");d.className="st-tab"+(id===selected?" sel":"")+(live?" live":"");d.textContent="Session "+(i+1);
      d.onclick=function(){selected=id;renderAll();};ht.appendChild(d);});
    var ag=$("hud-agents"),c=(s&&s.counts)||{};
    if(!s){ag.innerHTML="";}else{var ags=Object.keys(s.agents).map(function(k){return s.agents[k];}).sort(function(a,b){return (b.ts||0)-(a.ts||0);});
      ag.innerHTML=ags.map(function(a){var lbl={run:"running",done:"done",ask:"waiting",error:"error",idle:"—"}[a.state]||a.state;
        return '<div class="agentline '+(a.state==="run"?"run":"")+'" data-n="'+esc(a.name)+'" style="--ac:'+(a.color||"#5f7a95")+'"><span class="sd"></span><span class="n">'+esc(a.name)+'</span><span class="st">'+lbl+'</span></div>';}).join("")||'<div class="agentline" style="--ac:#37506a">—</div>';
      Array.prototype.forEach.call(ag.children,function(el){var n=el.getAttribute("data-n");if(n)el.onclick=function(){openInspector(n);};});}
    $("k-run").textContent=s?Object.keys(s.agents).filter(function(k){var st=s.agents[k].state;return st==="run"||st==="ask";}).length:0;
    $("k-done").textContent=c.done||0;$("k-ask").textContent=c.ask||0;$("k-err").textContent=c.error||0;
    var fd=$("hud-feed");var counts={};(s&&s.tools||[]).forEach(function(t){var ty=toolType(t.tool);counts[ty]=(counts[ty]||0)+1;});
    fd.innerHTML=TORDER.map(function(ty){var T=TOOLTYPES[ty];var n=counts[ty]||0;
      return '<div style="display:flex;align-items:center;gap:.4rem;padding:.1rem 0;'+(n?'':'opacity:.45')+'"><span style="width:30px;text-align:center;font-weight:700;font-size:.58rem;color:'+T.c+';border:1px solid '+T.c+';border-radius:4px">'+T.k+'</span><span style="flex:1;color:var(--hud-mut)">'+T.l+'</span>'+(n?'<b style="color:'+T.c+'">'+n+'</b>':'')+'</div>';}).join("");}

  /* ---------- Testovi rail (docked to the window's right edge, HUD·All ONLY) ----------
     Shows live test runs while HUD·All is active. State is two arrays of run objects
     in the BACKEND contract shape (NOT the preview's field names):
       run = {run_id, session, cmd, total, done, passed, failed,
              status:"running"|"passed"|"failed"|"error", started, ended, fails:[]}
     Seed with GET /api/tests -> {active:[…], recent:[…]}; live deltas arrive on the
     SSE stream as {type:"testrun", run:{…}} and are dispatched from core.js into
     applyTestRun (a top-level global here — the split files share one scope).
     The rail element is created from JS and appended to <body> because index.html is
     off-limits for this change; app.css scopes its visibility to body.v-all. */
  var testActive=[],testRecent=[],testRailEl=null;
  function testRailEnsure(){if(testRailEl)return testRailEl;
    var el=document.createElement("div");el.className="testrail";el.setAttribute("aria-hidden","true");
    el.innerHTML='<h3>&#129514; Testovi <span class="tr-cnt" id="tr-cnt"></span></h3>'+
      '<div class="tr-scroll"><div id="tr-active"></div>'+
      '<div class="tr-rh" id="tr-recent-h" hidden>Skora&#353;nji</div><div id="tr-recent"></div></div>'+
      '<div class="tr-note"><b>Dokovano uz desnu ivicu.</b> Live N/M dok test radi + ishod kad se zavr&#353;i.</div>';
    (document.body||document.documentElement).appendChild(el);testRailEl=el;return el;}
  // started/ended are epoch seconds (tolerate ms); elapsed in seconds, or null if unknown.
  function testElapsed(run){var s=run&&run.started;if(s==null)return null;
    function toS(v){v=+v;return v>1e12?v/1000:v;}s=toS(s);
    var end=(run.ended!=null)?toS(run.ended):(Date.now()/1000);return Math.max(0,end-s);}
  function testDur(sec){if(sec==null)return "";sec=Math.round(sec);
    if(sec<60)return sec+"s";var m=Math.floor(sec/60),r=sec%60;return m+"m"+(r?(" "+r+"s"):"");}
  function testRunCard(run){
    var status=run.status||"running",running=(status==="running");
    var total=+run.total||0,done=+run.done||0,passed=+run.passed||0,failed=+run.failed||0;
    var bad=(status==="failed"||status==="error"||failed>0);
    var cls=running?"":(bad?"fail":"done");
    var icon=running?'<span class="tr-spin"></span>':(bad?'&#10007;':'&#10003;');
    var el=testElapsed(run),elTxt=(el!=null)?testDur(el):"";
    var right=running?(el!=null?("radi &middot; "+esc(elTxt)):"radi")
                      :("gotovo"+(elTxt?(" &middot; "+esc(elTxt)):""));
    var h='<div class="tr-run '+cls+'">'+
      '<div class="tr-cmd">'+icon+'<span class="tr-cmdtxt">'+esc(run.cmd||"test")+'</span></div>'+
      '<div class="tr-sub"><span>sesija &middot; '+esc(run.session||"—")+'</span>'+
        '<span'+(running?(' data-testel="'+esc(String(run.run_id))+'"'):'')+'>'+right+'</span></div>';
    if(total>0){                                   // N/M bar once the wrapper streams a total
      var passW=(passed/total*100),failW=(failed/total*100),pct=Math.round(done/total*100);
      h+='<div class="tr-bar"><div class="tr-pass" style="width:'+passW.toFixed(1)+'%"></div>'+
         '<div class="tr-fail" style="width:'+failW.toFixed(1)+'%"></div></div>'+
         '<div class="tr-nums"><span class="tr-p">&#10003; '+passed+'</span>'+
         (failed?('<span class="tr-f">&#10007; '+failed+'</span>'):'')+
         '<span class="tr-t">'+done+' / '+total+' ('+pct+'%)</span></div>';
    }else if(running){                             // hook 'start' before a total -> no bar yet
      h+='<div class="tr-nums"><span class="tr-t">zapo&#269;eto&hellip;</span></div>';
    }
    var fails=(run.fails||[]).slice(0,4);
    if(fails.length)h+='<div class="tr-fails">'+fails.map(function(x){return '<div>&#10007; '+esc(String(x))+'</div>';}).join("")+'</div>';
    return h+'</div>';}
  function testRecentRow(run){
    var status=run.status||"",failed=+run.failed||0,total=+run.total||0,passed=+run.passed||0;
    var ok=(status==="passed"&&!failed);
    var res=ok?((passed||total||"?")+"/"+(total||passed||"?"))
              :(status==="error"?"gre&#353;ka":((failed||"?")+" palo"+(total?(" / "+total):"")));
    var el=testElapsed(run),dur=(el!=null)?testDur(el):"";
    return '<div class="tr-rrow"><span class="tr-rc">'+esc(run.cmd||"test")+'</span>'+
      '<span class="tr-rr '+(ok?"ok":"bad")+'">'+(ok?'&#10003; ':'&#10007; ')+res+'</span>'+
      '<span class="tr-rd">'+esc(dur)+'</span></div>';}
  function renderTestRail(){testRailEnsure();
    var cnt=$("tr-cnt");if(cnt)cnt.textContent=testActive.length?(testActive.length+" u toku"):"";
    var a=$("tr-active");if(a)a.innerHTML=testActive.length?testActive.map(testRunCard).join(""):'<div class="tr-empty">Nema aktivnih testova</div>';
    var r=$("tr-recent");if(r)r.innerHTML=testRecent.map(testRecentRow).join("");
    var rh=$("tr-recent-h");if(rh)rh.hidden=(testRecent.length===0);}
  // SSE upsert by run_id; a terminal status moves the run from active -> recent.
  function applyTestRun(run){if(!run||run.run_id==null)return;var id=run.run_id;
    var terminal=(run.status==="passed"||run.status==="failed"||run.status==="error");
    testActive=testActive.filter(function(x){return x.run_id!==id;});
    if(terminal){testRecent=testRecent.filter(function(x){return x.run_id!==id;});
      testRecent.unshift(run);if(testRecent.length>8)testRecent=testRecent.slice(0,8);}
    else testActive.push(run);
    renderTestRail();}
  function testsFetch(){fetch("/api/tests").then(function(r){return r.ok?r.json():null;}).then(function(d){
    if(!d)return;testActive=(d.active||[]).slice();testRecent=(d.recent||[]).slice();renderTestRail();}).catch(function(){});}
  // Tick the elapsed clock of each running card in place (no re-render -> the spinner
  // never restarts and the N/M bar doesn't flicker between SSE deltas).
  function testTickElapsed(){if(!testRailEl)return;var byId={};
    testActive.forEach(function(run){byId[run.run_id]=run;});
    var nodes=testRailEl.querySelectorAll("[data-testel]");
    for(var i=0;i<nodes.length;i++){var n=nodes[i],run=byId[n.getAttribute("data-testel")];if(!run)continue;
      var e=testElapsed(run);n.innerHTML=(e!=null)?("radi &middot; "+esc(testDur(e))):"radi";}}
  testRailEnsure();renderTestRail();      // build the (empty-state) rail up front so v-all has a target
  // Own timers (setMode + the boot.js poll block are off-limits): seed on entering
  // v-all, a light refetch every ~6s, and the elapsed tick every ~1s — all gated to v-all.
  var _testsPrevMode=null,_testsTick=0;
  setInterval(function(){var inAll=(mode==="all");
    if(inAll&&_testsPrevMode!=="all")testsFetch();   // seed the moment HUD·All opens
    _testsPrevMode=mode;if(!inAll)return;
    if((++_testsTick)%6===0)testsFetch();            // light refetch
    testTickElapsed();},1000);

