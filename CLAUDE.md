# Prosjekt: Garmin økt-opplaster — kontekst for Claude Code

Dette er et personlig prosjekt. Sammendraget er skrevet for at du (Claude Code)
skal forstå hva som finnes fra før. Neste oppgave er **designforbedring av
mobilsiden** (se TODO nederst).

## Mål

Få treningsøkter laget av en språkmodell inn i Garmin Connect fra mobil.
Brukeren beskriver/sparrer en økt i en separat chat, får et JSON-objekt,
og laster det opp via en liten egen webtjeneste.

## Viktige arbeidsflyt-forutsetninger (les dette)

- Brukeren har **ingen terminal / ingen lokalt dev-miljø**. Han redigerer filer
  via GitHubs web-grensesnitt og deployer ved å committe.
- Hosting: **Railway**, som auto-bygger repoet ved hver commit (Nixpacks/Railpack,
  Python oppdaget automatisk via `requirements.txt`).
- Hold derfor løsninger enkle: helst få filer, ingen build-steg, ingen npm.
- Norsk UI. Brukeren liker rene, mobil-først grensesnitt (har tidligere laget
  neumorphic-pregede apper).

## Arkitektur

1. **Frontend = en HTML-side innebygd i backend-en** (servert på `/`).
   Ingen separat frontend-repo, ingen CORS-problemer.
1. **Backend = FastAPI på Railway** som holder Garmin-innlogging og laster opp.
1. Språkmodellen er IKKE integrert — JSON lages i en separat chat og limes inn.

## Filer (alle i repo-roten)

- `main.py` — FastAPI-app. Endepunkter: `/` (HTML-betjeningsside),
  `/health`, `/setup`, `/setup/mfa`, `/upload`. HTML-siden ligger som
  en stor streng-konstant `PAGE` i denne fila.
- `translate.py` — oversetter det enkle øktformatet til Garmins workout-objekter.
- `requirements.txt` — `fastapi`, `uvicorn[standard]`, `garminconnect==0.3.5`.
- `Procfile` — `web: uvicorn main:app --host 0.0.0.0 --port $PORT`.

## Teknisk

- Bibliotek: `garminconnect` 0.3.5 (cyberjunky/python-garminconnect), uoffisielt
  reverse-engineered Garmin-API. Auth via `garth` (OAuth-token).
- Miljøvariabler i Railway:
  - `APP_KEY` — hemmelig nøkkel; sendes som header `X-App-Key` på alle kall.
  - `GARMINTOKENS` — serialisert Garmin-token (laget via `/setup`, limt inn manuelt
    så innloggingen overlever omstart). Lastes ved oppstart.
- Innlogging: `/setup` (e-post+passord, `return_on_mfa=True`). Ved 2FA returneres
  `{"mfa_required": true}`, og koden sendes til `/setup/mfa`. Ved suksess
  returneres token-strengen (`client.dumps()`).
- Opplasting: `/upload` bygger via `translate.build()` og kaller riktig
  `upload_*_workout`-metode. Valgfritt felt `dato` (YYYY-MM-DD) → `schedule_workout`.

## Øktformatet (kontrakten språkmodellen fyller)

Toppnivå: `navn`, `sport`, så `steg` (utholdenhet) ELLER `ovelser` (styrke).

- `sport`: `løping`, `sykkel`, `svømming`, `gange` (styrke ennå ikke støttet).
- Utholdenhet — `steg`: liste der hvert steg har `type` + enten `varighet`
  (`"15 min"`/`"90 s"`) eller `distanse` (`"1000 m"`/`"5 km"`).
  - `type`: `oppvarming`, `rolig`, `intervall`, `pause`, `nedjogg`.
  - valgfri `intensitet`: `rolig`(sone 2), `moderat`(3), `terskel`(4), `maks`(5), `ingen`.
  - gjentakelser: `{ "gjenta": <n>, "steg": [ ... ] }`.
- Intensitet mappes til HR-sone via `zoneNumber` på steget.

Eksempel:

```json
{
  "navn": "Terskel 5x1000",
  "sport": "løping",
  "steg": [
    { "type": "oppvarming", "varighet": "15 min", "intensitet": "rolig" },
    { "gjenta": 5, "steg": [
        { "type": "intervall", "distanse": "1000 m", "intensitet": "terskel" },
        { "type": "pause", "varighet": "2 min", "intensitet": "rolig" }
    ]},
    { "type": "nedjogg", "varighet": "10 min", "intensitet": "rolig" }
  ]
}
```

## Status

- Fungerer ende-til-ende: innlogging, opplasting, økt dukker opp i Garmin Connect-appen.
- Bekreftet for løping. Andre utholdenhetssporter bør virke, ikke testet.

## Kjente punkter / TODO

- **HR-soner ikke visuelt verifisert** — usikkert om `zoneNumber` vises riktig
  på intervallene i Garmin. Sjekk og juster i `translate.py` ved behov.
- **Garmin-weblenken i opplastingssvaret åpner en tom side** (krever web-innlogging).
  Kosmetisk; kan fjernes eller endres til app-lenke.
- **Styrke ikke implementert** — biblioteket har ingen typed strength-workout;
  krever at vi bygger Garmins rå-JSON selv (egne øvelses-IDer, reps vs tid).
- **NESTE OPPGAVE: design/utforming av mobilsiden** (`PAGE`-strengen i `main.py`).
  Mobil-først, norsk, ren stil. Behold enkelheten: ett endepunkt-kall per handling,
  app-nøkkel lagres i `localStorage`. Ikke innfør build-steg.