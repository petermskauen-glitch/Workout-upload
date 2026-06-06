"""Minimal backend som tar imot en økt i det enkle JSON-formatet og legger
den i Garmin Connect. Lagd for én bruker (deg).

Miljøvariabler (settes i Railway):
  APP_KEY        – hemmelig nøkkel appen din må sende i header 'X-App-Key'
  GARMINTOKENS   – Garmin-token (genereres via /setup første gang)
"""
import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Header, Body
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
    except Exception:
        return False


@app.on_event("startup")
def _startup():
    _try_token_login()


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
    result = g.login()
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
