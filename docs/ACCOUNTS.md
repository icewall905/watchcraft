# Accounts, sign-in and profiles

The Hub has real accounts. An **account** (username + password, role `admin` or `user`) owns
**profiles** (adult or child). A client — browser, TV, Android app WebView — can stay signed in to
**several accounts at once** and switch between them; each account then shows its own profiles.

## Modes

| `BT_AUTH` | Behaviour |
|---|---|
| `accounts` (default) | Every `/api/*` call needs a signed-in account (exemptions below). |
| `open` | Legacy single-household mode, no sign-in. Used by the test suites and e2e server. Never in production. |

`GET /api/auth/status` reports the mode, so the web app picks its flow at boot.

## Sessions

- Each signed-in account on a client is one server-side session row (`accountsession`) and one
  httponly cookie `wc_s_<public_id>=<token>` (token stored only as SHA-256).
- The active account is the `wc_active=<public_id>` cookie. A tab may override it with the
  `X-Account: <public_id>` header (the web app keeps it in `sessionStorage`). A header whose session
  is gone answers 401 — it never falls back to another signed-in account.
- Sessions have no expiry. They end on sign-out, revoke (Devices, admin), account disable/delete,
  password reset, or 3 years without use. Browsers cap a cookie at 400 days, so the server re-sends
  the session cookie (Max-Age 400 days) at most once a day while the client is used.
- Active profile: `X-Profile-Id` header as before, now **only profiles of the active account**
  (403 otherwise). Without the header (media tags) the server uses the non-httponly cookie
  `wc_profile_<public_id>` that the web app sets on profile switch, then the account's first profile.
- Profile PINs work as before (`profile_pin_<id>` cookies).

## Roles

- Account `admin`: server settings, accounts admin. Admin actions also need a non-child active
  profile.
- Profile PINs are always optional, also next to child profiles: the account owner decides.
- Profile `role`: `adult` or `child` in accounts mode (`admin` is legacy, treated as adult).
- Any account manages its own profiles (create/edit/delete) from a non-child, unlocked profile.
- Admins manage accounts but cannot select other accounts' profiles.

## What other accounts can see

- Stats (`/stats/page`, `/stats/explore`) are admin-only and show the admin's own playlists; library
  and server totals stay server-wide.
- The agent activity feed shows the account's own events plus system events.
- Mood Board "send to a profile" lists every assigned profile on the server with its account name
  (a copy; child recipients get the kid-safety check).
- Play-on screens are server-wide devices: every account sees them and what they play; any
  account may register one (max 10), admins manage pairings.
- The offline library is shared. An account deletes what its own profiles saved; clearing it all is admin-only.
- Known limits: an admin can issue a reset link for any account and so could take it over; the
  reset signs the account out everywhere, which the owner notices. Media tags and the live agent
  stream follow the `wc_active` cookie, so two tabs on different accounts share that one choice
  for images/video. Login throttling is in memory per api process.

## Settings

Account keys — `appearance`, `player`, `sponsorblock`, `extras` — are stored per account and merged
over the server value; any account may change its own. All other keys are server settings and need
an admin. Non-admin accounts read `notify`, `llm`, `search`, `throttle`, `stt`, `moodboard_sources`
and `commonroom` as defaults (no hosts, webhooks or keys). Profile `prefs` still override `player`.

## API (all under `/api`)

Errors are `{detail: string}` with the usual status codes.

### Sign-in (no session needed)

| Call | Body | Result |
|---|---|---|
| `GET /auth/status` | – | `{mode, bootstrap, active, account, accounts}`; `account` = `{id, username, role}` of the active one or `null`; `accounts` = `[{public_id, username, role, active}]` signed in on this client; `bootstrap` true only on a fresh install with no accounts. |
| `POST /auth/login` | `{username, password, device_label?}` | `{public_id, account}`; sets session + active cookies. 401 wrong credentials, 429 too many attempts. Signing in to an account already signed in on this client reuses its session. |
| `POST /auth/bootstrap` | `{username, password}` | Fresh install only: creates the first admin and signs in. |
| `GET /auth/invite/{token}` | – | `{username, role, purpose}`; `purpose` = `register` or `reset`. 404 unknown/used/expired. |
| `POST /auth/register` | `{token, password, username?, device_label?}` | Sets the password (username only if the invite has none), signs in. Reset links also sign out all other sessions of that account. |
| `POST /auth/device/start` | `{device_label?}` | `{code, poll, expires_in, link}`; `code` like `K7QF-2M`, `link` = `/link?code=K7QF2M`. |
| `POST /auth/device/poll` | `{poll}` | `{status:"pending"}` · `{status:"approved", public_id, account}` (cookies set) · 410 expired. |

### Signed in

| Call | Body | Result |
|---|---|---|
| `POST /auth/switch` | `{public_id}` | Sets `wc_active`. 404 if not signed in on this client. |
| `POST /auth/logout` | `{public_id?, all?}` | Signs out one account (default: active) or all on this client. Returns status like `GET /auth/status`. |
| `POST /auth/password` | `{current, new}` | Change own password (min 8 chars). |
| `GET /auth/devices` | – | `[{public_id, device_label, user_agent, created_at, last_seen_at, current}]` for the active account. |
| `DELETE /auth/devices/{public_id}` | – | Revoke one session of the active account. |
| `POST /auth/device/approve` | `{code}` | Approve a TV code; the new session is for the active account. `{username}`. |

### Admin (active account is admin)

| Call | Body | Result |
|---|---|---|
| `GET /admin/accounts` | – | `[{id, username, role, disabled, registered, created_at, profiles:[{id,name,emoji,color,role}], sessions, invite:{purpose, expires_at}|null}]` |
| `POST /admin/accounts` | `{username?, role}` | `{account, link}` — `link` is a `/join/<token>` path, valid 14 days, single use. |
| `PATCH /admin/accounts/{id}` | `{role?, disabled?}` | Updated account. Keeps at least one enabled admin. |
| `DELETE /admin/accounts/{id}` | – | Deletes the account, its profiles and their data. Not yourself. |
| `POST /admin/accounts/{id}/reset` | – | `{link}` new reset (or registration) link; old unused links die. |
| `DELETE /admin/accounts/{id}/sessions` | – | Signs the account out everywhere. |
| `GET /admin/profiles/unowned` | – | Profiles from before accounts, not yet assigned. |
| `POST /admin/profiles/{pid}/assign` | `{account_id}` | Gives an unassigned profile (with all its data) to an account. Owned profiles can't be moved. |

### Profiles

`GET/POST/PATCH/DELETE /profiles…` as before, scoped to the active account. `role` accepts
`adult|child` in accounts mode.

## Command line

`python -m bt.accounts_cli` (inside the api container: `docker compose exec bt-api python -m bt.accounts_cli …`):

- `invite <username> [--admin] [--base https://host]` — prints a registration link.
- `reset <username> [--base …]` — prints a password reset link.
- `list` — accounts, roles, registered, sessions.
- `profiles` — every profile with its owner (or unassigned).
- `assign <profile id or exact name> <username>` — give an unassigned profile to an account.

The manage menus offer the same as "Create account invite" and "Reset account password".
