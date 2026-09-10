  /* ---------- Ask Claude (discoverable top-level entry) ---------- */
  function openClaude(){var p=$("claude-panel");
    if(typeof closeVoice==="function")closeVoice();   // same position + z-index: never both open
    p.classList.add("open");var s=$("claude-status");if(s){s.textContent="";s.className="cl-status";}var i=$("claude-in");if(i)i.focus();}
  function closeClaude(){$("claude-panel").classList.remove("open");}
  function claudeLaunch(){var ta=$("claude-in");if(!ta)return;var prompt=(ta.value||"").trim();var st=$("claude-status");
    if(!prompt){ta.focus();return;}
    var btn=$("claude-send");if(btn)btn.disabled=true;if(st){st.textContent="Launching…";st.className="cl-status";}
    aiPost("/api/claude/launch",{prompt:prompt}).then(function(d){
      if(btn)btn.disabled=false;
      if(d.error){if(st){st.textContent="Launch failed: "+d.error;st.className="cl-status err";}return;}
      if(st){st.textContent="Claude session launched";st.className="cl-status ok";}ta.value="";
      setTimeout(function(){if($("claude-panel").classList.contains("open"))closeClaude();},1400);
    }).catch(function(e){if(btn)btn.disabled=false;if(st){st.textContent="Launch failed: "+aiErrText(e);st.className="cl-status err";}});}
  $("askclaude").onclick=openClaude;$("claude-x").onclick=closeClaude;$("claude-send").onclick=claudeLaunch;
  $("claude-in").onkeydown=function(e){if((e.ctrlKey||e.metaKey)&&e.key==="Enter"){e.preventDefault();claudeLaunch();}};
  addEventListener("keydown",function(e){if(e.key!=="Escape")return;
    if($("voice-panel").classList.contains("open"))closeVoice();   // also STOPS listening
    else if($("claude-panel").classList.contains("open"))closeClaude();else if($("promptmodal").classList.contains("open"))closePrompt();});

  /* ---------- F6 glasovna komanda: panel wiring + the global shortcut ---------- */
  $("voice-mic").onclick=function(){if($("voice-panel").classList.contains("open"))closeVoice();else openVoice();};
  $("voice-x").onclick=closeVoice;
  $("voice-listen").onclick=voiceToggleListen;
  $("voice-lang").onclick=voiceToggleLang;
  $("voice-plan").onclick=voicePlan;
  $("voice-run").onclick=voiceRun;
  // Enter in the transcript box inserts a newline and NEVER runs anything; Ctrl/Cmd+Enter
  // sends to /api/voice/plan only — execution always needs the "Izvrsi oznacene" click.
  $("voice-text").onkeydown=function(e){
    if(e.key!=="Enter")return;
    if(e.ctrlKey||e.metaKey){e.preventDefault();voicePlan();}};
  // Ctrl+Shift+Space toggles LISTENING from anywhere — except while typing in some other
  // input/textarea/contenteditable, where the operator is writing, not commanding. The panel's
  // own transcript box is the one field where the shortcut still fires. e.code is used because
  // e.key for Space under Ctrl+Shift is a plain " " on some layouts.
  addEventListener("keydown",function(e){
    if(!e.ctrlKey||!e.shiftKey||e.altKey)return;
    if(e.code!=="Space"&&e.key!==" "&&e.key!=="Spacebar")return;
    var t=e.target||{},tag=String(t.tagName||"").toLowerCase();
    if((tag==="input"||tag==="textarea"||t.isContentEditable)&&t.id!=="voice-text")return;
    e.preventDefault();voiceShortcut();});

  function setMode(m){mode=m;localStorage.setItem("av_mode",m);document.body.className="v-"+m;
    $("mode-hud").classList.toggle("on",m==="hud"||m==="all");$("mode-table").classList.toggle("on",m==="table");$("mode-dash").classList.toggle("on",m==="dash");$("mode-flow").classList.toggle("on",m==="flow");$("mode-tickets").classList.toggle("on",m==="tickets");$("mode-brain").classList.toggle("on",m==="brain");$("mode-git").classList.toggle("on",m==="git");$("mode-prod").classList.toggle("on",m==="prod");$("mode-mail").classList.toggle("on",m==="mail");$("mode-profile").classList.toggle("on",m==="profile");$("mode-calendar").classList.toggle("on",m==="calendar");
    hoveredName=null;hoveredSid=null;if((m==="table"||m==="dash"||m==="flow"||m==="mail"||m==="tickets"||m==="brain"||m==="git"||m==="prod"||m==="profile"||m==="calendar")&&inspectedName)closeInsp();
    var hint=document.querySelector(".hud-hint");if(hint)hint.innerHTML=(m==="all")
      ?'<b>drag</b> = pan &middot; <b>wheel</b> = zoom &middot; <b>click core</b> = open session &middot; <b>click node</b> = inspect &middot; <kbd>0</kbd> recenter'
      :'<b>hover</b> = details &middot; <b>click</b> = inspector &middot; <b>drag</b> = move &middot; <b>right-click core</b> = All &middot; <kbd>dbl</kbd> = reset';
    if(m==="hud"||m==="dash"||m==="all")resize();if(m==="dash")buildDashboard();if(m==="all")updateZoom();if(m==="flow")buildFlow();
    // capsGate() paints "not configured, here is what to send Claude" and returns
    // false, so the view's own init never runs against a helpdesk that is not
    // there. It returns true whenever the capability is ready OR unknown -- an
    // unreachable /api/capabilities must not hide a tab that works.
    if(m==="tickets"&&capsGate(m))fetchTickets();
    if(m==="brain")fetchBrainLog();
    if(m==="git"&&capsGate(m))gitInit();
    if(m==="prod"&&capsGate(m))prodInit();
    if(m==="mail"&&capsGate(m))mailInit();
    if(m==="profile")profileInit();if(m==="calendar")calendarInit();renderAll();}
  $("mode-hud").onclick=function(){setMode("hud");};$("mode-hud").oncontextmenu=function(e){e.preventDefault();setMode("all");};$("mode-table").onclick=function(){setMode("table");};$("mode-dash").onclick=function(){setMode("dash");};$("mode-flow").onclick=function(){setMode("flow");};$("mode-tickets").onclick=function(){setMode("tickets");};$("mode-brain").onclick=function(){setMode("brain");};$("mode-git").onclick=function(){setMode("git");};$("mode-prod").onclick=function(){setMode("prod");};$("mode-mail").onclick=function(){setMode("mail");};$("mode-profile").onclick=function(){setMode("profile");};$("mode-calendar").onclick=function(){setMode("calendar");};
  $sound.classList.toggle("act",soundOn);$sound.innerHTML=soundOn?"&#128266;":"&#128263;";
  $sound.onclick=function(){soundOn=!soundOn;localStorage.setItem("av_sound",soundOn?"1":"0");$sound.classList.toggle("act",soundOn);$sound.innerHTML=soundOn?"&#128266;":"&#128263;";if(soundOn)playFor("done","human");};
  $voice.classList.toggle("act",voiceOn);$voice.innerHTML=voiceOn?"&#128483;":"&#128263;";
  if(!canSpeak){$voice.disabled=true;$voice.title="This browser has no Web Speech API";}
  $voice.onclick=function(){voiceOn=!voiceOn;localStorage.setItem("av_voice",voiceOn?"1":"0");$voice.classList.toggle("act",voiceOn);$voice.innerHTML=voiceOn?"&#128483;":"&#128263;";if(voiceOn)speak("voice on");};
  if(canSpeak){refreshVoices();refreshVoiceSelect();speechSynthesis.onvoiceschanged=function(){refreshVoices();refreshVoiceSelect();};}
  $("stopsrv").onclick=function(){
    if(!confirm("Stop the Live Agent View server? The page will stop updating."))return;
    fetch("/shutdown",{method:"POST"}).catch(function(){});
    setTimeout(function(){document.body.innerHTML=
      '<div style="display:grid;place-items:center;height:100vh;font-family:ui-monospace,Menlo,monospace;color:#5f7a95;background:#05080d;text-align:center;line-height:1.8">'+
      '<div>Server stopped.<br><span style="font-size:.8rem;color:#37506a">You can close this tab. Relaunch from the desktop shortcut.</span></div></div>';},450);
  };

  /* ---------- server-address chip (LAN ip:port so the tool opens on a phone) ---------- */
  function loadServerInfo(){fetch("/api/serverinfo").then(function(r){return r.json();}).then(function(d){
    if(!d||(!d.url&&!d.ip))return;                       // nothing usable -> leave the chip hidden
    var label=d.ip?(d.ip+(d.port?(":"+d.port):"")):String(d.url);
    var copy=d.url||label,el=$("ipchip");if(!el)return;
    el.setAttribute("data-copy",copy);el.setAttribute("data-label",label);
    el.innerHTML='&#128246; '+esc(label);                // 📶 signal glyph
    el.style.display="";
    el.onclick=function(){var text=el.getAttribute("data-copy")||label;
      function done(){el.innerHTML="&#10003; copied";el.classList.add("act");
        setTimeout(function(){el.innerHTML='&#128246; '+esc(el.getAttribute("data-label")||label);el.classList.remove("act");},1200);}
      if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(done,done);}
      else{try{var ta=document.createElement("textarea");ta.value=text;ta.style.position="fixed";ta.style.opacity="0";
        document.body.appendChild(ta);ta.select();document.execCommand("copy");document.body.removeChild(ta);}catch(e){}done();}};
  }).catch(function(){});}                               // fetch failed -> chip stays hidden

  setInterval(function(){renderTabs();renderHud();},3000);
  setInterval(fetchMeta,60000);
  // Git view live polls (only while the Git tab is open): repos+glow ~4s, selected repo ~15s, github ~20s
  setInterval(function(){if(mode==="git")gitFetchRepos(true);},4000);
  setInterval(function(){if(mode==="git"&&gitSel)gitFetchRepo(gitSel,true);},15000);
  setInterval(function(){if(mode==="git"&&gitSel)gitFetchGithub(gitSel,true);},20000);
  // Production view live poll (only while the Prod tab is open); the backend caches 5s so 8s is cheap.
  setInterval(function(){if(mode==="prod")prodFetchOverview(true);},8000);
  // The capability snapshot decides what several tabs may render, so the first
  // setMode() runs INSIDE the callback rather than racing it. Without that, a
  // fresh clone shows the Tiketi tab's own empty state for a moment -- the exact
  // "is it broken or unconfigured?" the gate exists to end.
  capsFetch(function(){
    setMode(mode);resize();fetchMeta();connect();loadServerInfo();focusInit();gameInit();
    requestAnimationFrame(draw);
  });
