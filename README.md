# College ERP

Multi-tenant cloud ERP for Indian colleges and universities: one platform, one PostgreSQL schema and one
subdomain per college, sold per student per year. Positioning: the ERP that gets a college accreditation-ready
(OBE, CO-PO attainment) while running daily operations.

Status: pilot MVP in progress. See `docs/GAP_LIST.md` for what exists against the pilot scope.

## Run it locally

See [docs/DEV_SETUP.md](docs/DEV_SETUP.md). In short: Python 3.13, PostgreSQL 17, copy `.env.example` to `.env`
and fill in a new `DJANGO_SECRET_KEY`, then `migrate_schemas --shared`, `create_demo_college`, `runserver`.

## Documents

- [docs/GAP_LIST.md](docs/GAP_LIST.md): base repo vs pilot scope
- [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md): audit of the inherited code, open items, rules for new code
- [docs/CO_PO_DATA_MODEL.md](docs/CO_PO_DATA_MODEL.md): OBE data model (draft)
- [docs/DEV_SETUP.md](docs/DEV_SETUP.md): setup, environment variables, checks

## Rules that every change must keep

Tenant isolation (College A never sees College B), a declared role policy on every endpoint, an audit log of every
data change, no secrets in the repository (everything comes from environment variables), university rules as
configuration. CI enforces them with tests, ruff, bandit, pip-audit, a secret scan and `check --deploy`.

## Licence

Started from [Ansarimajid/College-ERP](https://github.com/Ansarimajid/College-ERP) (MIT). The original notice is kept in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md); see also [LICENSE](LICENSE).
