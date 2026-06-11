"""Minimal backend som tar imot en økt i det enkle JSON-formatet og legger
den i Garmin Connect. Lagd for én bruker (deg).

Miljøvariabler (settes i Railway):
  APP_KEY        – hemmelig nøkkel appen din må sende i header 'X-App-Key'
  GARMINTOKENS   – Garmin-token (genereres via /setup første gang)
"""
import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Header, Body
from fastapi.responses import HTMLResponse
from garminconnect import Garmin

import translate

app = FastAPI(title="Garmin økt-opplaster")

APP_KEY = os.getenv("APP_KEY", "")
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


PAGE = """<!doctype html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Garmin økt-opplaster</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,system-ui,sans-serif; background:#0e1018; color:#e7e9f0;
         padding:18px; max-width:560px; margin:0 auto; -webkit-text-size-adjust:100%; }
  h1 { font-size:20px; margin:6px 0 2px; }
  p.sub { color:#8b90a3; margin:0 0 18px; font-size:14px; }
  .card { background:#171a26; border:1px solid #232737; border-radius:16px; padding:16px; margin-bottom:16px; }
  .card h2 { font-size:15px; margin:0 0 12px; color:#cdd2e4; }
  label { display:block; font-size:13px; color:#9aa0b4; margin:10px 0 4px; }
  input, textarea { width:100%; background:#0e1018; border:1px solid #2b3044; color:#e7e9f0;
         border-radius:10px; padding:12px; font-size:16px; font-family:inherit; }
  textarea { min-height:160px; resize:vertical; }
  button { width:100%; margin-top:14px; padding:13px; border:0; border-radius:10px;
         background:#6b5cff; color:#fff; font-size:16px; font-weight:600; }
  button:active { opacity:.85; }
  button.secondary { background:#2b3044; }
  .status { margin-top:12px; font-size:14px; white-space:pre-wrap; word-break:break-word; }
  .ok { color:#54d18c; } .err { color:#ff7a85; }
  .hidden { display:none; }
  .tokenbox { margin-top:10px; }
  code { background:#0e1018; padding:2px 6px; border-radius:6px; }
  a { color:#8aa0ff; }
</style>
</head>
<body>
  <h1>Garmin økt-opplaster</h1>
  <p class="sub">Din personlige tjeneste. Logg inn én gang, last opp økter.</p>

  <div class="card">
    <h2>App-nøkkel</h2>
    <label>X-App-Key (den du satte i Railway)</label>
    <input id="appkey" type="password" placeholder="lim inn nøkkelen din">
    <button class="secondary" onclick="saveKey()">Lagre nøkkel på denne enheten</button>
  </div>

  <div class="card">
    <h2>1 · Garmin-innlogging (én gang)</h2>
    <label>Garmin e-post</label>
    <input id="email" type="email" autocomplete="username">
    <label>Garmin passord</label>
    <input id="password" type="password" autocomplete="current-password">
    <button onclick="login()">Logg inn</button>

    <div id="mfaWrap" class="hidden">
      <label>Engangskode fra Garmin (2FA)</label>
      <input id="mfacode" inputmode="numeric" placeholder="f.eks. 123456">
      <button onclick="sendMfa()">Send kode</button>
    </div>

    <div id="loginStatus" class="status"></div>
    <div id="tokenWrap" class="tokenbox hidden">
      <label>Token – kopier denne</label>
      <textarea id="token" readonly></textarea>
      <button class="secondary" onclick="copyToken()">Kopier token</button>
      <p class="sub">Gå til Railway → tjenesten → <b>Variables</b> → ny variabel
         <code>GARMINTOKENS</code> = lim inn. Da slipper du å logge inn igjen.
         Du kan deretter fjerne e-post/passord-feltene over.</p>
    </div>
  </div>

  <div class="card">
    <h2>2 · Last opp en økt</h2>
    <label>Økt-JSON (lim inn fra språkmodellen)</label>
    <textarea id="workout" placeholder='{ "navn": "...", "sport": "løping", "steg": [ ... ] }'></textarea>
    <label>Dato i kalenderen (valgfritt)</label>
    <input id="dato" type="date">
    <button onclick="upload()">Last opp til Garmin</button>
    <div id="uploadStatus" class="status"></div>
  </div>

<script>
function key() { return document.getElementById('appkey').value.trim(); }
function saveKey() {
  localStorage.setItem('appkey', key());
  document.getElementById('appkey').value = key();
  alert('Nøkkel lagret på denne enheten.');
}
window.addEventListener('load', () => {
  const k = localStorage.getItem('appkey');
  if (k) document.getElementById('appkey').value = k;
});

async function post(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-App-Key': key() },
    body: JSON.stringify(body)
  });
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, data };
}

async function login() {
  const s = document.getElementById('loginStatus');
  s.textContent = 'Logger inn …'; s.className = 'status';
  const { ok, data } = await post('/setup', {
    email: document.getElementById('email').value.trim(),
    password: document.getElementById('password').value
  });
  if (data.mfa_required) {
    document.getElementById('mfaWrap').classList.remove('hidden');
    s.textContent = 'Skriv inn engangskoden Garmin sendte deg.';
    return;
  }
  handleToken(ok, data, s);
}

async function sendMfa() {
  const s = document.getElementById('loginStatus');
  s.textContent = 'Sjekker kode …'; s.className = 'status';
  const { ok, data } = await post('/setup/mfa', {
    code: document.getElementById('mfacode').value.trim()
  });
  handleToken(ok, data, s);
}

function handleToken(ok, data, s) {
  if (ok && data.token) {
    s.textContent = '✓ Innlogget mot Garmin!'; s.className = 'status ok';
    document.getElementById('mfaWrap').classList.add('hidden');
    document.getElementById('tokenWrap').classList.remove('hidden');
    document.getElementById('token').value = data.token;
  } else {
    s.textContent = '✗ ' + (data.detail || 'Noe gikk galt.'); s.className = 'status err';
  }
}

function copyToken() {
  const t = document.getElementById('token');
  t.select(); document.execCommand('copy');
  navigator.clipboard && navigator.clipboard.writeText(t.value);
  alert('Token kopiert.');
}

async function upload() {
  const s = document.getElementById('uploadStatus');
  s.textContent = 'Laster opp …'; s.className = 'status';
  let payload;
  try { payload = JSON.parse(document.getElementById('workout').value); }
  catch (e) { s.textContent = '✗ Ugyldig JSON: ' + e.message; s.className = 'status err'; return; }
  const dato = document.getElementById('dato').value;
  if (dato) payload.dato = dato;
  const { ok, data } = await post('/upload', payload);
  if (ok && data.workoutId) {
    s.innerHTML = '✓ Lagt i Garmin! <a href="' + data.lenke + '" target="_blank">Åpne økta</a>'
      + (data.lagtIKalender ? ('<br>Kalender: ' + data.lagtIKalender) : '');
    s.className = 'status ok';
  } else {
    s.textContent = '✗ ' + (data.detail || JSON.stringify(data)); s.className = 'status err';
  }
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return PAGE


@app.get("/health")
def health():
    return {"ok": True, "authenticated": _garmin is not None}


@app.post("/setup")
def setup(payload: dict = Body(...), x_app_key: Optional[str] = Header(None)):
    """Engangs-innlogging. Returnerer en token-streng du limer inn i
    miljøvariabelen GARMINTOKENS i Railway."""
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
    return {"token": _dump_token(g), "note": "Lim inn verdien i GARMINTOKENS i Railway, og fjern email/password."}


@app.post("/setup/mfa")
def setup_mfa(payload: dict = Body(...), x_app_key: Optional[str] = Header(None)):
    _check_key(x_app_key)
    global _garmin, _pending_mfa
    code = str(payload.get("code", "")).strip()
    if not _pending_mfa:
        raise HTTPException(400, "Ingen påbegynt innlogging. Start på nytt med /setup.")
    if not code:
        raise HTTPException(400, "Trenger 'code'.")
    g = _pending_mfa["client"]
    g.resume_login(_pending_mfa["state"], code)
    _garmin = g
    _pending_mfa = {}
    return {"token": _dump_token(g), "note": "Lim inn verdien i GARMINTOKENS i Railway, og fjern email/password."}


def _dump_token(g: Garmin) -> str:
    try:
        return g.client.dumps()
    except Exception as e:
        raise HTTPException(500, f"Klarte ikke å serialisere token: {e}")


@app.post("/upload")
def upload(payload: dict = Body(...), x_app_key: Optional[str] = Header(None)):
    """Bygg økta og legg den i Garmin. Valgfritt felt 'dato' (YYYY-MM-DD)
    legger økta i kalenderen på den datoen."""
    _check_key(x_app_key)
    if _garmin is None:
        raise HTTPException(503, "Ikke innlogget mot Garmin. Kjør /setup først.")

    dato = payload.pop("dato", None)
    try:
        workout = translate.build(payload)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"Ugyldig økt: {e}")

    sport = payload["sport"]
    method = {
        "løping": _garmin.upload_running_workout,
        "sykkel": _garmin.upload_cycling_workout,
        "svømming": _garmin.upload_swimming_workout,
        "gange": _garmin.upload_walking_workout,
    }[sport]

    try:
        result = method(workout)
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
