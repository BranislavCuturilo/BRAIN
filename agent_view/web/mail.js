  /* ---------- MAIL view (3-pane webmail; lazy: folders on account, list on folder, msg on click) ---------- */
  var mailAccts=[],mailFolders=[],mailMsgs=[],mailMsg=null;
  var mailAcct="",mailFolder="",mailQ="",mailShowHtml=false,mailLoaded=false;
  var mailSort="date",mailLimit=50,mailLastFull=false;   // list sort (date|from|subject|unread); mailLimit = page size; mailLastFull => the last page came back full, so more may exist
  var mailListReq=0,mailMsgReq=0,mailSearchT=null,mailFlashT=null;
  var mailTo=[],mailCc=[],mailFiles=[];   // compose state (reassigned per openCompose)
  var mailVisual=null;                    // AI-visual preview: {kind:"diagram",svg} | {kind:"image",mime,data_b64}
  // AI-layer state
  var mailSummary=null,mailAiBusy="",mailAiError="",mailBulkNote=false,mailSpeaking=false;
  var mailProfiles=null;
  var mailAskBusy=false,mailAskResult=null;
  // triage state: result cached per acct+folder key so re-opening the panel doesn't re-run the model
  var mailTriageBusy=false,mailTriageResult=null,mailTriageKey="";
  var mailSoundMuted=localStorage.getItem("mailSoundMuted")==="1",mailToastT=null,mailAC=null;
  // password-required state (F5): mailPwAcct is the account id currently showing the inline
  // "enter password" card in place of the folder/list area; null when it isn't shown.
  var mailPwAcct=null,mailPwErr="",mailPwBusy=false;
  // AI availability is assumed ON; a server error that signals a missing key turns it off
  // (detected in noteAiKey, not polled from a counter — the budget chip was removed).
  var mailNoKey=false;
  function aiAvailable(){return !mailNoKey;}
  function noteAiKey(d){if(d&&(d.no_key||/\bno .*key|missing .*key|api[_ ]?key|not configured|nije.*klju[čc]|nema.*klju[čc]/i.test(String(d.error||"")))){if(!mailNoKey){mailNoKey=true;applyAiVisibility();}}}
  // Every mail GET passes here: a 401 {"error":"password_required","acct"} from ANY route
  // opens the password card in one place (F5) — callers still receive the body.
  function mailApi(url){return fetch(url).then(function(r){return r.json().catch(function(){return {};}).then(function(d){if(r.status===401&&d&&d.error==="password_required")mailShowPwCard(d.acct||mailAcct);return d;});});}
  // Same guard for the mail POSTs (action/send/sync/save-attachments/profiles): returns {status, d}.
  function mailPost(url,body){return fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})})
    .then(function(r){return r.json().catch(function(){return {};}).then(function(d){d=d||{};if(r.status===401&&d.error==="password_required")mailShowPwCard(d.acct||mailAcct);if(!r.ok&&!d.error)d.error="HTTP "+r.status;return d;});});}
  function mailListOf(w){return w==="cc"?mailCc:mailTo;}
  function mailUnseenN(v){return Math.max(0,parseInt(v,10)||0);}
  function mailSize(n){n=parseInt(n,10)||0;return n>=1048576?(n/1048576).toFixed(1)+" MB":n>=1024?(n/1024).toFixed(0)+" KB":n+" B";}
  // compact attachment "type" label: prefer the filename extension (pdf/png/docx); else the
  // mime subtype (a.type|mime|content_type, whichever the backend supplies); else "file".
  function mailAttType(a){var nm=String((a&&a.name)||""),dot=nm.lastIndexOf(".");
    if(dot>0&&dot<nm.length-1)return nm.slice(dot+1).toLowerCase();
    var t=String((a&&(a.type||a.mime||a.content_type))||"").trim();
    if(t){var slash=t.indexOf("/");return (slash>=0?t.slice(slash+1):t).toLowerCase();}
    return "file";}
  // to/cc arrive as [{name,email}] objects; render "Name <email>" (or just email when name is empty), comma-joined, dropping blanks. Tolerates legacy plain strings. Caller esc()s the result.
  function mailAddrs(v){if(v==null)return "";if(!Array.isArray(v))return String(v);
    return v.map(function(x){if(x==null)return "";
      if(typeof x==="object"){var nm=String(x.name==null?"":x.name).trim(),em=String(x.email==null?"":x.email).trim();return nm&&em?nm+" <"+em+">":(em||nm);}
      return String(x);}).filter(function(s){return s;}).join(", ");}
  function mailAcctName(id){var a=mailAccts.filter(function(x){return String(x.id)===String(id);})[0];return a?(a.name||a.email||a.id):id;}
  // list date: HH:MM if today, else dd.mm.; falls back to the raw string (caller esc()s). Reuses tsOf().
  function mailFmtDate(s){if(!s)return "";var t=tsOf(s);if(t==null)return String(s);var d=new Date(t),now=new Date();
    if(d.toDateString()===now.toDateString())return ("0"+d.getHours()).slice(-2)+":"+("0"+d.getMinutes()).slice(-2);
    return ("0"+d.getDate()).slice(-2)+"."+("0"+(d.getMonth()+1)).slice(-2)+".";}
  function mailFlash(txt){var el=$("mail-flash");if(!el)return;el.textContent=txt;el.classList.add("show");clearTimeout(mailFlashT);mailFlashT=setTimeout(function(){el.classList.remove("show");},2600);}

  function mailInit(){if(mailLoaded)return;mailLoaded=true;renderMute();refreshVoiceSelect();loadAccounts();}
  function loadAccounts(){
    mailApi("/api/mail/accounts").then(function(d){
      mailAccts=(d&&d.accounts)||[];
      $("mail-acct").innerHTML=mailAccts.map(function(a){return '<option value="'+esc(a.id)+'">'+esc(a.name||a.email||a.id)+'</option>';}).join("");
      if(mailAccts.length){
        // keep the current selection if it still exists (e.g. reload right after saving a
        // password); otherwise fall back to the first account, as before.
        var keep=mailAcct&&mailAccts.some(function(a){return String(a.id)===String(mailAcct);});
        if(!keep)mailAcct=String(mailAccts[0].id);
        $("mail-acct").value=mailAcct;
        var cur=mailAccts.filter(function(a){return String(a.id)===String(mailAcct);})[0];
        if(cur&&cur.has_password===false)mailShowPwCard(mailAcct);
        else loadFolders();
      }else{
        $("mail-folders").innerHTML='<div class="mail-empty2">No accounts.<span class="mail-hint2">Add one under <code>mail_accounts</code> in the shared agent_view.config.json.</span></div>';
        $("mail-list").innerHTML='<div class="mail-empty2">&mdash;</div>';
      }
    }).catch(function(){$("mail-folders").innerHTML='<div class="mail-empty2">Server unavailable.</div>';});
  }
  function loadFolders(){
    $("mail-folders").innerHTML='<div class="mail-load">Loading&hellip;</div>';
    mailApi("/api/mail/folders?acct="+encodeURIComponent(mailAcct)).then(function(d){
      if(mailPwGuard(d))return;
      mailFolders=(d&&d.folders)||[];
      var keep=mailFolders.some(function(f){return f.path===mailFolder;});
      if(!keep){var inbox=mailFolders.filter(function(f){return /inbox/i.test(f.name||"")||/inbox/i.test(f.path||"");})[0];mailFolder=((inbox||mailFolders[0]||{}).path)||"";}
      renderFolders();
      if(mailFolder)loadList();else{mailMsgs=[];renderList();}
    }).catch(function(){mailFolders=[];$("mail-folders").innerHTML='<div class="mail-empty2">Failed.</div>';});
  }
  /* ---------- password-required (F5): inline card replaces the folder/list area, never a modal ---------- */
  function mailPwFindAcct(id){return mailAccts.filter(function(a){return String(a.id)===String(id);})[0]||{id:id};}
  // A mail call answering 401 {"error":"password_required","acct":...} lands here regardless of
  // which route hit it (folders/list/msg all funnel through mailApi -> this same shape).
  function mailPwGuard(d){if(d&&d.error==="password_required"){mailShowPwCard(d.acct||mailAcct);return true;}return false;}
  function mailShowPwCard(acctId){var same=(mailPwAcct===String(acctId));mailPwAcct=String(acctId);if(!same)mailPwErr="";mailPwBusy=false;
    $("mail-folders").innerHTML='<div class="mail-empty2">Nalog čeka lozinku</div>';
    renderMailPwCard();}
  function renderMailPwCard(){var host=$("mail-list");if(!host)return;var info=mailPwFindAcct(mailPwAcct);
    var label=esc(info.email||info.name||info.id||"");
    host.innerHTML=
      '<div class="mail-pw-wrap"><div class="mail-pw-card">'+
        '<div class="mail-pw-h">&#128274; Lozinka za '+label+'</div>'+
        '<div class="mail-pw-row"><span class="mail-pw-lab">Email</span><input class="mail-pw-in" value="'+esc(info.email||"")+'" readonly></div>'+
        '<div class="mail-pw-row"><span class="mail-pw-lab">Nalog</span><input class="mail-pw-in" value="'+esc(info.name||info.id||"")+'" readonly></div>'+
        '<div class="mail-pw-row"><span class="mail-pw-lab">Lozinka</span><input class="mail-pw-in" type="password" id="mail-pw-in" autocomplete="current-password" placeholder="lozinka"></div>'+
        (mailPwErr?'<div class="mail-pw-err">'+esc(mailPwErr)+'</div>':'')+
        '<button class="mail-pw-btn" id="mail-pw-save"'+(mailPwBusy?' disabled':'')+'>'+(mailPwBusy?"Povezivanje…":"Sačuvaj i poveži")+'</button>'+
        '<div class="mail-pw-hint">Lozinka se čuva šifrovano (DPAPI) samo na ovom računaru; ostali podaci naloga dolaze iz zajedničkog configa.</div>'+
      '</div></div>';
    var inp=$("mail-pw-in");
    if(inp){inp.focus();inp.onkeydown=function(e){if(e.key==="Enter"){e.preventDefault();mailPwSubmit();}};}
    var b=$("mail-pw-save");if(b)b.onclick=mailPwSubmit;}
  function mailPwSubmit(){if(mailPwBusy)return;var inp=$("mail-pw-in"),pw=inp?inp.value:"";
    if(!pw){mailPwErr="Unesite lozinku.";renderMailPwCard();return;}
    var acct=mailPwAcct;mailPwBusy=true;mailPwErr="";renderMailPwCard();
    fetch("/api/mail/account",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({id:acct,password:pw})})
      .then(function(r){return r.json().catch(function(){return {};}).then(function(d){d=d||{};if(!r.ok&&!d.error)d.error="HTTP "+r.status;return d;});})
      .then(function(d){mailPwBusy=false;
        if(d&&d.error==="password_required"){mailPwErr=d.rejected?"Server je odbio lozinku — proveri i pokušaj ponovo.":"Lozinka nije prihvaćena.";renderMailPwCard();return;}
        if(d&&d.error){mailPwErr=String(d.error);renderMailPwCard();return;}
        mailPwAcct=null;mailFlash("Nalog povezan");loadAccounts();   // reload accounts + folders
      }).catch(function(e){mailPwBusy=false;mailPwErr="Greška: "+aiErrText(e);renderMailPwCard();});}
  function renderFolders(){var host=$("mail-folders");
    if(!mailFolders.length){host.innerHTML='<div class="mail-empty2">No folders</div>';return;}
    host.innerHTML=mailFolders.map(function(f){var un=mailUnseenN(f.unseen);
      return '<div class="mail-folder'+(f.path===mailFolder?" sel":"")+'" data-path="'+esc(f.path)+'">'+
        '<span class="mf-name">'+esc(f.name||f.path)+'</span>'+(un?'<span class="mf-un">'+un+'</span>':'')+'</div>';}).join("");
    Array.prototype.forEach.call(host.querySelectorAll(".mail-folder"),function(el){
      el.onclick=function(){var p=el.getAttribute("data-path");if(p===mailFolder)return;mailFolder=p;mailLimit=50;mailMsg=null;mailQ="";$("mail-search").value="";mailTriageReset();renderFolders();renderReader();loadList();};});}
  // Fresh load (offset 0, replaces) or append=true (offset = current count, concatenates the
  // next page). "Load more" is shown ONLY when the last page came back full (mailLastFull), so a
  // short folder (INBOX = 18 < limit) shows its count and no dead button; Sent/Trash page on.
  function loadList(append){var host=$("mail-list");var offset=append?mailMsgs.length:0;
    if(!append)host.innerHTML='<div class="mail-load">Loading messages&hellip;</div>';
    var req=++mailListReq;
    var url="/api/mail/list?acct="+encodeURIComponent(mailAcct)+"&folder="+encodeURIComponent(mailFolder)+
      "&limit="+mailLimit+"&offset="+offset+"&sort="+encodeURIComponent(mailSort)+(mailQ?("&q="+encodeURIComponent(mailQ)):"");
    mailApi(url).then(function(d){if(req!==mailListReq)return;if(mailPwGuard(d))return;var page=(d&&d.messages)||[];
      mailLastFull=page.length>=mailLimit;   // a full page => there is probably a next one
      mailMsgs=append?mailMsgs.concat(page):page;renderList();})
      .catch(function(){if(req!==mailListReq)return;if(!append){mailMsgs=[];host.innerHTML='<div class="mail-empty2">Failed to load.</div>';}});}
  function renderList(){var host=$("mail-list");
    if(!mailMsgs.length){host.innerHTML='<div class="mail-empty2">'+(mailQ?"No results.":"Empty folder.")+'</div>';return;}
    var rowsHtml=mailMsgs.map(function(m){var isNew=!m.seen,sel=mailMsg&&String(mailMsg.uid)===String(m.uid);
      return '<div class="mail-row'+(isNew?" unseen":"")+(sel?" sel":"")+'" data-uid="'+esc(m.uid)+'">'+
        '<span class="mr-dot'+(isNew?" new":"")+'"></span>'+
        '<div class="mr-main">'+
          '<div class="mr-top"><span class="mr-from">'+esc(m.from_name||m.from_email||"&mdash;")+'</span><span class="mr-date">'+esc(mailFmtDate(m.date))+'</span></div>'+
          '<div class="mr-subj">'+(m.has_attach?'<span class="mr-clip">&#128206;</span>':'')+esc(m.subject||"(no subject)")+'</div>'+
          '<div class="mr-snip">'+esc(m.snippet||"")+'</div>'+
        '</div></div>';}).join("");
    // "Load more" ONLY when the last page came back full (more likely exist); else the count.
    var foot=mailLastFull
      ? '<button class="mail-more" id="mail-more">Load more (showing '+mailMsgs.length+')</button>'
      : '<div class="mail-count">'+mailMsgs.length+' message'+(mailMsgs.length===1?"":"s")+'</div>';
    host.innerHTML=rowsHtml+foot;
    Array.prototype.forEach.call(host.querySelectorAll(".mail-row"),function(el){el.onclick=function(){openMsg(el.getAttribute("data-uid"));};});
    var mm=$("mail-more");if(mm)mm.onclick=function(){loadList(true);};}   // append the next page via &offset=
  function mailBumpUnseen(delta){var f=mailFolders.filter(function(x){return x.path===mailFolder;})[0];if(f)f.unseen=Math.max(0,mailUnseenN(f.unseen)+delta);}
  function mailPostAction(uid,op){
    return fetch("/api/mail/action",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({acct:mailAcct,folder:mailFolder,uid:uid,op:op})})
      .then(function(r){return r.json();}).then(function(j){return !!(j&&j.ok);}).catch(function(){return false;});}
  function openMsg(uid){var req=++mailMsgReq;$("mail-read").innerHTML='<div class="mail-load">Loading&hellip;</div>';mailShowHtml=false;
    mailStopSpeak();mailSummary=null;mailAiBusy="";mailAiError="";mailBulkNote=false;  // fresh AI state per message
    mailApi("/api/mail/msg?acct="+encodeURIComponent(mailAcct)+"&folder="+encodeURIComponent(mailFolder)+"&uid="+encodeURIComponent(uid)).then(function(d){
      if(req!==mailMsgReq)return;if(mailPwGuard(d))return;mailMsg=d||{uid:uid};
      mailShowHtml=mailHtmlPrimary(mailMsg);  // marketing mail is HTML-primary (thin text part looks "cut off"); default to the sandboxed HTML view — toggle still lets the user drop to text
      var row=mailMsgs.filter(function(m){return String(m.uid)===String(uid);})[0];
      if(row){
        if(mailMsg.bulk==null&&row.bulk!=null)mailMsg.bulk=row.bulk;  // list carries the newsletter/bulk flag; carry it onto the open message
        if(!row.seen){row.seen=true;mailBumpUnseen(-1);mailPostAction(uid,"seen");renderFolders();}  // server marks seen; keep UI in sync
      }
      renderList();renderReader();
    }).catch(function(){if(req!==mailMsgReq)return;$("mail-read").innerHTML='<div class="mail-empty"><b>Error</b>Failed to load message.</div>';});}
  // Wrap untrusted body_html in a minimal doc. The WHOLE string is esc()'d into the
  // srcdoc="" attribute at the call site, so it cannot break out of the attribute;
  // the sandbox (no allow-scripts / no allow-same-origin) makes any script/onerror/
  // javascript: inside inert and unable to reach this page.
  function mailHtmlDoc(html){return '<!doctype html><html><head><meta charset="utf-8">'+
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'">'+
    '<base target="_blank">'+
    '<style>body{font-family:sans-serif;color:#111;background:#fff;margin:8px;font-size:13px;word-break:break-word}img{max-width:100%;height:auto}a{color:#0a58ca}</style></head><body>'+String(html==null?"":html)+'</body></html>';}
  // HTML-primary = a real HTML body that dwarfs (or replaces) the plain-text part — the "looks cut off" marketing mails. Open those in the (still sandboxed) HTML view by default.
  function mailHtmlPrimary(m){if(!m)return false;var html=String(m.body_html==null?"":m.body_html),text=String(m.body_text==null?"":m.body_text);
    if(!html.trim())return false;if(!text.trim())return true;return html.length>text.length*1.3;}
  function renderReader(){var host=$("mail-read"),m=mailMsg;
    if(!m){host.innerHTML='<div class="mail-empty"><b>No message selected</b>Pick a message from the list.</div>';return;}
    var hasHtml=!!(m.body_html&&String(m.body_html).trim()),hasText=!!(m.body_text&&String(m.body_text).trim());
    var bodyHtml;
    if(mailShowHtml&&hasHtml){bodyHtml='<iframe class="mail-frame" sandbox srcdoc="'+esc(mailHtmlDoc(m.body_html))+'"></iframe>';}
    else{bodyHtml='<div class="mail-body">'+esc(hasText?m.body_text:(hasHtml?'[HTML message — click "Show HTML"]':"(empty message)"))+'</div>';}
    // Attachments render as a compact "Prilozi (N)" section pinned at the TOP of the
    // reader (under the header, above the body) so they're visible immediately and stay
    // put while the body region scrolls. Each chip is name · size · type + the existing
    // per-attachment download link; a "Preuzmi sve priloge" button saves them ALL to a
    // folder on the host (server-side POST; NOT a browser download). The whole section is
    // omitted when there are 0 attachments, so the button is absent then (never disabled-empty).
    var atts=Array.isArray(m.attachments)?m.attachments:[];
    var attHtml=atts.length?('<div class="mail-atts-top"><div class="mat-head">'+
        '<span class="mat-title">&#128206; Prilozi ('+atts.length+')</span>'+
        '<button class="mat-dlall" id="mail-dlall" title="Sa&#269;uvaj sve priloge u folder na ra&#269;unaru">&#11015; Preuzmi sve priloge</button>'+
      '</div><div class="mat-list">'+atts.map(function(a){
        // server-constructed same-origin URL; only the query VALUES are data, each encoded. Not a safeUrl() case (that requires http(s)).
        var url="/api/mail/attachment?acct="+encodeURIComponent(mailAcct)+"&folder="+encodeURIComponent(mailFolder)+"&uid="+encodeURIComponent(m.uid)+"&part="+encodeURIComponent(a.part);
        return '<a class="mail-chip" href="'+esc(url)+'" download title="'+esc(a.name||"")+'">&#128206; '+
          '<span class="mc-nm">'+esc(a.name||"attachment")+'</span>'+
          (a.size?'<span class="mc-sz">'+esc(mailSize(a.size))+'</span>':'')+
          '<span class="mc-ty">'+esc(mailAttType(a))+'</span></a>';
      }).join("")+'</div></div>'):'';
    var ccRow=(m.cc&&mailAddrs(m.cc))?'<div class="mail-meta"><span class="mail-mk">Cc</span><span class="mail-mv">'+esc(mailAddrs(m.cc))+'</span></div>':'';
    var toggle=hasHtml?('<button class="mail-tgl" id="mail-htmltgl">'+(mailShowHtml?"Show text":"Show HTML")+'</button>'):'';
    // AI buttons: Suggest hidden when the message is bulk OR no AI key; Summarize hidden when no key; Read aloud always (browser TTS, needs no key)
    var avail=aiAvailable(),aiBtns="";
    if(avail&&!m.bulk)aiBtns+='<button class="mail-ai-btn sug" id="mail-suggest-btn">&#10024; Suggest reply</button>';
    if(avail)aiBtns+='<button class="mail-ai-btn" id="mail-summarize-btn">&#9776; Summarize</button>';
    aiBtns+='<button class="mail-ai-btn spk'+(mailSpeaking?' act':'')+'" id="mail-speak-btn"'+(canSpeak?'':' disabled title="No Web Speech API"')+'>'+(mailSpeaking?'&#9209; Stop':'&#128266; Read aloud')+'</button>';
    // AI status/summary panel (above the body). The spinning side loader is the primary
    // (non-blocking) activity indicator; this inline note just says where the result lands.
    var aiPanel="";
    if(mailBulkNote)aiPanel+='<div class="mail-ai-note">&#128239; Newsletter/automated mail &mdash; no reply suggested.</div>';
    if(mailAiBusy)aiPanel+='<div class="mail-ai-panel busy"><span class="mail-spin"></span>'+(mailAiBusy==="suggest"?"Preparing reply&hellip;":"Summarizing&hellip;")+'</div>';
    else if(mailAiError)aiPanel+='<div class="mail-ai-panel err"><b>Error</b>'+esc(mailAiError)+'</div>';
    else if(mailSummary)aiPanel+='<div class="mail-ai-panel"><div class="mail-ai-h">&#10024; Summary</div><div class="mail-ai-body">'+esc(mailSummary)+'</div></div>';
    // Layout: header + attachments + toolbar are pinned (flex:none); only the body region
    // (aiPanel + body/HTML preview) scrolls, inside .mail-reader-scroll. So a huge plaintext
    // body OR the sandboxed HTML iframe never hides the attachments and never blows out the
    // segment — the top attachments stay visible while the body scrolls.
    host.innerHTML=
      '<div class="mail-rhead">'+
        '<h1 class="mail-subj">'+esc(m.subject||"(no subject)")+'</h1>'+
        '<div class="mail-meta"><span class="mail-mk">From</span><span class="mail-mv">'+esc(m.from_name||"")+' &lt;'+esc(m.from_email||"")+'&gt;</span></div>'+
        '<div class="mail-meta"><span class="mail-mk">To</span><span class="mail-mv">'+esc(mailAddrs(m.to))+'</span></div>'+
        ccRow+
        '<div class="mail-meta"><span class="mail-mk">Date</span><span class="mail-mv">'+esc(fmtAt(m.date))+'</span></div>'+
      '</div>'+
      attHtml+
      '<div class="mail-toolbar">'+
        aiBtns+(aiBtns?'<span class="mail-tb-div"></span>':'')+
        '<button class="mail-tb" data-op="reply">&#8617; Reply</button>'+
        '<button class="mail-tb" data-op="forward">&#8618; Forward</button>'+
        '<button class="mail-tb" data-op="archive">&#128451; Archive</button>'+
        '<button class="mail-tb" data-op="unseen">&#9679; Mark unread</button>'+
        '<button class="mail-tb del" data-op="delete">&#128465; Delete</button>'+
        '<span style="flex:1"></span>'+toggle+
      '</div>'+
      '<div class="mail-reader-scroll">'+aiPanel+bodyHtml+'</div>';
    Array.prototype.forEach.call(host.querySelectorAll(".mail-tb"),function(b){b.onclick=function(){mailToolbar(b.getAttribute("data-op"));};});
    var tg=$("mail-htmltgl");if(tg)tg.onclick=function(){mailShowHtml=!mailShowHtml;renderReader();};
    var sb=$("mail-suggest-btn");if(sb)sb.onclick=mailSuggest;
    var mb=$("mail-summarize-btn");if(mb)mb.onclick=mailSummarize;
    var kb=$("mail-speak-btn");if(kb)kb.onclick=mailReadAloud;
    var da=$("mail-dlall");if(da)da.onclick=mailSaveAttachments;}
  // "Preuzmi sve priloge": POST {acct,folder,uid} -> the server saves every attachment into a
  // folder ON THE HOST and opens it; the response is {ok,path,saved[],skipped[]}. There is NO
  // browser download here. The route is loopback-gated, so a request from a phone/LAN gets 403
  // -> a "dostupno na računaru" note (checked BEFORE .json(), like the git/prod 403 shape).
  function mailSaveAttachments(){var m=mailMsg;if(!m)return;
    var atts=Array.isArray(m.attachments)?m.attachments:[];if(!atts.length)return;
    var b=$("mail-dlall");if(!b||b.disabled)return;var old=b.innerHTML;
    b.disabled=true;b.innerHTML='<span class="mail-spin"></span> &#268;uvanje&hellip;';
    fetch("/api/mail/save-attachments",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({acct:mailAcct,folder:mailFolder,uid:m.uid})})
      .then(function(r){
        if(r.status===403)return {__forbidden:true};   // loopback-only route hit from a non-loopback device
        return r.json().catch(function(){return {};}).then(function(d){d=d||{};if(!r.ok&&!d.error)d.error="HTTP "+r.status;return d;});})
      .then(function(d){b.disabled=false;b.innerHTML=old;d=d||{};
        if(d.__forbidden){mailToast("Čuvanje priloga je dostupno na računaru (lokalni server)");return;}
        if(d.error){mailToast("Greška pri čuvanju: "+d.error);return;}
        if(d.ok===false){mailToast("Prilozi nisu sačuvani"+(d.status?": "+d.status:""));return;}
        var saved=Array.isArray(d.saved)?d.saved:[],skipped=Array.isArray(d.skipped)?d.skipped:[];
        mailToast("Sačuvano "+saved.length+" priloga u "+(d.path||"")+(skipped.length?" ("+skipped.length+" preskoceno)":""));})
      .catch(function(e){b.disabled=false;b.innerHTML=old;mailToast("Greška pri čuvanju: "+aiErrText(e));});}
  function mailRe(s,p){s=String(s||"");return s.toLowerCase().indexOf(p.trim().toLowerCase())===0?s:(p+s);}
  function mailQuote(m){var b=m.body_text||"";return "\n\n---- "+(m.from_name||m.from_email||"")+" ("+fmtAt(m.date)+") ----\n"+String(b).replace(/^/gm,"> ");}
  function mailToolbar(op){var m=mailMsg;if(!m)return;
    if(op==="reply"){openCompose({to:[m.from_email],cc:[],subject:mailRe(m.subject,"Re: "),body:mailQuote(m)});return;}
    if(op==="forward"){openCompose({to:[],cc:[],subject:mailRe(m.subject,"Fwd: "),body:mailQuote(m)});return;}
    if(op==="unseen"){mailPostAction(m.uid,"unseen").then(function(ok){if(!ok)return;var row=mailMsgs.filter(function(x){return String(x.uid)===String(m.uid);})[0];
      if(row&&row.seen){row.seen=false;mailBumpUnseen(1);renderFolders();renderList();}});return;}
    if(op==="archive"||op==="delete"){if(op==="delete"&&!confirm("Delete this message?"))return;
      mailPostAction(m.uid,op).then(function(ok){if(!ok){mailFlash("Failed.");return;}
        mailMsgs=mailMsgs.filter(function(x){return String(x.uid)!==String(m.uid);});mailMsg=null;renderList();renderReader();mailFlash(op==="delete"?"Deleted":"Archived");});return;}}

  /* ---------- MAIL AI layer ---------- */
  // Reader: suggest a reply. bulk:true -> subtle note, no draft. Else summary panel + compose in reply mode prefilled. Never auto-sends.
  function mailSuggest(){var m=mailMsg;if(!m)return;mailAiBusy="suggest";mailAiError="";mailBulkNote=false;renderReader();
    var op=aiLoaderStart("Suggest reply");
    aiPost("/api/mail/suggest",{acct:mailAcct,folder:mailFolder,uid:m.uid}).then(function(d){
      aiLoaderStop(op);mailAiBusy="";noteAiKey(d);
      if(d.error){mailAiError="Suggest failed: "+d.error;renderReader();return;}
      if(d.bulk){m.bulk=true;mailBulkNote=true;renderReader();return;}        // newsletter fallback: flag it, no draft
      if(d.summary)mailSummary=String(d.summary);
      renderReader();
      openCompose({to:[m.from_email],cc:[],subject:mailRe(m.subject,"Re: "),body:d.draft||""});  // REPLY mode; human clicks Send
    }).catch(function(e){aiLoaderStop(op);mailAiBusy="";mailAiError="Suggest failed: "+aiErrText(e);renderReader();});}
  // Reader: summarize into the same panel.
  function mailSummarize(){var m=mailMsg;if(!m)return;mailAiBusy="summarize";mailAiError="";renderReader();
    var op=aiLoaderStart("Summarize");
    aiPost("/api/mail/summarize",{acct:mailAcct,folder:mailFolder,uid:m.uid}).then(function(d){
      aiLoaderStop(op);mailAiBusy="";noteAiKey(d);
      if(d.error){mailAiError="Summarize failed: "+d.error;renderReader();return;}
      mailSummary=String(d.summary||"");renderReader();
    }).catch(function(e){aiLoaderStop(op);mailAiBusy="";mailAiError="Summarize failed: "+aiErrText(e);renderReader();});}
  // Read-aloud: pure browser speechSynthesis, no backend. Prefers the shown summary, else body_text, else tag-stripped body_html.
  function mailStripTags(h){return String(h==null?"":h).replace(/<style[\s\S]*?<\/style>/gi," ").replace(/<[^>]+>/g," ").replace(/\s+/g," ").trim();}
  function mailUpdateSpeakBtn(){var b=$("mail-speak-btn");if(!b)return;b.innerHTML=mailSpeaking?"&#9209; Stop":"&#128266; Read aloud";b.classList.toggle("act",mailSpeaking);}
  function mailStopSpeak(){try{if(canSpeak)speechSynthesis.cancel();}catch(e){}mailSpeaking=false;mailUpdateSpeakBtn();}
  function mailReadAloud(){if(!canSpeak)return;if(mailSpeaking){mailStopSpeak();return;}var m=mailMsg;if(!m)return;
    var text=mailSummary||m.body_text||mailStripTags(m.body_html);text=String(text||"").trim();if(!text)return;
    // Voice: the user's explicit pick (localStorage mailVoice) wins; else auto-pick a Serbian
    // voice (lang starts "sr"); the shared cache is refreshed on voiceschanged so a first-call
    // empty getVoices() still resolves.
    var sr=chosenVoice()||pickVoice("sr")||speechVoices.filter(function(v){return /serbian|srpski/i.test(v.name||"");})[0];
    try{speechSynthesis.cancel();var u=new SpeechSynthesisUtterance(text.slice(0,6000));
      u.rate=1;u.pitch=1;u.volume=1;
      if(sr){u.voice=sr;u.lang=sr.lang||"sr-RS";}else{u.lang="sr-RS";}   // fall back to sr-RS on the default voice
      u.onend=function(){mailSpeaking=false;mailUpdateSpeakBtn();};u.onerror=function(){mailSpeaking=false;mailUpdateSpeakBtn();};
      mailSpeaking=true;mailUpdateSpeakBtn();speechSynthesis.speak(u);
    }catch(e){mailSpeaking=false;mailUpdateSpeakBtn();}}
  // AI availability — no polling and no budget chip; features are on until a call
  // reports a missing key (noteAiKey flips mailNoKey), then Ask AI / Writing style hide.
  function applyAiVisibility(){var avail=aiAvailable();
    var ask=$("mail-ask-toggle");if(ask)ask.style.display=avail?"":"none";
    var stil=$("mail-stil-toggle");if(stil)stil.style.display=avail?"":"none";
    var tri=$("mail-triage-toggle");if(tri)tri.style.display=avail?"":"none";
    var vis=$("mail-visual");if(vis)vis.style.display=avail?"":"none";   // AI-visual section gated like the other AI features
    if(!avail){closeAsk();closeStil();closeTriage();}
    if(mailMsg)renderReader();}
  // Side-chat: ask AI across the mailbox.
  function mailAsk(){var q=($("mail-ask-in").value||"").trim();if(!q)return;mailAskBusy=true;mailAskResult=null;renderAskPanel();
    var op=aiLoaderStart("Ask AI");
    aiPost("/api/mail/ask",{acct:mailAcct,question:q}).then(function(d){
      aiLoaderStop(op);mailAskBusy=false;noteAiKey(d);
      if(d.error){mailAskResult={error:"Ask failed: "+d.error,question:q};renderAskPanel();return;}
      mailAskResult={answer:String(d.answer||""),sources:Array.isArray(d.sources)?d.sources:[],question:q};renderAskPanel();
    }).catch(function(e){aiLoaderStop(op);mailAskBusy=false;mailAskResult={error:"Ask failed: "+aiErrText(e),question:q};renderAskPanel();});}
  function renderAskPanel(){var host=$("mail-ask-body");if(!host)return;
    if(mailAskBusy){host.innerHTML='<div class="mp-busy"><span class="mail-spin"></span>Searching your mail&hellip;</div>';return;}
    var r=mailAskResult;
    if(!r){host.innerHTML='<div class="mp-hint">Ask a question about your mail &mdash; e.g. &bdquo;find my credentials for the fasper FTP&ldquo;.</div>';return;}
    if(r.error){host.innerHTML='<div class="mail-ai-panel err"><b>Error</b>'+esc(r.error)+'</div>';return;}
    var chips=(r.sources||[]).map(function(s,i){return '<button class="mail-src" data-i="'+i+'" title="'+esc((s.subject||"")+" — "+(s.from||""))+'">&#9993; '+esc(s.subject||("message "+(s.uid==null?i:s.uid)))+'</button>';}).join("");
    host.innerHTML='<div class="mp-answer">'+esc(r.answer||"").replace(/\n/g,"<br>")+'</div>'+
      (chips?('<div class="mp-srch">Sources</div><div class="mp-srcs">'+chips+'</div>'):'')+
      '<div class="mp-actions"><button class="mail-ai-btn" id="mail-ask-claude">&rarr; Send to Claude</button></div>';
    Array.prototype.forEach.call(host.querySelectorAll(".mail-src"),function(b){b.onclick=function(){var s=(mailAskResult.sources||[])[parseInt(b.getAttribute("data-i"),10)];if(s)mailOpenSource(s.folder,s.uid);};});
    var cb=$("mail-ask-claude");if(cb)cb.onclick=function(){mailToClaude((mailAskResult&&mailAskResult.question)||"");};}
  function mailOpenSource(folder,uid){if(uid==null)return;
    if(folder&&folder!==mailFolder){mailFolder=folder;mailQ="";$("mail-search").value="";renderFolders();loadList();}
    closeAsk();openMsg(uid);}
  // Fire-and-forget: launch a Claude terminal with the question as the prompt.
  function mailToClaude(prompt){if(!prompt)return;
    fetch("/api/claude/launch",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({prompt:prompt})}).then(function(r){return r.json();}).then(function(d){
      mailFlash(d&&d.ok?"Claude session launched":"Failed to launch.");}).catch(function(){mailFlash("Failed to launch.");});}
  // Stil pisanja: learned writing profiles.
  function loadProfiles(){mailApi("/api/mail/profiles").then(function(d){mailProfiles=d||null;renderStilPanel();}).catch(function(){mailProfiles=null;renderStilPanel();});}
  function renderStilPanel(){var host=$("mail-stil-body");if(!host)return;var p=mailProfiles;
    if(!p){host.innerHTML='<div class="mp-hint">No style data yet. Click &bdquo;Refresh style&ldquo;.</div>';}
    else{
      var groups=p.groups||{},gkeys=Array.isArray(groups)?groups.map(function(g){return g&&g.name;}).filter(Boolean):Object.keys(groups);
      var rows=gkeys.map(function(k){var g=Array.isArray(groups)?groups.filter(function(x){return x&&x.name===k;})[0]:groups[k];g=g||{};
        var tone=g.tone||g.ton||"",form=g.formality||g.formalnost||"";   // ton/formalnost are legacy server DATA keys — keep reading them
        return '<div class="stil-grp"><span class="stil-gn">'+esc(k)+'</span>'+
          (tone?'<span class="stil-tag">tone: '+esc(tone)+'</span>':'')+
          (form?'<span class="stil-tag">formality: '+esc(form)+'</span>':'')+'</div>';}).join("");
      var cfg=p.config||{},dm=cfg.domain_map||{},personal=cfg.personal||[];
      var dmRows=Object.keys(dm).map(function(d){return '<div class="stil-map"><code>'+esc(d)+'</code> &rarr; '+esc(dm[d])+'</div>';}).join("");
      var persN=Array.isArray(p.persons)?p.persons.length:(parseInt(p.persons,10)||0);
      var selKeys=gkeys.length?gkeys:["acme","acme","fakultet","personal","other"];
      host.innerHTML=
        '<div class="stil-meta">version '+esc(p.version==null?"—":p.version)+' &middot; '+persN+' people</div>'+
        '<div class="stil-sec">Groups</div>'+(rows||'<div class="mp-hint">No groups.</div>')+
        (dmRows?('<div class="stil-sec">Domains</div>'+dmRows):'')+
        (personal.length?('<div class="stil-sec">Personal</div><div class="stil-map">'+personal.map(function(x){return esc(x);}).join(", ")+'</div>'):'')+
        '<div class="stil-sec">Move domain/address</div>'+
        '<div class="stil-add"><input class="mp-in" id="stil-dom" placeholder="domain or address" autocomplete="off">'+
          '<select class="stil-sel" id="stil-grp">'+selKeys.map(function(k){return '<option>'+esc(k)+'</option>';}).join("")+'</select>'+
          '<button class="mail-ai-btn" id="stil-add-btn">Add</button></div>';
      var ab=$("stil-add-btn");if(ab)ab.onclick=mailStilAdd;}
    var rb=$("mail-stil-refresh");if(rb){rb.disabled=!aiAvailable();rb.title=aiAvailable()?"Relearn style (uses a little quota)":"No AI key";}}
  function mailStilAdd(){var d=($("stil-dom").value||"").trim(),g=$("stil-grp").value;if(!d)return;
    mailProfiles=mailProfiles||{};mailProfiles.config=mailProfiles.config||{domain_map:{},personal:[]};
    var cfg=mailProfiles.config;cfg.domain_map=cfg.domain_map||{};cfg.personal=cfg.personal||[];
    if(d.indexOf("@")>=0&&g==="personal"){if(cfg.personal.indexOf(d)<0)cfg.personal.push(d);}else cfg.domain_map[d]=g;
    fetch("/api/mail/profiles",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({op:"config",domain_map:cfg.domain_map,personal:cfg.personal})}).then(function(r){return r.json();}).then(function(){
      mailFlash("Saved");loadProfiles();}).catch(function(){mailFlash("Failed.");});}
  function mailStilRefresh(){if(!aiAvailable())return;
    if(!confirm("Refresh writing style from your mail? Uses a little AI quota."))return;
    var rb=$("mail-stil-refresh"),old=rb?rb.textContent:"";if(rb){rb.disabled=true;rb.textContent="Refreshing…";}
    var op=aiLoaderStart("Refresh style");
    aiPost("/api/mail/profiles",{op:"refresh",acct:mailAcct}).then(function(d){
      aiLoaderStop(op);if(rb){rb.disabled=false;rb.textContent=old;}noteAiKey(d);
      if(d.error){mailFlash("Refresh failed: "+d.error);return;}
      var n=(d.count!=null?d.count:(d.persons!=null?d.persons:(d.updated!=null?d.updated:null)));
      mailFlash("Style refreshed"+(n!=null?(": "+n):""));loadProfiles();
    }).catch(function(e){aiLoaderStop(op);if(rb){rb.disabled=false;rb.textContent=old;}mailFlash("Refresh failed: "+aiErrText(e));});}
  // Panels: only one docked panel open at a time.
  function openAsk(){closeStil();closeTriage();$("mail-ask-panel").classList.add("open");var i=$("mail-ask-in");if(i)i.focus();}
  function closeAsk(){var p=$("mail-ask-panel");if(p)p.classList.remove("open");}
  function toggleAsk(){var p=$("mail-ask-panel");if(p.classList.contains("open"))closeAsk();else openAsk();}
  function openStil(){closeAsk();closeTriage();$("mail-stil-panel").classList.add("open");if(!mailProfiles)loadProfiles();else renderStilPanel();}
  function closeStil(){var p=$("mail-stil-panel");if(p)p.classList.remove("open");}
  function toggleStil(){var p=$("mail-stil-panel");if(p.classList.contains("open"))closeStil();else openStil();}

  /* ---------- triage: "what needs my attention" (unread scan of the current folder) ---------- */
  function mailTriageCurKey(){return mailAcct+"\n"+mailFolder;}
  // Reset the cached result (folder/account changed) and drop the panel if it's showing.
  function mailTriageReset(){mailTriageResult=null;mailTriageKey="";closeTriage();}
  function openTriage(){closeAsk();closeStil();$("mail-triage-panel").classList.add("open");
    // fetch only when there's no fresh result for THIS acct+folder; otherwise show the cache
    if(!mailTriageBusy&&(!mailTriageResult||mailTriageKey!==mailTriageCurKey()))mailTriage();else renderTriagePanel();}
  function closeTriage(){var p=$("mail-triage-panel");if(p)p.classList.remove("open");}
  function toggleTriage(){var p=$("mail-triage-panel");if(p.classList.contains("open"))closeTriage();else openTriage();}
  function mailTriage(){if(mailTriageBusy)return;var key=mailTriageCurKey();
    mailTriageBusy=true;mailTriageResult=null;renderTriagePanel();
    var op=aiLoaderStart("Triage inbox");
    aiPost("/api/mail/triage",{acct:mailAcct,folder:mailFolder}).then(function(d){
      aiLoaderStop(op);mailTriageBusy=false;noteAiKey(d);
      if(d.error){mailTriageResult={error:"Triage failed: "+d.error};mailTriageKey="";renderTriagePanel();return;}
      mailTriageResult={items:Array.isArray(d.items)?d.items:[],conclusion:String(d.conclusion||""),
        count:(d.count==null?null:(parseInt(d.count,10)||0)),truncated:!!d.truncated};
      mailTriageKey=key;renderTriagePanel();
    }).catch(function(e){aiLoaderStop(op);mailTriageBusy=false;mailTriageResult={error:"Triage failed: "+aiErrText(e)};mailTriageKey="";renderTriagePanel();});}
  function mailTriageRow(it,important){
    return '<div class="mtri-row'+(important?" imp":"")+'" data-uid="'+esc(it.uid)+'">'+
      '<div class="mtri-rtop">'+(important?'<span class="mtri-badge">Important</span>':'')+
        '<span class="mtri-from">'+esc(it.from||"&mdash;")+'</span>'+
        (it.category?'<span class="mtri-cat">'+esc(it.category)+'</span>':'')+'</div>'+
      '<div class="mtri-subj">'+esc(it.subject||"(no subject)")+'</div>'+
      (it.reason?'<div class="mtri-reason">'+esc(it.reason)+'</div>':'')+'</div>';}
  function renderTriagePanel(){var host=$("mail-triage-body");if(!host)return;
    if(mailTriageBusy){host.innerHTML='<div class="mp-busy"><span class="mail-spin"></span>Reading your unread&hellip;</div>';return;}
    var r=mailTriageResult;
    if(!r){host.innerHTML='<div class="mp-hint">Scan the unread mail in this folder and tell you what to look at first.</div>';return;}
    if(r.error){host.innerHTML='<div class="mail-ai-panel err"><b>Error</b>'+esc(r.error)+'</div>';return;}
    var items=r.items||[];
    if(!items.length){host.innerHTML='<div class="mtri-empty"><b>&#9993; No unread messages</b><span>You&rsquo;re all caught up in this folder.</span></div>';return;}
    var imp=items.filter(function(it){return it&&it.important;}),rest=items.filter(function(it){return !(it&&it.important);});
    var html="";
    if(r.conclusion)html+='<div class="mtri-concl"><div class="mtri-concl-h">&#9873; What to look at first</div><div class="mtri-concl-b">'+esc(r.conclusion)+'</div></div>';
    if(imp.length)html+='<div class="mtri-grp mtri-grp-imp">Important ('+imp.length+')</div>'+imp.map(function(it){return mailTriageRow(it,true);}).join("");
    if(rest.length)html+='<div class="mtri-grp">Everything else ('+rest.length+')</div>'+rest.map(function(it){return mailTriageRow(it,false);}).join("");
    if(r.truncated)html+='<div class="mtri-trunc">Showing first '+items.length+(r.count!=null&&r.count>items.length?" of "+r.count:"")+' unread</div>';
    host.innerHTML=html;
    // clicking a row closes the docked panel (so the reader underneath is visible) and opens the message
    Array.prototype.forEach.call(host.querySelectorAll(".mtri-row"),function(el){el.onclick=function(){var uid=el.getAttribute("data-uid");closeTriage();openMsg(uid);};});}

  /* ---------- sync all folders (server-side background sync; button spinner -> toast) ---------- */
  function mailSyncAll(){var b=$("mail-syncall");if(!b||b.disabled)return;var old=b.innerHTML;
    b.disabled=true;b.innerHTML='<span class="mail-spin"></span> Starting sync&hellip;';
    aiPost("/api/mail/sync",{acct:mailAcct}).then(function(d){
      b.disabled=false;b.innerHTML=old;
      if(d.error){mailToast("Sync failed: "+d.error);return;}
      if(d.ok===false){mailToast("Sync failed"+(d.status?": "+d.status:""));return;}
      mailToast("Syncing all folders…");
    }).catch(function(e){b.disabled=false;b.innerHTML=old;mailToast("Sync failed: "+aiErrText(e));});}
  // New-mail notification: mute toggle, WebAudio chime, toast.
  function renderMute(){var b=$("mail-mute");if(!b)return;b.innerHTML=mailSoundMuted?"&#128277;":"&#128276;";
    b.classList.toggle("muted",mailSoundMuted);b.title=mailSoundMuted?"new-mail sound off":"new-mail sound on";}
  function mailAudioCtx(){try{if(!mailAC){var C=window.AudioContext||window.webkitAudioContext;if(!C)return null;mailAC=new C();}
    if(mailAC.state==="suspended")mailAC.resume();return mailAC;}catch(e){return null;}}
  function mailChime(){var ac=mailAudioCtx();if(!ac)return;try{var t=ac.currentTime;
    [[880,0],[1320,0.16]].forEach(function(p){var o=ac.createOscillator(),g=ac.createGain();o.type="sine";o.frequency.value=p[0];
      o.connect(g);g.connect(ac.destination);var s=t+p[1];
      g.gain.setValueAtTime(0.0001,s);g.gain.exponentialRampToValueAtTime(0.18,s+0.02);g.gain.exponentialRampToValueAtTime(0.0001,s+0.14);
      o.start(s);o.stop(s+0.17);});}catch(e){}}
  function mailToast(txt){var el=$("mail-toast");if(!el)return;el.textContent=txt;el.classList.add("show");
    clearTimeout(mailToastT);mailToastT=setTimeout(function(){el.classList.remove("show");},3200);}
  function onMailNew(msg){var n=Math.max(1,parseInt(msg&&msg.count,10)||1);
    if(!mailSoundMuted)mailChime();
    mailToast(n+" "+(n===1?"new message":"new messages"));
    if(mode==="mail"&&mailLoaded&&String(msg.acct)===String(mailAcct)){loadFolders();if(msg.folder&&msg.folder===mailFolder)loadList();}}

  /* --- compose overlay --- */
  function mailChipHtml(list){return list.map(function(a,i){return '<span class="mail-recip">'+esc(a)+'<span class="mr-x" data-i="'+i+'">&times;</span></span>';}).join("");}
  function renderChips(){
    $("mail-to-chips").innerHTML=mailChipHtml(mailTo);
    $("mail-cc-chips").innerHTML=mailChipHtml(mailCc);
    bindChipRemove("mail-to-chips",mailTo);bindChipRemove("mail-cc-chips",mailCc);}
  function bindChipRemove(hostId,list){Array.prototype.forEach.call($(hostId).querySelectorAll(".mr-x"),function(x){
    x.onclick=function(){list.splice(parseInt(x.getAttribute("data-i"),10),1);renderChips();};});}
  function addRecips(which){var inp=$(which==="cc"?"mail-cc-in":"mail-to-in"),list=mailListOf(which),added=false;
    (inp.value||"").split(/[,;]+/).forEach(function(p){p=p.trim();if(p){list.push(p);added=true;}});
    inp.value="";if(added)renderChips();}
  function renderFileChips(){
    $("mail-file-chips").innerHTML=mailFiles.map(function(f,i){return '<span class="mail-fchip">&#128206; '+esc(f.name)+' <span class="mc-sz">'+esc(mailSize(f.size))+'</span><span class="mr-x" data-i="'+i+'">&times;</span></span>';}).join("");
    Array.prototype.forEach.call($("mail-file-chips").querySelectorAll(".mr-x"),function(x){x.onclick=function(){mailFiles.splice(parseInt(x.getAttribute("data-i"),10),1);renderFileChips();};});}
  function openCompose(pre){pre=pre||{};
    mailTo=(pre.to||[]).filter(Boolean).map(String);mailCc=(pre.cc||[]).filter(Boolean).map(String);mailFiles=[];
    $("mail-subj-in").value=pre.subject||"";$("mail-body-in").value=pre.body||"";
    $("mail-to-in").value="";$("mail-cc-in").value="";$("mail-file-in").value="";
    var ai=$("mail-assist-in");if(ai)ai.value="";
    var vin=$("mail-visual-in");if(vin)vin.value="";mailVisualReset();
    renderChips();renderFileChips();
    $("mail-compose-panel").classList.add("open");$("mail-to-in").focus();}
  function closeCompose(){$("mail-compose-panel").classList.remove("open");}
  // FileReader -> base64 (strip the data: URL prefix), matching the send contract's {name,data_b64}.
  // The attachments array mixes real File objects (Attach button) with pre-encoded objects
  // (AI visual, which already carry data_b64) — pass the latter through untouched, since
  // FileReader.readAsDataURL throws on a non-Blob.
  function mailReadFiles(files){return Promise.all(files.map(function(f){return new Promise(function(res){
    if(f&&typeof f.data_b64==="string"){res({name:f.name,data_b64:f.data_b64});return;}
    var fr=new FileReader();fr.onload=function(){var s=String(fr.result||""),i=s.indexOf(",");res({name:f.name,data_b64:i>=0?s.slice(i+1):s});};
    fr.onerror=function(){res({name:f.name,data_b64:""});};fr.readAsDataURL(f);});}));}
  function mailSend(){
    addRecips("to");addRecips("cc");   // commit any typed-but-not-chipped text first
    if(!mailTo.length){alert("Add at least one recipient (To).");return;}
    var subj=$("mail-subj-in").value||"",body=$("mail-body-in").value||"";
    if(!confirm('Send from account "'+mailAcctName(mailAcct)+'" to '+mailTo.length+' recipient(s)? Mail goes to real people.'))return;
    var btn=$("mail-send"),old=btn.textContent;btn.disabled=true;btn.textContent="Sending…";
    mailReadFiles(mailFiles).then(function(atts){
      return fetch("/api/mail/send",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({acct:mailAcct,to:mailTo,cc:mailCc,subject:subj,body:body,attachments:atts})}).then(function(r){return r.json();});
    }).then(function(j){btn.disabled=false;btn.textContent=old;
      if(j&&j.ok){closeCompose();mailFlash("Sent ✓");}else{alert("Send failed.");}
    }).catch(function(){btn.disabled=false;btn.textContent=old;alert("Send failed (network).");});}
  // Compose-assist: rough intent -> professional {subject, draft}. Fills the compose fields; NEVER auto-sends.
  function mailComposeAssist(){var ta=$("mail-assist-in");if(!ta)return;var intent=(ta.value||"").trim();
    if(!intent){ta.focus();return;}
    addRecips("to");var to=mailTo[0]||"";   // commit any typed recipient so the model can address it
    var btn=$("mail-assist-btn");if(btn)btn.disabled=true;
    var op=aiLoaderStart("Draft email");
    aiPost("/api/mail/compose",{acct:mailAcct,intent:intent,to:to}).then(function(d){
      aiLoaderStop(op);if(btn)btn.disabled=false;noteAiKey(d);
      if(d.error){mailFlash("Draft failed: "+d.error);return;}
      if(d.subject!=null)$("mail-subj-in").value=String(d.subject);
      if(d.draft!=null)$("mail-body-in").value=String(d.draft);
      mailFlash("Draft ready — review & send");
    }).catch(function(e){aiLoaderStop(op);if(btn)btn.disabled=false;mailFlash("Draft failed: "+aiErrText(e));});}

  /* --- AI visual: intent -> Diagram (SVG) or Image, preview, then attach as PNG --- */
  // UTF-8-safe base64 for the SVG string -> data: URL (matches the img.src contract).
  function mailB64Utf8(s){return btoa(unescape(encodeURIComponent(String(s))));}
  // approximate byte size of a base64 payload, for the attachment chip
  function mailB64Size(b){b=String(b||"");var pad=b.slice(-2)==="=="?2:b.slice(-1)==="="?1:0;return Math.max(0,Math.floor(b.length*3/4)-pad);}
  // reset preview between compose sessions / regenerations
  function mailVisualReset(){mailVisual=null;var vp=$("mail-visual-preview");if(vp)vp.hidden=true;
    var im=$("mail-visual-img");if(im)im.removeAttribute("src");var vk=$("mail-visual-kind");if(vk)vk.textContent="";}
  function mailVisualShow(src){var vp=$("mail-visual-preview"),im=$("mail-visual-img"),vk=$("mail-visual-kind");
    if(im)im.src=src;if(vk)vk.textContent=mailVisual?(mailVisual.kind==="image"?"image":"diagram"):"";if(vp)vp.hidden=false;}
  // Generate. kind="diagram" -> /api/mail/diagram {intent}->{svg}; kind="image" -> /api/mail/image {prompt}->{mime,data_b64}.
  function mailVisualGen(kind){var inp=$("mail-visual-in");if(!inp)return;var intent=(inp.value||"").trim();
    if(!intent){inp.focus();return;}
    var label=kind==="image"?"Image":"Diagram",db=$("mail-visual-diagram"),ib=$("mail-visual-image");
    if(db)db.disabled=true;if(ib)ib.disabled=true;
    var op=aiLoaderStart(kind==="image"?"Generate image":"Generate diagram");
    var url=kind==="image"?"/api/mail/image":"/api/mail/diagram",body=kind==="image"?{prompt:intent}:{intent:intent};
    aiPost(url,body).then(function(d){
      aiLoaderStop(op);if(db)db.disabled=false;if(ib)ib.disabled=false;noteAiKey(d);
      if(d.error){mailToast(label+" failed: "+d.error);return;}
      if(kind==="image"){
        if(!d.data_b64){mailToast(label+" failed: empty response");return;}
        mailVisual={kind:"image",mime:d.mime||"image/png",data_b64:String(d.data_b64)};
        mailVisualShow("data:"+mailVisual.mime+";base64,"+mailVisual.data_b64);
      }else{
        if(!d.svg){mailToast(label+" failed: empty response");return;}
        mailVisual={kind:"diagram",svg:String(d.svg)};
        var src;try{src="data:image/svg+xml;base64,"+mailB64Utf8(mailVisual.svg);}catch(e){mailToast(label+" failed: cannot encode");return;}
        mailVisualShow(src);   // an <img> src never runs scripts inside the SVG (no innerHTML injection)
      }
    }).catch(function(e){aiLoaderStop(op);if(db)db.disabled=false;if(ib)ib.disabled=false;mailToast(label+" failed: "+aiErrText(e));});}
  // Pull width/height (px) or viewBox from an SVG string, for sizing the raster canvas.
  function mailSvgDims(svg){svg=String(svg||"");var w=0,h=0;
    var vb=/viewBox\s*=\s*["']([-\d.,\s]+)["']/i.exec(svg);
    if(vb){var p=vb[1].trim().split(/[\s,]+/).map(parseFloat);if(p.length>=4){w=p[2];h=p[3];}}
    var mw=/\bwidth\s*=\s*["']\s*([\d.]+)\s*(px)?\s*["']/i.exec(svg),mh=/\bheight\s*=\s*["']\s*([\d.]+)\s*(px)?\s*["']/i.exec(svg);
    if(mw)w=parseFloat(mw[1])||w;if(mh)h=parseFloat(mh[1])||h;
    return {w:w>0?w:0,h:h>0?h:0};}
  // Rasterize an SVG string to a PNG base64 via canvas. White bg fill so a transparent SVG
  // reads on any client. Tainted-canvas / broken-SVG rejects -> caller falls back to raw .svg.
  function mailRasterizeSvg(svg){return new Promise(function(resolve,reject){
    var d=mailSvgDims(svg),im=new Image();
    im.onload=function(){try{
      var w=d.w||im.naturalWidth||800,h=d.h||im.naturalHeight||600;
      var scale=Math.min(3,Math.max(1,1000/Math.max(w,h)));   // upscale small diagrams; cap the canvas
      var cw=Math.max(1,Math.round(w*scale)),ch=Math.max(1,Math.round(h*scale));
      var cv=document.createElement("canvas");cv.width=cw;cv.height=ch;var ctx=cv.getContext("2d");
      ctx.fillStyle="#ffffff";ctx.fillRect(0,0,cw,ch);ctx.drawImage(im,0,0,cw,ch);
      var durl=cv.toDataURL("image/png"),i=durl.indexOf(","),b64=i>=0?durl.slice(i+1):durl;
      if(!b64)throw new Error("empty");resolve(b64);
    }catch(e){reject(e);}};
    im.onerror=function(){reject(new Error("svg load failed"));};
    try{im.src="data:image/svg+xml;base64,"+mailB64Utf8(svg);}catch(e){reject(e);}});}
  // Push a pre-encoded attachment into the SAME array the Send path reads; show a chip.
  function mailPushAttach(name,data_b64){mailFiles.push({name:name,size:mailB64Size(data_b64),data_b64:String(data_b64)});renderFileChips();}
  // Attach the current preview to the email. Image -> push directly. Diagram -> rasterize to
  // PNG (survives every mail client); on failure fall back to attaching the raw .svg.
  function mailAttachVisual(){if(!mailVisual)return;
    if(mailVisual.kind==="image"){mailPushAttach("image.png",mailVisual.data_b64);mailToast("Attached to email");return;}
    var ab=$("mail-visual-attach");if(ab)ab.disabled=true;
    mailRasterizeSvg(mailVisual.svg).then(function(b64){if(ab)ab.disabled=false;
      mailPushAttach("diagram.png",b64);mailToast("Attached to email");
    }).catch(function(){if(ab)ab.disabled=false;
      try{mailPushAttach("diagram.svg",mailB64Utf8(mailVisual.svg));mailToast("Attached as SVG (PNG export blocked)");}
      catch(e){mailToast("Attach failed: could not rasterize");}});}

  // static mail controls (elements exist in the DOM; wire once, like the tickets controls)
  $("mail-acct").onchange=function(){mailAcct=this.value;mailFolder="";mailLimit=50;mailMsg=null;mailQ="";$("mail-search").value="";mailTriageReset();renderReader();loadFolders();};
  $("mail-search").oninput=function(){mailQ=this.value;mailLimit=50;clearTimeout(mailSearchT);mailSearchT=setTimeout(loadList,250);};
  $("mail-sort").onchange=function(){mailSort=this.value;mailLimit=50;loadList();};
  $("mail-compose").onclick=function(){openCompose({});};
  $("mail-assist-btn").onclick=mailComposeAssist;
  $("mail-compose-x").onclick=closeCompose;
  $("mail-send").onclick=mailSend;
  $("mail-visual-diagram").onclick=function(){mailVisualGen("diagram");};
  $("mail-visual-image").onclick=function(){mailVisualGen("image");};
  $("mail-visual-attach").onclick=mailAttachVisual;
  $("mail-file-in").onchange=function(){for(var i=0;i<this.files.length;i++)mailFiles.push(this.files[i]);this.value="";renderFileChips();};
  ["to","cc"].forEach(function(w){var inp=$(w==="cc"?"mail-cc-in":"mail-to-in");
    inp.onkeydown=function(e){if(e.key==="Enter"||e.key===","){e.preventDefault();addRecips(w);}
      else if(e.key==="Backspace"&&!inp.value){var l=mailListOf(w);if(l.length){l.pop();renderChips();}}};
    inp.onblur=function(){addRecips(w);};});
  addEventListener("keydown",function(e){if(e.key==="Escape"&&$("mail-compose-panel").classList.contains("open"))closeCompose();});
  // AI-layer static controls
  $("mail-ask-toggle").onclick=toggleAsk;
  $("mail-ask-x").onclick=closeAsk;
  $("mail-ask-send").onclick=mailAsk;
  $("mail-ask-in").onkeydown=function(e){if(e.key==="Enter"){e.preventDefault();mailAsk();}};
  $("mail-stil-toggle").onclick=toggleStil;
  $("mail-stil-x").onclick=closeStil;
  $("mail-stil-refresh").onclick=mailStilRefresh;
  $("mail-triage-toggle").onclick=toggleTriage;
  $("mail-triage-x").onclick=closeTriage;
  $("mail-triage-rerun").onclick=mailTriage;
  $("mail-syncall").onclick=mailSyncAll;
  $("mail-mute").onclick=function(){mailSoundMuted=!mailSoundMuted;localStorage.setItem("mailSoundMuted",mailSoundMuted?"1":"0");renderMute();};
  var mvsel=$("mail-voicesel");if(mvsel)mvsel.onchange=function(){localStorage.setItem("mailVoice",this.value||"");};
  renderMute();applyAiVisibility();refreshVoiceSelect();
  addEventListener("keydown",function(e){if(e.key!=="Escape")return;
    if($("mail-triage-panel").classList.contains("open"))closeTriage();
    else if($("mail-ask-panel").classList.contains("open"))closeAsk();
    else if($("mail-stil-panel").classList.contains("open"))closeStil();});

