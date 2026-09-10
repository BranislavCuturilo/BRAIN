/* ---------- CALENDAR view (month/week/day + recurrence CRUD from /api/calendar/*) ----------
   Wrapped in an IIFE — unlike the other segment files, which are global-scoped and
   name-prefix everything. This adapts a standalone preview whose helpers are named
   render()/close()/today/view/ref: leaking those would collide with the shared
   globals and break the other tabs. Only window.calendarInit is exported; boot.js
   calls it on tab-open, exactly like gitInit()/prodInit(). Reuses the global esc()
   and aiPost() (core.js) for escaping and JSON POSTs.

   Occurrence contract (GET /api/calendar/events?from=&to= → flat list):
     {master_id, occ_id, occ_date, title, start, end, allday, cat, notes, is_recurring,
      remind_min}   (remind_min: minutes before start, 0 = none — Phase 2)
   Writes: POST /api/calendar/{create,update,delete}; create/update carry remind_min.
   Editing/deleting a recurring occurrence asks "ova instanca / ceo niz" and sends
   scope:"this"|"all" (+ occ_date).
   Phase 3 — Gemini bar: POST /api/calendar/gemini {text} → {ok, reply, events_changed};
   reply shown as a toast, events_changed re-fetches. ok:false shows the error reply.
   Reminders reuse mail.js's shared chime (mailChime) + global mute (mailSoundMuted) and
   the shared #focus-cards bottom-right stack, so they surface on ANY tab.

   F7 — "Predlozi" strip: GET /api/calendar/suggestions?from=&to= (same [from,to]
   as /api/calendar/events, refetched on every range change from load()) → chip
   rows an operator accepts (POST /api/calendar/suggestions/accept {sid,from,to},
   then a full reload — events change too) or dismisses (POST .../dismiss {sid},
   suggestions-only reload). Collapsed/open state persists in localStorage. A
   gear icon opens a MODAL (reuses .cal-ov/.cal-dlg — .cal-surface below clips
   overflow, so an anchored popover there would be cut off) with five
   GET/POST /api/calendar/sources checkboxes. An accepted suggestion becomes a
   normal event carrying source/ref (calsvc's schema); every occurrence whose
   `source` isn't "manual" renders with a small badge + dashed accent and opens
   through the SAME openEdit() as a hand-typed event — no second detail view. */
