# Frontend migration note (plan §3, §48, §68)

The `frontend/` directory currently holds the **reference implementation**
(Next.js 14 App Router, Tailwind, Firebase client). It stays until its
successor exists so the repo always has a working UI to compare UX against.

## Plan

Phases 1–2 are backend-first; the new frontend lands from Phase 3 onward:

1. New app shell per §3/§48 target structure (`app/` routes, `features/`
   modules, `components/ui` design system §49).
2. API client that speaks the `{"success", "data"}` envelope and forwards
   Firebase ID tokens + `X-Request-ID`.
3. Screens in plan order: Login → Dashboard → Media Library → Upload →
   Search → Contexts → Jobs → Profile → Admin (§48), consuming the endpoints
   catalogued in `docs/API.md`.
4. `lib/firebase.ts` initialises from `firebase-applet-config.json` (already
   at repo root); the backend needs only `FIREBASE_PROJECT_ID` to verify
   tokens — for pure-local dev use `AUTH_MODE=dev`.
5. Once the new app reaches parity on login/dashboard/media/search, the
   reference UI is deleted (git history keeps it).

## Interim wiring

`frontend/lib/api.ts` in the reference UI targets the legacy un-enveloped
APIs; treat it as incompatible with `backend/` and rebuild the client layer
rather than adapting it.
