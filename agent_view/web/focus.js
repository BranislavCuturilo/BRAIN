  "use strict";
  /* ================= Focus timer + wellness reminders =================
     ALWAYS-ON HUD chrome (NOT a segment): a "pilula" in the top bar + non-blocking
     reminder cards bottom-right. The backend contract is that EVERY /api/focus*
     route returns the SAME view object, so we re-render from any response.
     Reuses core.js globals: $ (getElementById), esc, aiPost (the generic
     error-surfacing POST — its "ai" name is historical; it is just POST-json).
     GET /api/focus is a plain fetch (like fetchMeta). See focus.py for the shape. */

  var focusView = null;            // latest view object from the server
  var focusCards = {};             // card key -> the card element currently shown
  var focusPopoverOpen = false;    // the click-through STATS dropdown under the pilula
  var focusModalOpen = false;      // the full settings MODAL (opened from Profil ⚙ / the dropdown)
  var focusSettingsPopulated = false;  // one-time populate of the settings inputs
  var focusDayReport = null;       // richest end-day report we hold (survives the poll)
  var FOCUS_POLL_MS = 15000;       // GET poll cadence (a separate 1s ticker moves elapsed)

  // Reminder card copy (Serbian, per the approved UI). Glyphs are HTML entities so
  // they render regardless of how the .js is charset-served; text goes in via innerHTML.
  var FOCUS_CARD_DEFS = {
    water:    {title:"Popij vodu &#128167;",                               ok:"Popio &#10003;"},
    stretch:  {title:"Vreme za istezanje &#129336;",                        ok:"Uradio &#10003;"},
    exercise: {title:null,                                                  ok:"Uradio &#10003;"},   // built from the picked exercise
    coffee:   {title:"Fokus opada &#9749; &mdash; kafa/energetsko?",        ok:"Ok &#10003;"}
  };
  var FOCUS_TYPES = ["water","stretch","exercise","coffee"];

  // Per-category reminder ENABLE/DISABLE — the four FIXED dimensions each get an
  // "Uključi podsetnik" toggle at their section header, bound to config.reminders_enabled[<key>]
  // (default true when the backend hasn't set it) and POSTing {reminders_enabled:{<key>:bool}}.
  // OFF greys+disables that section's body and reveals a "neutralna osnova u Karakteru" hint, so
  // it reads as tying into the RPG character. Keys per the backend contract: Voda→water,
  // Istezanje→stretch, Vežbe→exercise, Fokus→fokus (Serbian spelling, NOT "focus"). Podsetnici
  // keep their per-item toggles — there is no section switch there.
  var FOCUS_SEC_BY_KEY = {water:"fset-voda", stretch:"fset-istezanje", exercise:"fset-vezbe", fokus:"fset-fokus"};

  // Exercise dropdown option sets (SPEC: exercises carry `muscle` one-of-7 and `stat` RPG attr,
  // default snaga). Values are ASCII ids the backend stores; labels carry Serbian diacritics as
  // HTML entities (charset-agnostic, like the rest of this file). The 7 muscles are fixed by the
  // spec; the stat set is our best-known RPG list — final option set is pending the backend enum,
  // so focusOptions() always injects the current stored value if it isn't in our list (a value the
  // backend hands us always round-trips, never silently lost).
  var FOCUS_MUSCLES = [["grudi","Grudi"],["ramena","Ramena"],["biceps","Biceps"],
    ["podlaktice","Podlaktice"],["trbusnjaci","Trbu&#353;njaci"],["kvadriceps","Kvadriceps"],["listovi","Listovi"]];
  var FOCUS_STATS = [["snaga","Snaga"],["izdrzljivost","Izdr&#382;ljivost"],["kondicija","Kondicija"],
    ["agilnost","Agilnost"],["fokus","Fokus"]];
  // Build <option>s for a select, marking `sel` selected; if `sel` isn't one of our known ids we
  // prepend it as its own option so a backend-supplied value is never dropped on the next save.
  function focusOptions(pairs, sel){
    sel = sel != null ? String(sel) : "";
    var found = false, html = "";
    for(var i=0;i<pairs.length;i++){
      var val = pairs[i][0];
      if(val === sel) found = true;
      html += '<option value="' + esc(val) + '"' + (val === sel ? ' selected' : '') + '>' + pairs[i][1] + '</option>';
    }
    if(sel && !found) html = '<option value="' + esc(sel) + '" selected>' + esc(sel) + '</option>' + html;
    return html;
  }
  // Where each config section mounts inside the settings modal. Sub-builders append into their
  // section's GREYABLE body (.fset-body) so a disabled category dims them too; if a section has no
  // body (Podsetnici, which has no on/off switch) they fall back to the section, then the host.
  function focusSecHost(id){
    var sec = document.getElementById(id);
    if(sec){ var body = sec.querySelector(".fset-body"); return body || sec; }
    return focusCfgHost();
  }

  // Elapsed is a DURATION, not a time-of-day — core.js's hhmmss() formats an epoch
  // as wall-clock time (wrong here, and it would wrap past 24h), so this is a
  // deliberately-separate local formatter (craft-reuse: "exists but wrong for you").
  function focusDur(sec){
    sec = Math.max(0, Math.floor(sec || 0));
    var h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
    function p(n){return (n<10?"0":"")+n;}
    return (h>0 ? h+":" : "") + p(m) + ":" + p(s);
  }
  function focusNow(){return Date.now()/1000;}   // same machine as the server (loopback) → shared clock

  // Elapsed seconds from a session block: now - started_at - paused_total_sec, clamped
  // at >=0. While paused it FREEZES — the span is measured to paused_at, not the live
  // clock — matching how focus.py freezes the score at its break value.
  function focusElapsed(sess){
    if(!sess) return 0;
    var start = +sess.started_at || 0, pt = +sess.paused_total_sec || 0;
    var end = (sess.paused && sess.paused_at != null) ? (+sess.paused_at) : focusNow();
    return Math.max(0, end - start - pt);
  }

  // The focus-score ring. HIGH score is GOOD, so colour runs green -> amber -> red as it
  // FALLS (the inverse of a usage ring): >=70 calm, >=40 warn, <40 bad — the <40 red lines
  // up with focus.py's default low_score_threshold (the coffee nudge fires there).
  function focusRing(score){
    var s = Math.max(0, Math.min(100, Math.round(+score || 0)));
    var tok = s>=70 ? "--acc" : s>=40 ? "--warn" : "--bad";
    var r = 9, c = 2*Math.PI*r, off = (c*(1 - s/100)).toFixed(2);
    return '<svg class="fp-ringsvg" viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">'
      + '<circle class="fp-track" cx="12" cy="12" r="'+r+'"/>'
      + '<circle class="fp-prog" cx="12" cy="12" r="'+r+'" style="stroke:var('+tok+')" '
      +   'stroke-dasharray="'+c.toFixed(2)+'" stroke-dashoffset="'+off+'"/>'
      + '<text class="fp-scoretext" x="12" y="12">'+s+'</text></svg>';
  }

  /* ---------- fetch + apply ---------- */
  function focusPoll(){
    fetch("/api/focus").then(function(r){return r.json();}).then(function(v){
      if(v && !v.error) focusApply(v);
    }).catch(function(){});
  }
  // Single funnel every response (GET poll or any POST) runs through, so the pilula,
  // counters and cards always reflect the latest server truth.
  function focusApply(v){
    focusView = v;
    focusRenderPilula(v);
    focusReconcileCards(v);
    focusReconcilePause(v);    // centered pause overlay tracks session.paused
    focusReconcileDayEnd(v);   // day-ended modal appears on EVERY poll while day_ended
    if(focusPopoverOpen) focusRenderStats(v);   // keep the open stats dropdown live on every poll
    // One-time populate of the Profil settings inputs: covers the case where the view
    // arrives AFTER the Profil tab was opened. Subsequent polls do NOT re-fill the inputs,
    // so an in-progress edit is never clobbered (the save handlers re-render rows themselves).
    var sHost = $("focus-settings");
    if(sHost && sHost.getAttribute("data-built") && !focusSettingsPopulated){
      focusPopulateSettings(v); focusSettingsPopulated = true;
    }
    focusTick();               // refresh elapsed immediately, don't wait for the 1s tick
  }

  /* ---------- pilula render ---------- */
  function focusRenderPilula(v){
    var pill = $("focus-pill"); if(!pill || !v) return;
    var cfg = v.config || {}, sess = v.session || {};
    pill.classList.toggle("off", cfg.enabled === false);
    pill.classList.toggle("paused", !!sess.paused);
    var score = (v.focus && v.focus.score != null) ? v.focus.score : 0;
    var ring = $("fp-ring"); if(ring) ring.innerHTML = focusRing(score);
    // NOTE: today's activity counters moved OFF the bar into the click-through stats
    // dropdown (focusRenderStats) — the pilula keeps only ring + elapsed + Pauza/Nastavi.
    var pb = $("fp-pause"); if(pb) pb.textContent = sess.paused ? "Nastavi" : "Pauza";
    var pz = $("fp-pauza"); if(pz) pz.hidden = !sess.paused;
  }
  // 1s ticker: moves ONLY the elapsed display (we do not poll every second).
  function focusTick(){
    if(!focusView) return;
    var t = $("fp-time"); if(!t) return;
    t.textContent = focusDur(focusElapsed(focusView.session));
  }

  /* ---------- pause / resume ---------- */
  function focusTogglePause(){
    if(!focusView) return;
    var paused = !(focusView.session && focusView.session.paused);
    var ov = $("focus-pause-ov"); if(ov) ov.hidden = !paused;   // optimistic; the response confirms
    aiPost("/api/focus/pause", {paused:paused}).then(function(v){ if(v && !v.error) focusApply(v); });
  }
  // Resume from the centered overlay (button or backdrop). Optimistically hide, then confirm.
  function focusResume(){
    var ov = $("focus-pause-ov"); if(ov) ov.hidden = true;
    aiPost("/api/focus/pause", {paused:false}).then(function(v){ if(v && !v.error) focusApply(v); });
  }
  // The overlay follows server truth: shown only while paused AND the day is not ended.
  function focusReconcilePause(v){
    var ov = $("focus-pause-ov"); if(!ov) return;
    var sess = v.session || {};
    ov.hidden = !!sess.day_ended || !sess.paused;
  }

  /* ---------- stats dropdown (opens on a pilula click) ----------
     The dropdown that used to hold the config is now a compact "today" stats panel in the
     SAME position: the score + the counters that were removed from the bar. ALL settings
     moved to the Profil tab (focusBuildSettings). The footer has "Kraj dana" (end-day) and
     "Podešavanja" (→ Profil tab). NO config controls live here anymore. */
  function focusTogglePopover(){ focusPopoverOpen ? focusClosePopover() : focusOpenPopover(); }
  function focusOpenPopover(){
    var pop = $("focus-pop"); if(!pop || !focusView) return;
    focusRenderStats(focusView);
    pop.classList.add("open"); focusPopoverOpen = true;
    focusPositionPopover();
  }
  function focusClosePopover(){
    var pop = $("focus-pop"); if(!pop) return;
    pop.classList.remove("open"); focusPopoverOpen = false;
  }
  // Fixed positioning under the pilula so the top bar's overflow:auto never clips it.
  function focusPositionPopover(){
    var pop = $("focus-pop"), pill = $("focus-pill"); if(!pop || !pill) return;
    var r = pill.getBoundingClientRect(), w = pop.offsetWidth || 250;
    var left = Math.min(r.left, innerWidth - w - 10); if(left < 10) left = 10;
    pop.style.left = left + "px";
    pop.style.top  = (r.bottom + 8) + "px";
  }
  // The "today" stats — score ring + the counters that used to sit on the bar (water /
  // stretch / exercise reps from metrics.today; pauses / snoozes from the work-day `day`
  // block; the streak from metrics.streak_days). Read-only; no POST from here.
  function focusRenderStats(v){
    var box = $("focus-statbody"); if(!box || !v) return;
    var m = v.metrics || {}, day = v.day || {};
    var score = (v.focus && v.focus.score != null) ? Math.round(v.focus.score) : 0;
    var water   = (m.water    && m.water.today)       || 0;
    var stretch = (m.stretch  && m.stretch.today)     || 0;
    var reps    = (m.exercise && m.exercise.today_reps) || 0;
    var pauses  = (day.pauses  != null) ? day.pauses  : ((m.pauses  && m.pauses.count)  || 0);
    var snoozes = (day.snoozes != null) ? day.snoozes : ((m.snoozes && m.snoozes.count) || 0);
    var streak  = (m.streak_days) || 0;
    box.innerHTML =
        '<div class="fstat-score"><span class="fstat-ring">' + focusRing(score) + '</span>'
      +   '<span class="fstat-scorelab">fokus danas</span></div>'
      + '<div class="fstat-grid">'
      +   focusStatCell("&#128167;", water,   "voda")
      +   focusStatCell("&#129336;", stretch, "istezanje")
      +   focusStatCell("&#128170;", reps,    "ve&#382;be")
      +   focusStatCell("&#9208;",   pauses,  "pauze")
      +   focusStatCell("&#128564;", snoozes, "odlaganja")
      +   focusStatCell("&#128293;", streak,  "dana zaredom")
      + '</div>';
  }
  function focusStatCell(glyph, val, lab){
    return '<div class="fstat-cell"><span class="fstat-ico">' + glyph + '</span>'
         +   '<span class="fstat-val">' + (val || 0) + '</span>'
         +   '<span class="fstat-lab">' + lab + '</span></div>';
  }

  /* ---------- settings host (the modal) ----------
     ALL config controls live in the settings MODAL (#focus-smodal → #focus-settings), opened from
     the Profil header ⚙ button and the pilula stats-dropdown. focusBuildSettings mounts the SAME
     builders (activities / coffee / exercises / custom) into #focus-settings — ONE implementation,
     not a fork. focusCfgHost is the single reference the builders target. The layout is 5
     clearly-separated sections (Voda · Istezanje · Vežbe · Fokus · Podsetnici) + a general strip
     (enable + snooze) at the top; each sub-builder appends into its section via focusSecHost. */
  function focusCfgHost(){ return $("focus-settings"); }
  function focusBuildSettings(){
    var host = focusCfgHost(); if(!host) return;
    if(!host.getAttribute("data-built")){
      host.setAttribute("data-built", "1");
      // Skeleton: error line, a general strip (enable + snooze), then the 5 section shells the
      // sub-builders mount into. Interval rows carry data-iv; each section is a titled group.
      host.innerHTML =
          '<div class="fpop-err" id="fpop-err"></div>'
        + '<div class="fset-gen">'
        +   '<label class="fpop-row fset-enable"><span>Aktivno</span><input type="checkbox" id="fpop-enabled"></label>'
        +   '<label class="fpop-row"><span>Odlaganje (snooze)</span><input type="number" min="1" step="1" id="fpop-snooze"><span class="u">min</span></label>'
        + '</div>'
        + '<div class="fset-sec" id="fset-voda">'
        +   focusSecHeadHtml("water", "&#128167; Voda")
        +   '<div class="fset-body">'
        +     '<div class="fset-hint">Podsetnik da popije&#353; vodu na svakih N minuta.</div>'
        +     '<label class="fpop-row"><span>Interval</span><input type="number" min="1" step="1" data-iv="water"><span class="u">min</span></label>'
        +     focusSoundRowHtml("water")
        +   '</div>'
        + '</div>'
        + '<div class="fset-sec" id="fset-istezanje">'
        +   focusSecHeadHtml("stretch", "&#129336; Istezanje")
        +   '<div class="fset-body">'
        +     '<div class="fset-hint">Podsetnik za istezanje na svakih N minuta.</div>'
        +     '<label class="fpop-row"><span>Interval</span><input type="number" min="1" step="1" data-iv="stretch"><span class="u">min</span></label>'
        +     focusSoundRowHtml("stretch")
        +   '</div>'
        + '</div>'
        + '<div class="fset-sec" id="fset-vezbe">'
        +   focusSecHeadHtml("exercise", "&#128170; Ve&#382;be")
        +   '<div class="fset-body">'
        +     '<label class="fpop-row"><span>Interval</span><input type="number" min="1" step="1" data-iv="exercise"><span class="u">min</span></label>'
        +     focusSoundRowHtml("exercise")
        +   '</div>'   // focusEnsureExMenu appends the exercise table into this .fset-body
        + '</div>'
        + '<div class="fset-sec" id="fset-fokus">'
        +   focusSecHeadHtml("fokus", "&#127919; Fokus")
        +   '<div class="fset-body">'
        +     '<div class="fset-hint">Iska&#269;e kad fokus padne &mdash; ne na interval.</div>'
        +   '</div>'   // focusEnsureFocusActivities + focusEnsureCoffeeRow append into this .fset-body
        + '<div class="fset-sec" id="fset-podsetnici">'
        +   '<div class="fset-head">&#128276; Podsetnici</div>'
        + '</div>';  // focusEnsureCustomMenu appends the custom-reminder table here
      // Mount the sub-builders into their sections (Fokus: activities ABOVE coffee).
      focusEnsureExMenu();
      focusEnsureFocusActivities();
      focusEnsureCoffeeRow();
      focusEnsureCustomMenu();
      var ivs = host.querySelectorAll("input[data-iv]");
      for(var i=0;i<ivs.length;i++){ (function(inp){
        inp.addEventListener("change", function(){ focusSaveInterval(inp.getAttribute("data-iv"), inp.value); });
      })(ivs[i]); }
      // per-type sound toggles (data-sound="water|stretch|exercise|coffee") — one shared save that
      // reads every checkbox into config.sound. Queried AFTER the ensure-fns so the coffee toggle
      // (added by focusEnsureCoffeeRow) is included.
      var snds = host.querySelectorAll("input[data-sound]");
      for(var s=0;s<snds.length;s++) snds[s].addEventListener("change", focusSaveSound);
      var en = $("fpop-enabled"); if(en) en.addEventListener("change", function(){ focusSaveEnabled(en.checked); });
      var sn = $("fpop-snooze"); if(sn) sn.addEventListener("change", function(){ focusSaveSnooze(sn.value); });
      // per-section ENABLE toggles (data-secen="water|stretch|exercise|fokus"): each POSTs its own
      // reminders_enabled flag and greys its section body when off.
      var secens = host.querySelectorAll("input[data-secen]");
      for(var q=0;q<secens.length;q++){ (function(inp){
        inp.addEventListener("change", function(){ focusSaveSectionEnabled(inp.getAttribute("data-secen"), inp.checked); });
      })(secens[q]); }
    }
    if(focusView){ focusPopulateSettings(focusView); focusSettingsPopulated = true; }
  }
  // A compact "🔔 zvuk" on/off toggle row for a reminder section (data-sound = the type key).
  function focusSoundRowHtml(type){
    return '<label class="fpop-row fset-sound"><span>&#128276; zvuk</span>'
      +      '<input type="checkbox" data-sound="' + esc(type) + '"></label>';
  }
  // A fixed section's header: the emoji+title, an "Uključi podsetnik" ENABLE toggle
  // (data-secen=<key>), and the OFF hint (revealed by .sec-off) tying the disable to the RPG
  // Karakter. The head stays OUTSIDE .fset-body so the toggle is always operable and the hint
  // never dims. `title` already carries its HTML entities (rendered via innerHTML like the rest).
  function focusSecHeadHtml(key, title){
    return '<div class="fset-head">'
      +      '<span class="fset-head-title">' + title + '</span>'
      +      '<label class="fset-secen" title="Uključi/isključi ovaj podsetnik">'
      +        '<input type="checkbox" data-secen="' + esc(key) + '">'
      +        '<span>Uklju&#269;i podsetnik</span>'
      +      '</label>'
      +    '</div>'
      + '<div class="fset-offhint">isklju&#269;eno &mdash; dimenzija na neutralnoj osnovi u Karakteru</div>';
  }
  // Collect every section toggle into config.sound {water,stretch,exercise,coffee} and POST it.
  function focusSaveSound(){
    var host = focusCfgHost(); if(!host) return;
    var snds = host.querySelectorAll("input[data-sound]"), sound = {};
    for(var i=0;i<snds.length;i++){ sound[snds[i].getAttribute("data-sound")] = !!snds[i].checked; }
    aiPost("/api/focus/config", {sound: sound}).then(focusAfterConfig);
  }
  function focusPopulateSound(cfg){
    var host = focusCfgHost(); if(!host) return;
    var snd = (cfg && cfg.sound) || {}, snds = host.querySelectorAll("input[data-sound]");
    for(var i=0;i<snds.length;i++){ var k = snds[i].getAttribute("data-sound"); snds[i].checked = snd[k] !== false; }
  }
  /* ---------- per-category enable/disable (reminders_enabled) ---------- */
  // Toggle one category on/off: grey the body optimistically, POST just that flag, then re-sync the
  // toggles + greying from the returned view (so a value the backend normalizes always round-trips).
  function focusSaveSectionEnabled(key, checked){
    focusApplySectionState(key, !!checked);              // optimistic — instant grey/ungrey
    var re = {}; re[key] = !!checked;
    aiPost("/api/focus/config", {reminders_enabled: re}).then(function(v){
      if(!v || v.error){ return focusConfigErr(v && v.error ? esc(v.error) : "gre&#353;ka"); }
      var err = $("fpop-err"); if(err) err.textContent = "";
      focusApply(v);                                     // pilula/cards reflect the (dis)abled dimension
      focusPopulateSectionEnabled(v.config || {});       // re-sync every toggle from server truth
    });
  }
  // Set each section's toggle from config.reminders_enabled (DEFAULT true when the backend hasn't
  // set the key yet) and apply the greyed state to its body.
  function focusPopulateSectionEnabled(cfg){
    var re = (cfg && cfg.reminders_enabled) || {}, host = focusCfgHost(); if(!host) return;
    var toggles = host.querySelectorAll("input[data-secen]");
    for(var i=0;i<toggles.length;i++){
      var key = toggles[i].getAttribute("data-secen");
      var on = re[key] !== false;                         // unset → ON (the backend defaults all true)
      toggles[i].checked = on;
      focusApplySectionState(key, on);
    }
  }
  // Grey/restore a section's body when its category is OFF/ON. The .sec-off class drives the CSS
  // (dim + pointer-events:none on .fset-body; reveals .fset-offhint); we also flip the body controls'
  // `disabled` so a keyboard user can't tab into a disabled dimension. The head (toggle + hint) sits
  // OUTSIDE .fset-body, so it stays fully operable and readable.
  function focusApplySectionState(key, on){
    var secId = FOCUS_SEC_BY_KEY[key], sec = secId && document.getElementById(secId); if(!sec) return;
    sec.classList.toggle("sec-off", !on);
    var body = sec.querySelector(".fset-body"); if(!body) return;
    var ctrls = body.querySelectorAll("input,select,button,textarea");
    for(var i=0;i<ctrls.length;i++) ctrls[i].disabled = !on;
  }
  function focusPopulateSettings(v){
    if(!v) return;
    var cfg = v.config || {}, iv = cfg.intervals_min || {}, host = focusCfgHost(); if(!host) return;
    var inps = host.querySelectorAll("input[data-iv]");
    for(var i=0;i<inps.length;i++){ var k = inps[i].getAttribute("data-iv"); if(iv[k] != null) inps[i].value = Math.round(iv[k]); }
    var en = $("fpop-enabled"); if(en) en.checked = cfg.enabled !== false;
    var sn = $("fpop-snooze"); if(sn && cfg.snooze_min != null) sn.value = Math.round(cfg.snooze_min);
    focusPopulateCoffee(cfg);
    focusPopulateSound(cfg);
    focusRenderExercises(v);
    focusRenderFocusActivities(v);
    focusRenderCustom(v);
    focusPopulateSectionEnabled(cfg);   // AFTER the row renders, so disabled applies to fresh controls
  }
  function focusPopulateCoffee(cfg){
    var cc = (cfg && cfg.coffee) || {};
    var cen = $("fpop-coffee-en"); if(cen) cen.checked = cc.enabled !== false;
    var cthr = $("fpop-coffee-thr"); if(cthr && cc.low_score_threshold != null) cthr.value = Math.round(cc.low_score_threshold);
    var ccd = $("fpop-coffee-cd"); if(ccd && cc.cooldown_min != null) ccd.value = Math.round(cc.cooldown_min);
  }
  function focusSaveInterval(type, val){
    var n = parseInt(val, 10);
    if(!(n >= 1)){ return focusConfigErr("interval mora biti &ge; 1"); }
    var iv = {}; iv[type] = n;
    aiPost("/api/focus/config", {intervals_min:iv}).then(focusAfterConfig);
  }
  function focusSaveEnabled(checked){
    aiPost("/api/focus/config", {enabled:!!checked}).then(focusAfterConfig);
  }
  function focusSaveSnooze(val){
    var n = parseInt(val, 10);
    if(!(n >= 1)){ return focusConfigErr("odlaganje mora biti &ge; 1 min"); }
    aiPost("/api/focus/config", {snooze_min:n}).then(focusAfterConfig);
  }
  function focusConfigErr(msg){
    var err = $("fpop-err"); if(err) err.innerHTML = msg;
    if(focusView) focusPopulateSettings(focusView);   // revert the offending field to last-good
  }
  function focusAfterConfig(v){
    if(!v || v.error){ return focusConfigErr(v && v.error ? esc(v.error) : "gre&#353;ka"); }
    var err = $("fpop-err"); if(err) err.textContent = "";
    focusApply(v);      // update the pilula/stats; leave settings inputs as the user set them
  }

  /* ---------- exercise-management menu (inside the config popover) ----------
     The whole set is edited here and POSTed as the FULL `exercises` array — the
     backend REPLACES the set and re-normalises (ids, ordering), so we always
     re-render the rows from the returned view rather than trusting local state.
     Built dynamically (focusEnsureExMenu) so this widget is self-contained in
     focus.js and mounts into the settings host (focusCfgHost), not the popover. */
  function focusEnsureExMenu(){
    if(!focusCfgHost()) return;
    if($("fpop-ex")) return;                          // section already built
    var sec = document.createElement("div");
    sec.className = "fpop-ex"; sec.id = "fpop-ex";
    sec.innerHTML =
        '<div class="fpop-exhead">Ve&#382;be <span class="fpx-hint">naziv &middot; cilj pon. &middot; te&#382;. &middot; atribut &middot; mi&#353;i&#263;i %</span></div>'
      + '<div class="fpop-exrows" id="fpop-exrows"></div>'
      + '<div class="fpop-exrow fpop-exadd">'
      +   '<input class="fpx-name" id="fpx-add-name" type="text" placeholder="nova ve&#382;ba" aria-label="naziv nove vežbe">'
      +   '<input class="fpx-target" id="fpx-add-target" type="number" min="1" step="1" placeholder="12" aria-label="ciljni broj ponavljanja">'
      +   '<input class="fpx-weight" id="fpx-add-weight" type="number" min="0" step="0.05" placeholder="0.2" aria-label="težina">'
      +   '<select class="fpx-muscle" id="fpx-add-muscle" aria-label="mišićna grupa">' + focusOptions(FOCUS_MUSCLES, "") + '</select>'
      +   '<select class="fpx-stat" id="fpx-add-stat" aria-label="atribut">' + focusOptions(FOCUS_STATS, "snaga") + '</select>'
      +   '<button class="fpx-addbtn" id="fpx-add" type="button" title="Dodaj vežbu" aria-label="Dodaj vežbu">&#43;</button>'
      + '</div>';
    focusSecHost("fset-vezbe").appendChild(sec);
    // Delegated handlers on the ROWS container (survives innerHTML re-renders). Any input/select
    // change in a row saves the whole set; the add row is a sibling of #fpop-exrows so it's excluded.
    // Each exercise is now a BLOCK (.fpx-exwrap): a main row + a muscle-% sub-panel, so the
    // delegated click routes between add-muscle / remove-muscle / delete-exercise.
    var rows = $("fpop-exrows");
    if(rows){
      rows.addEventListener("change", function(e){
        var el = e.target; if(!el) return;
        if(el.tagName === "INPUT" || el.tagName === "SELECT") focusSaveExercises(focusCollectExercises());
      });
      // live pct-sum hint while a % is being typed (no save until the change/blur commits)
      rows.addEventListener("input", function(e){
        var el = e.target; if(!el || !el.classList || !el.closest) return;
        if(el.classList.contains("fpx-pct")){ var w = el.closest(".fpx-exwrap"); if(w) focusUpdatePctHint(w); }
      });
      rows.addEventListener("click", function(e){
        var t = e.target; if(!t || !t.closest) return;
        var madd = t.closest(".fpx-maddbtn");
        if(madd){ var wrap = madd.closest(".fpx-exwrap"); if(wrap){ focusAddMuscleRow(wrap); focusSaveExercises(focusCollectExercises()); } return; }
        var mdel = t.closest(".fpx-mdel");
        if(mdel){ var mrow = mdel.closest(".fpx-mrow"), wrapM = mdel.closest(".fpx-exwrap");
                  if(mrow && mrow.parentNode) mrow.parentNode.removeChild(mrow);
                  if(wrapM) focusUpdatePctHint(wrapM);
                  focusSaveExercises(focusCollectExercises()); return; }
        var del = t.closest(".fpx-del");
        if(del){ var row = del.closest(".fpx-exwrap"); if(row && row.parentNode) row.parentNode.removeChild(row);
                 focusSaveExercises(focusCollectExercises()); return; }   // backend 400s on an empty set → shown in fpop-err
      });
    }
    var addBtn = $("fpx-add"); if(addBtn) addBtn.addEventListener("click", focusAddExercise);
    var addName = $("fpx-add-name");
    if(addName) addName.addEventListener("keydown", function(e){ if(e.key === "Enter"){ e.preventDefault(); focusAddExercise(); } });
  }
  // Read every exercise block back into the {id?,name,emoji,weight,target,muscles,muscle,stat,reps?}
  // array the API takes. `muscles` is the SPEC [{muscle,pct}] list; the scalar `muscle` (= the
  // dominant row) is kept so a backend still validating the legacy field accepts the save during
  // the rollout. reps is preserved via data-reps (no longer a visible column) as emoji via data-emoji.
  function focusCollectExercises(){
    var host = $("fpop-exrows"); if(!host) return [];
    var rows = host.querySelectorAll(".fpx-exwrap"), out = [];
    for(var i=0;i<rows.length;i++){
      var row = rows[i];
      var nameEl = row.querySelector(".fpx-name"), tEl = row.querySelector(".fpx-target"),
          wEl = row.querySelector(".fpx-weight"), sEl = row.querySelector(".fpx-stat");
      var w = parseFloat(wEl && wEl.value), target = parseInt(tEl && tEl.value, 10);
      var muscles = focusCollectMuscles(row);
      var tgt = isNaN(target) ? 0 : target;
      // `reps` is REQUIRED by the backend (int 1..1000): preserve the row's data-reps when present,
      // else seed it from the visible target (backend seeds target=reps for a fresh exercise), else 10.
      var repsN = parseInt(row.getAttribute("data-reps"), 10);
      if(isNaN(repsN) || repsN < 1) repsN = (tgt >= 1 ? tgt : 10);
      var e = {name: (nameEl && nameEl.value != null ? String(nameEl.value) : "").trim(),
               weight: isNaN(w) ? 0 : w, target: tgt, reps: repsN,
               stat: (sEl && sEl.value) ? String(sEl.value) : "snaga",
               muscles: muscles,
               muscle: focusDominantMuscle(muscles),
               emoji: row.getAttribute("data-emoji") || ""};
      var id = row.getAttribute("data-id"); if(id) e.id = id;   // omit → backend slugs from name
      out.push(e);
    }
    return out;
  }
  // Read the {muscle,pct} sub-rows of one exercise block into a list (blank-muscle rows skipped).
  function focusCollectMuscles(exwrap){
    var mrows = exwrap.querySelectorAll(".fpx-mrow"), out = [];
    for(var i=0;i<mrows.length;i++){
      var mEl = mrows[i].querySelector(".fpx-muscle"), pEl = mrows[i].querySelector(".fpx-pct");
      var muscle = (mEl && mEl.value) ? String(mEl.value) : "";
      if(!muscle) continue;
      var p = parseFloat(pEl && pEl.value); if(isNaN(p) || p < 0) p = 0;
      out.push({muscle: muscle, pct: p});
    }
    return out;
  }
  function focusDominantMuscle(muscles){
    var best = "", bestP = -1;
    for(var i=0;i<muscles.length;i++){ if(muscles[i].pct > bestP){ bestP = muscles[i].pct; best = muscles[i].muscle; } }
    return best || (muscles[0] && muscles[0].muscle) || "grudi";
  }
  // Normalise an exercise's muscle mapping to a [{muscle,pct}] list, tolerating the legacy single
  // `muscle` string and a missing value (→ one default row) — we render whatever the view returns.
  function focusExMuscles(e){
    e = e || {};
    if(Array.isArray(e.muscles) && e.muscles.length){
      var out = [];
      for(var i=0;i<e.muscles.length;i++){
        var m = e.muscles[i] || {};
        out.push({muscle: (m.muscle != null ? String(m.muscle) : ""),
                  pct: (m.pct != null && !isNaN(+m.pct)) ? +m.pct : 0});
      }
      return out;
    }
    if(e.muscle != null && String(e.muscle)) return [{muscle: String(e.muscle), pct: 100}];
    return [{muscle: "", pct: 100}];
  }
  // One {muscle,pct} sub-row: muscle select + % input + remove ×.
  function focusMuscleRowHtml(muscle, pct){
    return '<div class="fpx-mrow">'
      +   '<select class="fpx-muscle" aria-label="mišićna grupa">' + focusOptions(FOCUS_MUSCLES, muscle) + '</select>'
      +   '<input class="fpx-pct" type="number" min="0" max="100" step="1" value="' + esc(pct) + '" aria-label="procenat">'
      +   '<span class="u">%</span>'
      +   '<button class="fpx-mdel" type="button" title="Ukloni mi&#353;i&#263;" aria-label="Ukloni mišić">&times;</button>'
      + '</div>';
  }
  // The muscle sub-panel for one exercise: the rows, an add-muscle (+) button, and a subtle sum hint
  // (only shown when the pcts don't ~sum to 100 — the hint never blocks saving).
  function focusExMusclesHtml(muscles){
    var rows = "";
    for(var i=0;i<muscles.length;i++) rows += focusMuscleRowHtml(muscles[i].muscle, muscles[i].pct);
    return '<div class="fpx-muscles">'
      +   '<div class="fpx-mrows">' + rows + '</div>'
      +   '<div class="fpx-mfoot">'
      +     '<button class="fpx-maddbtn" type="button" title="Dodaj mi&#353;i&#263;" aria-label="Dodaj mišić">&#43; mi&#353;i&#263;</button>'
      +     '<span class="fpx-pcthint" hidden></span>'
      +   '</div>'
      + '</div>';
  }
  // Append a {muscle,pct} sub-row, defaulting the pct to the remaining share to 100 (first muscle →
  // 100; adding to an already-full split → 0, so the sum hint appears). Picks a not-yet-used muscle.
  function focusAddMuscleRow(exwrap){
    var mrows = exwrap.querySelector(".fpx-mrows"); if(!mrows) return;
    var have = focusCollectMuscles(exwrap), sum = 0, usedSet = {};
    for(var i=0;i<have.length;i++){ sum += have[i].pct; usedSet[have[i].muscle] = true; }
    var rem = Math.max(0, 100 - Math.round(sum)), pick = FOCUS_MUSCLES[0][0];
    for(var j=0;j<FOCUS_MUSCLES.length;j++){ if(!usedSet[FOCUS_MUSCLES[j][0]]){ pick = FOCUS_MUSCLES[j][0]; break; } }
    mrows.insertAdjacentHTML("beforeend", focusMuscleRowHtml(pick, rem));
    focusUpdatePctHint(exwrap);
  }
  // Subtle sum hint for one exercise: shows "Σ N% (cilj 100)" only when out of a ±2 tolerance.
  function focusUpdatePctHint(exwrap){
    var hint = exwrap.querySelector(".fpx-pcthint"); if(!hint) return;
    var muscles = focusCollectMuscles(exwrap), sum = 0;
    for(var i=0;i<muscles.length;i++) sum += muscles[i].pct;
    sum = Math.round(sum);
    if(!muscles.length || Math.abs(sum - 100) <= 2){ hint.hidden = true; hint.textContent = ""; }
    else { hint.hidden = false; hint.innerHTML = "&#8721; " + sum + "% (cilj 100)"; }
  }
  function focusRenderExercises(v){
    var host = $("fpop-exrows"); if(!host) return;
    var exs = (v && v.config && v.config.exercises) || [], html = "";
    for(var i=0;i<exs.length;i++){
      var e = exs[i] || {};
      var id = e.id != null ? String(e.id) : "";
      var emoji = e.emoji != null ? String(e.emoji) : "";
      // The visible progression column is `target`; fall back to a legacy reps value, then 10.
      var target = e.target != null ? e.target : (e.reps != null ? e.reps : 10);
      var stat = e.stat != null ? String(e.stat) : "snaga";
      var reps = e.reps != null ? String(e.reps) : "";   // preserved, not shown
      html +=
          '<div class="fpx-exwrap" data-id="' + esc(id) + '" data-emoji="' + esc(emoji) + '" data-reps="' + esc(reps) + '">'
        +   '<div class="fpx-main">'
        +     '<input class="fpx-name" type="text" value="' + esc(e.name != null ? e.name : "") + '" aria-label="naziv vežbe">'
        +     '<input class="fpx-target" type="number" min="1" step="1" value="' + esc(target) + '" aria-label="ciljni broj ponavljanja">'
        +     '<input class="fpx-weight" type="number" min="0" step="0.05" value="' + esc(e.weight != null ? e.weight : 0) + '" aria-label="težina">'
        +     '<select class="fpx-stat" aria-label="atribut">' + focusOptions(FOCUS_STATS, stat) + '</select>'
        +     '<button class="fpx-del" type="button" title="Obri&#353;i ve&#382;bu" aria-label="Obriši vežbu">&times;</button>'
        +   '</div>'
        +   focusExMusclesHtml(focusExMuscles(e))
        + '</div>';
    }
    host.innerHTML = html;
    var wraps = host.querySelectorAll(".fpx-exwrap");
    for(var k=0;k<wraps.length;k++) focusUpdatePctHint(wraps[k]);   // recompute hints after (re)render
  }
  function focusSaveExercises(list){
    aiPost("/api/focus/config", {exercises:list}).then(function(v){
      if(!v || v.error){ return focusConfigErr(v && v.error ? esc(v.error) : "gre&#353;ka"); }
      var err = $("fpop-err"); if(err) err.textContent = "";
      focusApply(v);                 // pilula + cards
      focusRenderExercises(v);       // re-render rows from the server's normalised set
    });
  }
  function focusAddExercise(){
    var nameEl = $("fpx-add-name"), tEl = $("fpx-add-target"), wEl = $("fpx-add-weight"),
        mEl = $("fpx-add-muscle"), sEl = $("fpx-add-stat");
    var name = (nameEl && nameEl.value != null ? String(nameEl.value) : "").trim();
    if(!name){ if(nameEl) nameEl.focus(); return focusConfigErr("naziv ve&#382;be je obavezan"); }
    var w = parseFloat(wEl && wEl.value); if(isNaN(w) || w < 0) w = 1;
    var target = parseInt(tEl && tEl.value, 10); if(!(target >= 1)) target = 12;
    var muscle = (mEl && mEl.value) ? String(mEl.value) : "grudi";
    var stat = (sEl && sEl.value) ? String(sEl.value) : "snaga";
    var list = focusCollectExercises();
    // seed a single 100% muscle (backend auto-suggests a fuller split on add — we render whatever it
    // returns); reps is REQUIRED by the backend, seeded from target for a fresh exercise.
    list.push({name:name, weight:w, target:target, reps:target, stat:stat,
               muscles:[{muscle:muscle, pct:100}], muscle:muscle, emoji:""});
    if(nameEl) nameEl.value = ""; if(tEl) tEl.value = ""; if(wEl) wEl.value = "";
    if(mEl) mEl.selectedIndex = 0; if(sEl) sEl.value = "snaga";
    focusSaveExercises(list);
  }

  /* ---------- focus-activity pool (the focus-drop menu), split into TWO groups ----------
     SPEC: config.focus_activities=[{id,name,focus_boost(0-100),kind:'mandatory'|'random',
     daily_limit(mandatory),weight(random)}]. TWO clearly-labeled groups share ONE array:
       - "Obavezne (dnevni limit)" → name · focus_boost · daily_limit  (kind:'mandatory')
       - "Nasumične"               → name · focus_boost · weight       (kind:'random')
     Both groups POST the FULL merged focus_activities[] on any change and re-render from the
     returned view (same machinery as the exercise/custom menus). Mounts into the Fokus section,
     ABOVE the coffee controls (which stay in this section). */
  function focusEnsureFocusActivities(){
    if(!focusCfgHost()) return;
    if($("fpop-facts-wrap")) return;
    var sec = document.createElement("div");
    sec.className = "fpop-facts-wrap"; sec.id = "fpop-facts-wrap";
    sec.innerHTML =
        '<div class="fpop-exhead">Fokus-aktivnosti</div>'
      + '<div class="fset-hint">Obavezne se moraju ispuniti (kvota/dan); nasumi&#269;ne se izvla&#269;e na pad fokusa.</div>'
      + '<div class="fpop-fgroup">'
      +   '<div class="fpop-fglabel">Obavezne (dnevni limit) <span class="fpx-hint">naziv &middot; fokus+ &middot; limit/dan</span></div>'
      +   '<div class="fpop-facts" id="fpop-facts-mand"></div>'
      +   '<div class="fpop-exrow fpop-exadd">'
      +     '<input class="fpx-name" id="fpx-mand-name" type="text" placeholder="npr. Kafa" aria-label="naziv obavezne aktivnosti">'
      +     '<input class="fpx-boost" id="fpx-mand-boost" type="number" min="0" max="100" step="1" placeholder="10" aria-label="fokus boost">'
      +     '<input class="fpx-limit" id="fpx-mand-limit" type="number" min="1" step="1" placeholder="3" aria-label="dnevni limit">'
      +     '<button class="fpx-addbtn" id="fpx-mand-add" type="button" title="Dodaj obaveznu" aria-label="Dodaj obaveznu">&#43;</button>'
      +   '</div>'
      + '</div>'
      + '<div class="fpop-fgroup">'
      +   '<div class="fpop-fglabel">Nasumi&#269;ne <span class="fpx-hint">naziv &middot; fokus+ &middot; te&#382;.</span></div>'
      +   '<div class="fpop-facts" id="fpop-facts-rand"></div>'
      +   '<div class="fpop-exrow fpop-exadd">'
      +     '<input class="fpx-name" id="fpx-rand-name" type="text" placeholder="npr. &#352;ah 1min" aria-label="naziv nasumične aktivnosti">'
      +     '<input class="fpx-boost" id="fpx-rand-boost" type="number" min="0" max="100" step="1" placeholder="10" aria-label="fokus boost">'
      +     '<input class="fpx-weight" id="fpx-rand-weight" type="number" min="0" step="0.05" placeholder="1" aria-label="težina">'
      +     '<button class="fpx-addbtn" id="fpx-rand-add" type="button" title="Dodaj nasumičnu" aria-label="Dodaj nasumičnu">&#43;</button>'
      +   '</div>'
      + '</div>';
    focusSecHost("fset-fokus").appendChild(sec);
    focusWireActGroup($("fpop-facts-mand"));
    focusWireActGroup($("fpop-facts-rand"));
    var ma = $("fpx-mand-add"); if(ma) ma.addEventListener("click", function(){ focusAddFocusActivity("mandatory"); });
    var ra = $("fpx-rand-add"); if(ra) ra.addEventListener("click", function(){ focusAddFocusActivity("random"); });
    var mn = $("fpx-mand-name"); if(mn) mn.addEventListener("keydown", function(e){ if(e.key === "Enter"){ e.preventDefault(); focusAddFocusActivity("mandatory"); } });
    var rn = $("fpx-rand-name"); if(rn) rn.addEventListener("keydown", function(e){ if(e.key === "Enter"){ e.preventDefault(); focusAddFocusActivity("random"); } });
  }
  // Delegated change/delete on one activity group container. BOTH groups feed one merged POST, so a
  // change in either re-collects the full set (collect reads both containers).
  function focusWireActGroup(host){
    if(!host) return;
    host.addEventListener("change", function(e){
      var el = e.target; if(!el) return;
      if(el.tagName === "INPUT") focusSaveFocusActivities(focusCollectFocusActivities());
    });
    host.addEventListener("click", function(e){
      var del = e.target.closest && e.target.closest(".fpx-del"); if(!del) return;
      var row = del.closest(".fpop-exrow"); if(row && row.parentNode) row.parentNode.removeChild(row);
      focusSaveFocusActivities(focusCollectFocusActivities());
    });
  }
  // The full merged focus_activities[] = mandatory rows (with daily_limit) + random rows (with weight).
  function focusCollectFocusActivities(){
    var out = [];
    focusCollectActGroup($("fpop-facts-mand"), "mandatory", out);
    focusCollectActGroup($("fpop-facts-rand"), "random", out);
    return out;
  }
  function focusCollectActGroup(host, kind, out){
    if(!host) return;
    var rows = host.querySelectorAll(".fpop-exrow");
    for(var i=0;i<rows.length;i++){
      var row = rows[i];
      var nameEl = row.querySelector(".fpx-name"), bEl = row.querySelector(".fpx-boost");
      var b = parseFloat(bEl && bEl.value); if(isNaN(b) || b < 0) b = 0;
      var a = {name: (nameEl && nameEl.value != null ? String(nameEl.value) : "").trim(), kind: kind, focus_boost: b};
      if(kind === "mandatory"){
        var lEl = row.querySelector(".fpx-limit"); var l = parseInt(lEl && lEl.value, 10);
        a.daily_limit = (l >= 1) ? l : 1;
      } else {
        var wEl = row.querySelector(".fpx-weight"); var w = parseFloat(wEl && wEl.value);
        a.weight = (isNaN(w) || w < 0) ? 1 : w;
      }
      var id = row.getAttribute("data-id"); if(id) a.id = id;   // omit → backend slugs from name
      out.push(a);
    }
  }
  function focusRenderFocusActivities(v){
    var mand = $("fpop-facts-mand"), rand = $("fpop-facts-rand");
    if(!mand && !rand) return;
    var list = (v && v.config && v.config.focus_activities) || [], mh = "", rh = "";
    for(var i=0;i<list.length;i++){
      var a = list[i] || {};
      var id = a.id != null ? String(a.id) : "";
      var name = a.name != null ? a.name : "";
      var boost = (a.focus_boost != null && !isNaN(+a.focus_boost)) ? +a.focus_boost : 0;
      // legacy rows carry only name+weight (no kind) → render them in the random group
      if(a.kind === "mandatory"){
        var lim = (a.daily_limit != null && +a.daily_limit >= 1) ? +a.daily_limit : 1;
        mh += focusActRowHtml(id, name, boost, "fpx-limit", lim);
      } else {
        var wt = (a.weight != null && !isNaN(+a.weight)) ? +a.weight : 1;
        rh += focusActRowHtml(id, name, boost, "fpx-weight", wt);
      }
    }
    if(mand) mand.innerHTML = mh;
    if(rand) rand.innerHTML = rh;
  }
  // One activity row: name · focus_boost · (daily_limit|weight) · delete. `lastCls` selects the third
  // column — "fpx-limit" (mandatory, integer ≥1) or "fpx-weight" (random, 0.05-step).
  function focusActRowHtml(id, name, boost, lastCls, lastVal){
    var isLimit = (lastCls === "fpx-limit");
    return '<div class="fpop-exrow" data-id="' + esc(id) + '">'
      +   '<input class="fpx-name" type="text" value="' + esc(name) + '" aria-label="naziv aktivnosti">'
      +   '<input class="fpx-boost" type="number" min="0" max="100" step="1" value="' + esc(boost) + '" aria-label="fokus boost">'
      +   '<input class="' + lastCls + '" type="number" min="' + (isLimit ? "1" : "0") + '" step="' + (isLimit ? "1" : "0.05") + '" value="' + esc(lastVal) + '" aria-label="' + (isLimit ? "dnevni limit" : "težina") + '">'
      +   '<button class="fpx-del" type="button" title="Obri&#353;i" aria-label="Obri&#353;i">&times;</button>'
      + '</div>';
  }
  function focusSaveFocusActivities(list){
    aiPost("/api/focus/config", {focus_activities: list}).then(function(v){
      if(!v || v.error){ return focusConfigErr(v && v.error ? esc(v.error) : "gre&#353;ka"); }
      var err = $("fpop-err"); if(err) err.textContent = "";
      focusApply(v);
      focusRenderFocusActivities(v);   // re-render rows from the server's normalised set
    });
  }
  function focusAddFocusActivity(kind){
    var pfx = (kind === "mandatory") ? "fpx-mand" : "fpx-rand";
    var nameEl = $(pfx + "-name"), bEl = $(pfx + "-boost");
    var name = (nameEl && nameEl.value != null ? String(nameEl.value) : "").trim();
    if(!name){ if(nameEl) nameEl.focus(); return focusConfigErr("naziv aktivnosti je obavezan"); }
    var b = parseFloat(bEl && bEl.value); if(isNaN(b) || b < 0) b = 10;
    var list = focusCollectFocusActivities();
    var a = {name: name, kind: kind, focus_boost: b};
    if(kind === "mandatory"){
      var lEl = $("fpx-mand-limit"); var l = parseInt(lEl && lEl.value, 10); a.daily_limit = (l >= 1) ? l : 3;
      if(lEl) lEl.value = "";
    } else {
      var wEl = $("fpx-rand-weight"); var w = parseFloat(wEl && wEl.value); a.weight = (isNaN(w) || w < 0) ? 1 : w;
      if(wEl) wEl.value = "";
    }
    list.push(a);
    if(nameEl) nameEl.value = ""; if(bEl) bEl.value = "";
    focusSaveFocusActivities(list);
  }

  /* ---------- coffee row (productivity-driven, NOT interval) ----------
     Mounts into the Fokus section (below the activity pool); POSTs config
     {coffee:{enabled,low_score_threshold,cooldown_min}}. Coffee fires only when the focus score
     drops below the threshold — the hint says so. */
  function focusEnsureCoffeeRow(){
    if(!focusCfgHost() || $("fpop-coffee")) return;
    var sec = document.createElement("div");
    sec.className = "fpop-coffee"; sec.id = "fpop-coffee";
    sec.innerHTML =
        '<div class="fpop-exhead">&#9749; Kafa / energija <span class="fpx-hint">kad fokus padne</span></div>'
      + '<label class="fpop-row"><span>Uklju&#269;eno</span><input type="checkbox" id="fpop-coffee-en"></label>'
      + '<label class="fpop-row"><span>Prag fokusa</span><input type="number" min="0" max="100" step="1" id="fpop-coffee-thr"><span class="u">/100</span></label>'
      + '<label class="fpop-row"><span>Pauza izme&#273;u</span><input type="number" min="1" step="1" id="fpop-coffee-cd"><span class="u">min</span></label>'
      + focusSoundRowHtml("coffee")
      + '<div class="fpx-hint fpop-coffee-note">Ne javlja se na interval &mdash; samo kad fokus padne ispod praga.</div>';
    focusSecHost("fset-fokus").appendChild(sec);
    var en = $("fpop-coffee-en"); if(en) en.addEventListener("change", focusSaveCoffee);
    var thr = $("fpop-coffee-thr"); if(thr) thr.addEventListener("change", focusSaveCoffee);
    var cd = $("fpop-coffee-cd"); if(cd) cd.addEventListener("change", focusSaveCoffee);
  }
  function focusSaveCoffee(){
    var en = $("fpop-coffee-en"), thr = $("fpop-coffee-thr"), cd = $("fpop-coffee-cd");
    var t = parseInt(thr && thr.value, 10);
    if(!(t >= 0 && t <= 100)){ return focusConfigErr("prag fokusa mora biti 0&ndash;100"); }
    var c = parseInt(cd && cd.value, 10);
    if(!(c >= 1)){ return focusConfigErr("pauza mora biti &ge; 1 min"); }
    var coffee = {enabled: !!(en && en.checked), low_score_threshold: t, cooldown_min: c};
    aiPost("/api/focus/config", {coffee: coffee}).then(focusAfterConfig);
  }

  /* ---------- custom-reminder menu (below exercises) ----------
     name + interval rows with inline edit + delete + an add row. POSTs the FULL
     custom_reminders array; the backend replaces + re-normalises (ids), so we re-render
     rows from the returned view. Their due cards render as plain nudges (focusAddCustomCard). */
  function focusEnsureCustomMenu(){
    if(!focusCfgHost() || $("fpop-custom")) return;
    var sec = document.createElement("div");
    sec.className = "fpop-custom"; sec.id = "fpop-custom";
    sec.innerHTML =
        '<div class="fpop-exhead">Vlastiti podsetnici <span class="fpx-hint">naziv &middot; min</span></div>'
      + '<div class="fpop-customrows" id="fpop-customrows"></div>'
      + '<div class="fpop-exrow fpop-exadd">'
      +   '<input class="fpx-name" id="fpc-add-name" type="text" placeholder="novi podsetnik" aria-label="naziv podsetnika">'
      +   '<input class="fpx-int" id="fpc-add-int" type="number" min="1" step="1" placeholder="30" aria-label="interval u minutima">'
      +   '<span class="u">min</span>'
      +   '<button class="fpx-addbtn" id="fpc-add" type="button" title="Dodaj podsetnik" aria-label="Dodaj podsetnik">&#43;</button>'
      + '</div>';
    focusSecHost("fset-podsetnici").appendChild(sec);
    var rows = $("fpop-customrows");
    if(rows){
      rows.addEventListener("change", function(e){
        var el = e.target; if(!el) return;
        if(el.classList.contains("fpx-name") || el.classList.contains("fpx-int") || el.classList.contains("fpx-sound"))
          focusSaveCustom(focusCollectCustom());
      });
      rows.addEventListener("click", function(e){
        var del = e.target.closest && e.target.closest(".fpx-del"); if(!del) return;
        var row = del.closest(".fpop-exrow"); if(row && row.parentNode) row.parentNode.removeChild(row);
        focusSaveCustom(focusCollectCustom());
      });
    }
    var addBtn = $("fpc-add"); if(addBtn) addBtn.addEventListener("click", focusAddCustom);
    var addName = $("fpc-add-name");
    if(addName) addName.addEventListener("keydown", function(e){ if(e.key === "Enter"){ e.preventDefault(); focusAddCustom(); } });
  }
  function focusCollectCustom(){
    var host = $("fpop-customrows"); if(!host) return [];
    var rows = host.querySelectorAll(".fpop-exrow"), out = [];
    for(var i=0;i<rows.length;i++){
      var row = rows[i];
      var nameEl = row.querySelector(".fpx-name"), iEl = row.querySelector(".fpx-int"), sEl = row.querySelector(".fpx-sound");
      var iv = parseInt(iEl && iEl.value, 10);
      var c = {name: (nameEl && nameEl.value != null ? String(nameEl.value) : "").trim(),
               interval_min: isNaN(iv) ? 0 : iv,
               sound: sEl ? !!sEl.checked : true};
      var id = row.getAttribute("data-id"); if(id) c.id = id;   // omit → backend slugs from name
      out.push(c);
    }
    return out;
  }
  function focusRenderCustom(v){
    var host = $("fpop-customrows"); if(!host) return;
    var list = (v && v.config && v.config.custom_reminders) || [], html = "";
    for(var i=0;i<list.length;i++){
      var c = list[i] || {};
      var id = c.id != null ? String(c.id) : "";
      html +=
          '<div class="fpop-exrow" data-id="' + esc(id) + '">'
        +   '<input class="fpx-name" type="text" value="' + esc(c.name != null ? c.name : "") + '" aria-label="naziv podsetnika">'
        +   '<input class="fpx-int" type="number" min="1" step="1" value="' + esc(c.interval_min != null ? c.interval_min : 30) + '" aria-label="interval u minutima">'
        +   '<span class="u">min</span>'
        +   '<label class="fpx-soundlab" title="zvuk za ovaj podsetnik">&#128276;<input type="checkbox" class="fpx-sound"' + (c.sound !== false ? ' checked' : '') + '></label>'
        +   '<button class="fpx-del" type="button" title="Obri&#353;i" aria-label="Obri&#353;i">&times;</button>'
        + '</div>';
    }
    host.innerHTML = html;
  }
  function focusSaveCustom(list){
    aiPost("/api/focus/config", {custom_reminders: list}).then(function(v){
      if(!v || v.error){ return focusConfigErr(v && v.error ? esc(v.error) : "gre&#353;ka"); }
      var err = $("fpop-err"); if(err) err.textContent = "";
      focusApply(v);
      focusRenderCustom(v);          // re-render rows from the server's normalised set
    });
  }
  function focusAddCustom(){
    var nameEl = $("fpc-add-name"), iEl = $("fpc-add-int");
    var name = (nameEl && nameEl.value != null ? String(nameEl.value) : "").trim();
    if(!name){ if(nameEl) nameEl.focus(); return focusConfigErr("naziv podsetnika je obavezan"); }
    var iv = parseInt(iEl && iEl.value, 10); if(!(iv >= 1)) iv = 30;
    var list = focusCollectCustom();
    list.push({name: name, interval_min: iv, sound: true});
    if(nameEl) nameEl.value = ""; if(iEl) iEl.value = "";
    focusSaveCustom(list);
  }

  /* ---------- end-day report + day-ended modal ----------
     end-day returns the REPORT (not the view); it carries claude_sessions/commits the
     poll never sees. We keep the richest report in focusDayReport; on a plain reload the
     modal rebuilds from v.day (no claude_sessions/commits). Driven from focusApply, so the
     blinking modal reappears on EVERY poll while session.day_ended is true. */
  function focusReconcileDayEnd(v){
    var ended = !!(v.session && v.session.day_ended);
    if(!ended){ focusDayReport = null; focusHideDayEnd(); return; }
    focusShowDayEnd(focusDayReport || focusReportFromDay(v.day));
  }
  function focusReportFromDay(day){
    day = day || {};
    return {date: day.work_day, hours_worked: day.hours_worked, water: day.water,
            stretch: day.stretch, exercise_total: day.exercise_total,
            exercise_by_id: day.exercise_by_id, pauses: day.pauses,
            snoozes: day.snoozes, work_blocks: day.work_blocks};
  }
  function focusShowDayEnd(rep){
    var ov = $("focus-dayend"); if(!ov) return;
    focusRenderDayReport(rep);
    ov.hidden = false;
  }
  function focusHideDayEnd(){ var ov = $("focus-dayend"); if(ov) ov.hidden = true; }
  function focusStatRow(k, val){
    return '<div class="fde-stat"><span class="k">' + k + '</span><span class="val">' + val + '</span></div>';
  }
  function focusExName(id){
    var exs = (focusView && focusView.config && focusView.config.exercises) || [];
    for(var i=0;i<exs.length;i++){ if(exs[i] && exs[i].id === id) return exs[i].name; }
    return id;
  }
  function focusRenderDayReport(rep){
    rep = rep || {};
    var body = $("fde-body"), dt = $("fde-date");
    if(dt) dt.textContent = rep.date || "";
    if(!body) return;
    var html = "";
    html += focusStatRow("Sati rada", (+rep.hours_worked || 0).toFixed(1) + " h");
    html += focusStatRow("&#128167; Voda", (+rep.water || 0));
    html += focusStatRow("&#129336; Istezanje", (+rep.stretch || 0));
    html += focusStatRow("&#128170; Ve&#382;be (pon.)", (+rep.exercise_total || 0));
    var by = rep.exercise_by_id;
    if(by && typeof by === "object"){
      for(var id in by){ if(!by.hasOwnProperty(id)) continue;
        html += '<div class="fde-sub">&mdash; ' + esc(focusExName(id)) + ': ' + esc(by[id]) + '</div>';
      }
    }
    html += focusStatRow("Pauze", (+rep.pauses || 0));
    html += focusStatRow("Odlaganja", (+rep.snoozes || 0));
    if(rep.work_blocks != null)     html += focusStatRow("Radni blokovi", (+rep.work_blocks || 0));
    if(rep.claude_sessions != null) html += focusStatRow("&#129302; Claude sesije", (+rep.claude_sessions || 0));
    if(rep.commits != null)         html += focusStatRow("&#9095; Commit-ovi", (+rep.commits || 0));
    body.innerHTML = html;
  }
  function focusEndDay(){
    aiPost("/api/focus/end-day", {}).then(function(rep){
      if(rep && rep.error){ return focusConfigErr(esc(rep.error)); }
      focusDayReport = rep || null;   // richest report (has claude_sessions/commits)
      focusClosePopover();
      focusShowDayEnd(focusDayReport);
      focusPoll();                    // refresh the view → day_ended state everywhere
    });
  }
  function focusNewDay(){
    aiPost("/api/focus/new-day", {}).then(function(v){
      if(v && !v.error){ focusDayReport = null; focusApply(v); }   // resets + hides the modal
    });
  }

  /* ---------- non-blocking reminder cards ---------- */
  // Cards are keyed: a fixed type ("water"/"stretch"/"exercise"/"coffee") or a custom
  // reminder ("custom:<id>"). Every card carries ✓ / Odloži (snooze) / Preskoči.
  var focusFirstReconcile = true;   // suppress the chime on initial hydration (mail-style: only NEW)
  function focusReconcileCards(v){
    var active = (v.config && v.config.enabled !== false) && !(v.session && v.session.paused);
    if(!active){ focusClearCards(); return; }   // paused or disabled → no nags; clear any showing
    var rem = v.reminders || {}, chimeKeys = [];
    for(var i=0;i<FOCUS_TYPES.length;i++){
      var t = FOCUS_TYPES[i], r = rem[t];
      if(r && r.due && !focusCards[t]){ focusAddCard(t, v); chimeKeys.push(t); }
      // a card that's shown is dismissed only by the user (ack/snooze/skip), never yanked mid-read.
    }
    var custom = rem.custom || {};             // id -> {last_at,next_at,due}
    for(var cid in custom){
      if(!custom.hasOwnProperty(cid)) continue;
      var key = "custom:" + cid;
      if(custom[cid] && custom[cid].due && !focusCards[key]){ focusAddCustomCard(cid, v); chimeKeys.push(key); }
    }
    // Sound alert on a card's FIRST appearance — gated PER TYPE. Suppressed on the initial hydration
    // so a reload with already-due cards doesn't blast; a re-poll for an already-shown card can't
    // re-fire (the !focusCards guard). Each newly-shown card is checked against its own flag.
    if(!focusFirstReconcile){ for(var c=0;c<chimeKeys.length;c++) focusMaybeChime(chimeKeys[c], v); }
    focusFirstReconcile = false;
  }
  // Whether a reminder's alert should play: NOT muted globally (mail-mute) AND its per-type flag is
  // on. Fixed types read config.sound[type] (default true); a custom reminder reads its own `sound`
  // (default true). So water-off silences ONLY water while coffee still beeps.
  function focusSoundOn(typeKey, v){
    var cfg = (v && v.config) || {};
    if(typeKey.indexOf("custom:") === 0){
      var wanted = typeKey.slice(7), list = cfg.custom_reminders || [];
      for(var i=0;i<list.length;i++){ if(list[i] && String(list[i].id) === wanted) return list[i].sound !== false; }
      return true;
    }
    var snd = cfg.sound || {};
    return snd[typeKey] !== false;   // default true when unset
  }
  // Reuse mail.js's new-mail chime + its mute (both cross-file globals; guarded so a missing or
  // late-loaded mail.js never throws). Plays only when NOT globally muted AND the type's flag is on.
  function focusMaybeChime(typeKey, v){
    try{
      if(typeof mailSoundMuted !== "undefined" && mailSoundMuted) return;   // shared new-mail mute is on
      if(!focusSoundOn(typeKey, v)) return;                                 // this type's sound is off
      if(typeof mailChime === "function") mailChime();
    }catch(e){}
  }
  function focusClearCards(){ for(var k in focusCards){ if(focusCards.hasOwnProperty(k)) focusRemoveCard(k); } }
  function focusRemoveCard(key){ var el = focusCards[key]; if(el && el.parentNode) el.parentNode.removeChild(el); delete focusCards[key]; }
  // Fixed-type card (water/stretch/exercise/coffee).
  function focusAddCard(t, v){
    var host = $("focus-cards"), def = FOCUS_CARD_DEFS[t]; if(!host || !def) return;
    var title = def.title, input = "", swapType = null, quota = null;
    if(t === "exercise"){
      // The picked exercise the backend chose for THIS due (persisted so a poll
      // never re-rolls it); emoji + name are user-supplied → esc them. Fallback
      // covers the theoretical due-without-pick race. The reps input defaults to the
      // exercise's progression TARGET (SPEC), falling back to a legacy reps value, then 5.
      var picked = (v.reminders && v.reminders.exercise && v.reminders.exercise.exercise) || null;
      var reps = (picked && picked.target != null) ? picked.target
               : (picked && picked.reps != null) ? picked.reps : 5;
      var emoji = (picked && picked.emoji) ? String(picked.emoji) : "";
      var name = (picked && picked.name) ? String(picked.name) : "Vežba";
      title = (emoji ? esc(emoji) + " " : "") + esc(name) + ' <span class="fc-reps-hint">&times;' + esc(reps) + '</span>';
      input = '<input type="number" class="fc-reps" min="1" step="1" value="' + esc(reps) + '" aria-label="broj ponavljanja">';
      swapType = "exercise";
    } else if(t === "coffee"){
      // Show the picked focus-drop activity when the backend supplies one
      // (reminders.coffee.activity = {id,name,kind,focus_boost}).
      var act = (v.reminders && v.reminders.coffee && v.reminders.coffee.activity) || null;
      if(act && act.name) title = '&#9749; Fokus opada &mdash; ' + esc(act.name) + '?';
      // Zameni re-rolls a RANDOM pick; a mandatory activity is fixed (its daily quota must be met),
      // so it gets no swap — but it DOES show its remaining quota on the card.
      if(act && act.kind === "mandatory"){ swapType = null; quota = focusActQuota(v, act); }
      else { swapType = "coffee"; }
    }
    focusBuildCard(t, t, null, title, def.ok, input, host, swapType, quota);
  }
  // Remaining-quota label for a mandatory Fokus activity, read defensively from whatever the view
  // exposes. The REAL backend puts per-mandatory quota in a top-level `mandatory` list keyed by id
  // ({daily_limit,done_today,remaining}); we also accept the numbers inlined on the activity or on
  // reminders.coffee, so the chip survives either shape. Returns e.g. "1/3 danas".
  function focusActQuota(v, act){
    var coffee = (v && v.reminders && v.reminders.coffee) || {};
    var done = focusFirstNum([act && act.done_today, coffee.done_today, act && act.count_today]);
    var limit = focusFirstNum([act && act.daily_limit, coffee.daily_limit]);
    if((limit == null || done == null) && act && act.id != null && v && Array.isArray(v.mandatory)){
      for(var i=0;i<v.mandatory.length;i++){
        var mq = v.mandatory[i];
        if(mq && String(mq.id) === String(act.id)){
          if(limit == null) limit = focusFirstNum([mq.daily_limit]);
          if(done == null) done = focusFirstNum([mq.done_today]);
          break;
        }
      }
    }
    if(limit == null || limit < 1) return null;
    if(done == null) done = 0;
    return done + "/" + limit + " danas";
  }
  function focusFirstNum(cands){
    for(var i=0;i<cands.length;i++){ var n = cands[i]; if(n != null && !isNaN(+n)) return +n; }
    return null;
  }
  // Custom reminder card — a plain nudge (name + ✓/Odloži/Preskoči); ack {type:'custom',id}.
  function focusAddCustomCard(cid, v){
    var host = $("focus-cards"); if(!host) return;
    var name = focusCustomName(v, cid) || "Podsetnik";
    focusBuildCard("custom:" + cid, "custom", cid, esc(name), "Ok &#10003;", "", host);
  }
  // One builder for both kinds. `type` is the POST body type; `cid` set only for custom;
  // `key` is the focusCards handle; `quota` (coffee/mandatory only) is a remaining-quota chip.
  function focusBuildCard(key, type, cid, title, okLabel, input, host, swapType, quota){
    var card = document.createElement("div");
    card.className = "focus-card fc-" + (cid ? "custom" : type);
    card.setAttribute("role", "alert");
    // Zameni (swap) re-rolls the exercise / focus-activity pick without dismissing the card.
    var swap = swapType ? '<button class="fc-swap" type="button" title="Zameni izbor" aria-label="Zameni">&#128260; Zameni</button>' : "";
    var quotaHtml = quota ? '<span class="fc-quota">' + esc(quota) + '</span>' : "";
    // "U fokusu sam" (coffee/Fokus only) = I'm still focused, defer this nudge (not ack, not skip).
    var infocus = (type === "coffee") ? '<button class="fc-infocus" type="button" title="Jo&#353; sam fokusiran" aria-label="U fokusu sam">&#127919; U fokusu sam</button>' : "";
    card.innerHTML =
        '<div class="fc-body"><span class="fc-title">' + title + '</span>' + quotaHtml + input + swap + '</div>'
      + '<div class="fc-acts">'
      +   infocus
      +   '<button class="fc-ok" type="button">'     + okLabel + '</button>'
      +   '<button class="fc-snooze" type="button">Odlo&#382;i</button>'
      +   '<button class="fc-skip" type="button">Presko&#269;i</button>'
      + '</div>';
    card.querySelector(".fc-ok").addEventListener("click",     function(){ focusAct("ack",    type, cid, key, card); });
    card.querySelector(".fc-snooze").addEventListener("click", function(){ focusAct("snooze", type, cid, key, card); });
    card.querySelector(".fc-skip").addEventListener("click",   function(){ focusAct("skip",   type, cid, key, card); });
    if(swapType){ var sw = card.querySelector(".fc-swap"); if(sw) sw.addEventListener("click", function(){ focusSwap(swapType, key, card); }); }
    var inf = card.querySelector(".fc-infocus"); if(inf) inf.addEventListener("click", function(){ focusInFocus(key, card); });
    host.appendChild(card);
    focusCards[key] = card;
  }
  // "U fokusu sam" — tell the server I'm still focused so it DEFERS the coffee/Fokus nudge (not an
  // ack, not a skip). POST /api/focus/infocus {type:"coffee"}; the reminder is no longer due in the
  // returned view, so remove the card and re-apply — same shape as focusSwap / focusAct.
  function focusInFocus(key, card){
    focusCardBusy(card, true);
    aiPost("/api/focus/infocus", {type: "coffee"}).then(function(v){
      if(v && !v.error){ focusRemoveCard(key); focusApply(v); }
      else focusCardBusy(card, false);
    });
  }
  // Re-roll the picked exercise / focus-activity. POST /api/focus/swap {type}; the reminder stays
  // due, so removing the card then re-applying lets focusReconcileCards rebuild it with the NEW
  // pick — reusing the card builder rather than patching the DOM in place.
  function focusSwap(type, key, card){
    focusCardBusy(card, true);
    aiPost("/api/focus/swap", {type: type}).then(function(v){
      if(v && !v.error){ focusRemoveCard(key); focusApply(v); }
      else focusCardBusy(card, false);
    });
  }
  // The custom reminder's display name lives in config (the slot carries only timers).
  function focusCustomName(v, cid){
    var list = (v && v.config && v.config.custom_reminders) || [];
    for(var i=0;i<list.length;i++){ if(list[i] && list[i].id === cid) return list[i].name; }
    return null;
  }
  // Single funnel for ack / snooze / skip on any card — builds {type(,id)(,value)}, POSTs,
  // and re-renders from the returned view (which carries the moved next_at / bumped counters).
  function focusAct(action, type, cid, key, card){
    var body = {type: type};
    if(type === "custom") body.id = cid;
    if(action === "ack" && type === "exercise"){
      var inp = card.querySelector(".fc-reps"); var n = parseInt(inp && inp.value, 10);
      if(n >= 1) body.value = n;
    }
    focusCardBusy(card, true);
    aiPost("/api/focus/" + action, body).then(function(v){
      if(v && !v.error){ focusRemoveCard(key); focusApply(v); }
      else focusCardBusy(card, false);
    });
  }
  function focusCardBusy(card, b){
    if(!card) return;
    card.classList.toggle("busy", b);
    var els = card.querySelectorAll("button,input");
    for(var i=0;i<els.length;i++) els[i].disabled = b;
  }

  /* ---------- settings MODAL — full-viewport overlay hosting #focus-settings ----------
     Opened from the Profil header ⚙ button and the pilula stats-dropdown Podešavanja. The dialog
     surface uses CHROME tokens (--panel/--line) because the config rows inside use chrome tokens
     that flip with the theme — a dialog on --panelbg would be dark text on a dark panel in light
     mode. Close: ×, Esc, backdrop-click. */
  function focusOpenModal(){
    var m = $("focus-smodal"); if(!m) return;
    focusBuildSettings();                    // idempotent build + populate from focusView
    m.hidden = false; focusModalOpen = true;
    focusPoll();                             // pull the freshest config while it is open
  }
  function focusCloseModal(){
    var m = $("focus-smodal"); if(!m) return;
    m.hidden = true; focusModalOpen = false;
  }

  /* ---------- init (called once from boot.js, like loadServerInfo) ---------- */
  function focusInit(){
    var pill = $("focus-pill"); if(!pill) return;    // markup absent → no-op, never throw
    pill.addEventListener("click", function(e){
      if(e.target.closest && e.target.closest("#fp-pause")) return;   // the pause button handles itself
      focusTogglePopover();
    });
    pill.addEventListener("keydown", function(e){
      if(e.key === "Enter" || e.key === " "){ e.preventDefault(); focusTogglePopover(); }
    });
    var pb = $("fp-pause"); if(pb) pb.addEventListener("click", function(e){ e.stopPropagation(); focusTogglePause(); });

    var pop = $("focus-pop");
    if(pop){
      // The popover is the STATS dropdown — its only actions are Kraj dana (end-day) and
      // Podešavanja, which now opens the settings MODAL (every config control lives there).
      var ed = $("fpop-endday"); if(ed) ed.addEventListener("click", focusEndDay);
      var settingsBtn = $("fpop-settings");
      if(settingsBtn) settingsBtn.addEventListener("click", function(){ focusClosePopover(); focusOpenModal(); });
      pop.addEventListener("click", function(e){ e.stopPropagation(); });   // clicks inside never close it
    }
    // Settings modal: × / backdrop close (Esc is handled in the shared keydown below). Building the
    // host now (idempotent) means a config change persists even before the modal is first opened.
    var sm = $("focus-smodal");
    if(sm){
      sm.addEventListener("click", function(e){ if(e.target === sm) focusCloseModal(); });   // backdrop only
      var smx = $("fsm-x"); if(smx) smx.addEventListener("click", focusCloseModal);
    }
    focusBuildSettings();

    // Pause overlay — button OR backdrop resumes. The button stops propagation only so
    // the two handlers don't both fire; both do the same thing, so it is harmless either way.
    var resume = $("focus-resume"); if(resume) resume.addEventListener("click", function(e){ e.stopPropagation(); focusResume(); });
    var pauseOv = $("focus-pause-ov"); if(pauseOv) pauseOv.addEventListener("click", focusResume);
    // Day-ended modal — Novi dan resets; Ugasi server reuses the top-bar #stopsrv behavior.
    var nd = $("focus-newday"); if(nd) nd.addEventListener("click", focusNewDay);
    var sd = $("focus-shutdown"); if(sd) sd.addEventListener("click", function(){ var s = $("stopsrv"); if(s) s.click(); });

    document.addEventListener("click", function(e){
      if(!focusPopoverOpen) return;
      if(e.target.closest && (e.target.closest("#focus-pop") || e.target.closest("#focus-pill"))) return;
      focusClosePopover();
    });
    document.addEventListener("keydown", function(e){
      if(e.key !== "Escape") return;
      if(focusModalOpen) focusCloseModal();          // modal takes priority over the dropdown
      else if(focusPopoverOpen) focusClosePopover();
    });
    window.addEventListener("resize", function(){ if(focusPopoverOpen) focusPositionPopover(); });

    focusPoll();                             // once on init
    setInterval(focusPoll, FOCUS_POLL_MS);   // ~15s poll
    setInterval(focusTick, 1000);            // 1s local elapsed ticker (no network)
  }
