# Repository Guidelines

## Project Structure & Module Organization

FlowHub combines workflow collaboration with versioned Expert agents.

- `frontend/src/`: React 19 and TypeScript pages, components, stores, hooks, and shared types; static assets live in `frontend/public/`.
- `backend/src/flowhub_api/`: FastAPI routes, domain models, schemas, authorization, services, integrations, and database setup.
- `frontend/tests/`: Node.js regression tests. `backend/unit_tests/` contains isolated tests; `backend/tests/` contains API, integration, and workflow tests.
- `docs/` and `backend/docs/`: architecture, designs, evaluations, and API contracts. `backend/deploy/` contains Docker Compose configuration.

## Build, Test, and Development Commands

Run frontend commands from `frontend/`:

- `npm ci`: install dependencies from the lockfile.
- `npm run dev`: start Vite; `/api` requests proxy to port 8000.
- `npm test`: run `tests/*.test.mjs` using Node’s test runner.
- `npm run lint`: run ESLint, including TypeScript and React Hooks rules.
- `npm run build`: run TypeScript project checks and build production assets.

Run backend commands from `backend/`:

- `uv sync --group dev`: install application and test dependencies.
- `uv run uvicorn flowhub_api.main:app --reload --port 8000`: start the API.
- `uv run pytest unit_tests`: run isolated backend tests.
- `uv run pytest tests`: run integration tests against configured services.

## Coding Style & Naming Conventions

Use four-space Python indentation and two-space TypeScript/TSX indentation. Follow existing file conventions for quotes and semicolons. Use `snake_case` for Python functions/modules, `PascalCase` for React components and types, and `camelCase` for JavaScript variables. Frontend filenames commonly use kebab-case, such as `expert-center.tsx`. Keep business logic in services, validate API inputs with schemas, and prefer immutable application-state updates.

## Testing Guidelines

Backend tests use pytest, pytest-asyncio, and FastAPI TestClient; name files `test_*.py`. Frontend tests use `*.test.mjs`. Add regression tests for changed behavior and target at least 80% coverage; tooling currently enforces no coverage threshold. Exercise critical workflow paths from `TEST_PLAN.md`.

Integration fixtures reset the `flowhub_test` schema. Verify `FLOWHUB_TEST_DB_HOST` points to a disposable test database before running them.

## Commit & Pull Request Guidelines

History uses Conventional Commit prefixes, including `feat:`, `fix:`, and `docs:`, with English or Chinese summaries. Keep commits focused. PRs should describe resulting behavior, link relevant issues, report validation commands/results, and include screenshots for UI changes. Explain migrations and deployment requirements.

## Security & Configuration

Use `backend/.env.example` as the configuration template. Keep credentials out of commits and logs. Preserve authorization checks, audit records, frozen workflow versions, and human approval boundaries when changing Expert execution.
