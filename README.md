# Smart Kilimo

Irrigation advisory for smallholder farmers. Smart Kilimo runs a FAO-56 water
balance calculation against real daily rainfall for every registered plot,
and tells the farmer exactly how much to water — before the deficit crosses
their crop's alert threshold. The same engine drives three channels: a web
dashboard, USSD, and SMS.

**Live demo:** https://smart-kilimo-i46n.onrender.com
Deployed on Render's free tier — see [DEPLOYMENT.md](DEPLOYMENT.md) for how
it's deployed, including the Africa's Talking USSD/SMS simulator walkthrough
and the free-tier spin-down caveat. **This README covers running the project
locally instead.**

## What it does

- **FAO-56 water balance per plot.** Every plot accumulates a soil-moisture
  deficit daily, offset by real rainfall pulled from Open-Meteo for its
  region. When the deficit crosses the crop-stage's alert threshold, the
  farmer is notified with exactly how much water to apply.
- **Three channels, one engine.** The web dashboard (farmer + admin), USSD
  (`core/ussd_session.py`), and SMS (Africa's Talking) all call the same
  `services/plot_evaluation.py` logic — nothing is duplicated or
  approximated per channel.
- **A daily automated cycle** (`scheduler/jobs.py`, 06:00 Africa/Nairobi by
  default) re-evaluates every plot against that day's rainfall and sends
  alerts/reminders without anyone triggering it manually.
- **Crop-stage awareness.** Water needs scale with FAO-56 Kc values and
  effective root depth as a crop moves through its growth stages, not a
  single flat number per crop (see `db/seed.py` for the full derivation).

Supported out of the box: **Maize, Beans, Onions** across **Eastern Kenya,
Rift Valley, Coast** — seeded automatically on first boot (`db/seed.py`).

## Tech stack

| Layer         | Choice                                                        |
|---------------|----------------------------------------------------------------|
| Backend       | Flask 3, SQLAlchemy 2, Flask-Login, Flask-WTF (CSRF)           |
| Scheduling    | APScheduler (in-process, single worker — see note below)       |
| Database      | SQLite locally, Postgres in production (`DATABASE_URL`-driven) |
| SMS / USSD    | Africa's Talking                                                |
| Weather       | Open-Meteo                                                      |
| Frontend      | Server-rendered Jinja templates, Tailwind CSS (compiled, not CDN), Chart.js |
| Tests         | pytest, pytest-flask                                            |

## Project layout

```
api/            Flask app factory, HTTP/USSD/SMS routes, WTForms
core/           Domain models, enums, constants, the FAO-56 irrigation engine
db/             SQLAlchemy engine/session setup, reference-data + admin seeding
services/       Auth, weather client, SMS client, plot evaluation, daily cycle logic
scheduler/      APScheduler job wiring for the daily cycle
migrations/     Alembic environment (scaffolded — see note in Environment variables section)
templates/      Jinja templates: landing, auth, farmer dashboard, admin dashboard
static/         Tailwind source (static/src/input.css) + compiled output (static/tailwind.css), favicon
scripts/        seed_demo_data.py — populates sample farmers/plots for demos
tests/          pytest suite: auth, irrigation engine, daily cycle, USSD, web routes
```

## Prerequisites

- **Python 3.12.** The dev environment for this project ran on 3.12;
  SQLAlchemy 2.0.36 is known to break on Python 3.14 (see
  [DEPLOYMENT.md](DEPLOYMENT.md) for the exact failure) if you're on a very
  new interpreter.
- **Node.js + npm** — only needed if you plan to edit templates/CSS and
  rebuild Tailwind. Not required just to run the app: `static/tailwind.css`
  is already committed, pre-built.
- **(Optional) A free [Africa's Talking](https://account.africastalking.com/auth/register)
  sandbox account** — only needed to exercise the USSD/SMS paths. The web
  dashboard works fully without one.

## Local setup

### 1. Clone the repo

```bash
git clone https://github.com/Mich-O/smart_kilimo.git
cd smart_kilimo
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Then edit `.env`. See the full [environment variables](#environment-variables)
reference below — at minimum, `FLASK_SECRET_KEY` and `INTERNAL_API_TOKEN`
need to be some non-empty string, and `AT_USERNAME` / `AT_API_KEY` need to be
set to *something* (the app boots fine with sandbox placeholders; SMS sends
will just fail quietly until they're real credentials).

### 5. Run the app

```bash
python run.py
```

On first boot this automatically:
1. creates every table (`init_db()` — no manual migration step needed for a
   fresh database);
2. seeds region/crop/growth-stage reference data (`db/seed.py`);
3. creates one admin account from `DEFAULT_ADMIN_USERNAME` /
   `DEFAULT_ADMIN_PASSWORD` in your `.env` (defaults to `admin` /
   `ChangeMe123!` if you don't set them — change this after first login via
   the account menu).

Visit **http://localhost:5000**. Log in as admin at `/admin/login`, or
register a farmer at `/farmer/register`.

> **Phone number format:** farmer accounts are keyed by phone number, and
> the registration/login forms enforce a Kenyan E.164 format —
> `+254` followed by `7` or `1`, then 8 digits (`^\+254[17]\d{8}$`, see
> `api/forms.py`), e.g. `+254712345678`. Use a number in this shape
> everywhere in the project — the web forms will reject anything else, and
> keeping to it also means the same farmer identity works whether you're
> testing through the web app or through the Africa's Talking USSD/SMS
> simulator (see below), since both are keyed by the same phone number.

> **Testing admin and farmer at the same time?** Login uses a standard
> Flask session cookie, which is shared by every tab in a browser profile —
> logging in as a farmer in one tab will log out an admin session open in
> another tab of the same profile (this is normal cookie behavior, not a
> bug)
> To have both logged in at once, use two different browser profiles, or a
> regular window plus an Incognito/private window, one role per window.

### 6. (Optional) Seed demo data

```bash
python scripts/seed_demo_data.py
```

Creates four sample farmers with plots run through ten simulated days of
rainfall, so the dashboards show real history instead of an empty state.
It's idempotent — safe to re-run. Farmer login is printed to stdout when you
run it (all demo farmers share the password `DemoPass123!`).

### 7. (Optional) Rebuild Tailwind CSS

Only needed if you add or change a Tailwind class in a template — the
compiled CSS is otherwise already up to date and committed.

```bash
npm install          # first time only
npm run build:css    # one-off rebuild
npm run watch:css     # rebuild on every save while you work
```

Commit the regenerated `static/tailwind.css` alongside your template
changes — there's no CSS build step in production, so what's committed is
what ships (see [DEPLOYMENT.md](DEPLOYMENT.md) §0).

### 8. Run the tests

```bash
pytest
```

`tests/conftest.py` points `DATABASE_URL` at an isolated temporary SQLite
file and sets `FLASK_ENV=testing` (which skips starting a real scheduler
thread), so running tests never touches your dev `smart_kilimo.db`.

## Testing the USSD/SMS paths locally

Africa's Talking calls your app over a public URL (`/ussd`,
`/sms/callback`), so its sandbox simulator can't reach a bare `localhost`.
Two ways around that:

- Tunnel your local server (e.g. with ngrok) and point the Africa's
  Talking sandbox's USSD/SMS callback URLs at the tunnel instead of
  `localhost`.
- Or skip local tunneling entirely and use the already-hosted deployment —
  see [DEPLOYMENT.md](DEPLOYMENT.md) §4 for the full simulator walkthrough
  against the live app.

Whichever route you use, dial in from the AT simulator with a `+254...`
number (see the phone number format note above) so the session lands on
the same farmer identity your web account uses.

## Environment variables

All read from `.env` via `config.py`; `.env.example` has the full list with
inline comments.

| Variable                       | Required | Default                                      | Notes |
|---------------------------------|----------|-----------------------------------------------|-------|
| `FLASK_ENV`                     | no       | `development`                                 | `testing` skips starting the scheduler (see `tests/conftest.py`) |
| `FLASK_SECRET_KEY`               | **yes**  | —                                              | Flask session signing key |
| `INTERNAL_API_TOKEN`             | **yes**  | —                                              | Bearer token gating `/internal/run-cycle` |
| `DATABASE_URL`                   | no       | `sqlite:///smart_kilimo.db`                    | Point at Postgres in production |
| `AT_USERNAME`                    | **yes**  | —                                              | Africa's Talking username (`sandbox` for the sandbox) |
| `AT_API_KEY`                     | **yes**  | —                                              | Africa's Talking API key |
| `AT_SENDER_ID`                   | no       | *(blank)*                                     | Leave blank unless you've registered a shortcode/alphanumeric ID — an unregistered one causes silent send failures, not a clear error |
| `AT_ENVIRONMENT`                 | no       | `sandbox`                                     | |
| `WEATHER_API_BASE_URL`           | no       | Open-Meteo forecast endpoint                  | |
| `WEATHER_TIMEZONE`               | no       | `Africa/Nairobi`                               | |
| `WEATHER_PAST_DAYS`              | no       | `1`                                            | |
| `RAINFALL_RESET_THRESHOLD_MM`    | no       | `5.0`                                          | Rainfall (mm) that resets a plot's deficit/active alert |
| `DAILY_CYCLE_HOUR` / `_MINUTE`   | no       | `6` / `0`                                      | When the daily evaluation cycle runs |
| `REMINDER_INTERVAL_HOURS`        | no       | `4`                                            | Hours between repeat SMS reminders while an alert is active |
| `MAX_REMINDERS`                  | no       | `3`                                            | Cap on reminders sent per alert |
| `DEFAULT_ADMIN_USERNAME`/`_PASSWORD` | no   | `admin` / `ChangeMe123!`                       | Seeded once, only if no admin account exists yet |
| `SEED_DEMO_DATA`                 | no       | `false`                                        | If `true`, runs the demo-data seed automatically on boot (used by the Render deploy, not needed locally — run `scripts/seed_demo_data.py` manually instead) |

**Note on `migrations/`:** Alembic is scaffolded (`migrations/env.py`) for
future schema changes, but nothing has been generated into
`migrations/versions/` yet — the schema is currently created directly via
`init_db()` on every boot (`Base.metadata.create_all`, safe to call
repeatedly). You won't need to run `alembic upgrade` to get a fresh database
running locally.

## Useful commands

| Command                                | What it does |
|-----------------------------------------|--------------|
| `python run.py`                         | Run the Flask dev server on port 5000 (debug mode, auto-reload) |
| `pytest`                                 | Run the full test suite against an isolated temp SQLite DB |
| `python scripts/seed_demo_data.py`       | Populate sample farmers/plots for a non-empty dashboard |
| `npm run build:css`                     | Rebuild `static/tailwind.css` after a template change |
| `npm run watch:css`                     | Same, but watches for changes |
| `gunicorn wsgi:app --workers 1 --threads 4` | Run with the same server/worker config as production |

## Deploying

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full Render deployment guide,
including the Africa's Talking simulator setup and known free-tier
constraints (spin-down, single worker). The live instance is at
https://smart-kilimo-i46n.onrender.com.
