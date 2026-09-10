  /* ---------- SETUP GATE: a tab that is not configured says what to do ----------
   *
   * Without this, someone who clones the repo opens the HUD and finds an empty
   * Tiketi tab. Empty is the worst possible answer: it looks identical to
   * broken, so the reader either files a bug or gives up, and in both cases
   * never learns that the only thing missing was the address of their helpdesk.
   *
   * Two states, two different affordances, and the difference is deliberate:
   *
   *   not_configured  -> the LINE to paste into Claude. Not a form: deciding
   *                      which helpdesk you have, where its API lives and which
   *                      adapter fits is a conversation, and the thing that can
   *                      hold that conversation is the agent.
   *   needs_secret    -> an INPUT. The reader already has the token in their
   *                      clipboard; sending them to a conversation to be told
   *                      "paste your token" spends a turn to arrive at the box
   *                      we could have drawn.
   *
   * The value goes to a gitignored file through /api/capabilities/secret, which
   * is loopback-only and refuses to write into a tracked path.
   */
  var CAPS = null;                    // last snapshot from /api/capabilities
  var CAP_FOR = {tickets: "tickets", mail: "mail", prod: "monitor", git: "git"};

  function capsFetch(then){
    fetch("/api/capabilities").then(function(r){return r.json();}).then(function(rows){
      CAPS = rows; if(then) then(rows);
    }).catch(function(){
      // The HUD must not go dark because one endpoint failed. No snapshot means
      // no gate: every tab renders as it always did.
      CAPS = null; if(then) then(null);
    });
  }

  function capOf(mode){
    var id = CAP_FOR[mode]; if(!id || !CAPS) return null;
    for(var i=0;i<CAPS.length;i++) if(CAPS[i].id===id) return CAPS[i];
    return null;
  }

  /* True when the view may render its own content. Unknown -> true: an
   * unreachable capability endpoint must not hide a tab that works. */
  function capsReady(mode){
    var c = capOf(mode); return !c || c.state === "ready";
  }

  function capEl(tag, cls, html){
    var d = document.createElement(tag);
    if(cls) d.className = cls;
    if(html != null) d.innerHTML = html;
    return d;
  }

  function capsPanelFor(cap, mode){
    var wrap = capEl("div","setup-gate");
    wrap.appendChild(capEl("h2", null, "&#9881; " + esc(cap.name) + " nije podešen"));
    wrap.appendChild(capEl("p","sg-why", esc(cap.why)));

    if(cap.state === "not_configured"){
      wrap.appendChild(capEl("p","sg-lead",
        "Otvori Claude Code u ovom folderu i pošalji ovo:"));
      var row = capEl("div","sg-cmd");
      var code = capEl("code",null,esc(cap.ask));
      var btn = capEl("button","sg-copy",null); btn.type="button"; btn.textContent="Kopiraj";
      btn.onclick=function(){
        // Older or locked-down browsers have no clipboard API; select the text
        // so the reader can copy it by hand rather than being told nothing.
        function fallback(){ try{ var r=document.createRange(); r.selectNodeContents(code);
          var s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
          btn.textContent="označeno — Ctrl+C"; }catch(e){ btn.textContent="kopiraj ručno"; } }
        if(navigator.clipboard && navigator.clipboard.writeText){
          navigator.clipboard.writeText(cap.ask).then(function(){
            btn.textContent="kopirano";
            setTimeout(function(){btn.textContent="Kopiraj";},1500);
          }).catch(fallback);
        } else fallback();
      };
      row.appendChild(code); row.appendChild(btn);
      wrap.appendChild(row);
      var need = cap.missing.filter(function(m){return !m.secret;});
      if(need.length){
        wrap.appendChild(capEl("p","sg-need","Traži se: " +
          need.map(function(m){return esc(m.label);}).join(", ")));
      }
      return wrap;
    }

    /* needs_secret */
    wrap.appendChild(capEl("p","sg-lead",
      "Adresa je podešena. Fali još samo pristup — upiši ga ovde:"));
    cap.missing.forEach(function(m){
      var f = capEl("label","sg-field");
      f.appendChild(capEl("span","sg-label", esc(m.label)));
      var inp = document.createElement("input");
      inp.type = m.secret ? "password" : "text";
      inp.placeholder = m.placeholder || "";
      inp.autocomplete = "off";
      f.appendChild(inp);
      if(m.hint) f.appendChild(capEl("span","sg-hint", esc(m.hint)));
      var save = document.createElement("button");
      save.type="button"; save.className="sg-save"; save.textContent="Sačuvaj";
      var msg = capEl("span","sg-msg",null);
      save.onclick=function(){
        var v = inp.value.trim();
        if(!v){ msg.textContent="prazno"; msg.className="sg-msg bad"; return; }
        save.disabled = true; msg.textContent="čuvam…"; msg.className="sg-msg";
        fetch("/api/capabilities/secret",{method:"POST",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify({key:m.key, value:v})})
        .then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});})
        .then(function(res){
          if(!res.ok){
            // The server's refusal text is the useful part -- it names the file
            // and why it would not write. Never replace it with "greška".
            msg.textContent = res.j.error || "odbijeno";
            msg.className = "sg-msg bad"; save.disabled=false; return;
          }
          inp.value=""; msg.textContent="sačuvano — lokalno, van git-a";
          msg.className="sg-msg ok";
          CAPS = res.j.capabilities || CAPS;
          setTimeout(function(){ setMode(mode); }, 700);
        })
        .catch(function(){ msg.textContent="server ne odgovara";
          msg.className="sg-msg bad"; save.disabled=false; });
      };
      f.appendChild(save); f.appendChild(msg);
      wrap.appendChild(f);
    });
    wrap.appendChild(capEl("p","sg-alt","Ili, ako više voliš razgovor: <code>" +
      esc(cap.ask) + "</code>"));
    return wrap;
  }

  /* Replace the view's body with the gate. Returns false when the view should
   * NOT run its own init -- an init that assumes a helpdesk is what throws. */
  function capsGate(mode){
    if(capsReady(mode)) return true;
    var host = $("view-" + mode);
    if(!host) return true;
    host.innerHTML = "";
    host.appendChild(capsPanelFor(capOf(mode), mode));
    return false;
  }
