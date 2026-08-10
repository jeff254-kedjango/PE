# Backend tests

Starter pytest suite — smoke + security-critical logic, **not** exhaustive
coverage. It is deliberately database-independent so it can run in CI without
Postgres.

```bash
cd weespas
pip install -r requirements.txt
pytest
```

`conftest.py` sets `DATABASE_URL` (a throwaway SQLite file) and `SECRET_KEY`
before the app imports, so tests never connect to the live Postgres
`commercial` database.

| File | Covers |
|---|---|
| `test_health.py` | App boots; `/health` and `/` respond via `TestClient` |
| `test_auth_security.py` | bcrypt hash round-trip; JWT sign/verify; wrong-secret rejection |
| `test_rbac.py` | `require_agent/staff/admin` allow/deny; ownership check + admin bypass |

## Worth adding next
- Endpoint-level integration tests against a transactional SQLite/Postgres
  session fixture (register → login → protected route).
- Property CRUD ownership enforcement through the HTTP layer.
