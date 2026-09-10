  "use strict";
  /* ================= RPG KARAKTER — a sub-page of the Profil tab =================
     Opened from the Profil header button "🎮 Karakter" (profile.js swaps #view-profile's main
     content to #pf-rpg-view). Reads GET /api/focus/rpg (a plain LAN GET). The character is fully
     DERIVED read-only from focus history/config by the backend; nothing here writes.

     The character is a DIFFERENT CREATURE depending on which wellness dimensions are tracked.
     The BACKEND is authoritative on WHICH creature (form) + its class — we do NOT recompute the
     selection here, we just draw the given `form` and print `char_class` / `class_sub`.
       - form: "human" | "skelet" | "slime" | "meduza" | "wisp" | "iskra"  (adapted from the
         approved rpg_forms_preview: each is a distinct SVG creature; only the matching one shows),
       - enabled:{water,stretch,exercise,fokus} — drives the soft fokus halo (fokus), the ".dry"
         desaturate tint (human & !water) and the istezanje "limber" sway (stretch),
       - stats_base:[stat keys on the neutral baseline] — those radar axes + stat rows go grey with
         an "osnova" tag; the enabled ones show real values,
       - a d20 attribute radar built from stats.* (each /20), plus a stat list,
       - Život / Stamina / Mana bars from vitals.* (0..1, already renormalized by the backend),
       - Level + XP (xp is a 0..1 fraction toward the next level),
       - a muscle-hover tag on the body (only the humanoid carries muscle groups),
       - a real "Današnji buff-ovi" feed from gains_today (only-risen stats/muscles + their deltas,
         a LEVEL UP! flash when leveled_up).
     Adapted from the scratch preview; wired to the app's HUD tokens (always-dark world). New
     visual rules live in web/rpg.css. Reuses core.js globals: $ , esc. */

  var rpgView = null;      // latest rpg object from the server
  var rpgBuilt = false;    // one-time skeleton-build guard
  var rpgInited = false;   // one-time preload guard

  var RPG_FORMS = ["human", "skelet", "slime", "meduza", "wisp", "iskra"];

  // stat rows — ORDER MUST MATCH the backend RPG_STATS so the radar vertices line up with stats.*
  var RPG_STATS = [
    ["snaga",        "&#128170;", "Snaga",         "ve&#382;be"],       // 💪
    ["spretnost",    "&#129336;", "Spretnost",     "istezanje"],        // 🤸
    ["izdrzljivost", "&#128737;", "Izdr&#382;ljivost", "voda&middot;streak"], // 🛡
    ["intelekt",     "&#129504;", "Intelekt",      "commitovi"],        // 🧠
    ["fokus",        "&#127919;", "Fokus",         "deep-work"],        // 🎯
    ["volja",        "&#128293;", "Volja",         "doslednost"]        // 🔥
  ];
  var RPG_MUSCLES = [
    ["grudi", "Grudi"], ["ramena", "Ramena"], ["biceps", "Biceps"], ["podlaktice", "Podlaktice"],
    ["trbusnjaci", "Trbu&#353;njaci"], ["kvadriceps", "Kvadriceps"], ["listovi", "Listovi"]
  ];
  var RPG_MUSCLE_LBL = {
    grudi: "Grudi (pektorali)", ramena: "Ramena (deltoidi)", biceps: "Biceps",
    podlaktice: "Triceps / podlaktice", trbusnjaci: "Trbu&#353;njaci (plo&#269;ice)",
    kvadriceps: "Kvadriceps (butine)", listovi: "Listovi"
  };
  var RPG_STAT_MAX = 20;
  function rpgClamp(v, a, b){ return Math.max(a, Math.min(b, v)); }
  function rpgReduced(){ try{ return matchMedia("(prefers-reduced-motion: reduce)").matches; }catch(e){ return false; } }

  /* ---------- snapshot preload (instant paint on re-open) ---------- */
  function rpgSaveSnap(v){ try{ localStorage.setItem("av_focus_rpg", JSON.stringify(v)); }catch(e){} }
  function rpgLoadSnap(){ try{ var s = localStorage.getItem("av_focus_rpg"); return s ? JSON.parse(s) : null; }catch(e){ return null; } }

  /* ---------- the humanoid body (the ONLY form carrying data-m muscle groups for glow + hover) --- */
  function rpgHumanForm(){
    return '<g class="rpg-form" data-form="human">'
      + '<g stroke="#2ea6c4" stroke-width="1" fill="rgba(56,189,248,.05)" filter="url(#rpg-glow)">'
      +   '<ellipse cx="120" cy="42" rx="20" ry="24"/>'
      +   '<path d="M104 64 h32 l6 18 -44 0 z"/>'
      +   '<path d="M96 88 q24 -10 48 0 l6 116 q-30 12 -60 0 z"/>'
      +   '<path d="M92 200 q28 12 56 0 l-4 42 q-24 8 -48 0 z"/>'
      +   '<path d="M96 240 l-6 108 q12 6 22 0 l6 -104 z"/>'
      +   '<path d="M144 240 l6 108 q-12 6 -22 0 l-6 -104 z"/>'
      +   '<path d="M90 350 q12 6 22 0 l-3 60 q-9 4 -16 0 z"/>'
      +   '<path d="M150 350 q-12 6 -22 0 l3 60 q9 4 16 0 z"/>'
      +   '<path d="M92 92 q-30 6 -34 44 l-8 56 q10 6 18 0 l8 -52 q4 -30 20 -38 z"/>'
      +   '<path d="M148 92 q30 6 34 44 l8 56 q-10 6 -18 0 l-8 -52 q-4 -30 -20 -38 z"/>'
      + '</g>'
      + '<g fill="url(#rpg-mg)" filter="url(#rpg-glow)">'
      +   '<ellipse class="rpg-m" data-m="ramena" cx="70" cy="98" rx="15" ry="12"/>'
      +   '<ellipse class="rpg-m" data-m="ramena" cx="170" cy="98" rx="15" ry="12"/>'
      +   '<path class="rpg-m" data-m="grudi" d="M96 96 q18 -8 22 2 l0 26 q-16 6 -26 -2 q-2 -18 4 -26 z"/>'
      +   '<path class="rpg-m" data-m="grudi" d="M144 96 q-18 -8 -22 2 l0 26 q16 6 26 -2 q2 -18 -4 -26 z"/>'
      +   '<ellipse class="rpg-m" data-m="biceps" cx="62" cy="132" rx="10" ry="20"/>'
      +   '<ellipse class="rpg-m" data-m="biceps" cx="178" cy="132" rx="10" ry="20"/>'
      +   '<ellipse class="rpg-m" data-m="podlaktice" cx="55" cy="182" rx="8" ry="20"/>'
      +   '<ellipse class="rpg-m" data-m="podlaktice" cx="185" cy="182" rx="8" ry="20"/>'
      +   '<g class="rpg-m" data-m="trbusnjaci">'
      +     '<rect x="106" y="132" width="12" height="15" rx="3"/><rect x="122" y="132" width="12" height="15" rx="3"/>'
      +     '<rect x="106" y="150" width="12" height="15" rx="3"/><rect x="122" y="150" width="12" height="15" rx="3"/>'
      +     '<rect x="106" y="168" width="12" height="16" rx="3"/><rect x="122" y="168" width="12" height="16" rx="3"/>'
      +   '</g>'
      +   '<ellipse class="rpg-m" data-m="kvadriceps" cx="103" cy="290" rx="15" ry="46"/>'
      +   '<ellipse class="rpg-m" data-m="kvadriceps" cx="137" cy="290" rx="15" ry="46"/>'
      +   '<ellipse class="rpg-m" data-m="listovi" cx="101" cy="378" rx="11" ry="30"/>'
      +   '<ellipse class="rpg-m" data-m="listovi" cx="139" cy="378" rx="11" ry="30"/>'
      + '</g>'
      + '</g>';
  }

  /* ---------- the five non-human creatures (adapted verbatim from rpg_forms_preview) -----------
     SMIL <animate>/<animateTransform> is NOT stopped by the app's global CSS reduced-motion kill,
     so under reduced motion the animation nodes are omitted entirely (the shapes stay, static). */
  function rpgOtherForms(red){
    // SKELET — gipki kostur (no muscles → no data-m; radCore still shows)
    var skelet = '<g class="rpg-form" data-form="skelet" filter="url(#rpg-glow)" style="display:none">'
      + '<ellipse cx="120" cy="42" rx="16" ry="19" class="rpg-bone"/>'
      + '<circle cx="113" cy="42" r="3.5" class="rpg-bone"/><circle cx="127" cy="42" r="3.5" class="rpg-bone"/>'
      + '<line class="rpg-bone" x1="120" y1="66" x2="120" y2="200"/>'
      + '<path class="rpg-bone" d="M120 100 q-30 5 -34 20 M120 100 q30 5 34 20 M120 122 q-32 5 -35 22 M120 122 q32 5 35 22 M120 144 q-28 6 -30 20 M120 144 q28 6 30 20"/>'
      + '<path class="rpg-bone" d="M92 92 l-30 44 -6 56 M148 92 l30 44 6 56"/>'
      + '<path class="rpg-bone" d="M104 200 q16 8 32 0 l6 30 -44 0 z"/>'
      + '<line class="rpg-bone" x1="110" y1="228" x2="100" y2="408"/><line class="rpg-bone" x1="130" y1="228" x2="140" y2="408"/>'
      + '<circle class="rpg-bone" cx="60" cy="194" r="4"/><circle class="rpg-bone" cx="180" cy="194" r="4"/>'
      + '<circle class="rpg-bone" cx="100" cy="408" r="4"/><circle class="rpg-bone" cx="140" cy="408" r="4"/>'
      + '</g>';

    // SLUZ (slime)
    var slimeA = red ? '' : '<animateTransform attributeName="transform" type="scale" additive="sum" values="1 1;1.04 .96;1 1;.97 1.03;1 1" dur="3.4s" repeatCount="indefinite"/>';
    var slime = '<g class="rpg-form" data-form="slime" filter="url(#rpg-glow)" style="display:none">'
      + '<g>' + slimeA
      +   '<path d="M58 320 Q56 232 120 232 Q184 232 182 320 Q182 372 120 374 Q58 372 58 320 Z" fill="url(#rpg-aqua)" stroke="#38e6ff" stroke-width="1.4"/>'
      +   '<ellipse cx="98" cy="270" rx="20" ry="11" fill="rgba(255,255,255,.28)"/>'
      +   '<ellipse cx="104" cy="300" rx="6" ry="9" fill="#06121a"/><ellipse cx="140" cy="300" rx="6" ry="9" fill="#06121a"/>'
      +   '<circle cx="104" cy="298" r="2" fill="#eaffff"/><circle cx="140" cy="298" r="2" fill="#eaffff"/>'
      +   '<path d="M108 326 q12 10 24 0" stroke="#06121a" stroke-width="2.4" fill="none" stroke-linecap="round"/>'
      + '</g></g>';

    // MEDUZA
    var medA = red ? '' : '<animateTransform attributeName="transform" type="translate" values="0 0;0 -8;0 0;0 6;0 0" dur="5s" repeatCount="indefinite"/>';
    var tentL = red ? '' : '<animate attributeName="d" dur="4.5s" repeatCount="indefinite" values="M86 256 q-8 40 6 78 q-10 26 2 44;M86 256 q8 40 -6 78 q10 26 -2 44;M86 256 q-8 40 6 78 q-10 26 2 44"/>';
    var tentR = red ? '' : '<animate attributeName="d" dur="4.5s" repeatCount="indefinite" values="M154 256 q8 40 -6 78 q10 26 -2 44;M154 256 q-8 40 6 78 q-10 26 2 44;M154 256 q8 40 -6 78 q10 26 -2 44"/>';
    var meduza = '<g class="rpg-form" data-form="meduza" filter="url(#rpg-glow)" style="display:none">'
      + '<g>' + medA
      +   '<path d="M74 232 Q120 176 166 232 Q166 258 120 258 Q74 258 74 232 Z" fill="url(#rpg-aqua)" stroke="#7ff3ff" stroke-width="1.2" opacity=".85"/>'
      +   '<circle cx="120" cy="222" r="9" fill="#eaffff"/>'
      +   '<g stroke="#7ff3ff" stroke-width="1.3" fill="none" opacity=".8" stroke-linecap="round">'
      +     '<path d="M86 256 q-8 40 6 78 q-10 26 2 44">' + tentL + '</path>'
      +     '<path d="M104 258 q-4 44 4 86 q-6 24 2 44"/>'
      +     '<path d="M120 258 q0 46 0 90 q0 26 0 46"/>'
      +     '<path d="M136 258 q4 44 -4 86 q6 24 -2 44"/>'
      +     '<path d="M154 256 q8 40 -6 78 q10 26 -2 44">' + tentR + '</path>'
      +   '</g>'
      + '</g></g>';

    // DUH (wisp)
    var wispA = red ? '' : '<animateTransform attributeName="transform" type="translate" values="0 0;0 -10;0 0;0 8;0 0" dur="4.6s" repeatCount="indefinite"/>';
    var wisp = '<g class="rpg-form" data-form="wisp" filter="url(#rpg-glow)" style="display:none">'
      + '<g>' + wispA
      +   '<path d="M120 176 Q98 236 120 300 Q142 236 120 176 Z" fill="url(#rpg-foc)"/>'
      +   '<path d="M120 190 Q106 240 120 288 Q134 240 120 190 Z" fill="rgba(127,243,255,.35)" stroke="#7ff3ff" stroke-width="1"/>'
      +   '<circle cx="120" cy="250" r="11" fill="#eaffff"/>'
      +   '<path d="M120 300 q-10 18 0 34 q10 16 0 32" stroke="#a78bfa" stroke-width="1.4" fill="none" stroke-linecap="round" opacity=".7"/>'
      +   '<circle cx="96" cy="230" r="2.4" fill="#a78bfa"/><circle cx="146" cy="262" r="2" fill="#7ff3ff"/><circle cx="108" cy="300" r="1.8" fill="#a78bfa"/>'
      + '</g></g>';

    // ISKRA
    var iskraA = red ? '' : '<animate attributeName="r" values="11;15;11" dur="1.8s" repeatCount="indefinite"/>';
    var iskra = '<g class="rpg-form" data-form="iskra" filter="url(#rpg-glow)" style="display:none">'
      + '<g transform="translate(120 250)">'
      +   '<circle r="13" fill="#eaffff">' + iskraA + '</circle>'
      +   '<g stroke="#7ff3ff" stroke-width="2" stroke-linecap="round">'
      +     '<line x1="0" y1="-22" x2="0" y2="-34"/><line x1="0" y1="22" x2="0" y2="34"/><line x1="-22" y1="0" x2="-34" y2="0"/><line x1="22" y1="0" x2="34" y2="0"/>'
      +     '<line x1="-16" y1="-16" x2="-25" y2="-25"/><line x1="16" y1="16" x2="25" y2="25"/><line x1="16" y1="-16" x2="25" y2="-25"/><line x1="-16" y1="16" x2="-25" y2="25"/>'
      +   '</g>'
      + '</g></g>';

    return skelet + slime + meduza + wisp + iskra;
  }

  /* ---------- one-time skeleton (SVG body + cards); populated by rpgRender ---------- */
  function rpgBodySvg(){
    var red = rpgReduced();
    // The chest "radCore" spark (humanoid/skelet only) uses SMIL <animate>, which the global CSS
    // reduced-motion kill can't stop — so omit the animate node entirely under reduced motion.
    var core = red
      ? '<circle id="rpg-core" cx="120" cy="118" r="5" fill="#eaffff" filter="url(#rpg-glow)"/>'
      : '<circle id="rpg-core" cx="120" cy="118" r="5" fill="#eaffff" filter="url(#rpg-glow)"><animate attributeName="r" values="4.5;6;4.5" dur="1.8s" repeatCount="indefinite"/></circle>';
    return '<svg viewBox="0 0 240 460" aria-label="holografski karakter">'
      + '<defs>'
      +   '<radialGradient id="rpg-mg" cx="50%" cy="40%" r="65%"><stop offset="0%" stop-color="#7ff3ff"/><stop offset="100%" stop-color="#1c9fc0"/></radialGradient>'
      +   '<radialGradient id="rpg-foc" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="rgba(167,139,250,.55)"/><stop offset="60%" stop-color="rgba(167,139,250,.18)"/><stop offset="100%" stop-color="rgba(167,139,250,0)"/></radialGradient>'
      +   '<radialGradient id="rpg-aqua" cx="50%" cy="38%" r="62%"><stop offset="0%" stop-color="rgba(120,245,255,.5)"/><stop offset="100%" stop-color="rgba(30,140,190,.28)"/></radialGradient>'
      +   '<filter id="rpg-glow"><feGaussianBlur stdDeviation="2.4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
      + '</defs>'
      + '<ellipse id="rpg-focus-halo" cx="120" cy="120" rx="92" ry="150" fill="url(#rpg-foc)" opacity="0"/>'
      + rpgHumanForm()
      + rpgOtherForms(red)
      + core
      + '</svg>';
  }

  function rpgBuild(){
    var host = $("pf-rpg-view"); if(!host || rpgBuilt) return;
    rpgBuilt = true;
    host.innerHTML =
      '<div class="rpg-wrap">'
      + '<div class="pv-toolbar"><button class="btn pv-back" id="rpg-back" title="nazad na statistiku">&#8592; Statistika</button>'
      +   '<span class="rpg-derived">karakter je izveden iz fokus/wellness istorije &mdash; forma zavisi od pra&#263;enih dimenzija</span></div>'

      + '<header class="rpg-top">'
      +   '<div class="rpg-avatar">&#129399;</div>'
      +   '<div class="rpg-who"><div class="rpg-name">Operater <b>BANE</b></div>'
      +     '<div class="rpg-cls">Full-Stack Berserker</div></div>'
      +   '<div class="rpg-lvlwrap">'
      +     '<div class="rpg-lvl" id="rpg-lvl">&#8212;<small>NIVO</small></div>'
      +     '<div class="rpg-xp"><div class="rpg-xl"><span>Iskustvo</span><span id="rpg-xptxt">&#8212;</span></div>'
      +       '<div class="rpg-xbar"><div class="rpg-xfill" id="rpg-xpfill"></div></div>'
      +       '<div class="rpg-points" id="rpg-points"></div></div>'
      +   '</div>'
      + '</header>'

      + '<main class="rpg-main">'
      +   '<section class="rpg-card rpg-char">'
      +     '<div class="rpg-clsttl" id="rpg-clsttl">&#8212;</div>'
      +     '<div class="rpg-clssub" id="rpg-clssub"></div>'
      +     '<div class="rpg-holo" id="rpg-holo"><div class="rpg-scan"></div>'
      +       rpgBodySvg()
      +       '<div class="rpg-plinth"></div><div class="rpg-mtag" id="rpg-mtag"></div></div>'
      +     '<div class="rpg-flavor" id="rpg-flavor">Skeniraj: pre&#273;i mi&#353;em preko mi&#353;i&#263;ne grupe &middot; telo raste sa ve&#382;bom</div>'
      +   '</section>'

      +   '<aside class="rpg-aside">'
      +     '<div class="rpg-card"><h3>&#9889; Vitalno <span class="rpg-t">regeneri&#353;e se dnevno</span></h3>'
      +       '<div class="rpg-vit">'
      +         '<div class="rpg-vrow rpg-hp"><span class="rpg-vlab">&#381;ivot</span><div class="rpg-track"><div class="rpg-vf" id="rpg-v-hp"></div></div><span class="rpg-vnum" id="rpg-n-hp"></span></div>'
      +         '<div class="rpg-vrow rpg-st"><span class="rpg-vlab">Stamina</span><div class="rpg-track"><div class="rpg-vf" id="rpg-v-st"></div></div><span class="rpg-vnum" id="rpg-n-st"></span></div>'
      +         '<div class="rpg-vrow rpg-mn"><span class="rpg-vlab">Mana&middot;Fokus</span><div class="rpg-track"><div class="rpg-vf" id="rpg-v-mn"></div></div><span class="rpg-vnum" id="rpg-n-mn"></span></div>'
      +       '</div></div>'
      +     '<div class="rpg-card"><h3>&#127922; Atributi <span class="rpg-t">isklju&#269;eno &rarr; siva &bdquo;osnova&ldquo;</span></h3>'
      +       '<div class="rpg-attrgrid"><svg class="rpg-radar" id="rpg-radar" viewBox="0 0 200 200"></svg><div class="rpg-alist" id="rpg-alist"></div></div></div>'
      +     '<div class="rpg-card"><h3>&#128170; Razvoj mi&#353;i&#263;a <span class="rpg-t">use it or lose it</span></h3>'
      +       '<div class="rpg-mus" id="rpg-mus"></div></div>'
      +     '<div class="rpg-card"><h3>&#10024; Dana&#353;nji buff-ovi</h3><div class="rpg-gains" id="rpg-gains"></div></div>'
      +   '</aside>'
      + '</main>'

      + '<div class="rpg-note"><b>&#352;ta pokre&#263;e &#353;ta:</b> SNAGA &larr; ve&#382;be (reps) &middot; SPRETNOST &larr; istezanje &middot; '
      +   'IZDR&#381;LJIVOST &larr; voda + streak &middot; INTELEKT &larr; commitovi + output &middot; FOKUS &larr; focus-score + deep-work &middot; '
      +   'VOLJA &larr; doslednost/streakovi. Isklju&#269;i&#353; dimenziju &rarr; karakter postaje <b>drugo bi&#263;e</b> a ti statovi stoje na sivoj osnovi (bez decay-a). '
      +   'Mi&#353;i&#263;ne grupe rastu po tipu ve&#382;be i opadaju kad se dugo ne treniraju (grace ' + '<span id="rpg-grace">3</span>' + ' dana).</div>'
      + '</div>';

    var back = $("rpg-back");
    if(back) back.onclick = function(){ if(typeof profileShowSub === "function") profileShowSub("stats"); };
    rpgBuildStatList();
    rpgWireMuscleHover();
  }

  // The stat list rows are static labels; values + the base/osnova state are filled in rpgRender.
  function rpgBuildStatList(){
    var el = $("rpg-alist"); if(!el) return;
    var h = "";
    for(var i = 0; i < RPG_STATS.length; i++){
      var a = RPG_STATS[i];
      h += '<div class="rpg-arow" id="rpg-arow-' + a[0] + '"><span class="rpg-aic">' + a[1] + '</span>'
        +   '<span class="rpg-an">' + a[2] + '<em>' + a[3] + '</em></span>'
        +   '<span class="rpg-av" id="rpg-av-' + a[0] + '">&#8212;</span></div>';
    }
    el.innerHTML = h;
  }

  // Muscle hover tag (positioned within .rpg-holo, like the preview). Only the humanoid carries
  // .rpg-m groups; on other forms those nodes are inside a display:none group, so hover never fires.
  function rpgWireMuscleHover(){
    var holo = $("rpg-holo"), tag = $("rpg-mtag"); if(!holo || !tag) return;
    var groups = holo.querySelectorAll(".rpg-m[data-m]");
    for(var i = 0; i < groups.length; i++){
      (function(g){
        g.addEventListener("mousemove", function(e){
          var key = g.getAttribute("data-m"), r = holo.getBoundingClientRect();
          var pct = Math.round(((rpgView && rpgView.muscles ? +rpgView.muscles[key] : 0) || 0) * 100);
          tag.innerHTML = (RPG_MUSCLE_LBL[key] || key) + " &middot; " + pct + "%";
          tag.style.left = (e.clientX - r.left) + "px";
          tag.style.top = (e.clientY - r.top - 14) + "px";
          tag.style.opacity = "1";
        });
        g.addEventListener("mouseleave", function(){ tag.style.opacity = "0"; });
      })(groups[i]);
    }
  }

  /* ---------- fetch + render ---------- */
  function rpgFetch(){
    if(!$("pf-rpg-view")) return;
    rpgBuild();
    var btn = $("pf-refresh"); if(btn) btn.disabled = true;
    fetch("/api/focus/rpg").then(function(r){
      if(!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function(v){
      if(btn) btn.disabled = false;
      if(!v || v.error) return rpgError(v && v.error ? v.error : "gre&#353;ka");
      rpgView = v; rpgSaveSnap(v); rpgRender(v);
    }).catch(function(e){
      if(btn) btn.disabled = false;
      rpgError((e && e.message) ? e.message : "server nedostupan");
    });
  }
  function rpgError(msg){
    // keep a last-good character if one is showing; else show the error in the gains area
    if(rpgView) return;
    var g = $("rpg-gains");
    if(g) g.innerHTML = '<div class="git-note-inline">' + esc(msg) + '</div>';
  }

  function rpgRender(v){
    if(!v) return;
    rpgBuild();
    var stats = v.stats || {}, muscles = v.muscles || {}, vitals = v.vitals || {};

    // ----- WHICH CREATURE (backend authoritative) -----
    var form = (v.form && RPG_FORMS.indexOf(v.form) >= 0) ? v.form : "human";
    var forms = document.querySelectorAll(".rpg-form[data-form]");
    for(var fi = 0; fi < forms.length; fi++){
      forms[fi].style.display = (forms[fi].getAttribute("data-form") === form) ? "" : "none";
    }
    var ttl = $("rpg-clsttl"); if(ttl) ttl.textContent = (v.char_class != null && v.char_class !== "") ? v.char_class : "—";
    var sub = $("rpg-clssub"); if(sub) sub.textContent = (v.class_sub != null) ? v.class_sub : "";

    // enabled dimensions → halo / dry tint / limber sway / radCore
    var en = v.enabled || {};
    var holo = $("rpg-holo");
    if(holo){
      holo.classList.toggle("rpg-dry", form === "human" && !en.water);   // human & !water → desaturate
      holo.classList.toggle("rpg-limber", !!en.stretch);                 // istezanje → gipko njihanje
    }
    var halo = $("rpg-focus-halo"); if(halo) halo.setAttribute("opacity", en.fokus ? "0.9" : "0");
    var core = $("rpg-core"); if(core) core.style.display = (form === "human" || form === "skelet") ? "" : "none";

    // which stat keys sit on the neutral baseline (grey + "osnova" tag; radar axes greyed too)
    var base = {};
    if(Array.isArray(v.stats_base)){ for(var bi = 0; bi < v.stats_base.length; bi++){ base[v.stats_base[bi]] = true; } }

    // level + xp
    var lvl = $("rpg-lvl"); if(lvl) lvl.firstChild.nodeValue = String(v.level != null ? v.level : "—");
    var xp = rpgClamp(+v.xp || 0, 0, 1);
    var xf = $("rpg-xpfill"); if(xf) xf.style.width = (xp * 100).toFixed(0) + "%";
    var xt = $("rpg-xptxt"); if(xt) xt.innerHTML = Math.round(xp * 100) + "% &rarr; NIVO " + ((v.level != null ? v.level : 0) + 1);
    var pts = $("rpg-points"); if(pts) pts.innerHTML = "&#9733; " + (Math.round((+v.points || 0) * 10) / 10).toLocaleString() + " poena aktivnosti";

    // vitals bars (already renormalized by the backend — just render)
    rpgVital("hp", vitals.hp); rpgVital("st", vitals.stamina); rpgVital("mn", vitals.mana);

    // attributes: list values + base/osnova greying + radar
    for(var i = 0; i < RPG_STATS.length; i++){
      var k = RPG_STATS[i][0], isBase = !!base[k];
      var row = $("rpg-arow-" + k); if(row) row.classList.toggle("rpg-base", isBase);
      var av = $("rpg-av-" + k);
      if(av){
        var val = (stats[k] != null) ? String(stats[k]) : "—";
        av.innerHTML = esc(val) + (isBase ? ' <span class="rpg-osnova">osnova</span>' : '');
      }
    }
    rpgRadar(stats, base);

    // muscles: bars + body glow
    rpgMuscles(muscles);

    // today's buffs
    rpgGains(v.gains_today || {});

    // decay grace hint
    var grace = $("rpg-grace"); if(grace && v.decay && v.decay.grace_days != null) grace.textContent = String(v.decay.grace_days);
  }

  function rpgVital(key, val){
    var f = $("rpg-v-" + key), n = $("rpg-n-" + key);
    var p = rpgClamp(+val || 0, 0, 1);
    if(f) f.style.width = (p * 100).toFixed(0) + "%";
    if(n) n.textContent = Math.round(p * 100);
  }

  function rpgMuscles(muscles){
    var el = $("rpg-mus"); if(el){
      var h = "";
      for(var i = 0; i < RPG_MUSCLES.length; i++){
        var m = RPG_MUSCLES[i], p = rpgClamp(+muscles[m[0]] || 0, 0, 1);
        h += '<div class="rpg-mline"><span class="rpg-mn2">' + m[1] + '</span>'
          +   '<div class="rpg-mt"><div class="rpg-mfill" style="width:' + (p * 100).toFixed(0) + '%"></div></div></div>';
      }
      el.innerHTML = h;
    }
    // body glow: opacity scales with development (0.14 floor so the outline stays readable).
    // .rpg-m nodes only exist on the humanoid; setting opacity on the hidden group is harmless.
    var groups = document.querySelectorAll(".rpg-m[data-m]");
    for(var j = 0; j < groups.length; j++){
      var lvl = rpgClamp(+muscles[groups[j].getAttribute("data-m")] || 0, 0, 1);
      groups[j].style.opacity = (0.14 + 0.86 * lvl).toFixed(2);
    }
  }

  // d20 radar built from stats/20 (order = RPG_STATS, matching the backend so vertices align).
  // Axes + vertices for stats on the neutral baseline (base[key]) are greyed.
  function rpgRadar(stats, base){
    base = base || {};
    var svg = $("rpg-radar"); if(!svg) return;
    var cx = 100, cy = 100, R = 76, n = RPG_STATS.length, ns = "http://www.w3.org/2000/svg";
    svg.innerHTML = "";
    function pt(i, r){ var ang = -Math.PI / 2 + i * 2 * Math.PI / n; return [cx + Math.cos(ang) * r, cy + Math.sin(ang) * r]; }
    [0.33, 0.66, 1].forEach(function(g){
      var p = document.createElementNS(ns, "polygon"), pts = [];
      for(var i = 0; i < n; i++){ var q = pt(i, R * g); pts.push(q[0].toFixed(1) + "," + q[1].toFixed(1)); }
      p.setAttribute("points", pts.join(" ")); p.setAttribute("fill", "none");
      p.setAttribute("stroke", "rgba(56,230,255," + (0.10 + g * 0.06) + ")"); svg.appendChild(p);
    });
    for(var i = 0; i < n; i++){
      var q = pt(i, R), ln = document.createElementNS(ns, "line"), isBase = !!base[RPG_STATS[i][0]];
      ln.setAttribute("x1", cx); ln.setAttribute("y1", cy); ln.setAttribute("x2", q[0]); ln.setAttribute("y2", q[1]);
      ln.setAttribute("stroke", isBase ? "rgba(95,132,148,.35)" : "rgba(56,230,255,.12)"); svg.appendChild(ln);
    }
    var poly = document.createElementNS(ns, "polygon"), pp = [];
    for(var j = 0; j < n; j++){ var v = (+stats[RPG_STATS[j][0]] || 0) / RPG_STAT_MAX, q2 = pt(j, R * rpgClamp(v, .03, 1)); pp.push(q2[0].toFixed(1) + "," + q2[1].toFixed(1)); }
    poly.setAttribute("points", pp.join(" ")); poly.setAttribute("fill", "rgba(56,230,255,.18)");
    poly.setAttribute("stroke", "#38e6ff"); poly.setAttribute("stroke-width", "2"); svg.appendChild(poly);
    for(var k = 0; k < n; k++){
      var kb = !!base[RPG_STATS[k][0]];
      var v2 = (+stats[RPG_STATS[k][0]] || 0) / RPG_STAT_MAX, q3 = pt(k, R * rpgClamp(v2, .03, 1)), c = document.createElementNS(ns, "circle");
      c.setAttribute("cx", q3[0]); c.setAttribute("cy", q3[1]); c.setAttribute("r", "2.6"); c.setAttribute("fill", kb ? "#5f8494" : "#7ff3ff"); svg.appendChild(c);
    }
  }

  // gains_today → a real "buffs" feed: only-risen stats + muscles with their deltas, a LEVEL UP! flash.
  function rpgGains(g){
    var el = $("rpg-gains"); if(!el) return;
    var rows = "", statmap = {}, i;
    for(i = 0; i < RPG_STATS.length; i++) statmap[RPG_STATS[i][0]] = RPG_STATS[i];
    if(g.leveled_up){
      rows += '<div class="rpg-levelup">&#11088; LEVEL UP! &mdash; novi nivo dostignut danas</div>';
    }
    var gstats = g.stats || {};
    for(var s in gstats){
      if(!gstats.hasOwnProperty(s)) continue;
      var meta = statmap[s] || [s, "&#10024;", s.toUpperCase()];
      rows += '<div class="rpg-gain"><span class="rpg-gi">' + meta[1] + '</span>'
        +   '<span class="rpg-gl">' + meta[2] + '</span>'
        +   '<b>+' + (Math.round((+gstats[s]) * 100) / 100).toFixed(2) + '</b></div>';
    }
    var gmus = g.muscles || {}, mlbl = {};
    for(i = 0; i < RPG_MUSCLES.length; i++) mlbl[RPG_MUSCLES[i][0]] = RPG_MUSCLES[i][1];
    for(var m in gmus){
      if(!gmus.hasOwnProperty(m)) continue;
      rows += '<div class="rpg-gain rpg-gain-mus"><span class="rpg-gi">&#128170;</span>'
        +   '<span class="rpg-gl">' + (mlbl[m] || m) + '</span>'
        +   '<b>+' + (Math.round((+gmus[m]) * 1000) / 10).toFixed(1) + '%</b></div>';
    }
    if((+g.points || 0) > 0){
      rows += '<div class="rpg-gain rpg-gain-pts"><span class="rpg-gi">&#9733;</span>'
        +   '<span class="rpg-gl">Aktivnost / XP</span>'
        +   '<b>+' + (Math.round((+g.points) * 10) / 10).toLocaleString() + '</b></div>';
    }
    if(!rows){
      rows = '<div class="rpg-nogains">Danas jo&#353; nema buff-ova &mdash; ve&#382;baj, pij vodu, u&#273;i u fokus da statovi porastu.</div>';
    }
    el.innerHTML = rows;
  }

  /* ---------- init (called by profile.js when the Karakter sub-page opens) ---------- */
  function rpgInit(){
    if(!$("pf-rpg-view")) return;   // markup absent → no-op, never throw
    rpgBuild();
    if(!rpgInited){
      rpgInited = true;
      var snap = rpgLoadSnap();
      if(snap){ rpgView = snap; rpgRender(snap); }   // instant paint from last-known
    }
    rpgFetch();   // always refresh on open (NOT polled)
  }
