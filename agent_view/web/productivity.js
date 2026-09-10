  "use strict";
  /* ================= PRODUKTIVNOST — a sub-page of the Profil tab =================
     Opened from the Profil header button "📊 Produktivnost" (profile.js swaps #view-profile's
     main content to #pf-prod-view). Reads GET /api/focus/productivity (a plain LAN GET like the
     rest of /api/focus/*). Renders, over the recorded days (optionally a ?from=&to= range):
       - a headline that keeps WALL-CLOCK hours_worked as the primary work time, with the
         parallel-work numbers (session_hours / parallelism / max_concurrency) in a SEPARATE
         "koliko paralelno" indicator that visibly does NOT inflate the hours;
       - an aggregates band (averages incl. adherence, deep-work, commits/h, cache, tokens/h);
       - category highlights ("najbolji dan" chip per metric, from the `highlights` map);
       - a compact multi-series SVG history chart;
       - a per-day history table (newest first), each row CLICKABLE → a drill-down modal that
         GETs ?date=YYYY-MM-DD and shows the day's aggregated numbers + each raw session.
     CONTRACT NOTE: a null inside any per-day `productivity.*` means "not available" — it renders
     "—", NEVER 0 (0 would be a lie: e.g. cache_hit_ratio null = no tokens seen, not a 0% ratio).
     Reuses core.js globals: $ , esc , fmtTok , hudTipDelegate/hudTipAttr (shared tooltip). */

  var prodvView = null;      // latest productivity object from the server
  var prodvInited = false;   // one-time preload guard (init runs on every sub-page open)
  var prodvFrom = "", prodvTo = "";   // active ?from=/?to= filter ("" = unfiltered)

  // Metric descriptors: display label, glyph (HTML entity, charset-agnostic) and the number KIND
  // that picks a formatter. Covers both the raw aggregated fields and the derived productivity.* keys.
  var PV_METRICS = {
    hours_worked:         {label:"Sati rada",        g:"&#9201;",   kind:"h"},      // ⏱
    session_hours:        {label:"Sesijski sati",    g:"&#129525;", kind:"h"},      // 🧵
    parallelism:          {label:"Paralelizam",      g:"&#129525;", kind:"x"},      // 🧵
    max_concurrency:      {label:"Maks. paralelno",  g:"&#8649;",   kind:"num1"},   // ⇉
    avg_concurrency:      {label:"Pros. paralelno",  g:"&#8649;",   kind:"num1"},   // ⇉
    commits:              {label:"Commitovi",        g:"&#9095;",   kind:"int"},    // ⎇
    commits_per_hour:     {label:"Commit / h",       g:"&#9095;",   kind:"rate"},   // ⎇
    deep_work_ratio:      {label:"Deep-work",        g:"&#127919;", kind:"pct"},    // 🎯
    avg_session_length_h: {label:"Du&#382;. bloka",  g:"&#129521;", kind:"h"},      // 🧱
    break_adherence:      {label:"Pauze (cilj)",     g:"&#9208;",   kind:"pct"},    // ⏸
    water_adherence:      {label:"Voda (cilj)",      g:"&#128167;", kind:"pct"},    // 💧
    stretch_adherence:    {label:"Istezanje (cilj)", g:"&#129336;", kind:"pct"},    // 🤸
    movement_adherence:   {label:"Kretanje (cilj)",  g:"&#128170;", kind:"pct"},    // 💪
    cache_hit_ratio:      {label:"Cache hit",        g:"&#9889;",   kind:"pct"},    // ⚡
    output_per_hour:      {label:"Tokeni / h",       g:"&#128228;", kind:"tokens"}, // 📤
    output_tokens:        {label:"Output tokeni",    g:"&#128228;", kind:"tokens"}, // 📤
    water:                {label:"Voda",             g:"&#128167;", kind:"int"},    // 💧
    stretch:              {label:"Istezanje",        g:"&#129336;", kind:"int"},    // 🤸
    exercise_total:       {label:"Ve&#382;be (pon.)",g:"&#128170;", kind:"int"},    // 💪
    exercise_sets:        {label:"Ve&#382;be (serije)",g:"&#127947;",kind:"int"},   // 🏋
    claude_sessions:      {label:"Claude sesije",    g:"&#129302;", kind:"int"}     // 🤖
  };

  // The aggregates band, in display order (each cell shows the per-day AVERAGE of that metric).
  var PV_AGG_ORDER = ["hours_worked","commits_per_hour","deep_work_ratio","parallelism",
    "avg_session_length_h","break_adherence","water_adherence","stretch_adherence",
    "movement_adherence","cache_hit_ratio","output_per_hour","max_concurrency"];
  // Highlights ("najbolji dan"), in display order — only metrics that actually have an entry show.
  var PV_HL_ORDER = ["hours_worked","commits","commits_per_hour","deep_work_ratio","session_hours",
    "parallelism","max_concurrency","output_tokens","output_per_hour","cache_hit_ratio",
    "water","stretch","exercise_total","exercise_sets","claude_sessions",
    "break_adherence","water_adherence","stretch_adherence","movement_adherence"];

  /* ---------- formatting ---------- */
  // The one place the "null → em dash, never 0" contract lives. isAvg=true means the value is a
  // per-day mean (so integer-count metrics render with a decimal), else it is a raw/best value.
  function pvFmt(key, v, isAvg){
    if(v === null || v === undefined) return "&#8212;";       // — : "not available"
    var n = +v; if(!isFinite(n)) return "&#8212;";
    var kind = (PV_METRICS[key] || {}).kind || "int";
    switch(kind){
      case "h":      return n.toFixed(1) + "h";
      case "pct":    return Math.round(n * 100) + "%";
      case "x":      return "&#215;" + n.toFixed(2);           // ×N.NN
      case "rate":   return n.toFixed(2);
      case "num1":   return n.toFixed(1);
      case "tokens": return (typeof fmtTok === "function") ? fmtTok(Math.round(n)) : Math.round(n).toLocaleString();
      case "int":    return isAvg ? n.toFixed(1) : Math.round(n).toLocaleString();
    }
    return String(n);
  }
  function pvInt(n){ return Math.round(+n || 0).toLocaleString(); }
  function pvH(n){ return (+n || 0).toFixed(1) + "h"; }
  // a derived (productivity.*) value: null/undefined stays "—", never 0
  function pvDeriv(prod, key, isAvg){ return pvFmt(key, prod ? prod[key] : null, isAvg); }
  function pvLabel(key){ return (PV_METRICS[key] || {}).label || key; }
  function pvGlyph(key){ return (PV_METRICS[key] || {}).g || ""; }
  function pvTimeRange(a, b){
    function t(e){ try{ return new Date((+e || 0) * 1000).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"}); }catch(x){ return "&#8212;"; } }
    return t(a) + " &rarr; " + t(b);
  }

  /* ---------- snapshot preload (instant paint on re-open; only the UNFILTERED view) ---------- */
  function pvSaveSnap(v){ try{ if(!prodvFrom && !prodvTo) localStorage.setItem("av_focus_prodv", JSON.stringify(v)); }catch(e){} }
  function pvLoadSnap(){ try{ var s = localStorage.getItem("av_focus_prodv"); return s ? JSON.parse(s) : null; }catch(e){ return null; } }

  /* ---------- fetch ---------- */
  function pvQuery(){
    var q = [];
    if(prodvFrom) q.push("from=" + encodeURIComponent(prodvFrom));
    if(prodvTo)   q.push("to="   + encodeURIComponent(prodvTo));
    return q.length ? ("?" + q.join("&")) : "";
  }
  function productivityFetch(){
    var host = $("pf-prod-view"); if(!host) return;
    var btn = $("pf-refresh"); if(btn) btn.disabled = true;
    fetch("/api/focus/productivity" + pvQuery()).then(function(r){
      if(!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function(v){
      if(btn) btn.disabled = false;
      if(!v || v.error) return pvError(v && v.error ? v.error : "gre&#353;ka");
      prodvView = v; pvSaveSnap(v); productivityRender(v);
    }).catch(function(e){
      if(btn) btn.disabled = false;
      pvError((e && e.message) ? e.message : "server nedostupan");
    });
  }
  // A failed fetch keeps the last good page if one is showing (like the Statistika page).
  function pvError(msg){
    var host = $("pf-prod-view"); if(!host) return;
    if(prodvView && (+prodvView.days_recorded || 0) > 0) return;
    host.innerHTML = pvToolbar() + '<div class="git-note err"><b>Ne mogu da u&#269;itam produktivnost</b><span>' + esc(msg) + '</span></div>';
    pvWireToolbar();
  }

  /* ---------- render ---------- */
  function productivityRender(v){
    var host = $("pf-prod-view"); if(!host || !v) return;
    var days = +v.days_recorded || 0;
    var html = pvToolbar(v);
    if(!days){ host.innerHTML = html + pvEmpty(); pvWireToolbar(); return; }

    var agg = v.aggregates || {}, tot = agg.totals || {}, avg = agg.averages || {},
        hi = v.highlights || {}, rows = Array.isArray(v.days) ? v.days : [];
    html += pvHeadline(tot, avg, days);
    html += pvAggBand(avg);
    html += pvHighlights(hi);
    html += pvHistory(rows);
    host.innerHTML = html;

    pvWireToolbar();
    var chart = $("pv-chart"); if(chart && typeof hudTipDelegate === "function") hudTipDelegate(chart);
    var tbl = $("pv-table"); if(tbl) tbl.addEventListener("click", pvRowClick);
  }

  /* toolbar: back to Statistika + a date-range search (from/to) */
  function pvToolbar(v){
    var rangeNote = "";
    if(v && (prodvFrom || prodvTo)){
      rangeNote = ' &middot; opseg <b>' + esc(prodvFrom || "&hellip;") + '</b> &ndash; <b>' + esc(prodvTo || "&hellip;") + '</b>';
    }
    return '<div class="pv-toolbar">'
      +   '<button class="btn pv-back" id="pv-back" title="nazad na statistiku">&#8592; Statistika</button>'
      +   '<div class="pv-filter">'
      +     '<span class="pv-flab">Datum</span>'
      +     '<input type="date" id="pv-from" class="pv-date" value="' + esc(prodvFrom) + '" aria-label="od datuma">'
      +     '<span class="pv-fdash">&ndash;</span>'
      +     '<input type="date" id="pv-to" class="pv-date" value="' + esc(prodvTo) + '" aria-label="do datuma">'
      +     '<button class="btn" id="pv-apply">Primeni</button>'
      +     '<button class="btn" id="pv-clear" title="poni&#353;ti filter">Poni&#353;ti</button>'
      +     '<span class="pv-fnote">' + (prodvFrom || prodvTo ? 'filtrirano' + rangeNote : 'ceo period') + '</span>'
      +   '</div>'
      + '</div>';
  }
  function pvWireToolbar(){
    var back = $("pv-back"); if(back) back.onclick = function(){ if(typeof profileShowSub === "function") profileShowSub("stats"); };
    var apply = $("pv-apply"); if(apply) apply.onclick = pvApplyFilter;
    var clear = $("pv-clear"); if(clear) clear.onclick = function(){ prodvFrom = ""; prodvTo = ""; productivityFetch(); };
    var from = $("pv-from"), to = $("pv-to");
    function onKey(e){ if(e.key === "Enter"){ e.preventDefault(); pvApplyFilter(); } }
    if(from) from.onkeydown = onKey; if(to) to.onkeydown = onKey;
  }
  function pvApplyFilter(){
    var from = $("pv-from"), to = $("pv-to");
    prodvFrom = from ? (from.value || "") : "";
    prodvTo   = to ? (to.value || "") : "";
    productivityFetch();
  }

  /* headline: wall-clock hours (primary) + a SEPARATE parallel-work indicator */
  function pvHeadline(tot, avg, days){
    var totHours = +tot.hours_worked || 0, avgHours = avg.hours_worked;
    var totSess = +tot.session_hours || 0, avgSess = avg.session_hours, avgPar = avg.parallelism;
    var maxPar = (prodvView && prodvView.highlights && prodvView.highlights.max_concurrency)
                 ? prodvView.highlights.max_concurrency.value : (avg.max_concurrency != null ? avg.max_concurrency : null);
    return '<div class="pv-headline">'
      + '<div class="pv-hero pv-hero-work">'
      +   '<div class="pv-hero-k">Sati rada &middot; zidni sat</div>'
      +   '<div class="pv-hero-v">' + pvH(totHours) + '</div>'
      +   '<div class="pv-hero-sub">&#248; ' + pvFmt("hours_worked", avgHours, true) + ' / dan &middot; ' + pvInt(days) + ' dana</div>'
      + '</div>'
      + '<div class="pv-hero pv-hero-par">'
      +   '<div class="pv-hero-k">Koliko paralelno</div>'
      +   '<div class="pv-par-row"><span class="pv-par-n">' + pvH(totSess) + '</span><span class="pv-par-l">sesijski sati (uk.)</span></div>'
      +   '<div class="pv-par-row"><span class="pv-par-n">' + pvFmt("parallelism", avgPar, true) + '</span><span class="pv-par-l">&#248; paralelizam</span></div>'
      +   '<div class="pv-par-row"><span class="pv-par-n">' + pvFmt("max_concurrency", maxPar, false) + '</span><span class="pv-par-l">maks. istovremeno</span></div>'
      +   '<div class="pv-par-note">sesijski sati mere <b>paralelan rad</b> &mdash; NE ura&#269;unavaju se u sate rada (zidni sat)</div>'
      + '</div>'
      + '</div>';
  }

  /* aggregates band: one cell per metric, its per-day AVERAGE (or "—" when absent) */
  function pvAggBand(avg){
    var cells = "";
    for(var i = 0; i < PV_AGG_ORDER.length; i++){
      var k = PV_AGG_ORDER[i];
      var has = (avg[k] !== undefined && avg[k] !== null);
      cells += '<div class="pv-agg-cell' + (has ? "" : " off") + '">'
        +   '<span class="pv-agg-ico">' + pvGlyph(k) + '</span>'
        +   '<span class="pv-agg-v">' + pvFmt(k, has ? avg[k] : null, true) + '</span>'
        +   '<span class="pv-agg-l">' + pvLabel(k) + '</span>'
        + '</div>';
    }
    return '<div class="pv-secthead"><h3>Proseci</h3><span class="pv-legend">&#248; po danu &middot; nedostupno = &#8212;</span></div>'
      + '<div class="pv-agg-grid">' + cells + '</div>';
  }

  /* highlights: "najbolji dan" chip per metric that has an entry. Curated order first, then any
     highlight key the backend added that we did not list — so EVERY entry gets a chip. */
  function pvHighlights(hi){
    var order = PV_HL_ORDER.slice(), seen = {};
    for(var a = 0; a < order.length; a++) seen[order[a]] = true;
    for(var kk in hi){ if(hi.hasOwnProperty(kk) && !seen[kk]){ order.push(kk); seen[kk] = true; } }
    var chips = "";
    for(var i = 0; i < order.length; i++){
      var k = order[i], h = hi[k];
      if(!h || h.value === null || h.value === undefined) continue;   // skip metrics with no entry
      chips += '<div class="pv-hchip">'
        +   '<span class="pv-hico">' + pvGlyph(k) + '</span>'
        +   '<span class="pv-hbody"><span class="pv-hlab">' + pvLabel(k) + '</span>'
        +     '<span class="pv-hval">' + pvFmt(k, h.value, false) + '</span></span>'
        +   '<span class="pv-hdate">' + esc(h.date || "") + '</span>'
        + '</div>';
    }
    if(!chips) return "";
    return '<div class="pv-secthead"><h3>Najbolji dani</h3><span class="pv-legend">rekord po metrici</span></div>'
      + '<div class="pv-hgrid">' + chips + '</div>';
  }

  /* history: compact chart + a clickable per-day table */
  function pvHistory(rows){
    return '<div class="pv-secthead"><h3>Istorija</h3><span class="pv-legend">' + rows.length + ' dana &middot; klik na red = detalji dana</span></div>'
      + '<div class="pv-chartwrap" id="pv-chart">' + pvChart(rows) + '</div>'
      + '<div class="pv-chart-legend">'
      +   '<span class="pv-lk pv-lk-bar">Sati rada</span>'
      +   '<span class="pv-lk pv-lk-sess">Sesijski sati</span>'
      +   '<span class="pv-lk pv-lk-commits">Commitovi</span>'
      +   '<span class="pv-lk-note">serije skalirane zasebno</span>'
      + '</div>'
      + pvTable(rows);
  }

  /* multi-series SVG: hours_worked bars + session_hours line + commits line. Each series is scaled
     to its OWN max so a small one stays visible (exact numbers live in the table). viewBox is
     day-count*14 wide with preserveAspectRatio="none"; strokes carry vector-effect via CSS. Wrapped
     in overflow-x:auto so many days scroll rather than widen the page. */
  function pvChart(rows){
    var n = rows.length; if(!n) return "";
    var SLOT = 16, PAD = 3, H = 110, W = n * SLOT;
    function smax(key, deriv){ var mx = 0; for(var i = 0; i < n; i++){ var x = +pvRowVal(rows[i], key, deriv) || 0; if(x > mx) mx = x; } return mx; }
    var maxHours = Math.max(smax("hours_worked"), 0.001), maxSess = smax("session_hours"), maxCommits = smax("commits");

    var grid = "";
    for(var g = 1; g <= 4; g++){ var gy = (H * (1 - g / 4)).toFixed(1); grid += '<line class="pv-gridline" x1="0" y1="' + gy + '" x2="' + W + '" y2="' + gy + '"/>'; }

    var bars = "", bands = "";
    for(var i = 0; i < n; i++){
      var d = rows[i] || {}, prod = d.productivity || {};
      var h = (+d.hours_worked || 0) / maxHours * (H - 2);
      var bx = (i * SLOT + PAD).toFixed(2), bw = (SLOT - 2 * PAD).toFixed(2), by = (H - h).toFixed(2);
      bars += '<rect class="pv-bar" x="' + bx + '" y="' + by + '" width="' + bw + '" height="' + Math.max(0, h).toFixed(2) + '"/>';
      var tip = '<b>' + esc(d.date || "") + '</b><br>'
        + 'Sati rada ' + pvH(d.hours_worked) + ' &middot; sesij. ' + pvH(d.session_hours) + '<br>'
        + 'Paralelizam ' + pvDeriv(prod, "parallelism", false) + ' &middot; maks ' + pvInt(d.max_concurrency) + '<br>'
        + '&#9095; ' + pvInt(d.commits) + ' &middot; deep-work ' + pvDeriv(prod, "deep_work_ratio", false) + '<br>'
        + '&#128167; ' + pvInt(d.water) + ' &middot; &#129336; ' + pvInt(d.stretch) + ' &middot; &#128170; ' + pvInt(d.exercise_total);
      bands += '<rect x="' + (i * SLOT) + '" y="0" width="' + SLOT + '" height="' + H + '" fill="rgba(0,0,0,0)" pointer-events="all"' + hudTipAttr(tip) + '/>';
    }
    function poly(cls, key, mx){
      if(mx <= 0) return "";
      var pts = "";
      for(var i = 0; i < n; i++){ var px = (i * SLOT + SLOT / 2).toFixed(2); var py = (H - (+rows[i][key] || 0) / mx * (H - 4)).toFixed(2); pts += (i ? " " : "") + px + "," + py; }
      return '<polyline class="' + cls + '" points="' + pts + '" fill="none" vector-effect="non-scaling-stroke"/>';
    }
    return '<svg class="pv-chart-svg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="produktivnost po danu" style="width:' + Math.max(W, 320) + 'px">'
      + grid + bars
      + poly("pv-line-sess", "session_hours", maxSess)
      + poly("pv-line-commits", "commits", maxCommits)
      + bands
      + '</svg>';
  }
  // read a per-day value, from the raw field or (when deriv=true) the derived productivity block
  function pvRowVal(row, key, deriv){ if(deriv){ var p = row.productivity || {}; return p[key]; } return row[key]; }

  /* per-day table (newest first). Each row is clickable → the drill-down modal. */
  function pvTable(rows){
    var ord = rows.slice().reverse(), tb = "";
    for(var i = 0; i < ord.length; i++){
      var d = ord[i] || {}, prod = d.productivity || {};
      tb += '<tr class="pv-trow" data-date="' + esc(d.date || "") + '" title="klik za detalje dana">'
        +   '<td class="pv-td-date">' + esc(d.date || "") + '</td>'
        +   '<td>' + pvH(d.hours_worked) + '</td>'
        +   '<td>' + pvH(d.session_hours) + '</td>'
        +   '<td>' + pvDeriv(prod, "parallelism", false) + '</td>'
        +   '<td>' + pvInt(d.max_concurrency) + '</td>'
        +   '<td>' + pvInt(d.commits) + '</td>'
        +   '<td>' + pvDeriv(prod, "commits_per_hour", false) + '</td>'
        +   '<td>' + pvDeriv(prod, "deep_work_ratio", false) + '</td>'
        +   '<td>' + ((typeof fmtTok === "function") ? fmtTok(+d.output_tokens || 0) : pvInt(d.output_tokens)) + '</td>'
        +   '<td>' + pvInt(d.water) + '</td>'
        +   '<td>' + pvInt(d.stretch) + '</td>'
        +   '<td>' + pvInt(d.exercise_total) + '</td>'
        +   '<td>' + pvInt(d.claude_sessions) + '</td>'
        +   '<td class="pv-td-go">&#8250;</td>'
        + '</tr>';
    }
    return '<div class="pv-tablewrap"><table class="pv-table" id="pv-table"><thead><tr>'
      +   '<th>Datum</th><th>Sati</th><th title="sesijski sati">Sesij.</th><th title="paralelizam">&#215;</th>'
      +   '<th title="maks. istovremeno">Maks</th><th title="commitovi">&#9095;</th><th title="commit/h">C/h</th>'
      +   '<th title="deep-work">Deep</th><th title="output tokeni">Tok.</th>'
      +   '<th title="Voda">&#128167;</th><th title="Istezanje">&#129336;</th><th title="Ve&#382;be (pon.)">&#128170;</th>'
      +   '<th title="Claude sesije">&#129302;</th><th></th>'
      + '</tr></thead><tbody>' + tb + '</tbody></table></div>';
  }

  function pvEmpty(){
    return '<div class="pf-empty"><b>Jo&#353; nema podataka o produktivnosti</b>'
      + '<span>Zatvori prvi radni dan (&#127881; Kraj dana u fokus pilulici) da se pojave dnevne metrike.</span></div>';
  }

  /* ---------- drill-down: one day's aggregate + its raw sessions (body-level modal) ---------- */
  function pvRowClick(e){
    var tr = e.target && e.target.closest ? e.target.closest(".pv-trow") : null;
    if(!tr) return;
    var date = tr.getAttribute("data-date"); if(date) pvOpenDay(date);
  }
  // The modal is a body-level fixed overlay (its own layer), so it escapes any card clipping.
  function pvModal(){
    var m = document.getElementById("pv-modal");
    if(m) return m;
    m = document.createElement("div");
    m.id = "pv-modal"; m.className = "pv-modal"; m.setAttribute("hidden", "");
    m.innerHTML = '<div class="pvm-bd" id="pvm-bd"></div>'
      + '<div class="pvm-dlg" role="dialog" aria-modal="true">'
      +   '<div class="pvm-h"><span class="pvm-tt" id="pvm-tt"></span><button class="pvm-x" id="pvm-x" aria-label="zatvori">&times;</button></div>'
      +   '<div class="pvm-body" id="pvm-body"></div>'
      + '</div>';
    document.body.appendChild(m);
    m.querySelector("#pvm-bd").onclick = pvCloseModal;
    m.querySelector("#pvm-x").onclick = pvCloseModal;
    return m;
  }
  function pvCloseModal(){ var m = document.getElementById("pv-modal"); if(m) m.setAttribute("hidden", ""); }
  addEventListener("keydown", function(e){
    if(e.key !== "Escape") return;
    var m = document.getElementById("pv-modal");
    if(m && !m.hasAttribute("hidden")) pvCloseModal();
  });
  function pvOpenDay(date){
    var m = pvModal();
    $("pvm-tt").innerHTML = '&#128197; ' + esc(date);
    $("pvm-body").innerHTML = '<div class="git-loading">U&#269;itavanje dana&hellip;</div>';
    m.removeAttribute("hidden");
    fetch("/api/focus/productivity?date=" + encodeURIComponent(date)).then(function(r){
      if(!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function(v){ pvRenderDay(v); }).catch(function(e){
      $("pvm-body").innerHTML = '<div class="git-note err"><b>Gre&#353;ka</b><span>' + esc((e && e.message) || "server nedostupan") + '</span></div>';
    });
  }
  function pvRenderDay(v){
    var body = $("pvm-body"); if(!body) return;
    var day = v && v.day, sessions = (v && Array.isArray(v.sessions)) ? v.sessions : [];
    if(!day){
      body.innerHTML = '<div class="pf-empty"><b>Nema agregiranih podataka za ovaj dan</b><span>' + esc((v && v.date) || "") + '</span></div>';
      return;
    }
    var prod = day.productivity || {};
    // day aggregate: the same metrics, from the day row (raw + derived)
    var stats = [
      ["Sati rada", pvH(day.hours_worked)],
      ["Sesijski sati", pvH(day.session_hours)],
      ["Paralelizam", pvDeriv(prod, "parallelism", false)],
      ["Maks. paralelno", pvInt(day.max_concurrency)],
      ["Commitovi", pvInt(day.commits)],
      ["Commit / h", pvDeriv(prod, "commits_per_hour", false)],
      ["Deep-work", pvDeriv(prod, "deep_work_ratio", false)],
      ["Du&#382;. bloka", pvDeriv(prod, "avg_session_length_h", false)],
      ["Output tokeni", (typeof fmtTok === "function") ? fmtTok(+day.output_tokens || 0) : pvInt(day.output_tokens)],
      ["Tokeni / h", pvDeriv(prod, "output_per_hour", false)],
      ["Cache hit", pvDeriv(prod, "cache_hit_ratio", false)],
      ["&#128167; Voda", pvInt(day.water)],
      ["&#129336; Istezanje", pvInt(day.stretch)],
      ["&#128170; Ve&#382;be", pvInt(day.exercise_total)],
      ["Fokus skor", pvInt(day.focus_score)],
      ["Sesija (dan)", pvInt(day.sessions)]
    ];
    var grid = "";
    for(var i = 0; i < stats.length; i++){
      grid += '<div class="pvm-stat"><span class="pvm-sl">' + stats[i][0] + '</span><span class="pvm-sv">' + stats[i][1] + '</span></div>';
    }

    // raw sessions of the day: started→ended, its commits/session_hours/focus_score
    var sess = "";
    for(var j = 0; j < sessions.length; j++){
      var s = sessions[j] || {};
      sess += '<div class="pvm-sess">'
        +   '<div class="pvm-sess-time">' + pvTimeRange(s.started_at, s.ended_at) + '</div>'
        +   '<div class="pvm-sess-nums">'
        +     '<span><i>Sati</i> ' + pvH(s.hours_worked) + '</span>'
        +     '<span><i>Sesij.</i> ' + pvH(s.session_hours) + '</span>'
        +     '<span><i>&#9095;</i> ' + pvInt(s.commits) + '</span>'
        +     '<span><i>Maks &#8649;</i> ' + pvInt(s.max_concurrency) + '</span>'
        +     '<span><i>Fokus</i> ' + pvInt(s.focus_score) + '</span>'
        +     '<span><i>&#128228;</i> ' + ((typeof fmtTok === "function") ? fmtTok(+s.output_tokens || 0) : pvInt(s.output_tokens)) + '</span>'
        +   '</div>'
        + '</div>';
    }
    if(!sess) sess = '<div class="pvm-nosess">Nema pojedina&#269;nih sesija za ovaj dan.</div>';

    body.innerHTML = '<div class="pvm-grid">' + grid + '</div>'
      + '<div class="pvm-sesshead">Sesije dana <span>' + sessions.length + '</span></div>'
      + '<div class="pvm-sesslist">' + sess + '</div>';
  }

  /* ---------- init (called by profile.js when the Produktivnost sub-page opens) ---------- */
  function productivityInit(){
    if(!$("pf-prod-view")) return;   // markup absent → no-op, never throw
    if(!prodvInited){
      prodvInited = true;
      var snap = pvLoadSnap();
      if(snap){ prodvView = snap; productivityRender(snap); }   // instant paint from last-known
    }
    productivityFetch();   // always refresh on open (NOT polled)
  }
