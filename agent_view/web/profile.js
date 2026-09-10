  "use strict";
  /* ================= PROFILE segment — focus / wellness statistics =================
     A nav tab (#mode-profile → body.v-profile → #view-profile). On tab-open (and via a
     manual refresh button) it GETs /api/focus/profile and renders a rich stats page:
     header (days recorded + 3 streak chips), a 9-card metric grid (each with this-week /
     month / all-time totals+averages plus the two week-average flavors) and a history
     section (a compact multi-series SVG + a wide table). NOT polled — the aggregate is
     heavier than the 15s /api/focus poll, so it refreshes only on open + click.
     Reuses core.js globals: $ (getElementById), esc, hudTipDelegate/hudTipAttr (the shared
     floating tooltip). The endpoint is a plain LAN-readable GET (like /api/focus), so a
     plain fetch is used — NOT gitFetch (that is for the loopback-403-gated git/prod reads). */

  var profileView = null;      // latest profile object from the server
  var profileInited = false;   // one-time wiring guard (init runs on every tab-open)
  var profileSub = "stats";    // which sub-page shows: "stats" | "prod" | "rpg"

  // The metric cards, in display order. `key` matches a block in profile.metrics; `hours`
  // marks the one float metric (hours_worked) so totals/averages render as "6.0h", not "6".
  // Glyphs are HTML entities (charset-agnostic), labels carry their own entities (č/š/ž) and
  // are constants under our control — written as literal HTML, never esc()'d.
  var PF_METRICS = [
    {key:"hours_worked",    label:"Sati rada",         glyph:"&#9201;",   hours:true},  // ⏱
    {key:"water",           label:"Voda",              glyph:"&#128167;"},              // 💧
    {key:"stretch",         label:"Istezanje",         glyph:"&#129336;"},              // 🤸
    {key:"exercise_total",  label:"Ve&#382;be (pon.)", glyph:"&#128170;"},              // 💪
    {key:"pauses",          label:"Pauze",             glyph:"&#9208;"},                // ⏸
    {key:"snoozes",         label:"Snooze",            glyph:"&#128564;"},              // 😴
    {key:"claude_sessions", label:"Claude sesije",     glyph:"&#129302;"},              // 🤖
    {key:"work_blocks",     label:"Radni blokovi",     glyph:"&#129521;"},              // 🧱
    {key:"commits",         label:"Commitovi",         glyph:"&#9095;"}                 // ⎇
  ];

  /* ---------- number formatting ---------- */
  function pfInt(n){ return Math.round(+n || 0).toLocaleString(); }
  // a metric total: hours → "6.0h", every count → grouped integer
  function pfTotal(m, v){ return m.hours ? ((+v || 0).toFixed(1) + "h") : pfInt(v); }
  // a per-day average: hours → "6.0h", every count → one decimal
  function pfAvg(m, v){ return m.hours ? ((+v || 0).toFixed(1) + "h") : (+v || 0).toFixed(1); }
  function pfTime(epoch){ try{ return new Date((+epoch || 0) * 1000).toLocaleTimeString(); }catch(e){ return ""; } }

  /* ---------- fetch + snapshot preload ---------- */
  // A tiny localStorage snapshot so re-opening the tab paints instantly (house pattern,
  // like git/prod), never an empty "loading" flash; the live fetch then refreshes it.
  function profileSaveSnap(v){ try{ localStorage.setItem("av_focus_profile", JSON.stringify(v)); }catch(e){} }
  function profileLoadSnap(){ try{ var s = localStorage.getItem("av_focus_profile"); return s ? JSON.parse(s) : null; }catch(e){ return null; } }

  function profileFetch(){
    var btn = $("pf-refresh"); if(btn) btn.disabled = true;
    fetch("/api/focus/profile").then(function(r){
      if(!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function(v){
      if(btn) btn.disabled = false;
      if(!v || v.error){ return profileRenderError(v && v.error ? v.error : "gre&#353;ka"); }
      profileView = v; profileSaveSnap(v); profileRender(v);
    }).catch(function(e){
      if(btn) btn.disabled = false;
      profileRenderError((e && e.message) ? e.message : "server nedostupan");
    });
  }
  // An error card only replaces the body when we have nothing good showing — a failed
  // refresh over an already-rendered page keeps the last-known data on screen.
  function profileRenderError(msg){
    var body = $("pf-body"); if(!body) return;
    if(profileView && (+profileView.days_recorded || 0) > 0) return;
    body.innerHTML = '<div class="git-note err"><b>Ne mogu da u&#269;itam statistiku</b><span>' + esc(msg) + '</span></div>';
  }

  /* ---------- render ---------- */
  function profileRender(v){
    var body = $("pf-body"); if(!body || !v) return;
    var days = +v.days_recorded || 0, sub = $("pf-sub");
    if(sub){
      sub.innerHTML = days
        ? ('nedelja od <b>' + esc(v.week_start || "") + '</b> &middot; mesec od <b>' + esc(v.month_start || "") + '</b>'
           + (v.generated_at ? (' &middot; a&#382;urirano ' + esc(pfTime(v.generated_at))) : ''))
        : '';
    }
    if(!days){ body.innerHTML = pfEmpty(); return; }

    var metrics = v.metrics || {}, streaks = v.streaks || {}, hist = Array.isArray(v.history) ? v.history : [];
    var html = "";

    // header: days recorded + the three streak chips
    html += '<div class="pf-top">';
    html +=   '<div class="pf-daysrec"><b>' + pfInt(days) + '</b><span>zabele&#382;enih dana</span></div>';
    html +=   '<div class="pf-streaks">'
            +   pfStreak("&#128167;", streaks.water,    "Voda")
            +   pfStreak("&#129336;", streaks.stretch,  "Istezanje")
            +   pfStreak("&#128170;", streaks.exercise, "Ve&#382;be")
            + '</div>';
    html += '</div>';

    // metric grid
    html += '<div class="pf-gridhead"><h2>Metrike</h2><span class="pf-legend">uk. = ukupno &middot; &#248; = prosek po danu</span></div>';
    html += '<div class="pf-grid">';
    for(var i = 0; i < PF_METRICS.length; i++){ var m = PF_METRICS[i]; html += pfCard(m, metrics[m.key]); }
    html += '</div>';

    // history: chart + table
    html += '<div class="pf-gridhead"><h2>Istorija</h2><span class="pf-legend">poslednjih ' + hist.length + ' dana</span></div>';
    html += '<div class="pf-hist">';
    html +=   '<div class="pf-chart" id="pf-chart">' + profileChart(hist) + '</div>';
    html +=   '<div class="pf-chart-legend">'
            +   '<span class="pf-lk pf-lk-bar">Sati rada</span>'
            +   '<span class="pf-lk pf-lk-commits">Commitovi</span>'
            +   '<span class="pf-lk pf-lk-water">Voda</span>'
            +   '<span class="pf-lk-note">serije skalirane zasebno</span>'
            + '</div>';
    html +=   profileTable(hist);
    html += '</div>';

    body.innerHTML = html;
    // wire the shared floating tooltip on the (freshly rebuilt) chart host, once per render.
    var chart = $("pf-chart");
    if(chart && typeof hudTipDelegate === "function") hudTipDelegate(chart);
  }

  function pfEmpty(){
    return '<div class="pf-empty"><b>Jo&#353; nema zabele&#382;enih dana</b>'
         + '<span>Zatvori prvi dan (&#127881; Kraj dana u fokus pilulici) da se pojavi statistika.</span></div>';
  }

  function pfStreak(glyph, n, lab){
    n = +n || 0;
    return '<div class="pf-streak' + (n > 0 ? " on" : "") + '">'
         +   '<span class="pf-sk-ico">' + glyph + '</span>'
         +   '<span class="pf-sk-n">' + n + '</span>'
         +   '<span class="pf-sk-lab">' + lab + '<em>dana zaredom</em></span>'
         + '</div>';
  }

  // One metric card: label + hero (all-time total), a period breakdown (this week / month /
  // all-time, each total + ø/day), then the two all-time week-average flavors.
  function pfCard(m, blk){
    blk = blk || {};
    var periods = [
      ["ova nedelja", blk.total_week,  blk.avg_week],
      ["mesec",       blk.total_month, blk.avg_month],
      ["all-time",    blk.total_all,   blk.avg_all]
    ];
    var pr = "";
    for(var i = 0; i < periods.length; i++){
      pr += '<div class="pf-prow"><span class="pf-plab">' + periods[i][0] + '</span>'
          +   '<span class="pf-ptot">' + pfTotal(m, periods[i][1]) + '</span>'
          +   '<span class="pf-pavg">&#248; ' + pfAvg(m, periods[i][2]) + '</span></div>';
    }
    var wk = '<div class="pf-wrow"><span class="pf-wlab">radna <em>Pon&ndash;Pet</em></span><span class="pf-wval">&#248; ' + pfAvg(m, blk.avg_radna_nedelja) + '</span></div>'
           + '<div class="pf-wrow"><span class="pf-wlab">cela <em>Pon&ndash;Ned</em></span><span class="pf-wval">&#248; ' + pfAvg(m, blk.avg_cela_nedelja) + '</span></div>';
    return '<div class="pf-card">'
         +   '<div class="pf-card-h"><span class="pf-ico">' + m.glyph + '</span><span class="pf-name">' + m.label + '</span><span class="pf-hero">' + pfTotal(m, blk.total_all) + '</span></div>'
         +   '<div class="pf-periods">' + pr + '</div>'
         +   '<div class="pf-weeks">' + wk + '</div>'
         + '</div>';
  }

  /* ---------- history: compact multi-series SVG ----------
     hours_worked as bars (each scaled to the hours max), commits + water as overlaid
     polylines (each scaled to its OWN max so a small series stays visible — the exact
     numbers live in the table below). viewBox is DAY-count * 14 wide, 100 tall, drawn with
     preserveAspectRatio="none" so it stretches to the container; strokes carry
     vector-effect:non-scaling-stroke so lines stay 1.5px under the horizontal stretch
     (bars are rects → uniform scale is fine). Per-day transparent hover bands (data-tip via
     the shared hudTipDelegate) show that day's full breakdown. */
  function profileChart(hist){
    var n = hist.length; if(!n) return "";
    var SLOT = 14, PAD = 2, H = 100, W = n * SLOT;
    function seriesMax(key){ var mx = 0; for(var i = 0; i < n; i++){ var x = +hist[i][key] || 0; if(x > mx) mx = x; } return mx; }
    var maxHours = Math.max(seriesMax("hours_worked"), 0.001);
    var maxCommits = seriesMax("commits"), maxWater = seriesMax("water");

    var grid = "";
    for(var g = 1; g <= 4; g++){ var gy = (H * (1 - g / 4)).toFixed(1); grid += '<line class="pf-gridline" x1="0" y1="' + gy + '" x2="' + W + '" y2="' + gy + '"/>'; }

    var bars = "", bands = "";
    for(var i = 0; i < n; i++){
      var d = hist[i] || {};
      var h = (+d.hours_worked || 0) / maxHours * (H - 2);
      var bx = (i * SLOT + PAD).toFixed(2), bw = (SLOT - 2 * PAD).toFixed(2), by = (H - h).toFixed(2);
      bars += '<rect class="pf-bar" x="' + bx + '" y="' + by + '" width="' + bw + '" height="' + Math.max(0, h).toFixed(2) + '"/>';
      var tip = '<b>' + esc(d.date || "") + '</b><br>'
              + 'Sati rada ' + ((+d.hours_worked || 0).toFixed(1)) + 'h<br>'
              + '&#128167; ' + pfInt(d.water) + ' &middot; &#129336; ' + pfInt(d.stretch) + ' &middot; &#128170; ' + pfInt(d.exercise_total) + '<br>'
              + 'Pauze ' + pfInt(d.pauses) + ' &middot; &#129302; ' + pfInt(d.claude_sessions) + ' &middot; &#129521; ' + pfInt(d.work_blocks) + ' &middot; &#9095; ' + pfInt(d.commits);
      bands += '<rect x="' + (i * SLOT) + '" y="0" width="' + SLOT + '" height="' + H + '" fill="rgba(0,0,0,0)" pointer-events="all"' + hudTipAttr(tip) + '/>';
    }
    function poly(cls, key, mx){
      if(mx <= 0) return "";
      var pts = "";
      for(var i = 0; i < n; i++){ var px = (i * SLOT + SLOT / 2).toFixed(2); var py = (H - (+hist[i][key] || 0) / mx * (H - 4)).toFixed(2); pts += (i ? " " : "") + px + "," + py; }
      return '<polyline class="' + cls + '" points="' + pts + '" fill="none" vector-effect="non-scaling-stroke"/>';
    }
    return '<svg class="pf-chart-svg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="istorija po danu">'
         + grid + bars
         + poly("pf-line-commits", "commits", maxCommits)
         + poly("pf-line-water", "water", maxWater)
         + bands
         + '</svg>';
  }

  /* ---------- history: table (newest day first; overflow-x:auto so it never widens) ---------- */
  function profileTable(hist){
    var rows = hist.slice().reverse(), tb = "";
    for(var i = 0; i < rows.length; i++){
      var d = rows[i] || {};
      tb += '<tr>'
          +   '<td class="pf-td-date">' + esc(d.date || "") + '</td>'
          +   '<td>' + ((+d.hours_worked || 0).toFixed(1)) + '</td>'
          +   '<td>' + pfInt(d.water) + '</td>'
          +   '<td>' + pfInt(d.stretch) + '</td>'
          +   '<td>' + pfInt(d.exercise_total) + '</td>'
          +   '<td>' + pfInt(d.pauses) + '</td>'
          +   '<td>' + pfInt(d.snoozes) + '</td>'
          +   '<td>' + pfInt(d.claude_sessions) + '</td>'
          +   '<td>' + pfInt(d.work_blocks) + '</td>'
          +   '<td>' + pfInt(d.commits) + '</td>'
          + '</tr>';
    }
    return '<div class="pf-tablewrap"><table class="pf-table"><thead><tr>'
         +   '<th>Datum</th><th>Sati</th>'
         +   '<th title="Voda">&#128167;</th><th title="Istezanje">&#129336;</th><th title="Ve&#382;be (pon.)">&#128170;</th>'
         +   '<th title="Pauze">Pauze</th><th title="Snooze">Snooze</th>'
         +   '<th title="Claude sesije">&#129302;</th><th title="Radni blokovi">Blok.</th><th title="Commitovi">&#9095;</th>'
         + '</tr></thead><tbody>' + tb + '</tbody></table></div>';
  }

  /* ---------- sub-page switching (Statistika / Produktivnost / Karakter) ----------
     The two page buttons live in the Profil HEADER (NOT the nav bar). Clicking swaps which of
     the three sibling containers inside #view-profile is shown; the default is Statistika, and
     each sub-page also renders its own "← Statistika" back link. Clicking an already-active page
     button returns to Statistika. Osveži (#pf-refresh) refreshes whichever page is showing. */
  function profileShowSub(name){
    profileSub = name;
    var b = $("pf-body"), pv = $("pf-prod-view"), rp = $("pf-rpg-view"), sub = $("pf-sub");
    if(b)  b.style.display  = (name === "stats") ? "" : "none";
    if(pv) pv.style.display = (name === "prod")  ? "" : "none";
    if(rp) rp.style.display = (name === "rpg")   ? "" : "none";
    var pb = $("pf-prod-btn"), rb = $("pf-rpg-btn");
    if(pb) pb.classList.toggle("act", name === "prod");
    if(rb) rb.classList.toggle("act", name === "rpg");
    if(sub) sub.style.display = (name === "stats") ? "" : "none";   // the sub text describes Statistika
    if(name === "prod" && typeof productivityInit === "function") productivityInit();
    if(name === "rpg"  && typeof rpgInit === "function") rpgInit();
  }
  // Osveži dispatches to the active page's own fetch (each is a plain LAN GET, refreshed on demand).
  function profileRefresh(){
    if(profileSub === "prod"){ if(typeof productivityFetch === "function") productivityFetch(); }
    else if(profileSub === "rpg"){ if(typeof rpgFetch === "function") rpgFetch(); }
    else profileFetch();
  }

  /* ---------- init (called from setMode on every tab-open, like gitInit/prodInit) ---------- */
  function profileInit(){
    if(!$("view-profile")) return;   // markup absent → no-op, never throw
    if(!profileInited){
      profileInited = true;
      var btn = $("pf-refresh"); if(btn) btn.addEventListener("click", profileRefresh);
      // The focus/wellness settings MODAL (owned by focus.js) is opened from the pilula
      // stats-dropdown's "Podešavanja" (#fpop-settings) — the redundant ⚙ header button was
      // removed, so there is no #pf-settings-btn wiring here anymore.
      // The two sub-page switchers: toggle to the page, or back to Statistika if already active.
      var pb = $("pf-prod-btn");
      if(pb) pb.addEventListener("click", function(){ profileShowSub(profileSub === "prod" ? "stats" : "prod"); });
      var rb = $("pf-rpg-btn");
      if(rb) rb.addEventListener("click", function(){ profileShowSub(profileSub === "rpg" ? "stats" : "rpg"); });
      var snap = profileLoadSnap();
      if(snap){ profileView = snap; profileRender(snap); }   // instant paint from last-known
    }
    // Opening the Profil nav tab always lands on the canonical Statistika view (the sub-pages are
    // drill-ins reached from the header, never the nav-tab default). In-tab switches go through
    // profileShowSub; this only fires on a nav (setMode) re-entry.
    profileShowSub("stats");
    profileFetch();   // always refresh Statistika on tab-open (NOT polled — heavier than /api/focus)
  }