(function(){
  "use strict";
  var $=function(id){return document.getElementById(id);};

  // category key -> [cssClass, label, glyph]; `cat` arrives on each occurrence
  var CATS=[["cy","Posao","🖥️"],["vi","Lično","🏠"],
            ["gr","Zdravlje","❤️"],["am","Rok","⏰"],["pk","Sastanak","👥"]];
  var CATSET={cy:1,vi:1,gr:1,am:1,pk:1};
  function catClass(c){return CATSET[c]?c:"cy";}
  // F7: suggestion/occurrence source -> [label, glyph]. "manual" (operator-typed) is
  // never badged — calsug.suggestions() never emits it, and _mk_suggestion callers
  // enforce ticket|worklog|deploy|mail|chat only.
  var SRC={ticket:["tiket","🎫"],worklog:["rad","🛠️"],deploy:["deploy","🚀"],
           mail:["mail","✉️"],chat:["razgovor","💬"]};
  function srcInfo(s){return SRC[s]||[String(s||""),"•"];}
  var MON=["Januar","Februar","Mart","April","Maj","Jun","Jul","Avgust","Septembar","Oktobar","Novembar","Decembar"];
  var DOW=["Ned","Pon","Uto","Sre","Čet","Pet","Sub"];
  var BYDAY=[["MO","Pon"],["TU","Uto"],["WE","Sre"],["TH","Čet"],["FR","Pet"],["SA","Sub"],["SU","Ned"]];
  var REPEAT="↻";                      // ↻ marks a recurring occurrence
  var HH=52;                           // hour-row px height; MUST equal --cal-hh in calendar.css

  var pad=function(n){return("0"+n).slice(-2);};
  var iso=function(d){return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate());};
  // An occurrence's start/end arrive as ISO datetimes ("2026-08-12T09:30:00"), or a
  // bare date for all-day. Pull "HH:MM" for display + grid math (empty for all-day).
  function hm(s){s=String(s||"");var i=s.indexOf("T");return i<0?"":s.slice(i+1,i+6);}
  function minOf(s){var t=hm(s);if(!t)return 0;var p=t.split(":");return (+p[0])*60+(+p[1]);}
  // F7: an occurrence auto-created from a suggestion (source carried onto every
  // occurrence, like remind_min) reads as dashed + badged; "manual"/absent is unmarked.
  function isAuto(e){return !!(e.source&&e.source!=="manual");}
  function srcBadgeHtml(e){if(!isAuto(e))return "";var si=srcInfo(e.source);
    return '<span class="cal-src-badge" title="'+esc(si[0])+'">'+si[1]+'</span>';}
  function md(o,d){return new Date(o.getFullYear(),o.getMonth(),o.getDate()+d);}
  function midnight(){var d=new Date();d.setHours(0,0,0,0);return d;}
  function weekDays(r){var d=new Date(r),off=(d.getDay()+6)%7;d=md(d,-off);var a=[];for(var i=0;i<7;i++)a.push(md(d,i));return a;}
  function ymdCompact(ds){return String(ds||"").replace(/-/g,"");}
  // BYDAY key for a JS weekday (0=Sun..6=Sat)
  function bydayKey(jsDow){return ["SU","MO","TU","WE","TH","FR","SA"][jsDow];}

  var today=midnight();
  var view="month",ref=new Date(today);
  var OCC=[];                          // occurrences for the visible range (each gets _i = its index)
  var loading=false,loadErr=null;
  var editing=null;                    // occurrence being edited, or null in create mode
  var selCat="cy",selDays={};          // selDays: chosen BYDAY chips
  var scopeCb=null;                    // pending "this/all" chooser callback
  // Phase-2 reminders: a light [today .. today+2] window fetched independently of the
  // visible range, on a timer that runs regardless of the active tab, so a reminder
  // fires even when another segment is open. Each occ_id fires exactly once (tracked
  // in firedRemind). REM_WINDOW covers a "1 dan pre" (1440 min) lead on a +2d event.
  var remindOCC=[],firedRemind={},remindFetchAt=0,remindStarted=false;
  var REM_SCAN=45000,REM_REFETCH=270000,REM_WINDOW=2;
  // F7: suggestions for the CURRENT visible range — refetched alongside OCC in load().
  var SUG=[],sugLoading=false,sugErr=null;
  var sugOpen=(function(){try{return localStorage.getItem("av_cal_sug_open")!=="0";}catch(e){return true;}})();

  /* ------------------------------------------------------------ range + data */
  function rangeFor(){
    if(view==="month"){
      var first=new Date(ref.getFullYear(),ref.getMonth(),1),off=(first.getDay()+6)%7,start=md(first,-off);
      return {from:iso(start),to:iso(md(start,41))};
    }
    if(view==="week"){var w=weekDays(ref);return {from:iso(w[0]),to:iso(w[6])};}
    return {from:iso(ref),to:iso(ref)};                                        // day
  }
  function occByDate(ds){
    return OCC.filter(function(e){return e.occ_date===ds;})
              .sort(function(a,b){return String(a.start||"").localeCompare(String(b.start||""));});
  }
  function load(){
    var r=rangeFor();loading=true;loadErr=null;render();
    fetch("/api/calendar/events?from="+encodeURIComponent(r.from)+"&to="+encodeURIComponent(r.to))
      .then(function(res){return res.json().catch(function(){return {};}).then(function(d){
        if(!res.ok&&!(d&&d.error))d={error:"HTTP "+res.status};return d;});})
      .then(function(d){
        loading=false;
        if(d&&d.error){loadErr=String(d.error);OCC=[];return render();}
        OCC=Array.isArray(d)?d:((d&&(d.events||d.occurrences))||[]);
        OCC.forEach(function(e,i){e._i=i;});
        loadErr=null;render();
      })
      .catch(function(e){loading=false;loadErr=(e&&e.message)||"server nije dostupan";OCC=[];render();});
    loadSuggestions(r);                       // F7: same [from,to] window, independent fetch
  }

  /* ------------------------------------------------------------ F7: suggestions strip */
  function loadSuggestions(r){
    sugLoading=true;sugErr=null;renderSug();
    fetch("/api/calendar/suggestions?from="+encodeURIComponent(r.from)+"&to="+encodeURIComponent(r.to))
      .then(function(res){return res.json().catch(function(){return {};}).then(function(d){
        if(!res.ok&&!(d&&d.error))d={error:"HTTP "+res.status};return d;});})
      .then(function(d){
        sugLoading=false;
        if(d&&d.error){sugErr=String(d.error);SUG=[];return renderSug();}
        SUG=Array.isArray(d&&d.suggestions)?d.suggestions:[];
        sugErr=null;renderSug();
      })
      .catch(function(e){sugLoading=false;sugErr=(e&&e.message)||"server nije dostupan";SUG=[];renderSug();});
  }
  // s.start is an ISO date ("YYYY-MM-DD") or datetime — display day + (for timed) hh:mm.
  function sugWhen(s){
    var st=String(s.start||"");if(!st)return "";
    var d=new Date(st.length>10?st:st+"T00:00:00");
    if(isNaN(d.getTime()))return st;
    var ds=DOW[d.getDay()]+" "+d.getDate()+"."+(d.getMonth()+1)+".";
    return s.allday?ds:(ds+" "+hm(st));
  }
  function sugRowHtml(s){
    var si=srcInfo(s.source);
    return '<div class="cal-sug-row">'+
      '<span class="cal-sug-badge cal-src-'+esc(s.source)+'">'+si[1]+' '+esc(si[0])+'</span>'+
      '<span class="cal-sug-title" title="'+esc(s.why||"")+'">'+esc(s.title||"")+'</span>'+
      '<span class="cal-sug-when">'+esc(sugWhen(s))+'</span>'+
      '<button class="cal-sug-acc" type="button" data-sid="'+esc(s.sid)+'">+ prihvati</button>'+
      '<button class="cal-sug-dis" type="button" data-sid="'+esc(s.sid)+'">&times; odbaci</button>'+
      '</div>';
  }
  function renderSug(){
    var body=$("cal-sug-body"),cnt=$("cal-sug-count");
    if(!body||!cnt)return;                    // strip not injected yet
    cnt.textContent=String(SUG.length);
    if(sugLoading){body.innerHTML='<div class="cal-sug-note load">Učitavanje predloga…</div>';return;}
    if(sugErr){body.innerHTML='<div class="cal-sug-note">Ne mogu da učitam predloge — <b>'+esc(sugErr)+'</b></div>';return;}
    if(!SUG.length){body.innerHTML='<div class="cal-sug-empty">Nema predloga za ovaj period.</div>';return;}
    body.innerHTML=SUG.map(sugRowHtml).join("");
  }
  var sugBusy={};
  function sugAccept(sid){
    if(!sid||sugBusy[sid])return;sugBusy[sid]=true;
    var r=rangeFor();
    aiPost("/api/calendar/suggestions/accept",{sid:sid,from:r.from,to:r.to}).then(function(d){delete sugBusy[sid];
      if(d&&d.error){toast("⚠️ "+d.error,true);return;}
      toast("✅ Dodato u kalendar: "+((d&&d.event&&d.event.title)||""));
      load();                                  // re-fetch events AND suggestions
    });
  }
  function sugDismiss(sid){
    if(!sid)return;
    aiPost("/api/calendar/suggestions/dismiss",{sid:sid}).then(function(d){
      if(d&&d.error){toast("⚠️ "+d.error,true);return;}
      loadSuggestions(rangeFor());             // suggestions-only reload; events unaffected
    });
  }
  function sugBodyClick(e){
    var t=e.target;
    var acc=t.closest?t.closest(".cal-sug-acc"):null;
    if(acc){sugAccept(acc.getAttribute("data-sid"));return;}
    var dis=t.closest?t.closest(".cal-sug-dis"):null;
    if(dis){sugDismiss(dis.getAttribute("data-sid"));return;}
  }

  /* ------------------------------------------------------------ render */
  function render(){
    Array.prototype.forEach.call(document.querySelectorAll("#cal-views button"),
      function(b){b.classList.toggle("on",b.getAttribute("data-v")===view);});
    var note=loading?'<div class="cal-note load">Učitavanje…</div>'
      :loadErr?('<div class="cal-note">Ne mogu da učitam kalendar — <b>'+esc(loadErr)+'</b></div>'):"";
    $("cal-body").innerHTML=note+(view==="month"?monthHtml():timeHtml(view==="day"?[new Date(ref)]:weekDays(ref)));
    if(view!=="month"){var sc=$("cal-body").querySelector(".cal-tg-scroll");if(sc)sc.scrollTop=7*HH-10;}
    renderUpcoming();
  }
  function monthHtml(){
    $("cal-mlabel").textContent=MON[ref.getMonth()]+" "+ref.getFullYear();
    var first=new Date(ref.getFullYear(),ref.getMonth(),1),off=(first.getDay()+6)%7,start=md(first,-off),count=0;
    var h='<div class="cal-wk"><div>Pon</div><div>Uto</div><div>Sre</div><div>Čet</div><div>Pet</div><div>Sub</div><div>Ned</div></div><div class="cal-grid">';
    for(var i=0;i<42;i++){
      var dt=md(start,i),ds=iso(dt),evs=occByDate(ds),show=evs.slice(0,4),cells="";count+=evs.length;
      show.forEach(function(e){
        cells+='<div class="cal-ev '+catClass(e.cat)+(isAuto(e)?" auto":"")+'" data-i="'+e._i+'">'+
          (e.allday?"":'<span class="cal-t">'+esc(hm(e.start))+'</span>')+esc(e.title||"")+
          srcBadgeHtml(e)+(e.is_recurring?'<span class="cal-rep">'+REPEAT+'</span>':"")+'</div>';
      });
      if(evs.length>4)cells+='<div class="cal-more">+'+(evs.length-4)+' više</div>';
      h+='<div class="cal-cell'+(dt.getMonth()!==ref.getMonth()?" out":"")+(ds===iso(today)?" today":"")+
         '" data-date="'+ds+'"><div class="cal-dn">'+dt.getDate()+"</div>"+cells+"</div>";
    }
    h+="</div>";
    if(!count&&!loading&&!loadErr)h+='<div class="cal-empty">Nema događaja u ovom mesecu.<br>Klikni na dan da dodaš.</div>';
    return h;
  }
  function timeHtml(days){
    if(days.length===1){var d=days[0];$("cal-mlabel").textContent=DOW[d.getDay()]+", "+d.getDate()+". "+MON[d.getMonth()];}
    else{var a=days[0],b=days[6];$("cal-mlabel").textContent=a.getDate()+"."+(a.getMonth()+1)+" – "+b.getDate()+"."+(b.getMonth()+1)+". "+a.getFullYear();}
    var tc="56px repeat("+days.length+",1fr)",count=0;
    var h='<div class="cal-tg"><div class="cal-tg-head" style="grid-template-columns:'+tc+'"><div></div>';
    days.forEach(function(d){h+='<div class="cal-tg-dh'+(iso(d)===iso(today)?" t":"")+'">'+DOW[d.getDay()]+"<b>"+d.getDate()+"</b></div>";});
    h+='</div><div class="cal-tg-ad" style="grid-template-columns:'+tc+'"><div class="cal-lab">ceo dan</div>';
    days.forEach(function(d){
      var ad=occByDate(iso(d)).filter(function(e){return e.allday;}),s="";count+=ad.length;
      ad.forEach(function(e){s+='<div class="cal-ev '+catClass(e.cat)+(isAuto(e)?" auto":"")+'" data-i="'+e._i+'">'+esc(e.title||"")+
        srcBadgeHtml(e)+(e.is_recurring?'<span class="cal-rep">'+REPEAT+'</span>':"")+'</div>';});
      h+='<div class="cal-tg-adcol" data-date="'+iso(d)+'">'+s+"</div>";
    });
    h+='</div><div class="cal-tg-scroll"><div class="cal-tg-body" style="grid-template-columns:'+tc+'">';
    var axis='<div class="cal-tg-axis">';for(var hr=0;hr<24;hr++)axis+='<div class="cal-hr">'+pad(hr)+":00</div>";axis+="</div>";h+=axis;
    days.forEach(function(d){
      var col='<div class="cal-tg-col" data-date="'+iso(d)+'">';
      occByDate(iso(d)).filter(function(e){return !e.allday&&e.start;}).forEach(function(e){
        var sm=minOf(e.start),em=e.end?minOf(e.end):sm+60,top=sm/60*HH,hgt=Math.max(22,(em-sm)/60*HH);count++;
        col+='<div class="cal-tg-ev '+catClass(e.cat)+(isAuto(e)?" auto":"")+'" data-i="'+e._i+'" style="top:'+top+'px;height:'+hgt+'px">'+
             '<b>'+esc(hm(e.start))+"</b> "+esc(e.title||"")+srcBadgeHtml(e)+(e.is_recurring?'<span class="cal-rep">'+REPEAT+'</span>':"")+"</div>";
      });
      if(iso(d)===iso(today)){var now=new Date(),nm=now.getHours()*60+now.getMinutes();col+='<div class="cal-nowline" style="top:'+(nm/60*HH)+'px"></div>';}
      col+="</div>";h+=col;
    });
    h+="</div></div></div>";
    if(!count&&!loading&&!loadErr)h+='<div class="cal-empty">Nema događaja.<br>Klikni na termin da dodaš.</div>';
    return h;
  }
  function renderUpcoming(){
    var u=$("cal-upcoming");if(!u)return;u.innerHTML="";
    if(loadErr){u.innerHTML='<div class="cal-railempty">'+esc(loadErr)+"</div>";return;}
    var t0=iso(today);
    var fut=OCC.filter(function(e){return e.occ_date>=t0;})
      .sort(function(a,b){return (a.occ_date+(a.start||"")).localeCompare(b.occ_date+(b.start||""));}).slice(0,12);
    if(!fut.length){u.innerHTML='<div class="cal-railempty">Nema predstojećih u prikazanom periodu.</div>';return;}
    fut.forEach(function(e){
      var dd=new Date(e.occ_date+"T00:00:00");
      var when=(t0===e.occ_date?"Danas":DOW[dd.getDay()]+" "+dd.getDate()+"."+(dd.getMonth()+1)+".")+(e.allday?" · ceo dan":" · "+esc(hm(e.start)));
      var row=document.createElement("div");row.className="cal-up "+catClass(e.cat)+(isAuto(e)?" auto":"");
      row.innerHTML='<span class="cal-dot"></span><div><div class="u1">'+esc(e.title||"")+srcBadgeHtml(e)+
        (e.is_recurring?'<span class="cal-rep">'+REPEAT+'</span>':"")+'</div><div class="u2">'+when+"</div></div>";
      row.onclick=function(){openEdit(e);};u.appendChild(row);
    });
  }

  /* ------------------------------------------------------------ click routing (delegated once) */
  function bodyClick(e){
    var t=e.target,ev=t.closest?t.closest("[data-i]"):null;
    if(ev){var i=+ev.getAttribute("data-i");if(OCC[i])openEdit(OCC[i]);return;}
    var cell=t.closest?t.closest(".cal-cell"):null;
    if(cell&&cell.getAttribute("data-date")){openNew(cell.getAttribute("data-date"));return;}
    var adcol=t.closest?t.closest(".cal-tg-adcol"):null;
    if(adcol&&adcol.getAttribute("data-date")){openNew(adcol.getAttribute("data-date"),null,true);return;}
    var col=t.closest?t.closest(".cal-tg-col"):null;
    if(col&&col.getAttribute("data-date")){
      var y=e.clientY-col.getBoundingClientRect().top,hh=Math.max(0,Math.min(23,Math.floor(y/HH)));
      openNew(col.getAttribute("data-date"),pad(hh)+":00");
    }
  }

  /* ------------------------------------------------------------ category + recurrence chips */
  function renderCats(){
    var c=$("cal-cats");if(!c)return;c.innerHTML="";
    CATS.forEach(function(k){
      var d=document.createElement("div");d.className="cal-cat"+(k[0]===selCat?" sel":"");
      d.innerHTML='<span class="cal-sw" style="background:var(--cal-'+k[0]+')"></span>'+k[2]+" "+esc(k[1]);
      d.onclick=function(){selCat=k[0];renderCats();};c.appendChild(d);
    });
  }
  function renderByday(){
    var c=$("cal-byday");if(!c)return;c.innerHTML="";
    BYDAY.forEach(function(k){
      var d=document.createElement("div");d.className="cal-day"+(selDays[k[0]]?" sel":"");d.textContent=k[1];
      d.onclick=function(){if(selDays[k[0]])delete selDays[k[0]];else selDays[k[0]]=true;renderByday();syncRrulePreview();};
      c.appendChild(d);
    });
  }
  function buildRRule(){
    var f=$("cal-f-freq").value;if(f==="none")return "";
    var FREQ={daily:"DAILY",weekly:"WEEKLY",monthly:"MONTHLY",yearly:"YEARLY"}[f];
    var parts=["FREQ="+FREQ];
    if(f==="weekly"){
      var days=BYDAY.map(function(x){return x[0];}).filter(function(k){return selDays[k];});
      if(days.length)parts.push("BYDAY="+days.join(","));
    }
    var em=$("cal-f-end-mode").value;
    if(em==="until"){var uu=$("cal-f-until").value;if(uu)parts.push("UNTIL="+ymdCompact(uu));}
    else if(em==="count"){var n=parseInt($("cal-f-count").value,10);if(n>0)parts.push("COUNT="+n);}
    return parts.join(";");
  }
  // freq / end-mode changed → show the right sub-fields, refresh the preview
  function syncRecUI(){
    var f=$("cal-f-freq").value,on=f!=="none";
    $("cal-rec").hidden=!on;
    $("cal-byday-fld").hidden=f!=="weekly";
    if(f==="weekly"&&!Object.keys(selDays).length){
      var dv=$("cal-f-date").value;if(dv){selDays[bydayKey(new Date(dv+"T00:00:00").getDay())]=true;renderByday();}
    }
    var em=$("cal-f-end-mode").value;
    $("cal-until-fld").hidden=em!=="until";
    $("cal-count-fld").hidden=em!=="count";
    syncRrulePreview();
  }
  function syncRrulePreview(){var rr=buildRRule();$("cal-rrule-preview").textContent=rr?("rrule: "+rr):"";}

  /* ------------------------------------------------------------ modal open/close */
  function openNew(ds,tm,ad){
    editing=null;selCat="cy";selDays={};
    $("cal-dlgtitle").firstChild.textContent="Novi događaj ";
    $("cal-f-del").hidden=true;$("cal-recur-editor").hidden=false;$("cal-rec-badge").hidden=true;
    $("cal-f-title").value="";$("cal-f-date").value=ds||iso(today);
    $("cal-f-allday").checked=!!ad;$("cal-timerow").hidden=!!ad;
    $("cal-f-start").value=tm||"09:00";
    $("cal-f-end").value=tm?pad((+tm.slice(0,2)+1)%24)+":00":"10:00";
    $("cal-f-notes").value="";
    $("cal-f-freq").value="none";$("cal-f-end-mode").value="never";
    $("cal-f-until").value="";$("cal-f-count").value="10";
    setRemind(10);
    renderCats();renderByday();syncRecUI();
    openModal();setTimeout(function(){$("cal-f-title").focus();},50);
  }
  function openEdit(occ){
    editing=occ;selCat=catClass(occ.cat);
    $("cal-dlgtitle").firstChild.textContent="Izmena događaja ";
    $("cal-f-del").hidden=false;$("cal-recur-editor").hidden=true;      // recurrence pattern is create-time only
    $("cal-rec-badge").hidden=!occ.is_recurring;
    $("cal-f-title").value=occ.title||"";$("cal-f-date").value=occ.occ_date||iso(today);
    $("cal-f-allday").checked=!!occ.allday;$("cal-timerow").hidden=!!occ.allday;
    $("cal-f-start").value=hm(occ.start)||"09:00";$("cal-f-end").value=hm(occ.end)||"10:00";
    $("cal-f-notes").value=occ.notes||"";
    setRemind(typeof occ.remind_min==="number"?occ.remind_min:10);
    renderCats();openModal();
  }
  function openModal(){$("cal-ov").classList.add("on");}
  function closeModal(){$("cal-ov").classList.remove("on");}

  /* ------------------------------------------------------------ scope chooser (this / all) */
  function askScope(verb,cb){
    scopeCb=cb;
    $("cal-scope-msg").textContent="Ovaj događaj se ponavlja. Da li da "+verb+" samo OVU instancu ili CEO niz?";
    $("cal-scope-ov").classList.add("on");
  }
  function hideScope(){scopeCb=null;$("cal-scope-ov").classList.remove("on");}

  /* ------------------------------------------------------------ writes */
  // Build the backend payload: start/end are COMBINED ISO (a bare date for all-day,
  // "YYYY-MM-DDThh:mm" for timed). The service reads date+time off `start` — there is
  // no separate `date` field. end is null for all-day (single-day event).
  function collectFields(){
    var ad=$("cal-f-allday").checked,date=$("cal-f-date").value||iso(today);
    var st=$("cal-f-start").value||"09:00",en=$("cal-f-end").value||"10:00";
    return {title:$("cal-f-title").value.trim()||"Događaj",allday:ad,
      start:ad?date:(date+"T"+st),end:ad?null:(date+"T"+en),
      cat:selCat,notes:$("cal-f-notes").value.trim(),remind_min:remindVal()};
  }
  // The reminder select's minutes (0 = off). The field is injected in wire() (index.html
  // is off-limits), so guard for its absence and default to the app default of 10.
  function remindVal(){var s=$("cal-f-remind");if(!s)return 10;var m=parseInt(s.value,10);return (isNaN(m)||m<0)?0:m;}
  // Reflect a minutes value in the select, adding a bespoke option if it isn't a preset.
  function setRemind(min){
    var s=$("cal-f-remind");if(!s)return;
    min=parseInt(min,10);if(isNaN(min)||min<0)min=10;
    var has=false;for(var i=0;i<s.options.length;i++){if(+s.options[i].value===min){has=true;break;}}
    if(!has){var o=document.createElement("option");o.value=String(min);o.textContent=min+" min pre";s.appendChild(o);}
    s.value=String(min);
  }
  function doWrite(op,payload,msg){
    closeModal();
    aiPost("/api/calendar/"+op,payload).then(function(d){
      if(d&&d.error){toast("⚠️ "+d.error,true);return;}
      toast(msg);load();                                             // re-fetch the visible range
    });
  }
  function save(){
    var p=collectFields();
    if(editing){
      p.id=editing.master_id;                              // service keys writes off `id` (the master)
      if(editing.is_recurring){
        askScope("izmeniš",function(scope){p.scope=scope;if(scope==="this")p.occ_date=editing.occ_date;
          doWrite("update",p,"✏️ Izmenjeno: "+p.title);});
      }else{p.scope="all";doWrite("update",p,"✏️ Izmenjeno: "+p.title);}
    }else{
      var rr=buildRRule();if(rr)p.rrule=rr;
      doWrite("create",p,"✅ Dodato: "+p.title);
    }
  }
  function del(){
    if(!editing)return;
    var p={id:editing.master_id};                          // service keys writes off `id` (the master)
    if(editing.is_recurring){
      askScope("obrišeš",function(scope){p.scope=scope;if(scope==="this")p.occ_date=editing.occ_date;
        doWrite("delete",p,"🗑 Obrisano");});
    }else{p.scope="all";doWrite("delete",p,"🗑 Obrisano");}
  }

  /* ------------------------------------------------------------ toast */
  var tt;
  function toast(m,isErr,ms){var t=$("cal-toast");if(!t)return;t.textContent=m;t.className="cal-toast on"+(isErr?" err":"");
    clearTimeout(tt);tt=setTimeout(function(){t.className="cal-toast";},ms||2600);}

  /* ------------------------------------------------------------ Phase 2: event reminders */
  // The bottom-right card stack: reuse the always-present #focus-cards host (fixed,
  // z-55, pointer-events managed) so a reminder shows on ANY tab and never overlaps a
  // Fokus card — they share one column. Fall back to our own host if Fokus is absent.
  function remindHost(){
    var h=document.getElementById("focus-cards");if(h)return h;
    h=document.getElementById("cal-remind-host");
    if(!h){h=document.createElement("div");h.id="cal-remind-host";document.body.appendChild(h);}
    return h;
  }
  // Play the SAME alert as new-mail / Fokus reminders, gated by the SAME global mute
  // (mail-mute bell). Both are cross-file globals from mail.js — guarded so a missing or
  // late-loaded mail.js never throws. This mirrors focus.js's focusMaybeChime; it reuses
  // the shared sound path rather than reaching into focus.js internals.
  function remindChime(){
    try{
      if(typeof mailSoundMuted!=="undefined"&&mailSoundMuted)return;   // shared global mute is on
      if(typeof mailChime==="function")mailChime();                    // shared WebAudio chime
    }catch(e){}
  }
  // An occurrence's start as a Date: ISO datetime for timed, local midnight for all-day.
  function remindStartDate(o){
    if(o.allday)return new Date(String(o.occ_date||o.start)+"T00:00:00");
    var d=new Date(String(o.start||""));return isNaN(d.getTime())?null:d;
  }
  function remindWhenText(o){
    var dd=new Date(String(o.occ_date||"")+"T00:00:00");
    var day=(iso(today)===o.occ_date?"Danas":DOW[dd.getDay()]+" "+dd.getDate()+"."+(dd.getMonth()+1)+".");
    if(o.allday)return day+" · ceo dan";
    var st=remindStartDate(o),mins=st?Math.round((st.getTime()-Date.now())/60000):0;
    var lead=mins>0?(" · za "+(mins>=60?Math.round(mins/60)+"h":mins+" min")):"";
    return day+" u "+hm(o.start)+lead;
  }
  function remindFire(o){
    var host=remindHost();if(!host)return;
    var card=document.createElement("div");card.className="cal-remind "+catClass(o.cat);
    card.innerHTML='<div class="cal-remind-top"><span class="cal-remind-ico">⏰</span>'+
      '<div class="cal-remind-tt"><div class="cal-remind-title">'+esc(o.title||"Događaj")+'</div>'+
      '<div class="cal-remind-when">'+esc(remindWhenText(o))+'</div></div></div>'+
      '<div class="cal-remind-acts"><button class="cal-remind-open" type="button">Otvori</button>'+
      '<button class="cal-remind-ok" type="button">OK</button></div>';
    function dismiss(){if(card.parentNode)card.parentNode.removeChild(card);}
    var ds=o.occ_date;
    card.querySelector(".cal-remind-open").onclick=function(){remindOpen(ds);dismiss();};
    card.querySelector(".cal-remind-ok").onclick=dismiss;
    host.appendChild(card);
    remindChime();
  }
  // Jump to the occurrence's day. setMode("calendar") re-inits the tab (calls calendarInit
  // → load()), which honours the view/ref we set here; if already on the tab, load() alone.
  function remindOpen(ds){
    view="day";ref=new Date(String(ds)+"T00:00:00");
    if(typeof window.setMode==="function"&&window.mode!=="calendar")window.setMode("calendar");
    else load();
  }
  // Scan the merged reminder window + visible range for occurrences whose reminder time
  // has arrived. remindOCC is the always-fresh source (fires on any tab); OCC is merged so
  // a just-created event is eligible immediately. An occurrence already started is marked
  // fired (missed), never surfaced late; each fires exactly once.
  function remindScan(){
    var now=Date.now(),map={};
    remindOCC.forEach(function(o){map[o.occ_id]=o;});
    OCC.forEach(function(o){if(!map[o.occ_id])map[o.occ_id]=o;});
    for(var id in map){
      if(!map.hasOwnProperty(id)||firedRemind[id])continue;
      var o=map[id],rm=parseInt(o.remind_min,10);
      if(!(rm>0))continue;
      var st=remindStartDate(o);if(!st)continue;
      var sms=st.getTime(),fireAt=sms-rm*60000;
      if(now>=sms){firedRemind[id]=true;continue;}       // already started → missed, never fire late
      if(now>=fireAt){firedRemind[id]=true;remindFire(o);}
    }
  }
  function remindFetch(){
    remindFetchAt=Date.now();today=midnight();
    var from=iso(today),to=iso(md(today,REM_WINDOW));
    fetch("/api/calendar/events?from="+encodeURIComponent(from)+"&to="+encodeURIComponent(to))
      .then(function(res){return res.json().catch(function(){return {};});})
      .then(function(d){remindOCC=Array.isArray(d)?d:((d&&(d.events||d.occurrences))||[]);remindScan();})
      .catch(function(){});                                // offline → skip this tick, no error surfaced
  }
  function remindTick(){if(Date.now()-remindFetchAt>REM_REFETCH)remindFetch();else remindScan();}
  function remindStart(){
    if(remindStarted)return;remindStarted=true;
    remindFetch();
    setInterval(remindTick,REM_SCAN);
    document.addEventListener("visibilitychange",function(){if(!document.hidden)remindTick();});
    window.addEventListener("focus",remindTick);
  }

  /* ------------------------------------------------------------ Phase 3: Gemini bar */
  // Enable the "USKORO" stub: undisable the input/button, drop the badge, wire send.
  function enableGemini(){
    var gin=$("cal-gin"),gsend=$("cal-gsend"),gbar=$("cal-gbar");if(!gin||!gsend||!gbar)return;
    gin.disabled=false;gsend.disabled=false;gbar.removeAttribute("aria-disabled");gbar.classList.add("cal-gon");
    gsend.title="Pošalji Gemini-ju (Enter)";
    var soon=gbar.querySelector(".cal-soon");if(soon)soon.hidden=true;
    gin.placeholder='Reci Gemini-ju: „zakaži sastanak sutra u 15h", „obriši termin u petak"…';
    // In-flight guard: /api/calendar/gemini SPENDS the shared ~15 RPM Gemini quota (same
    // pool as mail/ticket triage), so refuse a concurrent/duplicate send while one is
    // pending — belt-and-suspenders alongside the disabled state, and ignore empty text.
    var gBusy=false;
    function send(){
      if(gBusy)return;
      var txt=(gin.value||"").trim();if(!txt)return;
      gBusy=true;
      var old=gsend.textContent;gsend.disabled=true;gin.disabled=true;gsend.textContent="…";
      aiPost("/api/calendar/gemini",{text:txt}).then(function(d){
        d=d||{};
        var ok=d.ok!==false&&!d.error;
        var reply=d.reply||d.error||(ok?"Gotovo.":"Gemini greška.");
        toast((ok?"🔮 ":"⚠️ ")+reply,!ok,5200);
        if(ok)gin.value="";
        if(d.events_changed){load();remindFetch();}       // re-fetch visible range + reminder window
      }).catch(function(e){toast("⚠️ "+((e&&e.message)||"Gemini nedostupan"),true,5200);})
        .then(function(){gBusy=false;gsend.disabled=false;gin.disabled=false;gsend.textContent=old;gin.focus();});
    }
    gsend.onclick=send;
    gin.onkeydown=function(e){if(e.key==="Enter"){e.preventDefault();send();}};
  }

  /* ------------------------------------------------------------ modal field injection (index.html is off-limits) */
  function injectRemindField(){
    if($("cal-f-remind"))return;
    var notes=$("cal-f-notes");if(!notes||!notes.parentNode)return;
    var fld=document.createElement("div");fld.className="cal-fld";
    fld.innerHTML='<label for="cal-f-remind">Podseti me</label>'+
      '<select id="cal-f-remind">'+
      '<option value="0">Isključeno</option>'+
      '<option value="5">5 min pre</option>'+
      '<option value="10" selected>10 min pre</option>'+
      '<option value="15">15 min pre</option>'+
      '<option value="30">30 min pre</option>'+
      '<option value="60">1 sat pre</option>'+
      '<option value="120">2 sata pre</option>'+
      '<option value="1440">1 dan pre</option>'+
      '</select>';
    notes.parentNode.parentNode.insertBefore(fld,notes.parentNode);
  }

  /* ------------------------------------------------------------ Google Calendar sync (discoverable placeholder — NOT wired) */
  // A subtle header entry point so a user sees the capability exists; there is no
  // backend. index.html is off-limits, so both the button and its info dialog are
  // injected here, and the dialog REUSES the segment's .cal-ov/.cal-dlg modal shell
  // (same styling as create/edit) rather than a bespoke popover — one modal system.
  function injectGCalSync(){
    var head=document.querySelector("#view-calendar .cal-head"),view=$("view-calendar");
    if(!head||!view||$("cal-gcal-btn"))return;
    var btn=document.createElement("button");
    btn.className="btn cal-gcal";btn.id="cal-gcal-btn";btn.type="button";
    btn.title="Google Calendar sinhronizacija (uskoro)";
    btn.innerHTML="🔄 Google Calendar";            // 🔄
    head.appendChild(btn);
    var ov=document.createElement("div");ov.className="cal-ov";ov.id="cal-gcal-ov";
    ov.innerHTML='<div class="cal-dlg cal-scope-dlg" role="dialog" aria-modal="true" aria-labelledby="cal-gcal-title">'+
      '<h2 id="cal-gcal-title">Google Calendar sinhronizacija '+
      '<span class="x" id="cal-gcal-x" role="button" tabindex="0" aria-label="Zatvori">✕</span></h2>'+
      '<p class="cal-scope-msg">Nije aktivirano — zahteva jednokratno Google OAuth podešavanje. '+
      'Uskoro (dvosmerni sync, GCal kao izvor istine).</p>'+
      '<div class="cal-dlgact"><button class="btn cal-p" id="cal-gcal-ok" type="button">U redu</button></div></div>';
    view.appendChild(ov);
    function close(){ov.classList.remove("on");}
    btn.onclick=function(){ov.classList.add("on");};
    ov.querySelector("#cal-gcal-x").onclick=close;
    ov.querySelector("#cal-gcal-ok").onclick=close;
    ov.onclick=function(e){if(e.target===ov)close();};
  }

  /* ------------------------------------------------------------ F7: "Predlozi" strip + izvori modal */
  // Inserted right after .cal-gbar, above .cal-layout — same "index.html is off-limits,
  // inject from here" approach as the reminder field / GCal button above.
  function injectSugStrip(){
    if($("cal-sug"))return;
    var gbar=document.querySelector("#view-calendar .cal-gbar");
    if(!gbar||!gbar.parentNode)return;
    var strip=document.createElement("div");
    strip.className="cal-sug"+(sugOpen?"":" collapsed");strip.id="cal-sug";
    strip.innerHTML=
      '<div class="cal-sug-head" id="cal-sug-toggle">'+
        '<span class="cal-sug-chev">&#9662;</span>'+
        '<span class="cal-sug-tt">Predlozi</span>'+
        '<span class="cal-sug-count" id="cal-sug-count">0</span>'+
        '<span class="spacer"></span>'+
        '<span class="cal-sug-refresh" id="cal-sug-refresh" role="button" tabindex="0" '+
          'title="Osveži predloge iz maila / razgovora (1 Gemini poziv dnevno, samo kad su ti izvori uključeni)">&#10227; osveži (AI)</span>'+
        '<span class="cal-sug-gear" id="cal-sug-gear" role="button" tabindex="0" '+
          'aria-label="Izvori predloga" title="Izvori predloga">&#9881;</span>'+
      '</div>'+
      '<div class="cal-sug-body" id="cal-sug-body"></div>';
    gbar.parentNode.insertBefore(strip,gbar.nextSibling);
    $("cal-sug-refresh").onclick=function(e){e.stopPropagation();var b=this;if(b.classList.contains("busy"))return;
      b.classList.add("busy");var r=rangeFor();
      aiPost("/api/calendar/suggestions/refresh",{from:r.from,to:r.to}).then(function(d){b.classList.remove("busy");
        if(d&&d.error){toast("⚠️ "+d.error,true);return;}
        SUG=(d&&d.suggestions)||[];sugRender();toast(d&&d.ai_used?"✅ Predlozi osveženi (AI)":"Predlozi osveženi (bez AI: mail/razgovori isključeni ili keš)");
      }).catch(function(){b.classList.remove("busy");toast("⚠️ osvežavanje nije uspelo",true);});};
    $("cal-sug-toggle").onclick=function(e){
      if(e.target.closest&&(e.target.closest("#cal-sug-gear")||e.target.closest("#cal-sug-refresh")))return;   // gear/refresh are not the collapse
      sugOpen=!sugOpen;strip.classList.toggle("collapsed",!sugOpen);
      try{localStorage.setItem("av_cal_sug_open",sugOpen?"1":"0");}catch(err){}
    };
    $("cal-sug-gear").onclick=function(e){e.stopPropagation();openSourcesModal();};
    $("cal-sug-body").onclick=sugBodyClick;
    renderSug();
  }
  // Five GET/POST /api/calendar/sources checkboxes, reusing the .cal-ov/.cal-dlg modal
  // shell (same reasoning as the GCal info dialog: .cal-surface below clips overflow,
  // so an anchored popover there risks being cut off — the shared modal has no such
  // ancestor). mail/chat carry the Gemini-quota note calsug.py's docstring documents
  // (one Lite call/day shared by both when either is on).
  var srcLast={};
  function injectSourcesModal(){
    var view=$("view-calendar");
    if(!view||$("cal-src-ov"))return;
    // "deploy" stays a config key but has no producer yet — no checkbox for it (a knob over dead code misleads).
    var rows=[["tickets","Tiketi (rokovi)"],["worklog","Radne sesije"],
              ["mail","Mail (poslednja 3 dana)"],["chat","Razgovori sa AI"]];
    var fields=rows.map(function(r){
      var note=(r[0]==="mail"||r[0]==="chat")
        ?'<div class="cal-src-note">troši 1 Gemini poziv dnevno</div>':"";
      return '<label class="cal-chk"><input type="checkbox" id="cal-src-'+r[0]+'"> '+esc(r[1])+'</label>'+note;
    }).join("");
    var ov=document.createElement("div");ov.className="cal-ov";ov.id="cal-src-ov";
    ov.innerHTML='<div class="cal-dlg cal-scope-dlg" role="dialog" aria-modal="true" aria-labelledby="cal-src-title">'+
      '<h2 id="cal-src-title">Izvori predloga '+
      '<span class="x" id="cal-src-x" role="button" tabindex="0" aria-label="Zatvori">&#10005;</span></h2>'+
      '<div class="cal-src-list">'+fields+'</div>'+
      '<div class="cal-dlgact"><button class="btn" id="cal-src-cancel" type="button">Otkaži</button>'+
      '<button class="btn cal-p" id="cal-src-save" type="button">Sačuvaj</button></div></div>';
    view.appendChild(ov);
    function close(){ov.classList.remove("on");}
    ov.querySelector("#cal-src-x").onclick=close;
    ov.querySelector("#cal-src-cancel").onclick=close;
    ov.onclick=function(e){if(e.target===ov)close();};
    ov.querySelector("#cal-src-save").onclick=function(){
      var body={};rows.forEach(function(r){var cb=$("cal-src-"+r[0]);body[r[0]]=!!(cb&&cb.checked);});
      body.deploy=!!srcLast.deploy;   // no checkbox (no producer yet) — carry the stored value, the route wants all five
      aiPost("/api/calendar/sources",body).then(function(d){
        if(d&&d.error){toast("⚠️ "+d.error,true);return;}
        toast("✅ Izvori sačuvani");close();loadSuggestions(rangeFor());
      });
    };
  }
  // Reads current toggle state fresh (GET) every open — this popover can be opened long
  // after boot, so a stale in-memory copy would show the wrong checkmarks.
  function openSourcesModal(){
    fetch("/api/calendar/sources")
      .then(function(res){return res.json().catch(function(){return {};});})
      .then(function(d){
        var src=(d&&d.sources)||{};srcLast=src;
        ["tickets","worklog","deploy","mail","chat"].forEach(function(k){
          var cb=$("cal-src-"+k);if(cb)cb.checked=!!src[k];
        });
      })
      .catch(function(){})
      .then(function(){$("cal-src-ov").classList.add("on");});
  }

  /* ------------------------------------------------------------ wiring (once, at load) */
  function wire(){
    injectRemindField();injectGCalSync();injectSugStrip();injectSourcesModal();enableGemini();
    $("cal-body").onclick=bodyClick;
    Array.prototype.forEach.call(document.querySelectorAll("#cal-views button"),
      function(b){b.onclick=function(){view=b.getAttribute("data-v");load();};});
    $("cal-prev").onclick=function(){ref=view==="month"?new Date(ref.getFullYear(),ref.getMonth()-1,1):md(ref,view==="week"?-7:-1);load();};
    $("cal-next").onclick=function(){ref=view==="month"?new Date(ref.getFullYear(),ref.getMonth()+1,1):md(ref,view==="week"?7:1);load();};
    $("cal-today").onclick=function(){ref=new Date(today);load();};
    $("cal-addbtn").onclick=function(){openNew(iso(view==="month"?today:ref));};
    $("cal-f-allday").onchange=function(){$("cal-timerow").hidden=this.checked;};
    $("cal-f-freq").onchange=syncRecUI;$("cal-f-end-mode").onchange=syncRecUI;
    $("cal-f-until").onchange=syncRrulePreview;$("cal-f-count").oninput=syncRrulePreview;$("cal-f-date").onchange=syncRecUI;
    $("cal-dlgx").onclick=closeModal;$("cal-f-cancel").onclick=closeModal;
    $("cal-ov").onclick=function(e){if(e.target===this)closeModal();};
    $("cal-f-save").onclick=save;$("cal-f-del").onclick=del;
    $("cal-scope-this").onclick=function(){var cb=scopeCb;hideScope();if(cb)cb("this");};
    $("cal-scope-all").onclick=function(){var cb=scopeCb;hideScope();if(cb)cb("all");};
    $("cal-scope-cancel").onclick=hideScope;$("cal-scope-x").onclick=hideScope;
    $("cal-scope-ov").onclick=function(e){if(e.target===this)hideScope();};
    addEventListener("keydown",function(e){if(e.key!=="Escape")return;
      var g=$("cal-gcal-ov"),s=$("cal-src-ov");
      if(s&&s.classList.contains("on"))s.classList.remove("on");
      else if(g&&g.classList.contains("on"))g.classList.remove("on");
      else if($("cal-scope-ov").classList.contains("on"))hideScope();
      else if($("cal-ov").classList.contains("on"))closeModal();});
  }

  // exported: boot.js calls this on tab-open (like gitInit/prodInit). Refresh "today"
  // in case the page has been open across midnight, then fetch the visible range and
  // freshen the reminder scan.
  function calendarInit(){today=midnight();load();remindTick();}

  wire();render();                       // wire handlers + paint the shell before first open
  remindStart();                         // reminders run regardless of the active tab (light: 3-day fetch/4.5min + 45s scan)
  window.calendarInit=calendarInit;
})();
