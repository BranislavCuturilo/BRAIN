  /* ---------- GLASOVNA KOMANDA (F6) ----------
     Mic (or, on a browser with no Web Speech, the plain text box) -> a transcript the
     operator can EDIT -> POST /api/voice/plan -> one row per step with its exact payload
     visible -> the operator ticks rows -> POST /api/voice/run.

     Four rules this file exists to keep:
       1. NOTHING is sent or executed without a click. Speech only ever fills the textarea;
          Ctrl+Enter sends to /plan (never to /run); every step needs its own checkbox, and
          a `high` risk step starts UNTICKED.
       2. The transcript is never persisted and never logged - no localStorage, no console.
          (The language CODE is persisted; that is a setting, not content.)
       3. Everything the server sends back is untrusted text: esc() into HTML, textContent
          otherwise. The payload is printed VERBATIM - a shell command nobody can read is a
          shell command nobody can approve.
       4. This file never decides what may run. The server validates every step twice (once
          when it signs it, again at run time); the page only shows what it sent and hands
          back the tokens for the rows that were ticked.

     ASCII-only strings on purpose (same as voice.py's operator text), so the file is safe
     under any editor/console encoding. */

  var VOICE={rec:null,on:false,lang:"sr-RS",plan:null,busy:false,done:false,restarts:0,lastStart:0};
  var VC_LANGS=["sr-RS","en-US"];
  var VC_RISK={none:"bez rizika",low:"nizak rizik",high:"VISOK RIZIK"};
  // hud open_view -> a mode setMode() understands. Serbian synonyms included because the
  // operator speaks Serbian and the model echoes the word it heard.
  var VC_VIEWS={hud:"hud",all:"all",sve:"all",table:"table",tabela:"table",dash:"dash",dashboard:"dash",
    flow:"flow",tok:"flow",tickets:"tickets",tiketi:"tickets",brain:"brain",mozak:"brain",git:"git",
    prod:"prod",produkcija:"prod",mail:"mail",posta:"mail",profile:"profile",profil:"profile",
    calendar:"calendar",kalendar:"calendar"};

  (function(){try{var v=localStorage.getItem("av_voice_lang");
    if(VC_LANGS.indexOf(v)>=0)VOICE.lang=v;}catch(e){}})();

  function voiceCtor(){return window.SpeechRecognition||window.webkitSpeechRecognition||null;}

  /* ---------- panel ---------- */
  function openVoice(){var p=$("voice-panel");if(!p)return;
    // Both panels are fixed at the same spot with the same z-index, so they must never be
    // open together (they would stack and the lower one would be unreachable).
    var cp=$("claude-panel");
    if(cp&&cp.classList.contains("open")&&typeof closeClaude==="function")closeClaude();
    p.classList.add("open");voiceSyncSupport();voiceSyncMic();
    var b=$("voice-lang");if(b)b.textContent=VOICE.lang;
    var ta=$("voice-text");if(ta)ta.focus();}
  function closeVoice(){var p=$("voice-panel");if(!p)return;voiceStop();p.classList.remove("open");}
  function voiceOpened(){var p=$("voice-panel");return !!(p&&p.classList.contains("open"));}

  // Ctrl+Shift+Space from anywhere: open the panel if it is closed, then toggle the mic.
  function voiceShortcut(){if(!voiceOpened())openVoice();voiceToggleListen();}
  function voiceToggleListen(){if(VOICE.on)voiceStop();else voiceStart();}

  function voiceStatus(text,cls){var el=$("voice-status");if(!el)return;
    el.textContent=text||"";el.className="cl-status"+(cls?(" "+cls):"");}

  // Unsupported browser is a designed state, not a dead button: the mic control is disabled
  // and the hint says why, while the textarea + "Posalji Gemini-ju" keep the flow identical.
  function voiceSyncSupport(){var ok=!!voiceCtor(),b=$("voice-listen"),h=$("voice-hint"),lg=$("voice-lang");
    if(b){b.disabled=!ok;b.title=ok?"Ctrl+Shift+Space":"Web Speech API ne postoji u ovom browseru";}
    // the language picker only steers recognition, so without it the control is dead - and a
    // dead control that still looks clickable is worse than an absent one
    if(lg)lg.disabled=!ok;
    if(h)h.textContent=ok
      ? "Izgovori sta treba da se uradi. Transkript mozes da ispravis pre slanja - nista se ne salje i nista se ne izvrsava bez klika."
      : "Ovaj browser nema Web Speech API (radi samo u Chromium/Chrome). Upisi komandu u polje ispod - ostatak toka je isti.";}

  /* ---------- listening ---------- */
  function voiceStart(){
    var C=voiceCtor();
    if(!C){voiceStatus("greska: nema Web Speech API (samo Chromium)","err");voiceSyncSupport();return;}
    if(VOICE.on)return;
    // Mark "wants to listen" NOW so a Stop during the permission prompt can cancel it.
    VOICE.on=true;VOICE.lastStart=Date.now();VOICE.restarts=0;voiceSyncMic();
    // Prime the mic permission FIRST via getUserMedia. Chrome's SpeechRecognition fails
    // SILENTLY (no result, no error) when the mic permission was only DISMISSED rather
    // than explicitly Allowed — the exact "panel opens, I speak, nothing lands, no error"
    // symptom. getUserMedia forces a clear prompt and surfaces a denial; we release the
    // stream at once since recognition opens its own.
    var md=navigator.mediaDevices;
    if(md&&md.getUserMedia){
      voiceStatus("trazim dozvolu za mikrofon...","");
      md.getUserMedia({audio:true}).then(function(stream){
        try{stream.getTracks().forEach(function(t){t.stop();});}catch(e){}
        if(VOICE.on)_voiceRun(C);            // still wanted (a Stop mid-prompt cleared VOICE.on)
      }).catch(function(err){
        VOICE.on=false;voiceSyncMic();
        voiceStatus("mikrofon nije dozvoljen ("+((err&&err.name)||"greska")+
          ") - klikni ikonicu mikrofona/katanca u adresnoj traci, izaberi Dozvoli, pa probaj ponovo","err");});
      return;
    }
    _voiceRun(C);
  }

  // The recognition itself, run only after the mic permission is confirmed. VOICE.on is
  // already true here (set by voiceStart), so this never re-arms it.
  function _voiceRun(C){
    var r;try{r=new C();}catch(e){VOICE.on=false;voiceSyncMic();voiceStatus("greska: mikrofon nije dostupan","err");return;}
    r.lang=VOICE.lang;r.continuous=true;r.interimResults=true;
    r.onstart=function(){voiceStatus("mikrofon startovan, govori...","");};
    r.onresult=function(ev){var interim="",fin="";
      for(var i=ev.resultIndex;i<ev.results.length;i++){
        var res=ev.results[i],txt=(res&&res[0]&&res[0].transcript)||"";
        if(res&&res.isFinal)fin+=txt;else interim+=txt;}
      if(fin)voiceAppend(fin);
      var el=$("voice-interim");if(el)el.textContent=interim;};   // interim stays dim + unsaved
    r.onerror=function(ev){var code=(ev&&ev.error)||"";
      if(code==="no-speech"||code==="aborted")return;             // routine; onend handles it
      voiceStatus("greska mikrofona: "+String(code),"err");
      if(code==="not-allowed"||code==="service-not-allowed"){VOICE.on=false;VOICE.rec=null;voiceSyncMic();}};
    // Legibility so a silent failure is diagnosable: confirm speech actually reaches the
    // recognizer, and flag audio-heard-but-nothing-recognized (usually the language pick).
    r.onspeechstart=function(){if(VOICE.on)voiceStatus("slusam (cujem govor)...","");};
    r.onnomatch=function(){voiceStatus("cuo sam ali nisam prepoznao rec - probaj sporije ili prebaci jezik (dugme jezika)","err");};
    r.onend=function(){
      // Chrome ends a continuous session by itself after silence, so it is restarted while
      // the operator still wants to listen - but NEVER in a tight loop: a denied mic ends
      // immediately and an unguarded restart would spin forever.
      if(!VOICE.on)return;
      var now=Date.now();
      VOICE.restarts=(now-VOICE.lastStart<900)?(VOICE.restarts+1):0;
      if(VOICE.restarts>4){VOICE.on=false;VOICE.rec=null;voiceSyncMic();
        voiceStatus("greska: slusanje prekinuto (mikrofon nije dostupan)","err");return;}
      VOICE.lastStart=now;
      try{r.start();}catch(e){VOICE.on=false;VOICE.rec=null;voiceSyncMic();}};
    VOICE.rec=r;VOICE.on=true;VOICE.lastStart=Date.now();VOICE.restarts=0;
    try{r.start();}
    catch(e){VOICE.on=false;VOICE.rec=null;voiceSyncMic();voiceStatus("greska: mikrofon nije dostupan","err");return;}
    voiceSyncMic();voiceStatus("slusam...","");}

  function voiceStop(){
    if(!VOICE.on&&!VOICE.rec)return;
    VOICE.on=false;var r=VOICE.rec;VOICE.rec=null;
    if(r){try{r.onend=null;r.onresult=null;r.onerror=null;r.stop();}catch(e){}}
    // Flush a not-yet-finalised phrase into the transcript before clearing it: a phrase
    // Chrome never marked isFinal (common in continuous mode / some languages) would
    // otherwise be silently lost — the "I spoke but nothing landed in the box" symptom.
    var el=$("voice-interim");
    if(el){if(el.textContent&&el.textContent.trim())voiceAppend(el.textContent);el.textContent="";}
    voiceSyncMic();
    var st=$("voice-status");
    if(st&&st.textContent==="slusam...")voiceStatus("","");}

  function voiceSyncMic(){
    var m=$("voice-mic");
    if(m){m.classList.toggle("rec",VOICE.on);m.setAttribute("aria-pressed",VOICE.on?"true":"false");}
    var b=$("voice-listen");
    if(b){b.textContent=VOICE.on?"Stani":"Slusaj";b.classList.toggle("on",VOICE.on);}}

  // Final chunks are APPENDED after whatever the operator typed or corrected, never
  // replacing it. Nothing here stores or logs the text.
  function voiceAppend(chunk){var ta=$("voice-text");if(!ta)return;
    var add=String(chunk||"").replace(/\s+/g," ").trim();if(!add)return;
    var cur=ta.value||"";
    ta.value=cur?(cur.replace(/\s+$/,"")+" "+add):add;
    ta.scrollTop=ta.scrollHeight;}

  function voiceToggleLang(){
    var i=VC_LANGS.indexOf(VOICE.lang);
    VOICE.lang=VC_LANGS[(i+1)%VC_LANGS.length];
    try{localStorage.setItem("av_voice_lang",VOICE.lang);}catch(e){}   // the CODE, never the transcript
    var b=$("voice-lang");if(b)b.textContent=VOICE.lang;
    if(VOICE.on){voiceStop();voiceStart();}}                           // lang only applies at start()

  /* ---------- plan ---------- */
  // cwd_hint is the selected session's repo, so "sredi ovo" lands in the right folder. It is
  // a hint only: the server validates every cwd it gets against the known repos.
    // The per-boot voice token (loopback GET) rides every /api/voice/* POST as X-Voice-Token —
  // a rebinding page cannot present it; fetched lazily, once.
  var VOICE_TOKEN="";
  function voiceToken(){if(VOICE_TOKEN)return Promise.resolve(VOICE_TOKEN);
    return fetch("/api/voice/token").then(function(r){return r.json();}).then(function(d){VOICE_TOKEN=(d&&d.token)||"";return VOICE_TOKEN;}).catch(function(){return "";});}
  function voicePost(url,body){return voiceToken().then(function(tok){
    return fetch(url,{method:"POST",headers:{"Content-Type":"application/json","X-Voice-Token":tok},body:JSON.stringify(body||{})})
      .then(function(r){return r.json().catch(function(){return {};}).then(function(d){d=d||{};if(!r.ok&&!d.error)d.error="HTTP "+r.status;return d;});});});}
  function voiceCwd(){
    try{var s=(typeof sessions!=="undefined"&&selected)?sessions[selected]:null;
      return (s&&s.cwd)?String(s.cwd):"";}catch(e){return "";}}

  function voicePlan(){
    if(VOICE.busy)return;
    var ta=$("voice-text");if(!ta)return;
    var text=(ta.value||"").trim();
    if(!text){ta.focus();voiceStatus("greska: prazan transkript","err");return;}
    VOICE.busy=true;voiceBtns();voiceStatus("obradjujem...","");
    var op=(typeof aiLoaderStart==="function")?aiLoaderStart("Glas -> plan koraka"):0;
    voicePost("/api/voice/plan",{text:text,cwd_hint:voiceCwd()}).then(function(d){
      if(op&&typeof aiLoaderStop==="function")aiLoaderStop(op);
      VOICE.busy=false;d=d||{};
      if(typeof refreshGemBudget==="function")refreshGemBudget();   // one Lite call was just spent
      if(d.error&&!(d.steps&&d.steps.length)){
        VOICE.plan=null;VOICE.done=false;voiceStatus("greska: "+String(d.error),"err");voiceRender();return;}
      VOICE.plan={plan_id:String(d.plan_id||""),
                  steps:(d.steps&&d.steps.length?d.steps:[]).slice(),
                  note:String(d.note||"")};
      VOICE.done=false;
      voiceStatus(VOICE.plan.steps.length?"plan spreman - oznaci korake":"plan bez koraka",
                  VOICE.plan.steps.length?"ok":"err");
      voiceRender();
    }).catch(function(e){
      if(op&&typeof aiLoaderStop==="function")aiLoaderStop(op);
      VOICE.busy=false;voiceBtns();
      voiceStatus("greska: "+(typeof aiErrText==="function"?aiErrText(e):"network error"),"err");});}

  /* ---------- steps ---------- */
  // The payload is what the operator is approving, so it is printed RAW - not JSON-encoded,
  // because a Windows path shown as C:\\repo is not what will run. Declared keys come first
  // in a fixed order, any key the server adds LATER is still printed (never silently hidden),
  // and the one free-text field (content / prompt) always comes LAST, under its own header,
  // so a crafted value can never fake a key line above it. The whole block is esc()'d once.
  var VC_KEYS={shell:["cmd","cwd"],write_file:["path","overwrite","content"],
               launch_claude:["cwd","prompt"],hud:["action","arg"],
               create_ticket:["module","title","category","priority","assign_to_me","description"],
               edit_ticket:["module","ticket_id","fields"]};
  var VC_LONG={content:1,prompt:1,description:1,fields:1};
  function voicePayloadText(kind,p){
    p=(p&&typeof p==="object")?p:{};
    var order=VC_KEYS[kind]||[],seen={},head=[],tail=[];
    function put(k){
      if(seen[k])return;seen[k]=1;
      var v=p[k];
      if(v===undefined&&order.indexOf(k)<0)return;
      // edit_ticket's "fields" is an object, not a string - JSON.stringify it so
      // it prints as its actual keys/values, not "[object Object]".
      var s=(v!==null&&typeof v==="object")?JSON.stringify(v):(v==null?null:String(v));
      if(VC_LONG[k])tail.push("--- "+k+" ---\n"+(s==null?"":s));
      else head.push(k+": "+(s==null?"(nije zadato)":s));}
    order.forEach(put);
    Object.keys(p).forEach(put);
    return head.concat(tail).join("\n");}

  function voiceStep(idx){var p=VOICE.plan;if(!p)return null;
    for(var i=0;i<p.steps.length;i++){var s=p.steps[i];if(s&&s.idx===idx)return s;}
    return null;}

  function voiceStepHtml(s){
    s=s||{};
    var idx=(typeof s.idx==="number")?s.idx:0,
        kind=String(s.kind||""),
        risk=VC_RISK[s.risk]?String(s.risk):"none",
        refused=s.refused?String(s.refused):"",
        token=String(s.approve_token||""),
        dead=!!(refused||!token);
    // A refused (or unsigned) step gets NO checkbox: it can never be approved, so offering
    // the click would be a lie. It stays visible, struck through, with the server's reason.
    var box=dead?'<span class="vc-no" aria-hidden="true">&times;</span>'
      :'<input type="checkbox" class="vc-cbx" id="vc-cbx-'+idx+'" data-idx="'+idx+'"'+
       (s.risk==="high"?"":" checked")+'>';
    var lab=esc(String(s.label||kind||"korak"));
    return '<div class="vc-step risk-'+risk+(dead?" refused":"")+'" data-idx="'+idx+'">'+
      '<div class="vc-sh">'+box+
        '<span class="vc-kind k-'+esc(kind)+'">'+esc(kind||"?")+'</span>'+
        '<span class="vc-risk r-'+risk+'">'+esc(VC_RISK[risk])+'</span>'+
        (dead?('<span class="vc-label">'+lab+'</span>')
             :('<label class="vc-label" for="vc-cbx-'+idx+'">'+lab+'</label>'))+
      '</div>'+
      (refused?('<div class="vc-reason">odbijeno: '+esc(refused)+'</div>'):"")+
      '<pre class="vc-pre">'+esc(voicePayloadText(kind,s.payload))+'</pre>'+
      '<div class="vc-res" id="vc-res-'+idx+'"></div></div>';}

  function voiceRender(){
    var host=$("voice-steps");if(!host)return;
    var p=VOICE.plan;
    if(!p){host.innerHTML="";voiceBtns();return;}
    var h=p.note?('<div class="vc-note">'+esc(p.note)+'</div>'):"";
    h+=p.steps.length?p.steps.map(voiceStepHtml).join("")
                     :'<div class="vc-empty">Model nije vratio nijedan korak.</div>';
    host.innerHTML=h;
    Array.prototype.forEach.call(host.querySelectorAll(".vc-cbx"),function(c){c.onchange=voiceBtns;});
    voiceBtns();}

  function voiceTicked(){
    var host=$("voice-steps"),out=[];
    if(!host||!VOICE.plan)return out;
    Array.prototype.forEach.call(host.querySelectorAll(".vc-cbx"),function(c){
      if(!c.checked)return;
      var s=voiceStep(+c.getAttribute("data-idx"));
      if(s&&s.approve_token)out.push({idx:s.idx,approve_token:String(s.approve_token)});});
    return out;}

  function voiceBtns(){
    var pb=$("voice-plan"),rb=$("voice-run");
    if(pb)pb.disabled=VOICE.busy;
    if(rb)rb.disabled=VOICE.busy||VOICE.done||!voiceTicked().length;}

  // A plan is executed ONCE from the page: its tokens stay valid for ten minutes, so a second
  // click would run every ticked step a second time.
  function voiceLock(){
    var host=$("voice-steps");if(!host)return;
    Array.prototype.forEach.call(host.querySelectorAll(".vc-cbx"),function(c){c.disabled=true;});}

  /* ---------- run ---------- */
  function voiceRun(){
    if(VOICE.busy||VOICE.done)return;
    var p=VOICE.plan;if(!p||!p.plan_id)return;
    var approvals=voiceTicked();
    if(!approvals.length){voiceStatus("greska: nijedan korak nije oznacen","err");return;}
    VOICE.busy=true;voiceBtns();
    voiceStatus("izvrsavam "+approvals.length+" korak(a)...","");
    VOICE.done=true;   // claimed at click time: a dropped connection after the server ran must not offer a re-run
    voicePost("/api/voice/run",{plan_id:p.plan_id,approvals:approvals}).then(function(d){
      VOICE.busy=false;d=d||{};
      var res=(d.results&&d.results.length)?d.results:[];
      if(!res.length){voiceStatus("greska: "+String(d.error||"server nije vratio rezultate"),"err");
        voiceBtns();return;}
      var ran=0,bad=0;
      res.forEach(function(r){
        r=r||{};
        var st=String(r.status||""),det=String(r.detail||""),el=$("vc-res-"+r.idx);
        if(el){el.textContent=st+(det?(" - "+det):"");   // textContent: server text is never HTML
          el.className="vc-res "+(st==="ok"?"ok":(st==="error"?"err":(st==="refused"?"warn":"")));}
        if(st==="ok")ran++;else if(st==="error"||st==="refused")bad++;
        // hud rows come back client:true - the PAGE performs them (it owns the tabs + modals)
        if(r.client&&st==="ok"){var s=voiceStep(r.idx);if(s&&s.kind==="hud")voiceHud(s.payload,el);}});
      VOICE.done=true;voiceLock();voiceBtns();
      voiceStatus("gotovo: "+ran+" izvrseno"+(bad?(", "+bad+" odbijeno/greska"):""),bad?"err":"ok");
    }).catch(function(e){VOICE.busy=false;voiceLock();voiceBtns();
      voiceStatus("greska: "+(typeof aiErrText==="function"?aiErrText(e):"network error")+" - koraci su mozda izvrseni; proveri konzolu, ne ponavljaj",  "err");});}

  /* ---------- hud actions performed by the PAGE ---------- */
  function voiceTixView(){
    if(typeof mode!=="undefined"&&mode!=="tickets"&&typeof setMode==="function")setMode("tickets");}
  // fetchTickets() is fire-and-forget, so an action that needs the ticket DATA (open a ticket,
  // analyse one) waits for the list - bounded, then gives up visibly. Rescan needs only the view.
  function voiceWhenTix(fn,fail,tries){
    tries=tries||0;
    var have=false;
    try{have=!!(typeof TIX!=="undefined"&&TIX&&(TIX.projects||[]).length);}catch(e){have=false;}
    if(have){fn();return;}
    if(tries>20){if(fail)fail();return;}
    setTimeout(function(){voiceWhenTix(fn,fail,tries+1);},200);}
  function voiceFindTix(arg){
    var id=String(arg||"").replace(/^#/,"").trim();
    if(!id||typeof allTix!=="function")return null;
    var hit=null;
    allTix().forEach(function(x){if(!hit&&x&&x.t&&String(x.t.id)===id)hit=x;});
    return hit;}

  function voiceHud(payload,el){
    payload=(payload&&typeof payload==="object")?payload:{};
    var act=String(payload.action||""),arg=String(payload.arg==null?"":payload.arg).trim();
    // el is the row's result line; textContent keeps server text inert.
    function note(t){if(el)el.textContent=(el.textContent||"")+" - "+t;}
    if(act==="open_view"){
      var m=VC_VIEWS[arg.toLowerCase()];
      if(!m){note("nepoznat pogled: "+arg);return;}
      if(typeof setMode==="function")setMode(m);
      note("pogled "+m);return;}
    if(act==="rescan"){
      voiceTixView();
      if(typeof doRescan==="function")doRescan();
      note("rescan pokrenut");return;}
    if(act==="open_ticket"||act==="analyze"){
      voiceTixView();
      voiceWhenTix(function(){
        var f=voiceFindTix(arg);
        if(!f){note("tiket "+arg+" nije nadjen");return;}
        if(act==="open_ticket"){if(typeof openTix==="function")openTix(f.dir,f.t.id);note("otvoren #"+f.t.id);}
        else{if(typeof tkAnalyzeIds==="function")tkAnalyzeIds([String(f.t.id)],false,"#"+f.t.id);
          note("analiza #"+f.t.id);}
      },function(){note("tiketi nisu ucitani");});
      return;}
    note("nepoznata radnja");}
