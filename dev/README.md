# PE/dev — local launchers for the 4-codebase stack

Two backends + two frontends + two Celery workers, each on its own port and (for the
backends) its own Python venv. The venvs are **mandatory**: Weespas pins
`fastapi==0.104.1` while InSAR needs `fastapi>=0.110` — they cannot share one
environment.

## Ports (no collisions)

| Service | Port | Venv | Launcher |
|---|---|---|---|
| Weespas backend (FastAPI) | **8000** | `weespas/.venv` | `weespas-backend.sh` |
| Weespas Celery workers | — (broker `/1`) | `weespas/.venv` | `weespas-celery.sh` |
| Weespas frontend (Vite) | **5174** | node | `weespas-frontend.sh` |
| InSAR read app (FastAPI) | **8002** | `InSAR-Final-main/backend/.venv` | `insar-backend.sh` |
| InSAR control API (FastAPI) | **8001** | InSAR venv | `insar-control.sh` |
| InSAR pipeline Celery | — (broker `/2`) | InSAR venv | `insar-celery.sh` |
| InSAR frontend (Vite) | **5173** | node | `insar-frontend.sh` |

`8000` stays Weespas because **ngrok already forwards there** for the M-Pesa STK
callback (`/api/v1/billing/mpesa/callback`). Redis is shared but DB-separated:
weespas cache `/0`, weespas celery `/1`, InSAR celery `/2`.

## First-time setup

```bash
# Backends — each venv already created; recreate with:
#   python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
# (InSAR also needs the DuckDB spatial extension once:
#   .venv/bin/python -c "import duckdb; duckdb.connect().execute('INSTALL spatial')" )

# Frontends:
( cd ../weespas-frontend && npm install )
( cd ../InSAR-Final-main/frontend && npm install )

# Optional shared dev config (admin token for InSAR rebuilds):
cp dev.env.example dev.env   # then edit
```

## Run (one terminal per server)

```bash
./weespas-backend.sh     # :8000   ← ngrok target
./weespas-frontend.sh    # :5174
./insar-backend.sh       # :8002
./insar-frontend.sh      # :5173  (proxies /api → :8002)
./insar-control.sh       # :8001  (needs INSAR_ADMIN_TOKEN for rebuilds)
./weespas-celery.sh      # only if testing async weespas tasks / billing reconcile
./insar-celery.sh        # only if running a pipeline rebuild
```

For the M-Pesa flow you only need: **weespas-backend** (ngrok → 8000) +
**weespas-frontend**. Each launcher refuses to start if its port is already taken.
