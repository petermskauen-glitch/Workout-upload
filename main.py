"""Backend for «Workout Wishes» — Garmin økt-opplaster.

Beskriv en utholdenhetsøkt på norsk -> en språkmodell (Gemini) lager den i det
enkle øktformatet -> last den opp til Garmin Connect. Frontend er én HTML-side
(PAGE) servert på /. Glass-bilder serveres fra /static.

Miljøvariabler (settes i Render):
  APP_KEY         – hemmelig nøkkel; sendes som header 'X-App-Key' på alle kall
  GARMINTOKENS    – serialisert Garmin-token (laget via Verktøy -> Garmin-innlogging)
  GEMINI_API_KEY  – Google AI Studio-nøkkel for /generate
  GEMINI_MODEL    – valgfri modell (standard: gemini-2.0-flash)
"""
import os
import json
import urllib.request
import urllib.error
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Header, Body
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from garminconnect import Garmin

import translate

app = FastAPI(title="Workout Wishes")
app.mount("/static", StaticFiles(directory="static"), name="static")

APP_KEY = os.getenv("APP_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

_garmin: Optional[Garmin] = None      # innlogget klient
_pending_mfa: dict[str, Any] = {}     # mellomlagret tilstand under MFA


def _check_key(key: Optional[str]):
    if not APP_KEY or key != APP_KEY:
        raise HTTPException(status_code=401, detail="Feil eller manglende X-App-Key.")


def _try_token_login() -> bool:
    """Logg inn med lagret token fra GARMINTOKENS, hvis den finnes."""
    global _garmin
    token = os.getenv("GARMINTOKENS")
    if not token:
        return False
    try:
        g = Garmin()
        g.login(token)            # token-streng lastes direkte
        _garmin = g
        return True
    except Exception as e:
        import traceback
        print(f"[GARMIN] Token-innlogging feilet: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return False


@app.on_event("startup")
def _startup():
    _try_token_login()


SYSTEM_PROMPT = (
    "Du er en treningsassistent som lager utholdenhetsøkter til Garmin. "
    "Brukeren beskriver en ønsket økt på norsk, eller justerer en tidligere økt. "
    "Du svarer ALLTID med ETT JSON-objekt på nøyaktig denne formen:\n"
    '{ "workout": <økt eller null>, "svar": "<kort norsk forklaring til brukeren>" }\n\n'
    "Regler for <økt> (det enkle øktformatet):\n"
    "- Toppnivå: navn (kort tittel), sport, og steg (liste).\n"
    "- sport: en av løping, sykkel, svømming, gange.\n"
    '- Hvert steg har "type" + enten "varighet" (f.eks. "15 min" eller "90 s") '
    'eller "distanse" ("1000 m" / "5 km").\n'
    "- type: en av oppvarming, rolig, intervall, pause, nedjogg.\n"
    "- valgfri intensitet: rolig, moderat, terskel, maks, ingen.\n"
    '- gjentakelser: { "gjenta": <antall>, "steg": [ ... ] }.\n\n'
    "Eksempel på <økt>:\n"
    '{ "navn": "Terskel 5x1000", "sport": "løping", "steg": ['
    '{ "type": "oppvarming", "varighet": "15 min", "intensitet": "rolig" }, '
    '{ "gjenta": 5, "steg": [ { "type": "intervall", "distanse": "1000 m", "intensitet": "terskel" }, '
    '{ "type": "pause", "varighet": "2 min", "intensitet": "rolig" } ] }, '
    '{ "type": "nedjogg", "varighet": "10 min", "intensitet": "rolig" } ] }\n\n'
    "Hvis brukeren justerer en tidligere økt, returner HELE den oppdaterte økta. "
    "Styrketrening er IKKE støttet ennå – sett da workout til null og forklar i svar. "
    "Hvis meldingen ikke er en økt-bestilling, sett workout til null og svar kort. "
    "I 'svar' gir du en kort, vennlig oppsummering av økta (ikke gjenta hele JSON-en)."
)


PAGE = """<!doctype html>
<html lang="no"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Workout Wishes</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0;}
  :root{--bg:#edeae2;--hi:rgba(255,255,255,.9);--sh:rgba(170,160,142,.55);--ink:#46413a;--soft:#8c8475;--blue:#6293c8;}
  body{font-family:-apple-system,'Segoe UI Variable','Segoe UI',system-ui,sans-serif;
    background:var(--bg);color:var(--ink);min-height:100vh;-webkit-text-size-adjust:100%;}
  body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
    opacity:.5;mix-blend-mode:soft-light;}
  .app{position:relative;width:100%;max-width:440px;margin:0 auto;min-height:100vh;overflow:hidden;}
  .page{position:relative;z-index:1;padding:54px 28px 38px;min-height:100vh;display:flex;flex-direction:column;}
  .head{display:inline-block;font-size:23px;font-weight:500;font-stretch:condensed;transform:scaleX(.9);transform-origin:left;}
  h1.head{color:var(--ink);margin:0 0 16px 6px;}
  .sub.head{color:var(--soft);margin:22px 0 12px 6px;}
  .box{background:var(--bg);border-radius:26px;box-shadow:-9px -9px 18px var(--hi),10px 10px 22px var(--sh);}
  .prompt{position:relative;height:120px;padding:16px 18px;}
  .prompt textarea{width:100%;height:100%;border:none;outline:none;background:transparent;resize:none;font:inherit;font-size:16px;color:#5b5446;padding-right:46px;}
  .ph{position:absolute;left:18px;top:16px;right:54px;font-size:16px;color:#a59c8c;pointer-events:none;}
  .ph.pulse{font-style:italic;font-weight:600;color:#d08f50;animation:breathe 3.2s ease-in-out infinite;}
  @keyframes breathe{0%,100%{opacity:.38}50%{opacity:.96}}
  .blue-circle{width:40px;height:40px;border-radius:50%;border:none;cursor:pointer;background:var(--blue);
    box-shadow:0 2px 6px rgba(40,80,140,.28);display:grid;place-items:center;}
  .blue-circle svg{width:23px;height:23px;stroke:#fff;stroke-width:2.4;fill:none;}
  .send-btn{position:absolute;right:12px;bottom:12px;}
  .answer{flex:1 1 auto;min-height:150px;padding:18px;color:#bdb4a3;font-size:15px;display:flex;flex-direction:column;gap:12px;overflow:auto;}
  .answer .placeholder{margin:auto auto 0 0;}
  .msg-user{align-self:flex-end;max-width:84%;background:rgba(255,255,255,.62);border:1px solid rgba(255,255,255,.7);
    border-radius:16px 16px 4px 16px;padding:11px 14px;color:#5b5446;font-size:14.5px;line-height:1.35;white-space:pre-wrap;}
  .msg-ai{align-self:flex-start;max-width:94%;color:#6b6254;font-size:14.5px;line-height:1.42;white-space:pre-wrap;}
  .msg-ai.thinking{opacity:.6;font-style:italic;}
  .msg-file{align-self:flex-start;max-width:94%;background:rgba(255,255,255,.55);border:1px solid rgba(255,255,255,.65);border-radius:14px;padding:11px 14px;color:#5b5446;font-size:14px;line-height:1.4;}
  .msg-file .nm{font-size:12px;color:#9a9080;}
  .msg-err{align-self:flex-start;color:#b5746a;font-size:14px;}
  .gear{position:absolute;top:30px;right:28px;width:46px;height:46px;border-radius:50%;z-index:3;border:none;background:var(--bg);cursor:pointer;
    display:grid;place-items:center;box-shadow:inset 4px 4px 8px var(--sh),inset -4px -4px 8px var(--hi);}
  .gear svg{width:20px;height:20px;stroke:#6b6357;stroke-width:1.7;fill:none;}
  .bottom{margin-top:18px;}
  .date-wrap{display:flex;justify-content:center;margin-bottom:12px;}
  .date-chip{display:inline-flex;align-items:center;padding:6px 16px;border-radius:999px;border:1px solid rgba(255,255,255,.72);
    background:transparent;font-style:italic;font-weight:500;font-size:13.5px;color:#9a9080;cursor:pointer;}
  .date-chip.set{font-style:normal;color:#7a7160;}
  .slide-track{position:relative;width:100%;height:66px;border-radius:33px;background:var(--bg);
    box-shadow:inset 5px 5px 10px var(--sh),inset -5px -5px 10px var(--hi);}
  .slide-label{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    font-style:italic;font-weight:500;font-size:16px;color:#9a9080;pointer-events:none;letter-spacing:.01em;}
  .slide-handle{position:absolute;left:4px;top:50%;transform:translateY(-50%);width:98px;height:58px;border-radius:29px;overflow:hidden;cursor:grab;touch-action:none;
    backdrop-filter:blur(3px) saturate(150%);-webkit-backdrop-filter:blur(3px) saturate(150%);box-shadow:0 2px 5px rgba(70,62,48,.16);}
  .slide-handle .pill-img{position:absolute;top:50%;left:50%;width:58px;height:98px;transform:translate(-50%,-50%) rotate(90deg);background:url('/static/glasspille.png') center/100% 100% no-repeat;}
  .slide-handle svg{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:22px;height:22px;stroke:#6b6357;stroke-width:2;fill:none;z-index:2;}
  .upload-row{display:flex;align-items:center;justify-content:center;gap:14px;margin-top:16px;}
  .upload-form{width:54px;height:54px;border-radius:50%;background:var(--bg);display:grid;place-items:center;
    box-shadow:inset 4px 4px 8px var(--sh),inset -4px -4px 8px var(--hi);}
  .upload-btn{width:42px;height:42px;border-radius:50%;overflow:hidden;border:none;cursor:pointer;
    background:url('/static/glassirkel.png') center/100% 100% no-repeat;
    backdrop-filter:blur(3px) saturate(150%);-webkit-backdrop-filter:blur(3px) saturate(150%);display:grid;place-items:center;}
  .upload-btn svg{width:20px;height:20px;stroke:#6b6357;stroke-width:1.9;fill:none;}
  .upload-label{font-style:italic;font-weight:500;font-size:15px;color:#9a9080;}
  .modal{position:absolute;inset:0;z-index:10;opacity:0;pointer-events:none;transition:opacity .3s ease;}
  .modal.open{opacity:1;pointer-events:auto;}
  .scrim{position:absolute;inset:0;background:rgba(40,36,30,.14);}
  .center{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%) scale(.92);
    display:flex;flex-direction:column;align-items:center;gap:16px;transition:transform .35s cubic-bezier(.22,1,.3,1);width:340px;max-width:92%;}
  .modal.open .center{transform:translate(-50%,-50%) scale(1);}
  .close-wrap{position:relative;}
  .close{width:48px;height:48px;border-radius:50%;overflow:hidden;border:none;cursor:pointer;
    background:url('/static/glassirkel.png') center/100% 100% no-repeat;
    backdrop-filter:blur(3px) saturate(150%);-webkit-backdrop-filter:blur(3px) saturate(150%);display:grid;place-items:center;}
  .close svg{width:16px;height:16px;stroke:#4a4438;stroke-width:2.3;fill:none;}
  .panel{position:relative;width:320px;max-width:100%;max-height:80vh;overflow-y:auto;border-radius:18px;border:none;
    background:url('/static/glassflate.png') center/100% 100% no-repeat;
    backdrop-filter:blur(6px) saturate(150%);-webkit-backdrop-filter:blur(6px) saturate(150%);padding:34px 28px 30px;}
  .hf{display:inline-block;font-weight:500;font-stretch:condensed;transform:scaleX(.9);transform-origin:left;}
  .panel h2{font-size:22px;color:#6a6254;margin-bottom:18px;}
  .item{background:transparent;border:1px solid rgba(255,255,255,.78);border-radius:16px;padding:13px 16px;margin-bottom:11px;font-size:16px;color:#7a7160;line-height:1.25;cursor:pointer;}
  .panel label{display:block;font-size:12.5px;color:#8c8475;margin:10px 0 4px;}
  .panel input,.panel textarea{width:100%;border:1px solid rgba(0,0,0,.12);background:rgba(255,255,255,.7);border-radius:12px;padding:11px 12px;font-size:15px;color:#3f3a32;font-family:inherit;outline:none;}
  .vbtn{width:100%;margin-top:12px;border:none;border-radius:14px;padding:12px;background:var(--blue);color:#fff;font-weight:600;font-size:15px;cursor:pointer;}
  .vbtn.sec{background:rgba(120,110,90,.25);color:#5b5446;}
  .vmsg{margin-top:10px;font-size:13.5px;color:#6b6254;white-space:pre-wrap;word-break:break-word;}
  .vmsg.ok{color:#3f8a5b;} .vmsg.err{color:#b5746a;}
  .back{font-size:13px;color:#8c8475;cursor:pointer;margin-bottom:6px;display:inline-block;}
  .abouttext{font-size:14px;color:#6b6254;line-height:1.5;}
  .hidden{display:none;}
  .cal-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;}
  .cal-head button{border:none;background:transparent;font-size:24px;line-height:1;color:#7a7160;cursor:pointer;width:34px;height:34px;border-radius:50%;}
  .cal-title{font-weight:600;font-size:17px;color:#5b5446;}
  .cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;}
  .cal-wd{text-align:center;font-size:11px;color:#9a9080;font-weight:600;padding-bottom:4px;}
  .cal-day{height:34px;display:flex;align-items:center;justify-content:center;border-radius:50%;font-size:14px;color:#5b5446;cursor:pointer;}
  .cal-day.blank{visibility:hidden;cursor:default;}
  .cal-day.sel{background:var(--blue);color:#fff;font-weight:600;}
  .datepanel{padding-bottom:74px;}
  .confirm-btn{position:absolute;right:22px;bottom:20px;}
  @supports (backdrop-filter: scale(1.15)) or (-webkit-backdrop-filter: scale(1.15)) {
    .panel{backdrop-filter:blur(6px) saturate(150%) scale(1.15);-webkit-backdrop-filter:blur(6px) saturate(150%) scale(1.15);}
    .close,.upload-btn{backdrop-filter:blur(3px) saturate(150%) scale(1.15);-webkit-backdrop-filter:blur(3px) saturate(150%) scale(1.15);}
    .slide-handle{backdrop-filter:blur(3px) saturate(150%) scale(1.25);-webkit-backdrop-filter:blur(3px) saturate(150%) scale(1.25);}
  }
</style></head>
<body>
  <div class="app" id="app">
    <div class="page">
      <button class="gear" id="gear" aria-label="Verktøy">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      </button>
      <h1 class="head">Workout Wishes</h1>
      <div class="box prompt">
        <textarea id="pt" rows="3"></textarea>
        <span class="ph" id="ph">Beskriv økta du ønsker deg …</span>
        <button class="send-btn blue-circle" id="send" aria-label="Send"><svg viewBox="0 0 24 24"><path d="M12 19V5M6 11l6-6 6 6"/></svg></button>
      </div>
      <div class="sub head">Your wish comes true</div>
      <div class="box answer" id="chat"><span class="placeholder">Språkmodellens svar dukker opp her …</span></div>
      <div class="bottom">
        <div class="date-wrap"><span class="date-chip" id="dchip">Set date (optional)</span></div>
        <div class="slide-track" id="track">
          <div class="slide-label" id="slabel">Launch to Garmin</div>
          <div class="slide-handle" id="sh"><div class="pill-img"></div><svg viewBox="0 0 24 24"><path d="M5 12h13M12 6l6 6-6 6"/></svg></div>
        </div>
        <div class="upload-row">
          <div class="upload-form"><button class="upload-btn" id="uploadBtn" aria-label="Upload existing file"><svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/></svg></button></div>
          <span class="upload-label">Upload existing file</span>
        </div>
      </div>
    </div>

    <div class="modal" id="tools">
      <div class="scrim" data-close="tools"></div>
      <div class="center">
        <div class="close-wrap"><button class="close" data-close="tools" aria-label="Lukk"><svg viewBox="0 0 24 24"><path d="M5 5l14 14M19 5L5 19"/></svg></button></div>
        <div class="panel" id="toolPanel">
          <div class="view view-menu">
            <h2 class="hf">Verktøy</h2>
            <div class="item" data-go="login"><span class="hf">Garmin-innlogging</span></div>
            <div class="item" data-go="status"><span class="hf">Tilkoblingsstatus</span></div>
            <div class="item" data-go="appkey"><span class="hf">App-nøkkel</span></div>
            <div class="item" data-act="forget"><span class="hf">Fjern app-nøkkel fra denne enheten</span></div>
            <div class="item" data-go="about"><span class="hf">Om appen</span></div>
          </div>
          <div class="view view-appkey hidden">
            <span class="back" data-back>‹ Tilbake</span>
            <h2 class="hf">App-nøkkel</h2>
            <label>X-App-Key (samme som APP_KEY på Render)</label>
            <input id="appkey" type="password" placeholder="lim inn nøkkelen">
            <button class="vbtn" onclick="saveKey()">Lagre på denne enheten</button>
            <div id="keyMsg" class="vmsg"></div>
          </div>
          <div class="view view-status hidden">
            <span class="back" data-back>‹ Tilbake</span>
            <h2 class="hf">Tilkoblingsstatus</h2>
            <button class="vbtn" onclick="checkStatus()">Sjekk status</button>
            <div id="statusMsg" class="vmsg"></div>
          </div>
          <div class="view view-login hidden">
            <span class="back" data-back>‹ Tilbake</span>
            <h2 class="hf">Garmin-innlogging</h2>
            <label>Garmin e-post</label><input id="gemail" type="email" autocomplete="username">
            <label>Garmin passord</label><input id="gpass" type="password" autocomplete="current-password">
            <button class="vbtn" onclick="login()">Logg inn</button>
            <div id="mfaWrap" class="hidden">
              <label>Engangskode fra Garmin (2FA)</label><input id="mfacode" inputmode="numeric" placeholder="f.eks. 123456">
              <button class="vbtn" onclick="sendMfa()">Send kode</button>
            </div>
            <div id="loginMsg" class="vmsg"></div>
            <div id="tokenWrap" class="hidden">
              <label>Token – kopier og lim inn i GARMINTOKENS på Render</label>
              <textarea id="token" readonly rows="3"></textarea>
              <button class="vbtn sec" onclick="copyToken()">Kopier token</button>
            </div>
          </div>
          <div class="view view-about hidden">
            <span class="back" data-back>‹ Tilbake</span>
            <h2 class="hf">Om appen</h2>
            <p class="abouttext">Workout Wishes — beskriv en utholdenhetsøkt, få den laget av en språkmodell, og send den til Garmin Connect. Din personlige tjeneste.</p>
          </div>
        </div>
      </div>
    </div>

    <div class="modal" id="datemodal">
      <div class="scrim" data-close="datemodal"></div>
      <div class="center">
        <div class="close-wrap"><button class="close" data-close="datemodal" aria-label="Lukk"><svg viewBox="0 0 24 24"><path d="M5 5l14 14M19 5L5 19"/></svg></button></div>
        <div class="panel datepanel">
          <div class="cal-head">
            <button id="calPrev" aria-label="Forrige">‹</button>
            <span class="cal-title" id="calTitle"></span>
            <button id="calNext" aria-label="Neste">›</button>
          </div>
          <div class="cal-grid" id="calGrid"></div>
          <button class="confirm-btn blue-circle" id="dateConfirm" aria-label="Bekreft dato"><svg viewBox="0 0 24 24"><path d="M5 13l4 4L19 7"/></svg></button>
        </div>
      </div>
    </div>
  </div>

  <input type="file" id="fileInput" accept=".json,application/json" hidden>

<script>
  var $ = function(id){ return document.getElementById(id); };
  var esc = function(s){ return String(s).replace(/[&<>]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]; }); };
  var APPKEY = localStorage.getItem('appkey') || '';
  function key(){ return APPKEY; }

  async function post(path, body){
    var r = await fetch(path, { method:'POST',
      headers:{ 'Content-Type':'application/json', 'X-App-Key': key() },
      body: JSON.stringify(body) });
    var data = await r.json().catch(function(){ return {}; });
    return { ok:r.ok, status:r.status, data:data };
  }

  // ---- modaler ----
  function openModal(id){ $(id).classList.add('open'); }
  function closeModal(id){ $(id).classList.remove('open'); }
  Array.prototype.forEach.call(document.querySelectorAll('[data-close]'), function(el){
    el.onclick = function(){ closeModal(el.getAttribute('data-close')); };
  });

  // ---- Verktøy-meny + visninger ----
  var panel = $('toolPanel');
  function showView(name){
    Array.prototype.forEach.call(panel.querySelectorAll('.view'), function(v){ v.classList.add('hidden'); });
    panel.querySelector('.view-'+name).classList.remove('hidden');
    if(name === 'appkey'){ $('appkey').value = APPKEY; $('keyMsg').textContent=''; }
  }
  $('gear').onclick = function(){ openModal('tools'); showView(APPKEY ? 'menu' : 'appkey'); };
  Array.prototype.forEach.call(panel.querySelectorAll('[data-go]'), function(el){
    el.onclick = function(){ showView(el.getAttribute('data-go')); };
  });
  Array.prototype.forEach.call(panel.querySelectorAll('[data-back]'), function(el){
    el.onclick = function(){ showView('menu'); };
  });
  Array.prototype.forEach.call(panel.querySelectorAll('[data-act="forget"]'), function(el){
    el.onclick = function(){
      if(confirm('Fjerne app-nøkkelen fra denne enheten?')){
        APPKEY=''; localStorage.removeItem('appkey'); showView('appkey'); $('keyMsg').textContent='Nøkkel fjernet.'; $('keyMsg').className='vmsg';
      }
    };
  });

  function saveKey(){
    APPKEY = $('appkey').value.trim();
    localStorage.setItem('appkey', APPKEY);
    var m=$('keyMsg'); m.textContent = APPKEY ? '✓ Lagret på denne enheten.' : 'Tomt — ingenting lagret.';
    m.className = 'vmsg ' + (APPKEY ? 'ok' : 'err');
  }

  async function checkStatus(){
    var m=$('statusMsg'); m.textContent='Sjekker …'; m.className='vmsg';
    try{
      var r = await fetch('/health'); var d = await r.json();
      m.textContent = 'Tilkoblet Garmin: ' + (d.authenticated ? 'Ja ✓' : 'Nei ✗')
        + '\\ngarminconnect ' + d.garminconnect + ', garth ' + d.garth;
      m.className = 'vmsg ' + (d.authenticated ? 'ok' : 'err');
    }catch(e){ m.textContent='Klarte ikke å hente status.'; m.className='vmsg err'; }
  }

  // ---- Garmin-innlogging (re-auth) ----
  async function login(){
    var m=$('loginMsg'); m.textContent='Logger inn …'; m.className='vmsg';
    var res = await post('/setup', { email:$('gemail').value.trim(), password:$('gpass').value });
    if(res.data && res.data.mfa_required){ $('mfaWrap').classList.remove('hidden'); m.textContent='Skriv inn engangskoden fra Garmin.'; return; }
    handleToken(res.ok, res.data);
  }
  async function sendMfa(){
    var m=$('loginMsg'); m.textContent='Sjekker kode …'; m.className='vmsg';
    var res = await post('/setup/mfa', { code:$('mfacode').value.trim() });
    handleToken(res.ok, res.data);
  }
  function handleToken(ok, data){
    var m=$('loginMsg');
    if(ok && data && data.token){
      m.textContent='✓ Innlogget mot Garmin!'; m.className='vmsg ok';
      $('mfaWrap').classList.add('hidden');
      $('tokenWrap').classList.remove('hidden');
      $('token').value = data.token;
    } else {
      m.textContent='✗ ' + ((data && data.detail) || 'Noe gikk galt.'); m.className='vmsg err';
    }
  }
  function copyToken(){
    var t=$('token'); t.select();
    try{ document.execCommand('copy'); }catch(e){}
    if(navigator.clipboard){ navigator.clipboard.writeText(t.value); }
    alert('Token kopiert.');
  }

  // ---- prompt -> Gemini -> chat ----
  var ta=$('pt'), ph=$('ph'), chat=$('chat');
  var history=[]; var currentWorkout=null; var answered=false;
  function refreshPh(){ ph.style.display = ta.value.trim() ? 'none' : 'block'; }
  ta.addEventListener('input', refreshPh);
  function addMsg(cls, html){ var d=document.createElement('div'); d.className=cls; d.innerHTML=html; chat.appendChild(d); chat.scrollTop=chat.scrollHeight; return d; }
  function setAdjust(){ ph.textContent='Adjust or Launch'; ph.classList.add('pulse'); refreshPh(); }

  $('send').onclick = async function(){
    var t = ta.value.trim(); if(!t) return;
    if(!key()){ openModal('tools'); showView('appkey'); $('keyMsg').textContent='Sett app-nøkkelen først.'; $('keyMsg').className='vmsg err'; return; }
    if(!answered){ chat.innerHTML=''; answered=true; }
    addMsg('msg-user', esc(t));
    history.push({ role:'user', text:t });
    ta.value=''; refreshPh();
    var think = addMsg('msg-ai thinking', 'Tenker …');
    var res = await post('/generate', { messages: history });
    think.remove();
    if(res.ok && res.data){
      var svar = res.data.svar || 'Her er forslaget.';
      addMsg('msg-ai', esc(svar));
      history.push({ role:'model', text: JSON.stringify(res.data) });
      if(res.data.workout && res.data.workout.sport){ currentWorkout = res.data.workout; }
      setAdjust();
    } else {
      addMsg('msg-err', esc((res.data && res.data.detail) || 'Noe gikk galt.'));
    }
  };

  // ---- last opp eksisterende fil ----
  $('uploadBtn').onclick = function(){ $('fileInput').click(); };
  $('fileInput').onchange = function(){
    var f = $('fileInput').files[0]; if(!f) return;
    var r = new FileReader();
    r.onload = function(){
      if(!answered){ chat.innerHTML=''; answered=true; }
      try{
        var data = JSON.parse(r.result);
        var navn = data.navn || f.name; var sport = data.sport || '?';
        currentWorkout = data;
        addMsg('msg-file', '<span class="nm">\\uD83D\\uDCC4 '+esc(f.name)+'</span><br><b>'+esc(navn)+'</b> · '+esc(sport));
        setAdjust();
      }catch(e){ addMsg('msg-err', 'Kunne ikke lese fila som gyldig JSON.'); }
      chat.scrollTop=chat.scrollHeight; $('fileInput').value='';
    };
    r.readAsText(f);
  };

  // ---- datovelger ----
  var MONS=['Januar','Februar','Mars','April','Mai','Juni','Juli','August','September','Oktober','November','Desember'];
  var MON3=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  var view=new Date(); view.setDate(1); var picked=null; var chosenISO=null;
  function renderCal(){
    $('calTitle').textContent = MONS[view.getMonth()]+' '+view.getFullYear();
    var g=$('calGrid'); g.innerHTML='';
    ['Ma','Ti','On','To','Fr','Lø','Sø'].forEach(function(w){ var d=document.createElement('div'); d.className='cal-wd'; d.textContent=w; g.appendChild(d); });
    var y=view.getFullYear(), m=view.getMonth();
    var offset=(new Date(y,m,1).getDay()+6)%7, days=new Date(y,m+1,0).getDate();
    for(var i=0;i<offset;i++){ var b=document.createElement('div'); b.className='cal-day blank'; g.appendChild(b); }
    for(var dd=1; dd<=days; dd++){ (function(dnum){
      var c=document.createElement('div'); c.className='cal-day'; c.textContent=dnum;
      if(picked && picked.getFullYear()===y && picked.getMonth()===m && picked.getDate()===dnum){ c.classList.add('sel'); }
      c.onclick=function(){ picked=new Date(y,m,dnum); renderCal(); };
      g.appendChild(c);
    })(dd); }
  }
  $('dchip').onclick=function(){ renderCal(); openModal('datemodal'); };
  $('calPrev').onclick=function(){ view.setMonth(view.getMonth()-1); renderCal(); };
  $('calNext').onclick=function(){ view.setMonth(view.getMonth()+1); renderCal(); };
  function pad(n){ return (n<10?'0':'')+n; }
  $('dateConfirm').onclick=function(){
    if(picked){
      chosenISO = picked.getFullYear()+'-'+pad(picked.getMonth()+1)+'-'+pad(picked.getDate());
      $('dchip').textContent = pad(picked.getDate())+MON3[picked.getMonth()]+String(picked.getFullYear()).slice(2);
      $('dchip').classList.add('set');
    }
    closeModal('datemodal');
  };

  // ---- slide to launch ----
  var track=$('track'), sh=$('sh'), label=$('slabel');
  var drag=false, sx=0, cx=0;
  function maxX(){ return track.clientWidth - sh.offsetWidth - 8; }
  function flash(txt){ label.textContent=txt; setTimeout(function(){ label.textContent='Launch to Garmin'; }, 2500); }
  async function launch(){
    if(!currentWorkout){ flash('Ingen økt klar ennå'); return; }
    if(!key()){ flash('Sett app-nøkkel i Verktøy'); return; }
    label.textContent='Sender …';
    var payload = JSON.parse(JSON.stringify(currentWorkout));
    if(chosenISO){ payload.dato = chosenISO; }
    var res = await post('/upload', payload);
    if(res.ok && res.data && res.data.workoutId){
      flash('Lagt i Garmin ✓');
    } else {
      flash('Feil: ' + ((res.data && res.data.detail) || 'ukjent'));
    }
  }
  sh.addEventListener('pointerdown', function(e){ drag=true; sx=e.clientX-cx; sh.style.transition='none'; sh.setPointerCapture(e.pointerId); });
  window.addEventListener('pointermove', function(e){ if(!drag) return; cx=Math.max(0, Math.min(maxX(), e.clientX-sx)); sh.style.left=(4+cx)+'px'; });
  window.addEventListener('pointerup', function(){
    if(!drag) return; drag=false; sh.style.transition='left .25s ease';
    if(cx >= maxX()*0.9){ launch(); }
    cx=0; sh.style.left='4px';
  });

  // ---- oppstart: be om app-nøkkel hvis den mangler ----
  if(!APPKEY){ openModal('tools'); showView('appkey'); }
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return PAGE


@app.get("/health")
def health():
    import importlib.metadata as _md
    def _ver(p):
        try:
            return _md.version(p)
        except Exception:
            return "?"
    return {
        "ok": True,
        "authenticated": _garmin is not None,
        "garminconnect": _ver("garminconnect"),
        "garth": _ver("garth"),
    }


@app.post("/generate")
def generate(payload: dict = Body(...), x_app_key: Optional[str] = Header(None)):
    """Lag en økt fra brukerens beskrivelse via Gemini. Returnerer
    {"workout": <økt eller null>, "svar": "<tekst>"}."""
    _check_key(x_app_key)
    if not GEMINI_API_KEY:
        raise HTTPException(503, "Mangler GEMINI_API_KEY på serveren.")
    messages = payload.get("messages") or []
    contents = []
    for m in messages:
        role = "model" if m.get("role") == "model" else "user"
        contents.append({"role": role, "parts": [{"text": str(m.get("text", ""))}]})
    if not contents:
        raise HTTPException(400, "Tom melding.")

    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.5},
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        raise HTTPException(502, f"Gemini-feil ({e.code}): {detail}")
    except Exception as e:
        raise HTTPException(502, f"Gemini-kall feilet: {type(e).__name__}: {e}")

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        raise HTTPException(502, f"Uventet Gemini-svar: {json.dumps(data)[:400]}")
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = {"workout": None, "svar": text}
    if not isinstance(parsed, dict):
        parsed = {"workout": None, "svar": str(parsed)}
    return {"workout": parsed.get("workout"), "svar": parsed.get("svar") or ""}


@app.post("/setup")
def setup(payload: dict = Body(...), x_app_key: Optional[str] = Header(None)):
    """Engangs-innlogging. Returnerer en token-streng du limer inn i
    miljøvariabelen GARMINTOKENS i Render."""
    _check_key(x_app_key)
    global _garmin, _pending_mfa
    email = payload.get("email")
    password = payload.get("password")
    if not email or not password:
        raise HTTPException(400, "Trenger 'email' og 'password'.")
    g = Garmin(email=email, password=password, return_on_mfa=True)
    try:
        result = g.login()
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(502, f"Garmin avviste innlogging: {type(e).__name__}: {e}")
    needs_mfa = result[0] if isinstance(result, tuple) else None
    if needs_mfa == "needs_mfa":
        _pending_mfa = {"client": g, "state": result[1]}
        return {"mfa_required": True, "neste": "Send koden til /setup/mfa"}
    _garmin = g
    return {"token": _dump_token(g), "note": "Lim inn verdien i GARMINTOKENS i Render."}


@app.post("/setup/mfa")
def setup_mfa(payload: dict = Body(...), x_app_key: Optional[str] = Header(None)):
    _check_key(x_app_key)
    global _garmin, _pending_mfa
    code = str(payload.get("code", "")).strip()
    if not _pending_mfa:
        raise HTTPException(400, "Ingen påbegynt innlogging. Start på nytt.")
    if not code:
        raise HTTPException(400, "Trenger 'code'.")
    g = _pending_mfa["client"]
    g.resume_login(_pending_mfa["state"], code)
    _garmin = g
    _pending_mfa = {}
    return {"token": _dump_token(g), "note": "Lim inn verdien i GARMINTOKENS i Render."}


def _dump_token(g: Garmin) -> str:
    try:
        return g.client.dumps()
    except Exception:
        try:
            return g.garth.dumps()
        except Exception as e:
            raise HTTPException(500, f"Klarte ikke å serialisere token: {e}")


@app.post("/upload")
def upload(payload: dict = Body(...), x_app_key: Optional[str] = Header(None)):
    """Bygg økta og legg den i Garmin. Valgfritt felt 'dato' (YYYY-MM-DD)
    legger økta i kalenderen på den datoen."""
    _check_key(x_app_key)
    if _garmin is None:
        raise HTTPException(503, "Ikke innlogget mot Garmin. Logg inn via Verktøy.")

    dato = payload.pop("dato", None)
    try:
        workout = translate.build(payload)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"Ugyldig økt: {e}")

    sport = payload.get("sport")
    methods = {
        "løping": _garmin.upload_running_workout,
        "sykkel": _garmin.upload_cycling_workout,
        "svømming": _garmin.upload_swimming_workout,
        "gange": _garmin.upload_walking_workout,
    }
    if sport not in methods:
        raise HTTPException(400, f"Ukjent eller manglende sport: {sport}")

    try:
        result = methods[sport](workout)
    except Exception as e:
        raise HTTPException(502, f"Garmin avviste økta: {e}")

    workout_id = result.get("workoutId")
    scheduled = None
    if dato and workout_id:
        try:
            _garmin.schedule_workout(workout_id, dato)
            scheduled = dato
        except Exception as e:
            scheduled = f"FEILET: {e}"

    return {
        "workoutId": workout_id,
        "lenke": f"https://connect.garmin.com/modern/workout/{workout_id}" if workout_id else None,
        "lagtIKalender": scheduled,
    }
