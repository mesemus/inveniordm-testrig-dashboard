# Invenio Testrig Dashboard — frontend

React + TypeScript + Vite + MUI. The backend lives in `../src/invenio_testrig_dashboard`.

## Development

Node version: see `.nvmrc` (24, also used by CI).

Run the API and Vite together; Vite proxies `/api` to `http://localhost:8000`.

```sh
uv run invenio-testrig-dashboard web --reload   # from the repository root
npm install
npm run dev
```

## Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server |
| `npm run build` | type-check, then build `dist/` (served by the API when it exists) |
| `npm test` | Vitest + React Testing Library |
| `npm run lint` | oxlint (includes `react/no-danger`) |
| `npm run typecheck` | `tsc -b` |
| `npm run gen:api` | regenerate `src/api/schema.d.ts` from `../openapi.json` (commit it) |
| `npm run test:e2e` | Playwright smoke test; starts the API itself, which needs a seeded database (see CI) |

The end-to-end test needs the Playwright browser (`npx playwright install chromium`) and a backend seeded with
`tests/fixtures/runs/e2e-base` and `e2e-head` (the `e2e` CI job does this). `E2E_PORT` changes the port (default 8000).

The page routes are also used in Discord messages, so keep them stable:
`/`, `/targets/:branch`, `/runs/:uid`, `/runs/:uid/tests`, `/test-cases/:id`, `/compare`.
