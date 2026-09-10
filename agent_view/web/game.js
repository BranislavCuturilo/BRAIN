  "use strict";
  /* ================= Idle-game overlay (F1 + F2) =================
     An ADDITIVE layer over the HUD. Mirrors focus.js: seeded from a GET, every
     response (poll OR POST) funnels through gameApply, a 1s ticker moves only the
     cosmetic token counter. Reuses core.js globals — $ (getElementById), esc,
     aiPost (the error-surfacing POST-json), hexA, fmtTok — and the global `mode`.
     Every DOM lookup is existence-guarded and onGameTool no-ops when the overlay
     is off, so the HUD is byte-identical with the game disabled.

     Contract (backend lands in parallel), GET /api/game/state:
       {config:{enabled}, economy:{tokens,lifetime,rate_per_min,knowledge,lifetime_knowledge},
        defense:{shield,shield_max,hp,hp_max}, level:{level,xp,points},
        collect:{available,multiplier,preview,hours_today},
        prestige:{rank,rank_name,mult,next_cost,can_prestige},
        quiz:{tier_active,accuracy,due_count,tags?,boss:{available,active_secs,interval_secs}},
        shop:[{id,name,category,price,owned,affordable,effect_desc}], stats:{}}
     POST /api/game/{collect,buy{id},toggle{enabled},prestige} each return the new state.
     Quiz/boss POSTs return PARTIAL payloads (not full state) — see each handler; the
     shield/knowledge deltas they carry are folded into gameView and a poll reconciles.

     F2 HONESTY: a wrong quiz answer NEVER subtracts — the reveal is framed as a
     scheduled win ("štit se puni na tačan; netačan ga samo ne puni; vraća se sutra").
     Correct / box-promotion / boss-victory / prestige MAY celebrate (game-side, §1.1);
     real WORK income stays ambient (unchanged from F1). */

  var gameView = null;               // latest state object from the server
  var gameEnabled = false;           // config.enabled
  var gameRate = 0;                  // economy.rate_per_min (drives the cosmetic tick)
  var gameServerTokens = 0;          // last authoritative token count (poll/POST)
  var gameDisplayTokens = 0;         // cosmetic animated count; reconciled every gameApply
  var GAME_POLL_MS = 25000;          // SLOW fallback GET; F3: SSE {type:"game"} now pushes live state on each mutation
  var igWasVisible = false;          // last visibility, so we only start/stop the canvas on change
  var igStatsOpenFlag = false;       // the Stats popover open state

  /* ---- F2 quiz / boss / tags / spotlight state ---- */
  var igQuizQ = null;                // the served question awaiting an answer ({qid,options,...}); null once answered
  var igQuizBusy = false;            // an answer/next/retire request is in flight (guards double-fire)
  var igQuizOpen = false;            // the card is showing
  var igQuizSpotlit = false;         // the card is in the centre-screen anti-phone spotlight
  var igQuizDismissedAt = 0;         // last manual dismiss (spotlight re-arms only after a fresh idle cycle)
  var igLastInputAt = Date.now();    // client presence signal: last pointer/key (drives "user idle")
  var IG_IDLE_MS = 45000;            // idle for this long + a live session => surface the card
  var igBoss = null;                 // active boss run {session_id,total,idx}; null when no run
  var igBossBusy = false;
  var igTagsWork = [];               // the tags modal's working selection (committed on Save)
  var igTagsKnown = [];              // last authoritative tag list (from state.quiz.tags or a tags POST)
  var IG_TAG_PALETTE = ["networking","databases","python","javascript","http","security",
    "git","linux","algorithms","data-structures","system-design","testing","docker",
    "concurrency","css","django","regex","caching"];

  function igFmt(n){ n = +n || 0; return Math.floor(n).toLocaleString("en-US"); }
  // knowledge / cost / mult can be fractional: show up to 1 decimal, trimming a trailing .0
  function igNum(n){ n = +n || 0; var r = Math.round(n*10)/10; return (r===Math.floor(r)) ? String(Math.floor(r)) : r.toFixed(1); }
  function igMult(n){ return (+n || 1).toFixed(2); }   // prestige multiplier — always 2dp (1.35 must not read 1.4)
  function igSetText(id,v){ var e=$(id); if(e) e.textContent=v; }
  function igSetHTML(id,h){ var e=$(id); if(e) e.innerHTML=h; }
  var igQuizShownQid = null;         // qid of the question currently displayed (retire target; survives answering)
  // epoch-seconds -> a short Serbian relative "due" ("sada" / "za 3h" / "sutra" / "za 5d")
  function igDueText(due){
    var d = +due || 0; if(!d) return "";
    var secs = d - Date.now()/1000;
    if(secs <= 60) return "sada";
    var h = secs/3600;
    if(h < 20) return "za "+Math.max(1,Math.round(h))+"h";
    var days = Math.round(h/24);
    return days<=1 ? "sutra" : ("za "+days+"d");
  }
  function igXpFrac(level){
    // level.xp is expected as a 0..1 progress fraction to the next level (RPG char).
    // Tolerate a 0..100 percentage too; a raw cumulative value falls back to 0 (bar
    // stays empty rather than fabricating a fill). FLAGGED to backend in the report.
    var x = level && typeof level.xp === "number" ? level.xp : 0;
    if(x >= 0 && x <= 1) return x;
    if(x > 1 && x <= 100) return x/100;
    return 0;
  }

  /* ---------- fetch + apply (the single funnel) ---------- */
  function gamePoll(){
    // Seed once (gameView===null) so we learn the enabled state; afterward only
    // reach out while the HUD is the active view (cheap, and honours the spec).
    if(gameView && mode !== "hud") return;
    fetch("/api/game/state").then(function(r){ return r.json(); }).then(function(v){
      if(v && !v.error) gameApply(v);
    }).catch(function(){});
  }
  function gameApply(state){
    if(!state) return;
    gameView = state;
    gameEnabled = !!(state.config && state.config.enabled);
    var eco = state.economy || {};
    gameRate = +eco.rate_per_min || 0;
    gameServerTokens = +eco.tokens || 0;
    gameDisplayTokens = gameServerTokens;             // reconcile the cosmetic counter to truth
    igRenderToggle(gameEnabled);
    igRenderBars(state.defense || {});
    igRenderEcon(state);
    igRenderProgress(state.level || {});
    igRenderCollect(state.collect || {});
    igRenderShop(state.shop || [], gameServerTokens);
    igRenderQuizAffordance(state);                    // Kviz-button due count + Boss availability
    if((state.quiz && Array.isArray(state.quiz.tags))) igTagsKnown = state.quiz.tags.slice();
    if(igStatsOpenFlag) igRenderStats(state);
    igMaybeRenderDash(state);                          // keep the Dashboard modal live while open
    gameReconcileVisible();                           // toggle game-on + drive the canvas
    igSpotlightCheck();                               // anti-phone: maybe surface the quiz card
  }
  // 1s ticker: reconciles visibility (body.className is wiped on every setMode, so
  // game-on must be re-asserted here) and moves ONLY the cosmetic token counter.
  function gameTick(){
    gameReconcileVisible();
    igReflowButtons();                                                            // track reminder cards appearing/disappearing
    if(!gameEnabled || mode !== "hud") return;
    if(gameRate > 0){ gameDisplayTokens += gameRate/60; igPaintTokens(false); }   // silent ambient rise — never a per-action flash
    igSpotlightCheck();                                                            // anti-phone: surface the quiz card while idle
  }

  /* ---------- renderers ---------- */
  function igPaintTokens(flash){
    var t = igFmt(gameDisplayTokens);
    igSetText("ig-tok", t); igSetText("ig-shop-tok", t); igSetText("ig-shopbtn-tok", t);
    if(flash){ var b=$("ig-tok"); if(b){ b.classList.add("flash"); setTimeout(function(){ b.classList.remove("flash"); },180); } }
  }
  function igRenderBars(d){
    var shMax = +d.shield_max || 100, hpMax = +d.hp_max || 100;
    var sh = Math.max(0,+d.shield||0), hp = Math.max(0,+d.hp||0);
    var sf=$("ig-sh-fill"), sv=$("ig-sh-val"), hf=$("ig-hp-fill"), hv=$("ig-hp-val");
    if(sf) sf.style.width = (shMax>0?Math.min(100,sh/shMax*100):0)+"%";
    if(sv) sv.textContent = Math.round(sh)+"/"+shMax;
    if(hf) hf.style.width = (hpMax>0?Math.min(100,hp/hpMax*100):0)+"%";
    if(hv) hv.textContent = Math.round(hp)+"/"+hpMax;
  }
  function igRenderEcon(state){
    var eco = state.economy || {}, lvl = state.level || {}, c = state.collect || {};
    igPaintTokens(false);
    igSetText("ig-rate", "+"+Math.round(+eco.rate_per_min||0)+" / min");
    // item 4 (per-session income model) lives in the Game Dashboard, not here — adding a row would
    // grow the Economy panel into the bottom-right button stack (Boss). Keep this panel F2-height.
    igSetText("ig-know", igNum(eco.knowledge));       // F2 learning currency (0 on an F1 file)
    igSetText("ig-econ-lv", "LV "+(+lvl.level||0));
    igSetText("ig-lifetime", igFmt(eco.lifetime));
    igSetText("ig-collect-state", c.available ? ("spremno ×"+(+c.multiplier||1).toFixed(2)) : "skupljeno");
    igSetText("ig-statsbtn-lv", "LV "+(+lvl.level||0));
  }
  function igRenderProgress(lvl){
    var frac = igXpFrac(lvl);
    igSetText("ig-prog-lv", "LV "+(+lvl.level||0));
    var pf=$("ig-prog-fill"); if(pf) pf.style.width = (frac*100).toFixed(0)+"%";
    igSetText("ig-prog-xp", "XP "+Math.round(frac*100)+"%");
  }
  function igRenderCollect(c){
    var btn=$("ig-collect"); if(!btn) return;
    if(c && c.available){
      btn.disabled = false;
      var mult = (+c.multiplier||1).toFixed(2);
      var prev = c.preview ? (" (+"+igFmt(c.preview)+")") : "";
      btn.textContent = "Skupi dnevni bonus ×"+mult+prev;
    } else {
      btn.disabled = true;
      btn.textContent = "Bonus skupljen — vrati se sutra";
    }
  }
  function igRenderStats(state){
    var lvl = state.level || {}, eco = state.economy || {}, pr = state.prestige || {};
    var frac = igXpFrac(lvl), lv = +lvl.level||0;
    var rank = +pr.rank||0, mult = +pr.mult||1;
    // F2: rank line + prestige mult are LIVE (rank_name + rank number from state.prestige).
    igSetText("ig-sp-rank", pr.rank_name || "Rang");
    igSetText("ig-sp-ranknum", "R"+rank);
    igSetText("ig-sp-lv", lv);
    var xf=$("ig-sp-xpfill"); if(xf) xf.style.width=(frac*100).toFixed(0)+"%";
    igSetText("ig-sp-xptext", Math.round(frac*100)+"% → LV "+(lv+1));
    // four real cells: income, knowledge, lifetime, points
    igSetText("ig-sp-income", "+"+Math.round(+eco.rate_per_min||0));
    igSetText("ig-sp-knowledge", igNum(eco.knowledge));
    igSetText("ig-sp-lifetime", igFmt(eco.lifetime));
    igSetText("ig-sp-points", igFmt(lvl.points));
    var m=$("ig-sp-mult"); if(m) m.innerHTML="prihod &times;<b>"+igMult(mult)+"</b> &middot; prestige";
  }
  /* ---------- per-session income model (item 4; no backend data invented) ----------
     Income is pooled from the LIVE Claude sessions (core.js `order`/`sessions`); shield & HP
     are SHARED. Counts a session live if seen in the last ~8s OR it has a running/asking agent
     — the same "live" notion sessions.js uses. Falls back to the open-session count. */
  function igLiveSessionCount(){
    try{
      if(typeof order==="undefined" || !order || !order.length) return 0;
      var n=0, nowS=Date.now()/1000;
      for(var i=0;i<order.length;i++){ var s=sessions[order[i]]; if(!s) continue;
        var live=(nowS-(s.last||0))<8;
        if(!live && s.agents){ for(var k in s.agents){ var a=s.agents[k]; if(a&&(a.state==="run"||a.state==="ask")){ live=true; break; } } }
        if(live) n++;
      }
      return n;
    }catch(e){ return 0; }
  }

  /* ---------- Game Dashboard (rich modal; live-re-rendered while open) ---------- */
  function igMaybeRenderDash(state){ var m=$("ig-dash"); if(m && !m.hidden && state) igRenderDash(state); }
  var IG_DASH_CATS={subagent:"Subagenti", tool:"Alati", skill:"Veštine"};
  function igRenderDash(state){
    var host=$("ig-dash-body"); if(!host || !state) return;
    var eco=state.economy||{}, lvl=state.level||{}, pr=state.prestige||{}, q=state.quiz||{}, st=state.stats||{}, df=state.defense||{};
    var frac=igXpFrac(lvl), lv=+lvl.level||0, boss=q.boss||{};
    var acc=(q.accuracy==null)?null:(+q.accuracy);
    igSetHTML("ig-dash-cur", "&#129689; <b>"+igFmt(eco.tokens)+"</b> tokena");
    function tile(v,l,cls){ return '<div class="ig-dh-tile'+(cls?(" "+cls):"")+'"><span class="v">'+v+'</span><span class="l">'+l+'</span></div>'; }
    // hero: rank + level with a big XP bar
    var hero='<div class="ig-dash-hero">'+
      '<div class="ig-dh-hcard rank"><div class="ig-dh-hk">Rang</div>'+
        '<div class="ig-dh-rankname">'+esc(pr.rank_name||"Rang")+'</div>'+
        '<div class="ig-dh-hsub">R'+(+pr.rank||0)+' &middot; prihod &times;<b>'+igMult(pr.mult)+'</b></div></div>'+
      '<div class="ig-dh-hcard lvl"><div class="ig-dh-hk">Level</div>'+
        '<div class="ig-dh-lvbig">'+lv+'</div>'+
        '<div class="ig-dh-xpbar"><div class="ig-dh-xpfill" style="width:'+(frac*100).toFixed(0)+'%"></div></div>'+
        '<div class="ig-dh-hsub">'+Math.round(frac*100)+'% &rarr; LV '+(lv+1)+'</div></div>'+
      '</div>';
    // headline economy + defense tiles
    var tiles='<div class="ig-dash-tiles">'+
      tile(igFmt(eco.tokens),"tokeni")+
      tile("+"+Math.round(+eco.rate_per_min||0),"prihod / min","g")+
      tile(igNum(eco.knowledge),"znanje","a")+
      tile(igFmt(eco.lifetime),"lifetime tokeni")+
      tile(igNum(eco.lifetime_knowledge),"lifetime znanje")+
      tile(igFmt(lvl.points),"poeni")+
      tile(Math.round(+df.shield||0)+"/"+(+df.shield_max||0),"štit","cyv")+
      tile(Math.round(+df.hp||0)+"/"+(+df.hp_max||0),"HP","accv")+
      '</div>';
    // owned upgrades grouped by category
    var shop=state.shop||[], byCat={}, catOrder=[];
    shop.forEach(function(it){ var c=it.category||"ostalo"; if(!byCat[c]){ byCat[c]=[]; catOrder.push(c); } byCat[c].push(it); });
    var cats=catOrder.map(function(c){
      var items=byCat[c], ownedN=items.reduce(function(a,it){ return a+(+it.owned||0); },0);
      var rows=items.map(function(it){ var owned=+it.owned||0;
        return '<div class="ig-dh-up'+(owned>0?" have":"")+'">'+
          '<span class="ig-dh-up-ico">'+igCatIcon(c)+'</span>'+
          '<span class="ig-dh-up-name">'+esc(it.name||it.id||"")+'</span>'+
          '<span class="ig-dh-up-eff">'+esc(it.effect_desc||"")+'</span>'+
          '<span class="ig-dh-up-own">&times;'+owned+'</span></div>';
      }).join("");
      return '<div class="ig-dash-catcard">'+
        '<div class="ig-dh-cathead"><span>'+igCatIcon(c)+' '+esc(IG_DASH_CATS[c]||c)+'</span>'+
          '<span class="ig-dh-catn">'+ownedN+' u vlasništvu</span></div>'+rows+'</div>';
    }).join("");
    var upgrades='<div class="ig-dash-sec">Nadogradnje</div><div class="ig-dash-cats">'+cats+'</div>';
    // quiz
    var accTxt=(acc==null)?"&mdash;":(Math.round(acc*100)+"%");
    var quizHtml='<div class="ig-dash-sec">Kviz &amp; boss</div><div class="ig-dash-tiles">'+
      tile(accTxt,"tačnost")+
      tile((+q.due_count||0),"na redu",((+q.due_count||0)>0?"warnv":""))+
      tile("T"+(+q.tier_active||1),"tier")+
      tile(((q.tags&&q.tags.length)||0),"tema")+
      tile((boss.available?"spreman":"—"),"boss",(boss.available?"violetv":""))+
      '</div>'+
      (acc!=null?('<div class="ig-dash-accbar"><div class="ig-dash-accfill" style="width:'+(acc*100).toFixed(0)+'%"></div></div>'):'');
    // income model (item 4)
    var liveN=igLiveSessionCount(), rate=+eco.rate_per_min||0, per=liveN>0?Math.round(rate/liveN):0;
    var incomeHtml='<div class="ig-dash-sec">Model prihoda</div><div class="ig-dash-income">'+
      '<div class="ig-di-row"><span class="ig-di-v">'+liveN+'</span><span class="ig-di-l">aktivnih sesija doprinosi prihodu</span></div>'+
      '<div class="ig-di-row"><span class="ig-di-v">+'+per+'</span><span class="ig-di-l">/min po sesiji (procena od +'+Math.round(rate)+' ukupno)</span></div>'+
      '<div class="ig-di-note">&#128737; Štit i HP su <b>zajednički</b> za sve sesije — svaka sesija je poseban izvor prihoda, ali odbrana je deljena.</div></div>';
    // lifetime stats
    var statsHtml='<div class="ig-dash-sec">Statistika</div><div class="ig-dash-tiles">'+
      tile(igFmt(st.collects),"skupljanja")+
      tile(igFmt(st.tools_credited),"tool događaja")+
      tile(igFmt(st.sessions_charged),"sesija ukupno")+
      '</div>';
    host.innerHTML=hero+tiles+upgrades+quizHtml+incomeHtml+statsHtml;
  }
  function igRenderToggle(on){
    var b=$("ig-toggle"); if(!b) return;
    b.classList.toggle("off", !on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.innerHTML = (on ? "&#127918; Idle igra: Uklju&#269;ena" : "&#127918; Idle igra: Isklju&#269;ena") +
      '<span class="fpop-game-hint">Overlay &#353;tita &amp; ekonomije preko HUD-a. Ne menja nijednu HUD funkciju.</span>';
  }

  /* ---------- shop (data-driven from state.shop[]; grouped by category) ---------- */
  function igCatIcon(cat){
    cat = (cat||"").toLowerCase();
    if(cat.indexOf("agent") >= 0) return "&#128373;";   // 🕵
    if(cat.indexOf("skill") >= 0) return "&#128295;";   // 🔧
    if(cat.indexOf("tool")  >= 0) return "&#9889;";     // ⚡
    return "&#128722;";                                  // 🛒
  }
  function igRenderShop(shop, tokens){
    igSetText("ig-shop-tok", igFmt(tokens));
    var host=$("ig-shop-body"); if(!host) return;
    var items = shop || [];
    if(!items.length){
      host.innerHTML = '<div class="ig-shop-empty">Prodavnica je prazna &mdash; nema dostupnih nadogradnji.</div>' + igPrestigeCard();
      igWireBuys(host); return;
    }
    var order=[], byCat={};
    items.forEach(function(it){ var c=it.category||"Ostalo"; if(!byCat[c]){ byCat[c]=[]; order.push(c); } byCat[c].push(it); });
    var html = order.map(function(cat){
      var cards = byCat[cat].map(function(it){
        var afford = !!it.affordable, owned = +it.owned||0;
        return '<div class="ig-card">'+
          '<div class="ig-card-top"><span class="ig-card-ico">'+igCatIcon(cat)+'</span>'+
            '<span class="ig-card-title">'+esc(it.name||it.id||"upgrade")+'</span>'+
            '<span class="ig-card-lvl">owned '+owned+'</span></div>'+
          (it.effect_desc?('<div class="ig-card-desc">'+esc(it.effect_desc)+'</div>'):'<div class="ig-card-desc"></div>')+
          '<button class="ig-card-buy" data-buy="'+esc(String(it.id||""))+'"'+(afford?'':' disabled')+'>'+
            'Kupi &middot; '+igFmt(it.price)+' &#129689;</button>'+
        '</div>';
      }).join("");
      return '<div class="ig-shop-cat">'+esc(cat)+'</div><div class="ig-shop-grid">'+cards+'</div>';
    }).join("");
    host.innerHTML = html + igPrestigeCard();
    igWireBuys(host);
  }
  // F2: the prestige card is LIVE — priced in knowledge, enabled on can_prestige, and it
  // calls gamePrestige() behind a confirm (it resets base stats: tokens + upgrades → 0).
  function igPrestigeCard(){
    var pr = (gameView && gameView.prestige) || {};
    var eco = (gameView && gameView.economy) || {};
    var cost = +pr.next_cost || 0, can = !!pr.can_prestige;
    var know = +eco.knowledge || 0, mult = +pr.mult || 1;
    return '<div class="ig-shop-cat">Prestige</div><div class="ig-shop-grid">'+
      '<div class="ig-card prestige">'+
        '<div class="ig-card-top"><span class="ig-card-ico">&#127775;</span>'+
          '<span class="ig-card-title">Prestige &mdash; onboard a new client</span>'+
          '<span class="ig-card-lvl">rang '+(+pr.rank||0)+'</span></div>'+
        '<div class="ig-card-desc">Reset koji banka&#345;i run kao <b>trajni</b> mno&#382;ilac prihoda. '+
          'Bri&#353;e tokene i nadogradnje; &#269;uva lifetime, Level i napredak kviza.</div>'+
        '<div class="ig-card-eff">prihod &times;'+igMult(mult)+' &rarr; &times;ve&#263;e &middot; ko&#353;ta '+igNum(cost)+' &#129504; ('+igNum(know)+' dostupno)</div>'+
        '<button class="ig-card-buy" data-prestige="1"'+(can?'':' disabled')+'>'+
          (can?('Prestige &middot; '+igNum(cost)+' &#129504;'):('Treba '+igNum(cost)+' &#129504; znanja'))+'</button>'+
      '</div></div>';
  }
  function igWireBuys(host){
    Array.prototype.forEach.call(host.querySelectorAll(".ig-card-buy[data-buy]"), function(b){
      b.onclick = function(){ gameBuy(b.getAttribute("data-buy")); };
    });
    var pb = host.querySelector(".ig-card-buy[data-prestige]");
    if(pb) pb.onclick = function(){ gamePrestige(); };
  }

  /* ---------- mutations (POST → gameApply) ---------- */
  function gameCollect(){
    var btn=$("ig-collect"); if(btn) btn.disabled=true;   // optimistic; the response confirms
    aiPost("/api/game/collect", {}).then(function(v){
      if(v && v.error){ igToast(v.error==="already collected today"?"Ve&#263; skupljeno danas":v.error,"no"); if(gameView) gameApply(gameView); return; }
      igToast("Dnevni bonus skupljen","ok"); gameApply(v);
    });
  }
  function gameBuy(id){
    if(!id) return;
    aiPost("/api/game/buy", {id:id}).then(function(v){
      if(v && v.error){ igToast(v.error==="insufficient tokens"?"Nedovoljno tokena":v.error,"no"); return; }
      igToast("Nadogradnja kupljena","ok"); gameApply(v);
    });
  }
  function gameToggle(){
    var next = !gameEnabled;
    igRenderToggle(next);                                  // optimistic label flip
    aiPost("/api/game/toggle", {enabled:next}).then(function(v){
      if(v && !v.error) gameApply(v);
      else { igRenderToggle(gameEnabled); if(v && v.error) igToast(v.error,"no"); }
    });
  }
  // IRREVERSIBLE base-stat reset — behind a confirm (resets tokens + upgrades; keeps
  // lifetime, Level and quiz progress). Returns the whole new state -> gameApply.
  function gamePrestige(){
    var pr = (gameView && gameView.prestige) || {};
    if(!pr.can_prestige){ igToast("Nedovoljno znanja za prestige","no"); return; }
    if(!confirm("Prestige resetuje tokene i sve nadogradnje na nulu.\n\nČuva: lifetime, Level i napredak kviza.\nDobijaš: viši rang + TRAJNO veći množilac prihoda.\n\nNastaviti?")) return;
    aiPost("/api/game/prestige", {}).then(function(v){
      if(v && v.error){ igToast(v.error==="insufficient knowledge"?"Nedovoljno znanja":v.error,"no"); return; }
      igToast("Prestige! Novi rang otključan","ok"); igCelebrate(3); gameApply(v);
    }).catch(function(){ igToast("Greška pri prestige-u","no"); });
  }

  /* ================= F2 — quiz card, boss, tags, spotlight ================= */

  /* ---------- affordances: Kviz-button due count + Boss availability ---------- */
  function igRenderQuizAffordance(state){
    var q = state.quiz || {}, boss = q.boss || {};
    var due = +q.due_count || 0;
    var db=$("ig-quizbtn-due"); if(db){ db.textContent = due>0 ? (due+" na redu") : "kviz"; db.classList.toggle("hot", due>0); }
    var bb=$("ig-bossbtn"); if(bb) bb.classList.toggle("avail", !!boss.available);
  }

  /* ---------- keep the bottom-right button stack clear of the wellness reminder cards ----------
     #focus-cards (focus.js) renders reminder cards bottom-right at z55, over the game buttons.
     Desktop: shift the whole stack LEFT of the reminder column (--ig-btn-shift = its width) so
     the buttons clear BOTH the reminders and the mid-right economy panel. Mobile (panel hidden):
     lift the stack UP over the reminder (--ig-btn-lift = its height). Both 0 when none showing. */
  var igLift = -1, igShift = -1;
  function igReflowButtons(){
    var host=$("focus-cards"), lift=0, shift=0;
    if(host && host.children.length){
      var r=host.getBoundingClientRect();
      if(r.height>0){ lift = Math.round(r.height)+16; shift = Math.round(r.width)+16; }
    }
    var root=document.documentElement.style;
    if(lift!==igLift){ igLift=lift; root.setProperty("--ig-btn-lift", lift+"px"); }
    if(shift!==igShift){ igShift=shift; root.setProperty("--ig-btn-shift", shift+"px"); }
  }

  /* ---------- anti-phone spotlight ----------
     A session is LIVE (rate_per_min>0 => agents are grinding passively) AND the user has
     been idle a while (the F1 presence idea, measured client-side here since state exposes
     no per-client idle flag) => surface the quiz card centre-screen so the idle minute
     teaches instead of feeding a phone. Any interaction dismisses it; it re-arms only after
     a fresh idle stretch. Otherwise the card is a quiet on-demand panel (Kviz button). */
  function igSessionLive(){ return !!(gameView && gameView.economy && (+gameView.economy.rate_per_min||0) > 0); }
  function igUserIdle(){ return (Date.now() - igLastInputAt) >= IG_IDLE_MS; }
  // A question can be surfaced when one is DUE, or when the server says a question is
  // AVAILABLE (due OR any unseen tag-matched) — the latter so a fresh player, whose
  // answered questions are all Leitner-scheduled forward (due_count 0), still gets quizzed.
  function igHasDue(){ var q=(gameView&&gameView.quiz)||{}; return (+q.due_count||0) > 0 || q.available===true; }
  function igSpotlightCheck(){
    if(!(gameEnabled && mode==="hud")) return;
    if(igQuizOpen || igBoss) return;                        // already engaged
    if(Date.now() - igQuizDismissedAt < IG_IDLE_MS) return; // don't re-nag within one idle cycle
    if(igSessionLive() && igUserIdle() && igHasDue()) gameQuizOpen(true);
  }
  function igNoteInput(){ igLastInputAt = Date.now(); }

  /* ---------- quiz card (spotlight OR quiet on-demand) ---------- */
  function gameQuizOpen(spotlight){
    var card=$("ig-quiz"); if(!card) return;
    if(!(gameEnabled && document.body.classList.contains("game-on"))) return;
    igQuizOpen = true; igQuizSpotlit = !!spotlight;
    card.classList.add("open");
    card.classList.toggle("spotlight", igQuizSpotlit);
    var scrim=$("ig-quiz-scrim"); if(scrim) scrim.classList.toggle("on", igQuizSpotlit);
    if(!igQuizQ) gameQuizNext();                            // load a question if none pending
  }
  function gameQuizClose(manual){
    var card=$("ig-quiz"); if(card) card.classList.remove("open","spotlight");
    var scrim=$("ig-quiz-scrim"); if(scrim) scrim.classList.remove("on");
    igQuizOpen = false; igQuizSpotlit = false;
    if(manual) igQuizDismissedAt = Date.now();
  }
  function gameQuizNext(){
    if(igQuizBusy) return;
    igQuizBusy = true; igQuizQ = null;
    igSetHTML("ig-quiz-body", '<div class="ig-quiz-empty">U&#269;itavam pitanje&hellip;</div>');
    fetch("/api/game/quiz/next").then(function(r){ return r.json(); }).then(function(q){
      igQuizBusy = false;
      if(!q || q.error){ igRenderQuizEmpty(q&&q.error, false); return; }
      if(q.empty){ igRenderQuizEmpty(null, true); return; }
      igQuizQ = q; igQuizShownQid = q.qid; igRenderQuizCard(q);
    }).catch(function(){ igQuizBusy=false; igRenderQuizEmpty("mreža", false); });
  }
  function igRenderQuizCard(q){
    igSetText("ig-quiz-badge", "TIER "+(+q.tier||1)+" · BOX "+(+q.box||1));
    var opts = (q.options||[]).map(function(o){
      return '<button class="ig-quiz-opt" data-key="'+esc(String(o.key))+'"><span class="k">'+esc(String(o.key))+
        '</span><span>'+esc(o.text)+'</span></button>';
    }).join("");
    igSetHTML("ig-quiz-body", '<div class="ig-quiz-stem">'+esc(q.stem)+'</div><div class="ig-quiz-opts">'+opts+'</div>');
    Array.prototype.forEach.call(document.querySelectorAll("#ig-quiz-body .ig-quiz-opt"), function(b){
      b.onclick = function(){ gameQuizAnswer(b.getAttribute("data-key")); };
    });
    var rb=$("ig-quiz-retire"); if(rb) rb.disabled=false;
  }
  function igRenderQuizEmpty(err, isEmpty){
    igSetText("ig-quiz-badge", "—"); igQuizQ=null; igQuizShownQid=null;
    var rb=$("ig-quiz-retire"); if(rb) rb.disabled=true;
    var msg = isEmpty
      ? "Banka pitanja je prazna. Postavi teme pa generi&#353;i prvu turu &mdash; sti&#382;e u pozadini."
      : (err ? ("Ne mogu da u&#269;itam pitanje ("+esc(String(err))+").") : "Nema pitanja na redu.");
    igSetHTML("ig-quiz-body", '<div class="ig-quiz-empty">'+msg+
      '<div><button class="ig-quiz-cta" id="ig-quiz-cta" type="button">&#127991; Postavi teme / generi&#353;i</button></div></div>');
    var cta=$("ig-quiz-cta"); if(cta) cta.onclick=function(){ gameTagsOpen(); };
  }
  var IG_WITHHOLD = "Štit se puni samo na tačan odgovor. Netačan ga ne puni — ali ti se ništa ne oduzima. Ovo pitanje se vraća sutra da ga zakucaš.";
  function gameQuizAnswer(choice){
    if(igQuizBusy || !igQuizQ) return;
    var qid = igQuizQ.qid;
    igQuizBusy = true;
    Array.prototype.forEach.call(document.querySelectorAll("#ig-quiz-body .ig-quiz-opt"), function(b){
      b.disabled = true; b.classList.toggle("dim", b.getAttribute("data-key")!==choice);
    });
    aiPost("/api/game/quiz/answer", {qid:qid, choice:choice}).then(function(res){
      igQuizBusy = false;
      if(res && res.error){ igToast(res.error,"no"); return; }
      igQuizQ = null;
      igApplyAnswerToState(res);
      igRenderQuizReveal(res, choice);
    }).catch(function(){ igQuizBusy=false; igToast("Gre&#353;ka pri odgovoru","no"); });
  }
  // fold the answer's shield + knowledge deltas into gameView (partial payload; a poll reconciles)
  function igApplyAnswerToState(res){
    if(!gameView) return;
    if(typeof res.shield === "number"){
      var oldSh = +((gameView.defense||{}).shield) || 0;
      gameView.defense = gameView.defense || {}; gameView.defense.shield = res.shield;
      // Don't ANIMATE a shield DROP right after an answer — a wrong answer never subtracts, but
      // the orthogonal F1 presence-drain can leave the value lower; showing the honest number is
      // fine, animating it sliding DOWN reads as a penalty. Suppress the transition on a drop only.
      var sf=$("ig-sh-fill");
      if(sf && res.shield < oldSh){ sf.style.transition="none"; igRenderBars(gameView.defense); void sf.offsetWidth; sf.style.transition=""; }
      else igRenderBars(gameView.defense);
    }
    if(typeof res.knowledge === "number"){ gameView.economy=gameView.economy||{}; gameView.economy.knowledge=res.knowledge;
      igRenderEcon(gameView); if(igStatsOpenFlag) igRenderStats(gameView); }
    setTimeout(gamePoll, 400);
  }
  function igRenderQuizReveal(res, chosen){
    var correct = !!res.correct, ckey = res.correct_key;
    Array.prototype.forEach.call(document.querySelectorAll("#ig-quiz-body .ig-quiz-opt"), function(b){
      var k=b.getAttribute("data-key"); b.disabled=true; b.classList.remove("dim");
      if(k===ckey) b.classList.add("correct");
      else if(k===chosen) b.classList.add("wrong");
      else b.classList.add("dim");
    });
    var html = '<div class="ig-quiz-reveal">';
    if(correct){
      html += '<div class="ig-quiz-verdict ok">&#10003; Ta&#269;no &mdash; &#353;tit napunjen</div>';
    } else {
      html += '<div class="ig-quiz-verdict miss">Nije ta&#269;no &mdash; ni&#353;ta se ne oduzima</div>';
      html += '<div class="ig-quiz-withhold">'+esc(IG_WITHHOLD)+'</div>';
    }
    if(res.explanation) html += '<div class="ig-quiz-expl"><span class="lab">Obja&#353;njenje</span>'+esc(res.explanation)+'</div>';
    if(!correct && res.chosen_why_wrong)
      html += '<div class="ig-quiz-why"><span class="lab">Za&#353;to tvoj izbor ne valja</span>'+esc(res.chosen_why_wrong)+'</div>';
    var rej = Array.isArray(res.rejections) ? res.rejections : [];
    var rows = "";
    rej.forEach(function(r){
      var k = r && (r.key!=null?r.key:""), why = r && (r.why_wrong||r.why||"");
      if(!why) return;
      if(String(k)===String(ckey)) return;                 // correct key is not a rejection
      if(!correct && String(k)===String(chosen)) return;    // chosen already shown above
      rows += '<div class="row"><span class="rk">'+esc(String(k))+'</span><span>'+esc(why)+'</span></div>';
    });
    if(rows) html += '<div class="ig-quiz-rej">'+rows+'</div>';
    html += '</div>';
    var opts=document.querySelector("#ig-quiz-body .ig-quiz-opts");
    if(opts) opts.insertAdjacentHTML("afterend", html);
    else { var b=$("ig-quiz-body"); if(b) b.insertAdjacentHTML("beforeend", html); }
    if(correct){ igToast("Ta&#269;no! &#352;tit +","ok"); igCvKill(); }
  }
  function gameQuizRetire(){
    var qid = igQuizShownQid;
    if(!qid){ igToast("Nema pitanja za prijavu","no"); return; }
    if(igQuizBusy) return;
    igQuizBusy = true;
    aiPost("/api/game/quiz/retire", {qid:qid}).then(function(v){
      igQuizBusy=false;
      if(v && v.error){ igToast(v.error,"no"); return; }
      igToast("Pitanje uklonjeno","ok"); gameQuizNext();
    }).catch(function(){ igQuizBusy=false; igToast("Gre&#353;ka","no"); });
  }

  /* ---------- boss run (reuses the .ig-modal shell + .ig-quiz-opt option styling) ----------
     CONTRACT NOTE (flag): boss/answer returns the reveal + advance fields; the NEXT question
     must ride on that response (no boss/next route exists). Read it defensively as res.next
     (fallback res.question). Progress is counted LOCALLY (igBoss.answered) so an off-by-one in
     the server's `idx` semantics can never desync the bar. */
  function gameBossStart(){
    if(igBossBusy) return;
    var m=$("ig-boss"); if(!m || !document.body.classList.contains("game-on")) return;
    igBossBusy = true; igBoss = null;
    m.hidden=false;
    igSetHTML("ig-boss-body", '<div class="ig-boss-empty">Sastavljam boss izazov&hellip;</div>');
    igSetHTML("ig-boss-cur", "");
    aiPost("/api/game/boss/start", {}).then(function(res){
      igBossBusy=false;
      if(res && res.error){ igRenderBossNotReady(res.error); return; }
      igBoss = {session_id:res.session_id, total:(+res.total||20), answered:0};
      igRenderBossQ(res.first, 0);
    }).catch(function(){ igBossBusy=false; igRenderBossNotReady("mreža"); });
  }
  function igRenderBossQ(q, pos){
    if(!q){ igToast("Slede&#263;e pitanje nije stiglo","no"); return; }
    var total = (igBoss&&igBoss.total) || (+q.total||20);
    igSetHTML("ig-boss-cur", "<b>"+(pos+1)+"</b> / "+total);
    var opts=(q.options||[]).map(function(o){
      return '<button class="ig-quiz-opt" data-key="'+esc(String(o.key))+'"><span class="k">'+esc(String(o.key))+
        '</span><span>'+esc(o.text)+'</span></button>';
    }).join("");
    var pct = total>0 ? (pos/total*100) : 0;
    igSetHTML("ig-boss-body",
      '<div class="ig-boss-prog"><span class="n">Pitanje '+(pos+1)+'/'+total+'</span>'+
        '<div class="ig-boss-track"><div class="ig-boss-fill" style="width:'+pct.toFixed(0)+'%"></div></div></div>'+
      '<div class="ig-boss-stem">'+esc(q.stem)+'</div><div class="ig-boss-opts">'+opts+'</div>');
    Array.prototype.forEach.call(document.querySelectorAll("#ig-boss-body .ig-quiz-opt"), function(b){
      b.onclick=function(){ gameBossAnswer(b.getAttribute("data-key")); };
    });
  }
  function gameBossAnswer(choice){
    if(igBossBusy || !igBoss) return;
    igBossBusy=true;
    Array.prototype.forEach.call(document.querySelectorAll("#ig-boss-body .ig-quiz-opt"), function(b){
      b.disabled=true; b.classList.toggle("dim", b.getAttribute("data-key")!==choice);
    });
    aiPost("/api/game/boss/answer", {choice:choice}).then(function(res){
      igBossBusy=false;
      if(res && res.error){ igToast(res.error,"no"); return; }
      igRenderBossFeedback(res, choice);
    }).catch(function(){ igBossBusy=false; igToast("Gre&#353;ka","no"); });
  }
  function igRenderBossFeedback(res, chosen){
    if(igBoss) igBoss.answered = (igBoss.answered||0) + 1;
    var correct=!!res.correct, ckey=res.correct_key;
    Array.prototype.forEach.call(document.querySelectorAll("#ig-boss-body .ig-quiz-opt"), function(b){
      var k=b.getAttribute("data-key"); b.disabled=true; b.classList.remove("dim");
      if(k===ckey) b.classList.add("correct");
      else if(k===chosen) b.classList.add("wrong");
      else b.classList.add("dim");
    });
    var total=(igBoss&&igBoss.total)||(+res.total||20), answered=(igBoss&&igBoss.answered)||0;
    var fill=document.querySelector("#ig-boss-body .ig-boss-fill");
    if(fill) fill.style.width=(total>0?(answered/total*100):0).toFixed(0)+"%";
    var html='<div class="ig-boss-fb"><div class="verdict '+(correct?"ok":"miss")+'">'+
      (correct?"&#10003; Ta&#269;no":"&#10007; Neta&#269;no")+'</div>';
    if(res.explanation) html+='<div>'+esc(res.explanation)+'</div>';
    if(!correct && res.chosen_why_wrong)
      html+='<div class="ig-quiz-why"><span class="lab">Za&#353;to</span>'+esc(res.chosen_why_wrong)+'</div>';
    html+='</div>';
    var done = !!res.done;
    html+='<button class="ig-boss-next" id="ig-boss-continue" type="button">'+(done?"Rezultat &rarr;":"Slede&#263;e pitanje &rarr;")+'</button>';
    var opts=document.querySelector("#ig-boss-body .ig-boss-opts");
    if(opts) opts.insertAdjacentHTML("afterend", html);
    else { var b=$("ig-boss-body"); if(b) b.insertAdjacentHTML("beforeend", html); }
    var cont=$("ig-boss-continue");
    if(cont) cont.onclick=function(){
      if(done){ igRenderBossSummary(res); return; }
      var nq = res.next || res.question;
      if(nq) igRenderBossQ(nq, (igBoss&&igBoss.answered)||0);
      else igToast("Slede&#263;e pitanje nije u odgovoru (proveri boss/answer)","no");
    };
  }
  function igRenderBossSummary(res){
    var passed=!!res.passed, total=(+res.total||(igBoss&&igBoss.total)||20), score=(+res.score||0);
    var rewards=res.rewards||{}, rHtml="";
    // Backend key is `shield_refill` (kept a `shield` fallback defensively). Render a chip
    // ONLY when its value is present AND > 0, so a FAIL (rewards 0/absent) shows no chips.
    var shReward = +(rewards.shield_refill!=null ? rewards.shield_refill : rewards.shield) || 0;
    var knReward = +rewards.knowledge || 0;
    if(shReward > 0) rHtml+='<div class="ig-boss-reward"><span class="v">+'+igNum(shReward)+'</span><span class="l">&#353;tit</span></div>';
    if(knReward > 0) rHtml+='<div class="ig-boss-reward"><span class="v">+'+igNum(knReward)+'</span><span class="l">znanje</span></div>';
    igSetHTML("ig-boss-body",
      '<div class="ig-boss-summary">'+
        '<div class="ig-boss-verdict-big '+(passed?"pass":"fail")+'">'+(passed?"POBEDA":"Boss izdr&#382;ao")+'</div>'+
        '<div class="ig-boss-score">'+score+' / '+total+' ta&#269;no</div>'+
        (passed
          ? '<div class="ig-boss-forgive">&#352;tit dopunjen i znanje upisano. Bravo.</div>'
          : '<div class="ig-boss-forgive">Pad ne poni&#353;tava napredak &mdash; ni&#353;ta se ne oduzima. Proma&#353;ena pitanja se vra&#263;aju u box 1 da ih usavr&#353;i&#353;; probaj ponovo kad budu spremna.</div>')+
        (rHtml?('<div class="ig-boss-rewards">'+rHtml+'</div>'):'')+
        '<button class="ig-boss-done" id="ig-boss-done" type="button">Zatvori</button>'+
      '</div>');
    igSetHTML("ig-boss-cur", "");
    var db=$("ig-boss-done"); if(db) db.onclick=function(){ gameBossClose(); };
    if(passed){ igCelebrate(4); igToast("Boss pobe&#273;en!","ok"); }
    igBoss=null;
    setTimeout(gamePoll, 400);
  }
  function igRenderBossNotReady(err){
    igBoss=null;
    var ready = (err==="boss not ready" || /409/.test(String(err)));
    var msg = ready
      ? "Boss jo&#353; nije spreman. Sprema se dok agenti aktivno rade &mdash; vrati se malo kasnije."
      : ("Ne mogu da pokrenem boss ("+esc(String(err||"?"))+").");
    igSetHTML("ig-boss-body", '<div class="ig-boss-empty">'+msg+'</div>');
    igSetHTML("ig-boss-cur", "");
  }
  // Closing the boss modal EARLY (X / Esc / backdrop / view-switch) while a run is still
  // active tells the server to drop the session (POST /boss/cancel), else the boss soft-locks
  // (session stays open -> never available again). A COMPLETED run cleared igBoss in the
  // summary already, so this no-ops on a normal finish (never cancels a legitimate win/loss).
  function gameBossClose(){
    if(igBoss){ aiPost("/api/game/boss/cancel", {}).catch(function(){}); igBoss=null; }
    var m=$("ig-boss"); if(m) m.hidden=true;
  }

  /* ---------- tags settings (reuses .ig-modal; native <input>, no dropdown) ---------- */
  function gameTagsOpen(){
    var m=$("ig-tags"); if(!m || !document.body.classList.contains("game-on")) return;
    igTagsWork = (igTagsKnown||[]).slice();
    m.hidden=false; igRenderTags("", false);
  }
  function gameTagsClose(){ var m=$("ig-tags"); if(m) m.hidden=true; }
  function igRenderTags(note, noteOk){
    var body=$("ig-tags-body"); if(!body) return;
    var sel = igTagsWork||[];
    var pal = IG_TAG_PALETTE.slice();
    sel.forEach(function(t){ if(pal.indexOf(t)<0) pal.push(t); });   // keep custom picks visible
    var chips = pal.map(function(t){
      var on = sel.indexOf(t)>=0;
      return '<button class="ig-tag'+(on?" on":"")+'" data-tag="'+esc(t)+'">'+esc(t)+(on?' <span class="x">&#10005;</span>':'')+'</button>';
    }).join("");
    var selLine = sel.length ? esc(sel.join(", ")) : '<span class="ig-tags-empty">sve teme (bez filtera)</span>';
    igSetHTML("ig-tags-body",
      '<div class="ig-tags-intro">Izaberi teme za kviz. Prazan izbor = sve teme. Snimi da primeni&#353;; generisanje tra&#382;i novu turu pitanja u pozadini.</div>'+
      '<div class="ig-tags-sech">Izabrano</div><div class="ig-tags-intro">'+selLine+'</div>'+
      '<div class="ig-tags-sech">Ponuda</div><div class="ig-tags-pal">'+chips+'</div>'+
      '<div class="ig-tags-sech">Dodaj svoju temu</div>'+
      '<div class="ig-tags-add"><input class="ig-tags-in" id="ig-tags-in" type="text" placeholder="npr. kubernetes" maxlength="40" autocomplete="off">'+
        '<button class="ig-tags-addbtn" id="ig-tags-addbtn" type="button">Dodaj</button></div>'+
      '<div class="ig-tags-acts">'+
        '<button class="ig-tags-gen" id="ig-tags-gen" type="button">&#9881; Generi&#353;i jo&#353;</button>'+
        '<button class="ig-tags-save" id="ig-tags-save" type="button">Snimi teme</button></div>'+
      '<div class="ig-tags-note'+(noteOk?" ok":"")+'">'+(note?esc(note):"")+'</div>');
    Array.prototype.forEach.call(document.querySelectorAll("#ig-tags-body .ig-tag[data-tag]"), function(b){
      b.onclick=function(){ gameTagsToggle(b.getAttribute("data-tag")); };
    });
    var ab=$("ig-tags-addbtn"); if(ab) ab.onclick=function(){ gameTagsAddFromInput(); };
    var inp=$("ig-tags-in"); if(inp) inp.onkeydown=function(e){ if(e.key==="Enter"){ e.preventDefault(); gameTagsAddFromInput(); } };
    var gb=$("ig-tags-gen"); if(gb) gb.onclick=function(){ gameGenerate(); };
    var sb=$("ig-tags-save"); if(sb) sb.onclick=function(){ gameTagsSave(); };
  }
  function gameTagsToggle(t){
    var i=igTagsWork.indexOf(t);
    if(i>=0) igTagsWork.splice(i,1); else igTagsWork.push(t);
    igRenderTags("", false);
  }
  function gameTagsAddFromInput(){
    var inp=$("ig-tags-in"); if(!inp) return;
    var t=(inp.value||"").trim().toLowerCase().replace(/\s+/g,"-").slice(0,40);
    if(!t) return;
    if(igTagsWork.indexOf(t)<0) igTagsWork.push(t);
    inp.value=""; igRenderTags("", false);
  }
  function gameTagsSave(){
    aiPost("/api/game/quiz/tags", {tags:igTagsWork}).then(function(v){
      if(v && v.error){ igRenderTags("Greška: "+v.error, false); return; }
      if(v && Array.isArray(v.tags)){ igTagsKnown=v.tags.slice(); igTagsWork=v.tags.slice(); }
      else igTagsKnown=igTagsWork.slice();
      if(gameView){ gameView.quiz=gameView.quiz||{}; gameView.quiz.tags=igTagsKnown.slice(); }
      igRenderTags("Teme snimljene. Generišem novu turu u pozadini…", true);
      setTimeout(gamePoll, 500);
    }).catch(function(){ igRenderTags("Greška pri snimanju tema", false); });
  }
  function gameGenerate(){
    var gb=$("ig-tags-gen"); if(gb) gb.disabled=true;
    var body = {tags:igTagsWork};
    var tier = (gameView&&gameView.quiz&&+gameView.quiz.tier_active)||0;
    if(tier>0) body.tier=tier;
    aiPost("/api/game/quiz/generate", body).then(function(v){
      if(gb) gb.disabled=false;
      if(v && v.error){ igRenderTags("Greška: "+v.error, false); return; }
      igRenderTags("Generisanje pokrenuto — pitanja stižu u pozadini.", true);
    }).catch(function(){ if(gb) gb.disabled=false; igRenderTags("Greška pri generisanju", false); });
  }

  /* ---------- game-side celebration (canvas enemy clears; reduced-motion draws static) ---------- */
  function igCelebrate(n){ n=n||2; for(var i=0;i<n;i++) igCvKill(); }

  /* ---------- SSE state push (F3) ----------
     core.js dispatches every {type:"game",state} broadcast here (mirrors onGameTool /
     applyTestRun). The server pushes a full state view — same shape as GET /api/game/state —
     on each mutation, so this is now the LIVE channel and gamePoll is only a ~25s fallback.
     Guard: ignore a push carrying no state (never crash the SSE handler). */
  function onGameState(msg){ if(!msg || !msg.state) return; gameApply(msg.state); }

  /* ---------- SSE tool hook (loud, game-side) ---------- */
  // Dispatched from core.js on every {type:"tool"} event. No-ops when the overlay is
  // off so the HUD stays byte-identical; when on, adds a kill-feed row + an OPTIMISTIC
  // display bump (reconciled by the next poll) + a cosmetic enemy clear on the canvas.
  function onGameTool(msg){
    if(!gameEnabled || mode !== "hud") return;
    var tool = (msg && msg.tool) || "tool";
    // A real tool event is a GAME-SIDE enemy clear ONLY — kill-feed row + canvas burst. It
    // must NOT touch the economy counter: coupling an itemised token gain (or a flash) to a
    // specific real work action is the overjustification trigger the design forbids (§1
    // two-channels). The economy rises ambiently from the server rate (gameTick) and
    // reconciles on the next poll — never per work action.
    igPushKill(tool, Math.random() < 0.18);
    igCvKill();
  }
  function igPushKill(tool, crit){
    // GAME-SIDE flourish for a cleared enemy (a tool fired). No token amount: an itemised
    // "+N" per real action would read as income-for-that-work (overjustification / surrogation
    // — it teaches "edits pay more"). The economy number stays decoupled and ambient.
    var feed=$("ig-killfeed"); if(!feed) return;
    var d=document.createElement("div"); d.className="ig-kill"+(crit?" crit":"");
    d.innerHTML='<span class="ig-kill-tool">'+esc(tool)+'</span><span class="ig-kill-star">&#10022;</span>';
    feed.appendChild(d);
    while(feed.children.length>5) feed.removeChild(feed.firstChild);
    setTimeout(function(){ if(d.parentNode) d.parentNode.removeChild(d); }, 3400);
  }

  /* ---------- toast ---------- */
  var igToastT=null;
  function igToast(msg, kind){
    var el=$("ig-toast"); if(!el) return;
    el.innerHTML=msg; el.className="ig-toast show "+(kind||"");
    clearTimeout(igToastT); igToastT=setTimeout(function(){ el.className="ig-toast"; }, 2600);
  }

  /* ---------- visibility: game-on class + canvas lifecycle ---------- */
  function gameReconcileVisible(){
    var on = gameEnabled && mode === "hud";
    document.body.classList.toggle("game-on", on);
    if(on && !igWasVisible){ igCvStart(); }
    else if(!on && igWasVisible){ igCvStop(); igCloseModals(); igStatsToggle(false); }
    igWasVisible = on;
  }
  function igCloseModals(){
    ["ig-shop","ig-tags","ig-dash"].forEach(function(id){ var m=$(id); if(m) m.hidden=true; });
    gameBossClose();                 // cancels an active run, then hides #ig-boss
    gameQuizClose(false);
  }

  /* ================= shield + enemy canvas (#ig-canvas) =================
     Purely visual: the shield-charge arc reflects server defense (shield/shield_max);
     enemies never change the real shield (F1 has no damage source). rAF is gated to
     the visible HUD, and reduced-motion draws one static frame instead of looping. */
  var igCv=null, igCtx=null, igW=0, igH=0, igDPR=Math.min(2, window.devicePixelRatio||1);
  var igCX=0, igCY=0, igR=0, igRAF=0, igT0=0;
  var igRM = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion:reduce)").matches);
  var IG_COL = {cy:"#38e6ff", cy2:"#0ea5e9", violet:"#a78bfa", warn:"#fbbf24", bad:"#fb7185", dim:"#37506a"};
  var IG_ENEMY_TYPES = [
    {c:IG_COL.bad,    shape:"diamond"},
    {c:IG_COL.warn,   shape:"triangle"},
    {c:IG_COL.violet, shape:"square"},
    {c:IG_COL.cy,     shape:"chevron"}
  ];
  var igEnemies=[], igParts=[], igSparks=[];
  /* F3 cosmetic combat: agent/tool nodes fire homing bolts at jammed enemies; the CLAUDE core
     emits an occasional blast wave that pushes them back. PURELY visual — never touches the real
     shield/HP (defense stays presence-driven and the combat always visually "wins"). Capped for
     perf; reduced-motion draws a static frame (igCvDrawStatic) with NO bolts/waves. */
  var igShots=[], igWaves=[], igWaveNextAt=0, igFireAcc=0, igLastNow=0;
  var IG_MAX_SHOTS=20, IG_MAX_PARTS=170, IG_MAX_SPARKS=120;
  var IG_SHOT_SPD=520;               // bolt speed, px/sec (CSS-pixel space)
  var IG_WAVE_SPD=560;               // blast-wave front expansion, px/sec
  function igReactorRadius(){
    var el=document.querySelector(".reactor"); if(!el) return Math.min(igW,igH)*0.22;
    var r=el.getBoundingClientRect(); return Math.min(r.width,r.height)/2 || Math.min(igW,igH)*0.22;
  }
  function igShieldFrac(){
    var d=(gameView&&gameView.defense)||{}, m=+d.shield_max||100;
    return m>0 ? Math.max(0,Math.min(1,(+d.shield||0)/m)) : 0;
  }
  function igCvResize(){
    if(!igCv) return;
    igW=window.innerWidth; igH=window.innerHeight;
    igCv.width=igW*igDPR; igCv.height=igH*igDPR; igCv.style.width=igW+"px"; igCv.style.height=igH+"px";
    igCtx.setTransform(igDPR,0,0,igDPR,0,0);
    igCX=igW/2; igCY=igH/2; igR=igReactorRadius();
  }
  function igSpawnEnemy(ang){
    var t=IG_ENEMY_TYPES[Math.floor(Math.random()*IG_ENEMY_TYPES.length)];
    var a=(ang==null)?Math.random()*Math.PI*2:ang;
    return {ang:a, dist:igR+60+Math.random()*(Math.hypot(igW/2,igH/2)-igR), park:igR+12+Math.random()*10,
      spd:0.5+Math.random()*0.9, size:6+Math.random()*4, rot:Math.random()*Math.PI, rotv:(Math.random()-0.5)*0.04,
      ph:Math.random()*Math.PI*2, drift:(Math.random()-0.5)*0.0016, type:t, jam:false};
  }
  function igBuildSwarm(n){ igEnemies=[]; igShots=[]; igWaves=[]; igWaveNextAt=0; igFireAcc=0; for(var i=0;i<n;i++) igEnemies.push(igSpawnEnemy((i/n)*Math.PI*2+Math.random()*0.15)); }
  function igBurst(x,y,c){ if(igParts.length>IG_MAX_PARTS) return; for(var i=0;i<10;i++){ var a=Math.random()*Math.PI*2,s=0.8+Math.random()*2.2; igParts.push({x:x,y:y,vx:Math.cos(a)*s,vy:Math.sin(a)*s,life:1,c:c}); } }
  /* Firing points = the REAL agent nodes sessions.js draws on #graph (z5, just below this
     canvas z6), read via its global nodesForDraw() — so a bolt looks like the agent itself
     firing WITHOUT redrawing #graph. Empty when no session/agents (the core fires instead). */
  function igTurrets(){
    var out=[];
    try{
      if(typeof nodesForDraw==="function" && mode==="hud"){
        var ns=nodesForDraw();
        for(var i=0;i<ns.length;i++){ var nd=ns[i];
          if(!isFinite(nd.x)||!isFinite(nd.y)) continue;
          out.push({x:nd.x, y:nd.y, c:(nd.col||IG_COL.cy), active:!!(nd.live&&(nd.live.state==="run"||nd.live.state==="ask"))});
        }
      }
    }catch(e){}
    return out;
  }
  function igFireShot(turrets, jammed){
    var src;
    if(turrets.length){ var act=turrets.filter(function(t){return t.active;}); var pool=act.length?act:turrets; src=pool[Math.floor(Math.random()*pool.length)]; }
    else src={x:igCX, y:igCY, c:IG_COL.cy};                 // no agents -> the CLAUDE core fires
    var tgt=jammed[Math.floor(Math.random()*jammed.length)];
    igShots.push({x:src.x, y:src.y, px:src.x, py:src.y, c:src.c, target:tgt,
      aimX:igCX+Math.cos(tgt.ang)*tgt.dist, aimY:igCY+Math.sin(tgt.ang)*tgt.dist, life:1.6});
  }
  function igCvKill(){
    if(!igEnemies.length) return;
    var jammed=igEnemies.filter(function(e){return e.jam;});
    var pool=jammed.length?jammed:igEnemies;
    var e=pool[Math.floor(Math.random()*pool.length)];
    igBurst(igCX+Math.cos(e.ang)*e.dist, igCY+Math.sin(e.ang)*e.dist, e.type.c);
    var idx=igEnemies.indexOf(e); if(idx>=0) igEnemies[idx]=igSpawnEnemy();
    if(igRM) igCvDrawStatic();
  }
  function igDrawShape(e,x,y){
    igCtx.save(); igCtx.translate(x,y); igCtx.rotate(e.rot);
    igCtx.strokeStyle=e.type.c; igCtx.lineWidth=1.4; igCtx.shadowColor=e.type.c; igCtx.shadowBlur=e.jam?10:5;
    igCtx.fillStyle="rgba(8,15,24,.55)"; var s=e.size; igCtx.beginPath();
    switch(e.type.shape){
      case "diamond": igCtx.moveTo(0,-s);igCtx.lineTo(s,0);igCtx.lineTo(0,s);igCtx.lineTo(-s,0);igCtx.closePath(); break;
      case "triangle": igCtx.moveTo(0,-s);igCtx.lineTo(s*0.9,s*0.7);igCtx.lineTo(-s*0.9,s*0.7);igCtx.closePath(); break;
      case "square": igCtx.rect(-s*0.8,-s*0.8,s*1.6,s*1.6); break;
      default: igCtx.moveTo(-s,-s*0.6);igCtx.lineTo(0,0);igCtx.lineTo(-s,s*0.6);igCtx.moveTo(0,-s*0.6);igCtx.lineTo(s,0);igCtx.lineTo(0,s*0.6);
    }
    if(e.type.shape!=="chevron") igCtx.fill();
    igCtx.stroke(); igCtx.restore();
  }
  function igDrawRing(){
    igCtx.save();
    igCtx.strokeStyle="rgba(56,230,255,.16)"; igCtx.lineWidth=1;
    igCtx.beginPath(); igCtx.arc(igCX,igCY,igR,0,Math.PI*2); igCtx.stroke();
    igCtx.setLineDash([4,10]); igCtx.strokeStyle="rgba(167,139,250,.16)";
    igCtx.beginPath(); igCtx.arc(igCX,igCY,igR-10,0,Math.PI*2); igCtx.stroke(); igCtx.setLineDash([]);
    var frac=igShieldFrac();
    igCtx.strokeStyle=IG_COL.cy; igCtx.lineWidth=3; igCtx.lineCap="round"; igCtx.shadowColor=IG_COL.cy; igCtx.shadowBlur=14;
    igCtx.beginPath(); igCtx.arc(igCX,igCY,igR,-Math.PI/2,-Math.PI/2+frac*Math.PI*2); igCtx.stroke();
    igCtx.shadowBlur=0; igCtx.restore();
  }
  // ---- blast wave: schedule + expand + push enemies the front overtakes + draw the ring ----
  function igUpdateWaves(now,dt){
    if(!igWaveNextAt) igWaveNextAt = now + (15000 + Math.random()*10000);       // first wave 15-25s out
    if(now >= igWaveNextAt && igWaves.length < 2){
      igWaves.push({r:igR*0.6, maxR:Math.hypot(igW/2,igH/2)+40, t:0});
      igWaveNextAt = now + (15000 + Math.random()*10000);
    }
    for(var wi=igWaves.length-1; wi>=0; wi--){
      var wv=igWaves[wi], prevR=wv.r; wv.r += IG_WAVE_SPD*dt; wv.t += dt;
      for(var ei=0; ei<igEnemies.length; ei++){ var en=igEnemies[ei];
        if(en.dist>=prevR && en.dist<=wv.r && en.dist>igR*0.5){        // front overtakes it -> push out, un-jam
          en.dist = wv.r + 30 + Math.random()*40; en.jam=false;
          if(Math.random()<0.5) igBurst(igCX+Math.cos(en.ang)*en.dist, igCY+Math.sin(en.ang)*en.dist, IG_COL.cy);
        }
      }
      var a=Math.max(0, 0.5*(1 - wv.r/wv.maxR));
      igCtx.save();
      if(wv.t<0.5){ var f=1-wv.t/0.5; igCtx.globalAlpha=f*0.5; igCtx.fillStyle=IG_COL.cy; igCtx.shadowColor=IG_COL.cy; igCtx.shadowBlur=30;
        igCtx.beginPath(); igCtx.arc(igCX,igCY, igR*0.4*(0.6+0.4*f),0,Math.PI*2); igCtx.fill(); }   // core launch flash
      igCtx.globalAlpha=a; igCtx.strokeStyle=IG_COL.cy; igCtx.lineWidth=3; igCtx.shadowColor=IG_COL.cy; igCtx.shadowBlur=16;
      igCtx.beginPath(); igCtx.arc(igCX,igCY,wv.r,0,Math.PI*2); igCtx.stroke();
      igCtx.globalAlpha=a*0.5; igCtx.lineWidth=1.4;
      igCtx.beginPath(); igCtx.arc(igCX,igCY,Math.max(0,wv.r-14),0,Math.PI*2); igCtx.stroke();
      igCtx.restore();
      if(wv.r>=wv.maxR) igWaves.splice(wi,1);
    }
  }
  // ---- projectiles: fire from agent turrets at jammed enemies, home in, burst + respawn on hit ----
  function igUpdateShots(now,dt){
    var turrets=igTurrets(), jammed=[];
    for(var i=0;i<igEnemies.length;i++){ if(igEnemies[i].jam) jammed.push(igEnemies[i]); }
    if(jammed.length){
      igFireAcc += dt;
      var fireEvery = 0.30 / Math.min(6, (turrets.length||1)), guard=0;   // more agents => denser fire
      while(igFireAcc>=fireEvery && igShots.length<IG_MAX_SHOTS && guard<6){ igFireAcc-=fireEvery; guard++; igFireShot(turrets, jammed); }
    } else igFireAcc=0;
    for(var si=igShots.length-1; si>=0; si--){
      var sh=igShots[si], alive=igEnemies.indexOf(sh.target)>=0, ex,ey;
      if(alive){ ex=igCX+Math.cos(sh.target.ang)*sh.target.dist; ey=igCY+Math.sin(sh.target.ang)*sh.target.dist; sh.aimX=ex; sh.aimY=ey; }
      else { ex=sh.aimX; ey=sh.aimY; }
      var dx=ex-sh.x, dy=ey-sh.y, d=Math.hypot(dx,dy)||1, step=IG_SHOT_SPD*dt;
      var hitR = alive ? Math.max(sh.target.size+7, 6) : 6;
      if(d<=Math.max(hitR,step)){                                  // impact -> clear + respawn (reuses igBurst/igSpawnEnemy)
        igBurst(ex,ey, alive?sh.target.type.c:sh.c);
        if(alive){ var idx=igEnemies.indexOf(sh.target); if(idx>=0) igEnemies[idx]=igSpawnEnemy(); }
        igShots.splice(si,1); continue;
      }
      sh.px=sh.x; sh.py=sh.y; sh.x+=dx/d*step; sh.y+=dy/d*step; sh.life-=dt;
      if(sh.life<=0){ igShots.splice(si,1); continue; }
      igCtx.save(); igCtx.strokeStyle=sh.c; igCtx.lineWidth=2; igCtx.lineCap="round"; igCtx.shadowColor=sh.c; igCtx.shadowBlur=8; igCtx.globalAlpha=.9;
      igCtx.beginPath(); igCtx.moveTo(sh.px,sh.py); igCtx.lineTo(sh.x,sh.y); igCtx.stroke();
      igCtx.globalAlpha=1; igCtx.fillStyle="#eaffff"; igCtx.shadowBlur=10;
      igCtx.beginPath(); igCtx.arc(sh.x,sh.y,2.1,0,Math.PI*2); igCtx.fill(); igCtx.restore();
    }
  }
  function igCvFrame(now){
    if(!(gameEnabled && mode==="hud")){ igRAF=0; if(igCtx) igCtx.clearRect(0,0,igW,igH); return; }
    var t=(now-igT0)/1000, dt=igLastNow?Math.min(0.05,(now-igLastNow)/1000):0.016; igLastNow=now;
    igCtx.clearRect(0,0,igW,igH); igDrawRing();
    igUpdateWaves(now,dt);                                          // under the enemies; may push some outward
    for(var i=0;i<igEnemies.length;i++){
      var e=igEnemies[i];
      if(e.dist>e.park){ e.dist-=e.spd; e.jam=false; }
      else { e.jam=true; e.dist=e.park+Math.sin(t*3+e.ph)*3; e.ang+=e.drift;
        if(igSparks.length<IG_MAX_SPARKS && Math.random()<0.006){ igSparks.push({x:igCX+Math.cos(e.ang)*igR,y:igCY+Math.sin(e.ang)*igR,life:1,c:e.type.c}); } }
      e.rot+=e.rotv;
      var x=igCX+Math.cos(e.ang)*e.dist, y=igCY+Math.sin(e.ang)*e.dist;
      if(e.jam){ igCtx.save(); igCtx.strokeStyle="rgba(56,230,255,.06)"; igCtx.lineWidth=1;
        igCtx.beginPath(); igCtx.moveTo(x,y); igCtx.lineTo(igCX+Math.cos(e.ang)*(igR-4),igCY+Math.sin(e.ang)*(igR-4)); igCtx.stroke(); igCtx.restore(); }
      igDrawShape(e,x,y);
    }
    igUpdateShots(now,dt);                                          // bolts on top of the enemies
    for(var s=igSparks.length-1;s>=0;s--){ var sp=igSparks[s]; sp.life-=0.05;
      if(sp.life<=0){ igSparks.splice(s,1); continue; }
      igCtx.save(); igCtx.globalAlpha=sp.life; igCtx.fillStyle=sp.c; igCtx.shadowColor=sp.c; igCtx.shadowBlur=8;
      igCtx.beginPath(); igCtx.arc(sp.x,sp.y,2+(1-sp.life)*4,0,Math.PI*2); igCtx.fill(); igCtx.restore(); }
    for(var p=igParts.length-1;p>=0;p--){ var pt=igParts[p]; pt.x+=pt.vx; pt.y+=pt.vy; pt.vx*=0.94; pt.vy*=0.94; pt.life-=0.035;
      if(pt.life<=0){ igParts.splice(p,1); continue; }
      igCtx.save(); igCtx.globalAlpha=Math.max(0,pt.life); igCtx.fillStyle=pt.c; igCtx.shadowColor=pt.c; igCtx.shadowBlur=6;
      igCtx.beginPath(); igCtx.arc(pt.x,pt.y,2,0,Math.PI*2); igCtx.fill(); igCtx.restore(); }
    igRAF=requestAnimationFrame(igCvFrame);
  }
  function igCvDrawStatic(){
    if(!igCtx) return; igCtx.clearRect(0,0,igW,igH); igDrawRing();
    for(var i=0;i<igEnemies.length;i++){ var e=igEnemies[i]; e.dist=e.park; e.jam=true;
      igDrawShape(e, igCX+Math.cos(e.ang)*e.dist, igCY+Math.sin(e.ang)*e.dist); }
  }
  function igCvEnsure(){
    if(igCv) return true;
    igCv=$("ig-canvas"); if(!igCv) return false;
    igCtx=igCv.getContext("2d"); igCvResize(); igBuildSwarm(10);   // fewer enemies on screen (was 20; user: too many)
    return true;
  }
  function igCvStart(){
    if(!igCvEnsure()) return;
    igCvResize();
    if(igRM){ igCvDrawStatic(); return; }                          // reduced motion: static frame, NO bolts/waves
    igT0=performance.now(); igLastNow=0; if(!igRAF) igRAF=requestAnimationFrame(igCvFrame);
  }
  function igCvStop(){
    if(igRAF){ cancelAnimationFrame(igRAF); igRAF=0; }
    igShots=[]; igWaves=[]; igWaveNextAt=0; igLastNow=0;           // don't resume mid-flight after a view switch
    if(igCtx) igCtx.clearRect(0,0,igW,igH);
  }

  /* ---------- Stats popover (opens above the Stats button) ---------- */
  function igStatsToggle(force){
    var pop=$("ig-stats-pop"), btn=$("ig-statsbtn"); if(!pop) return;
    var open = (force===undefined) ? pop.hidden : force;
    pop.hidden = !open; igStatsOpenFlag = open;
    if(btn) btn.setAttribute("aria-expanded", open?"true":"false");
    if(open && gameView) igRenderStats(gameView);
  }

  /* ---------- init (called from boot.js) ---------- */
  function gameInit(){
    // fpop enable/disable toggle
    var tg=$("ig-toggle"); if(tg) tg.addEventListener("click", function(e){ e.stopPropagation(); gameToggle(); });
    // fpop game-settings entry (quiz topics). Only visible while body.game-on (CSS), so game-on is
    // guaranteed here; close the timer popover first so the tags modal isn't stacked behind it.
    var tgset=$("ig-tagsbtn"); if(tgset) tgset.addEventListener("click", function(e){
      e.stopPropagation();
      if(typeof focusClosePopover === "function") focusClosePopover();
      gameTagsOpen();
    });
    // Collect
    var col=$("ig-collect"); if(col) col.addEventListener("click", function(){ if(gameEnabled) gameCollect(); });
    // Shop modal open/close (generic close: any [data-close] shuts the ig-<value> modal)
    var sb=$("ig-shopbtn"); if(sb) sb.addEventListener("click", function(){ var m=$("ig-shop"); if(m && document.body.classList.contains("game-on")) m.hidden=false; });
    // Game Dashboard modal open (mirrors the Shop gate); render immediately from the last state
    var dbn=$("ig-dashbtn"); if(dbn) dbn.addEventListener("click", function(){ var m=$("ig-dash"); if(m && document.body.classList.contains("game-on")){ m.hidden=false; if(gameView) igRenderDash(gameView); } });
    document.addEventListener("click", function(ev){
      var c=ev.target && ev.target.closest ? ev.target.closest("[data-close]") : null;
      if(!c) return;
      var which=c.getAttribute("data-close");
      if(which==="boss"){ gameBossClose(); return; }      // cancels an active run first
      var m=$("ig-"+which); if(m) m.hidden=true;
    });
    // F2: Kviz (on-demand card) + Boss (run) floating buttons
    var qb=$("ig-quizbtn"); if(qb) qb.addEventListener("click", function(){ gameQuizOpen(false); });
    var bb=$("ig-bossbtn"); if(bb) bb.addEventListener("click", function(){ gameBossStart(); });
    // F2: quiz-card foot controls + minimise + spotlight scrim
    var qmin=$("ig-quiz-min"); if(qmin) qmin.addEventListener("click", function(){ gameQuizClose(true); });
    var qret=$("ig-quiz-retire"); if(qret) qret.addEventListener("click", function(){ gameQuizRetire(); });
    var qtag=$("ig-quiz-tags"); if(qtag) qtag.addEventListener("click", function(){ gameTagsOpen(); });
    var qnx=$("ig-quiz-next"); if(qnx) qnx.addEventListener("click", function(){ gameQuizNext(); });
    var scr=$("ig-quiz-scrim"); if(scr) scr.addEventListener("click", function(){ gameQuizClose(true); });
    // Stats popover
    var stb=$("ig-statsbtn"); if(stb) stb.addEventListener("click", function(e){ e.stopPropagation(); if(document.body.classList.contains("game-on")) igStatsToggle(); });
    document.addEventListener("click", function(e){
      var pop=$("ig-stats-pop"); if(!pop || pop.hidden) return;
      if(pop.contains(e.target) || e.target===$("ig-statsbtn")) return;
      igStatsToggle(false);
    });
    document.addEventListener("keydown", function(e){
      if(e.key!=="Escape") return;
      var boss=$("ig-boss"); if(boss && !boss.hidden){ gameBossClose(); return; }
      var tags=$("ig-tags"); if(tags && !tags.hidden){ tags.hidden=true; return; }
      var m=$("ig-shop"); if(m && !m.hidden){ m.hidden=true; return; }
      var dm=$("ig-dash"); if(dm && !dm.hidden){ dm.hidden=true; return; }
      if(igQuizOpen){ gameQuizClose(true); return; }
      igStatsToggle(false);
    });
    // F2: client presence signal for the anti-phone spotlight — reset the idle clock on real input
    ["pointerdown","pointermove","keydown","wheel","touchstart"].forEach(function(ev){
      window.addEventListener(ev, igNoteInput, {passive:true});
    });
    // F2: react instantly when a wellness reminder card is added/removed so the button stack
    // lifts before it can overlap (the 1s tick is only a fallback for in-card height changes).
    var fc=$("focus-cards");
    if(fc && window.MutationObserver){ new MutationObserver(igReflowButtons).observe(fc, {childList:true, subtree:true}); }
    igReflowButtons();
    // F2: setMode() wipes body.className on every view switch. Close the game modals the INSTANT
    // the view/enable state stops being (v-hud + game-on) — don't wait for the 1s tick. This both
    // kills the flash on the new view (belt to the CSS double-gate's suspenders) and prevents a
    // stale modal reshowing on a fast switch BACK to the HUD. Idempotent, so it never double-cancels.
    if(window.MutationObserver){
      new MutationObserver(function(){
        if(!(document.body.classList.contains("v-hud") && document.body.classList.contains("game-on"))) igCloseModals();
      }).observe(document.body, {attributes:true, attributeFilter:["class"]});
    }
    // canvas responds to window/tab changes only while visible
    window.addEventListener("resize", function(){ if(!igCv) return; igCvResize(); if(igRM && igWasVisible) igCvDrawStatic(); });
    document.addEventListener("visibilitychange", function(){
      if(document.hidden){ if(igRAF){ cancelAnimationFrame(igRAF); igRAF=0; } }
      else if(gameEnabled && mode==="hud") igCvStart();
    });

    gamePoll();                              // seed once
    setInterval(gamePoll, GAME_POLL_MS);     // ~5s poll (gated to the HUD)
    setInterval(gameTick, 1000);             // 1s cosmetic tick + visibility reconcile
  }
