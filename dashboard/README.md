# Dashboard

Next.js 16 (App Router, TypeScript, Tailwind v4, shadcn/ui) frontend for the Games Industry Investment Intelligence Platform. Reads from and writes to the same Supabase project the Python pipeline (`../agents/`) populates. See the repo root `CLAUDE.md`'s "Dashboard internals" section for the architecture rules this app follows (read/write key separation, RLS policy pattern, `force-dynamic` requirement, etc.) before making changes here.

## Setup

```bash
npm install
cp .env.example .env.local
```

Fill in `.env.local`:
- `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` — from Supabase Dashboard → Settings → API. Safe for the browser.
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` — same project, service_role key. **Server-only** — never referenced from a Client Component (`src/lib/supabase/server.ts` is guarded by the `server-only` package).

If a page you're adding reads a table without an existing anon SELECT policy, RLS will silently return zero rows. Write a new numbered `database/migrations/NNN_*.sql` file (see `011`-`013` for the pattern) and get it applied via the Supabase SQL Editor — there's no way to apply it from this app or from this dev environment directly.

## Develop

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Pages built so far

- `/` — Portfolio Overview (positions, cumulative return vs. S&P 500)
- `/watchlist-proposals` — Discovery agent's approve/reject review queue (the one live write path)
- `/signals` — per-game signal cards (CCU trend, sentiment, patch cadence), `/signals/[gameId]` for the full sentiment trend chart

Still open: sentiment trend charts are done; weekly briefing feed, trade plan approval UI, trade history log, position breakdown view, and Vercel deploy are not. See the repo root `tasks.md`'s Phase 7 section for current status.

## Before shipping a change

```bash
npx tsc --noEmit
npx eslint .
npm run build   # confirm any new page shows as ƒ (Dynamic), not ○ (Static), in the route table
```
