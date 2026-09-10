  "use strict";
  /* ================= Claude plan limit, inside the timer pilula's popover =================
     The number Claude Code's /usage shows (5-hour session window, 7-day window), read by
     GET /api/claude/usage -> {state, windows:{five_hour:{percent,resets_at}, seven_day:{...}},
     problem}. It has NO pill of its own in the top bar (the bar is the timer's): it lives as
     a section of #focus-pop, which opens on CLICK (focus.js) and now also on HOVER over the
     pilula -- a hover-opened popover closes when the pointer leaves it, a clicked one stays.
     Rendered as "koliko do reseta", never as "5h": the window's length is not what the
     operator decides by; the minutes left are. Re-fetched at most once a minute, only while
     the popover is open; the countdown ticks every 10 s. Reuses core.js globals: $.
     Pure helpers (claudeFmtLeft, claudeLevel) are exported for node tests; the DOM wiring
     runs only in a browser. */
  var claudeUsage = null, claudeUsageAt = 0, claudeTick = null;
  var claudeHoverT = null, claudeHoverOpened = false;
  var CLAUDE_REFRESH_MS = 60000, CLAUDE_TICK_MS = 10000;

  /* "reset za 2 h 41 min" from an ISO time; "" when unknown; "reset upravo sad" when passed. */
  function claudeFmtLeft(iso, nowMs){
    if(!iso) return "";
    var t = new Date(iso).getTime(); if(isNaN(t)) return "";
    var ms = t - (nowMs != null ? nowMs : Date.now());
    if(ms <= 0) return "reset upravo sad";
    var min = Math.ceil(ms / 60000), h = Math.floor(min / 60), m = min % 60;
    if(h >= 48) return "reset za " + Math.round(h / 24) + " d";
    return "reset za " + (h ? h + " h " : "") + m + " min";
  }
  /* "" | "warn" | "bad" -- the same thresholds the status line and health.py use. */
  function claudeLevel(pct){ return pct >= 95 ? "bad" : pct >= 80 ? "warn" : ""; }
  function claudeHHMM(iso){
    if(!iso) return ""; var d = new Date(iso); if(isNaN(d.getTime())) return "";
    var today = new Date(); var same = d.toDateString() === today.toDateString();
    var hm = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    return same ? hm : d.toLocaleDateString(undefined, {weekday:"short"}) + " " + hm;
  }

  /* "sad" / "pre 3 min" / "pre 2 h" from an age in seconds. */
  function claudeAge(s){
    if(s == null || isNaN(s)) return "";
    var m = Math.floor(s / 60);
    return m < 1 ? "sad" : m < 60 ? "pre " + m + " min" : "pre " + Math.floor(m / 60) + " h";
  }
  /* "2 h 5 min" from milliseconds; "" for nothing. */
  function claudeDur(ms){
    if(!ms || ms < 60000) return "";
    var m = Math.floor(ms / 60000), h = Math.floor(m / 60);
    return (h ? h + " h " : "") + (m % 60) + " min";
  }
  function claudeCtxSize(n){
    if(!n) return "";
    return n >= 1e6 ? Math.round(n / 1e6) + "M" : Math.round(n / 1e3) + "k";
  }
  function claudeSessRow(s){
    var known = (s.ctx_pct != null);
    var ctx = known ? Math.max(0, Math.min(100, Math.round(s.ctx_pct))) : 0, lvl = known ? claudeLevel(ctx) : "";
    var name = String(s.cwd || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "?";
    var size = claudeCtxSize(s.ctx_size);
    var meta = [];
    if(s.cost_usd != null) meta.push("$" + Number(s.cost_usd).toFixed(2));
    if(s.lines_added != null || s.lines_removed != null) meta.push("+" + (s.lines_added || 0) + " / \u2212" + (s.lines_removed || 0));
    if(claudeDur(s.duration_ms)) meta.push(claudeDur(s.duration_ms));
    if(claudeAge(s.age_s)) meta.push(claudeAge(s.age_s));
    var E = (typeof esc === "function") ? esc : function(x){ return String(x); };
    return '<div class="fpc-row ' + lvl + '" title="' + E(s.cwd || "") + (s.version ? " \u00b7 Claude Code " + E(s.version) : "") + '">'
         +   '<span class="fpc-lab">' + E(name) + '<small>' + E(s.model || "?")
         +     (s.effort ? " \u00b7 " + E(s.effort) : "") + (s.entrypoint ? " \u00b7 " + E(String(s.entrypoint).replace(/^claude-/, "")) : "")
         +     (size ? " \u00b7 ctx " + size : "") + (s.over_200k && !size ? " \u00b7 >200k" : "") + '</small></span>'
         +   '<span class="fpc-bar"><span class="fpc-fill" style="width:' + ctx + '%"></span></span>'
         +   '<span class="fpc-pct">' + (known ? ctx + "%" : (s.ctx_tokens ? claudeCtxSize(s.ctx_tokens) : "\u2014")) + '</span>'
         +   '<span class="fpc-left">' + E(meta.join(" \u00b7 ")) + '</span>'
         + '</div>';
  }


  /* ---- What to offer when the window is nearly spent -------------------
     Both commands SPEND THE WEEKLY WINDOW. That is the whole reason this is a
     decision and not a pair of buttons: offering them while the weekly window
     is itself nearly gone would be advice that makes the situation worse. So
     the rule is a table, not a threshold:

       5h high, weekly has room   -> offer both
       5h high, weekly ALSO high  -> offer neither, say why
       weekly high alone          -> nothing helps but waiting

     `/limit-reset` is offered only when the endpoint says this account has it
     (`juniper_tide`); it is once a week and only works once you are actually
     at the wall. `/low-priority` cannot be set from here at all -- it is a
     mode inside the running client and there is no channel into it -- so it is
     copied for pasting, never presented as a button that does it. */
  var CLAUDE_NEAR = 80, CLAUDE_WEEKLY_TIGHT = 60;

  function claudeAdvice(w, limitResetOffered){
    var five = w && w.five_hour ? w.five_hour.percent : 0;
    var week = w && w.seven_day ? w.seven_day.percent : 0;
    if(week >= CLAUDE_NEAR && five < CLAUDE_NEAR){
      return {level:"bad", why:"Nedeljni prozor je pri kraju. Nijedna komanda ne pomaže — obe troše baš njega.", offer:[]};
    }
    if(five < CLAUDE_NEAR) return {level:"", why:"", offer:[]};
    if(week >= CLAUDE_WEEKLY_TIGHT){
      return {level:"bad",
              why:"Sesijski limit je pri kraju, ali je i nedeljni na " + week + "%. Obe komande troše nedeljni — sada bi odmogle.",
              offer:[]};
    }
    var offer = ["low-priority"];
    if(limitResetOffered) offer.unshift("limit-reset");
    return {level:"warn",
            why:"Sesijski limit je pri kraju, nedeljni ima prostora (" + week + "%).",
            offer:offer};
  }

  function claudeActions(d){
    var w = d.windows || {}, lr = (d.limit_reset || {}).offered;
    var a = claudeAdvice(w, lr);
    if(!a.why) return "";
    var out = '<div class="fpc-act ' + a.level + '"><div class="fpc-actwhy">' + a.why + '</div>';
    if(a.offer.indexOf("limit-reset") >= 0){
      out += '<button class="fpc-btn" id="fpc-reset" type="button" '
           + 'title="Otvara novu Claude sesiju koja pokreće /limit-reset. Jednom nedeljno, troši nedeljni limit.">'
           + '/limit-reset · briše sesijski limit</button>';
    }
    out += '<button class="fpc-btn ghost" id="fpc-slow" type="button" '
         + 'title="Ne može da se uključi odavde — /low-priority je režim unutar pokrenutog klijenta. Kopira se da ga nalepiš.">'
         + '/low-priority · kopiraj</button>';
    if(!lr && a.offer.length){
      out += '<div class="fpc-note">/limit-reset nije ponuđen na ovom nalogu (nema `juniper_tide` u odgovoru).</div>';
    }
    return out + "</div>";
  }

  /* The two handlers. `/limit-reset` goes through the SAME launch route the
     ticket actions use -- a new interactive session runs the client's own
     command, so nothing here guesses at an undocumented request body. */
  function claudeWireActions(){
    var slow = document.getElementById("fpc-slow");
    if(slow) slow.addEventListener("click", function(e){
      e.stopPropagation();
      var t = "/low-priority";
      if(navigator.clipboard) navigator.clipboard.writeText(t);
      slow.textContent = "kopirano — nalepi u sesiju";
      setTimeout(function(){ slow.textContent = "/low-priority · kopiraj"; }, 2500);
    });
    var rst = document.getElementById("fpc-reset");
    if(rst) rst.addEventListener("click", function(e){
      e.stopPropagation();
      // Two steps, like every other irreversible button here: it is once a
      // week and it spends the weekly window.
      if(rst.dataset.armed !== "1"){
        rst.dataset.armed = "1";
        rst.textContent = "Sigurno? Troši nedeljni limit, 1×/nedeljno";
        setTimeout(function(){
          if(rst.dataset.armed !== "1") return;
          rst.dataset.armed = "0";
          rst.textContent = "/limit-reset · briše sesijski limit";
        }, 4000);
        return;
      }
      rst.dataset.armed = "0";
      rst.textContent = "pokrećem…";
      fetch("/api/claude/launch", {method:"POST", headers:{"Content-Type":"application/json"},
                                   body: JSON.stringify({prompt:"/limit-reset"})})
        .then(function(r){ return r.json(); })
        .then(function(x){ rst.textContent = x && x.error ? ("greška: " + x.error)
                                                          : "otvorena sesija — potvrdi tamo"; })
        .catch(function(){ rst.textContent = "nije pokrenuto"; });
    });
  }

  function claudeFetch(cb){
    fetch("/api/claude/usage").then(function(r){ return r.json(); }).then(function(d){
      claudeUsage = d || {}; claudeUsageAt = Date.now(); if(cb) cb();
    }).catch(function(){ claudeUsage = {state:"error", windows:{}}; claudeUsageAt = Date.now(); if(cb) cb(); });
  }

  function claudeRow(label, sub, w){
    if(!w) return "";
    var pct = Math.max(0, Math.min(100, Math.round(w.percent || 0))), lvl = claudeLevel(pct);
    var left = claudeFmtLeft(w.resets_at), at = claudeHHMM(w.resets_at);
    return '<div class="fpc-row ' + lvl + '" title="' + (at ? "reset u " + at : "") + '">'
         +   '<span class="fpc-lab">' + label + '<small>' + sub + '</small></span>'
         +   '<span class="fpc-bar"><span class="fpc-fill" style="width:' + pct + '%"></span></span>'
         +   '<span class="fpc-pct">' + pct + '%</span>'
         +   '<span class="fpc-left">' + (left || "reset: nepoznat") + (at ? " &middot; " + at : "") + '</span>'
         + '</div>';
  }

  function claudeRender(){
    var box = $("fpop-claude"); if(!box) return;
    var d = claudeUsage || {}, w = d.windows || {};
    var head = '<div class="fpc-head"><span>&#129302; Claude plan</span><span class="fpc-src">isto &#353;to i /usage</span></div>';
    if(!w.five_hour && !w.seven_day){
      var msg = d.state === "needs_auth" ? "nema tokena &mdash; pokreni <code>claude</code> jednom"
              : d.state === "backoff"    ? "429 &mdash; &#269;ekam da pro&#273;e"
              : claudeUsage == null       ? "u&#269;itavam&hellip;"
              : "nema podatka";
      box.innerHTML = head + '<div class="fpc-note' + (d.state === "needs_auth" ? " bad" : "") + '">' + msg + '</div>';
      box.hidden = false; return;
    }
    var note = "";
    var ageMin = Math.floor((Date.now() - claudeUsageAt) / 60000);
    if(d.state === "stale") note = "podatak star " + Math.max(ageMin, 1) + " min";
    else if(d.state === "backoff") note = "429 &mdash; prikazujem poslednji broj";
    else if(d.problem) note = String(d.problem);
    var ss = d.sessions || [];
    box.innerHTML = head
      + claudeRow("Sesija", "5-satni prozor", w.five_hour)
      + claudeRow("Nedelja", "7-dnevni prozor", w.seven_day)
      + (note ? '<div class="fpc-note">' + note + '</div>' : "")
      + claudeActions(d)
      + (ss.length ? '<div class="fpc-sub">Sesije &middot; kontekst po sesiji</div>' + ss.slice(0, 6).map(claudeSessRow).join("") : "");
    box.hidden = false;
    claudeWireActions();
  }

  /* While the popover is open: refresh at most once a minute, tick the countdown every 10 s. */
  function claudeOnOpen(){
    claudeRender();
    if(Date.now() - claudeUsageAt > CLAUDE_REFRESH_MS) claudeFetch(claudeRender);
    if(claudeTick) clearInterval(claudeTick);
    claudeTick = setInterval(function(){
      var pop = $("focus-pop");
      if(!pop || !pop.classList.contains("open")){ clearInterval(claudeTick); claudeTick = null; return; }
      if(Date.now() - claudeUsageAt > CLAUDE_REFRESH_MS) claudeFetch(claudeRender); else claudeRender();
    }, CLAUDE_TICK_MS);
  }

  function claudeUsageInit(){
    var pill = $("focus-pill"), pop = $("focus-pop"); if(!pill || !pop) return;
    // Open -> render. Watching the class is what keeps focus.js untouched.
    new MutationObserver(function(){ if(pop.classList.contains("open")) claudeOnOpen(); })
      .observe(pop, {attributes:true, attributeFilter:["class"]});
    // Hover opens after a short dwell; leaving both the pilula and the popover closes a
    // hover-opened one. A click on a hover-opened popover PINS it: focus.js's toggle would
    // close it, so the click is swallowed here first (this listener registers before focusInit).
    pill.addEventListener("mouseenter", function(){
      clearTimeout(claudeHoverT);
      claudeHoverT = setTimeout(function(){
        if(typeof focusPopoverOpen !== "undefined" && !focusPopoverOpen && typeof focusOpenPopover === "function"){
          focusOpenPopover(); claudeHoverOpened = pop.classList.contains("open");
        }
      }, 220);
    });
    function leave(){
      clearTimeout(claudeHoverT);
      claudeHoverT = setTimeout(function(){
        if(claudeHoverOpened && pop.classList.contains("open") && !pop.matches(":hover") && !pill.matches(":hover")
           && typeof focusClosePopover === "function"){ focusClosePopover(); claudeHoverOpened = false; }
      }, 320);
    }
    pill.addEventListener("mouseleave", leave);
    pop.addEventListener("mouseleave", leave);
    pop.addEventListener("mouseenter", function(){ clearTimeout(claudeHoverT); });
    pill.addEventListener("click", function(e){
      if(e.target.closest && e.target.closest("#fp-pause")) return;
      if(claudeHoverOpened && pop.classList.contains("open")){ claudeHoverOpened = false; e.stopImmediatePropagation(); }
    });
  }

  if(typeof document !== "undefined" && typeof $ === "function"){
    claudeUsageInit();
  }
  if(typeof module !== "undefined" && module.exports){
    module.exports = {claudeFmtLeft: claudeFmtLeft, claudeLevel: claudeLevel,
                      claudeAge: claudeAge, claudeDur: claudeDur, claudeCtxSize: claudeCtxSize,
                      claudeAdvice: claudeAdvice, CLAUDE_NEAR: CLAUDE_NEAR,
                      CLAUDE_WEEKLY_TIGHT: CLAUDE_WEEKLY_TIGHT};
  }
