# Hoplyra Frontend

React dashboard for managing VPS servers, single-protocol VPN deployments, and multi-hop chains. Talks to the FastAPI backend when available; falls back to local demo data when the API is offline.

Main documentation: [../README.md](../README.md) · API details: [../backend/README.md](../backend/README.md)

The marketing site lives in a separate project: [hoplyra-landing](https://github.com/Linchevatel/Hoplyra) (sibling repo / `../hoplyra-landing`). It is not bundled with self-hosted installs.

## Prerequisites

- Node.js **20+**
- npm **10+**
- Running backend for real deploys (optional for UI-only demo)

## Scripts

```bash
npm install
npm run dev         # dev server — http://127.0.0.1:5173
npm run build       # production bundle → dist/
npm run preview     # preview production build
npm run typecheck   # TypeScript check
npm run lint        # ESLint
```

From the monorepo root you can also use `make dev-ui` / `make build-ui`.

## Development with API

Start the backend first (`make dev-api` from repo root), then:

```bash
npm run dev
```

Vite proxies `/api` → `http://127.0.0.1:8787` (see `vite.config.ts`). The dashboard auto-detects the API via `GET /api/health`.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `""` (same origin) | API base URL; set if the UI and API are on different hosts |

Example `.env.local` (gitignored):

```bash
# VITE_API_URL=http://127.0.0.1:8787
```

Leave empty in dev — the Vite proxy handles `/api`.

## Production build

```bash
npm run build
```

Output: `dist/`. The backend serves these files when `../frontend/dist` exists after build — single-port deployment.

Alternatively host `dist/` behind nginx/Caddy and point `VITE_API_URL` at your API origin at build time:

```bash
VITE_API_URL=https://api.example.com npm run build
```

## Routes

| Route | Description |
|-------|-------------|
| `/` | Dashboard — overview, servers, VPN, chains, client configs |
| `/servers`, `/vpn`, `/chains`, `/configs` | Dashboard tabs |

## UI behavior notes

- **Localhost VPS hidden** — `127.0.0.1` / `localhost` are filtered from the server list.
- **Demo mode** — without API, seeded demo servers load from `localStorage` on first visit.
- **API mode** — servers and configs sync from the backend; `localStorage` is not used for inventory.

## Project structure

```
src/
├── components/
│   ├── dashboard/    # Tabs: overview, servers, VPN, chains, …
│   ├── layout/       # Dashboard shell, logo
│   └── ui/           # Buttons, cards, badges
├── lib/
│   ├── api.ts        # REST client
│   ├── dashboard.tsx # Global state & deploy actions
│   ├── chain-*.ts    # Chain builder, deploy, presets
│   └── geo-utils.ts  # IP geolocation for server flags
├── pages/
└── main.tsx
```

## Tech stack

- **React 19** + **TypeScript**
- **Vite 8** — dev server & bundler
- **Tailwind CSS 4** — styling (`@tailwindcss/vite`)
- **React Router 7** — routing
- **Framer Motion** — animations
- **Lucide React** — icons

## License

MIT — see [../LICENSE](../LICENSE).
