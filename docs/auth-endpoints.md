# Auth — Email/Password Endpoints

**Status:** Implemented (`app/routers/auth.py`, migration `0002_email_password_auth`).
**Relationship:** Companion to [`docs/api-reference.md`](./api-reference.md), which also documents
these endpoints inline (§1). This doc is the focused spec/changelog for the email+password phase.
Sign in with Apple (`POST /v1/auth/apple`) remains implemented and functional, but is **deferred
client-side** — email/password is the current auth method for the iOS client.

Conventions (unchanged from the API reference): base path `/v1`, JSON request/response bodies,
`Content-Type: application/json`. `register`, `login`, and `refresh` are all **unauthenticated**
(no `Authorization` header). Passwords are never returned in any response. Passwords are hashed
with **argon2id** (`argon2-cffi`, default parameters) — never stored in plaintext, never logged.

---

## Shared response shape — `AuthTokens`

Identical to `POST /v1/auth/apple`:

```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "member_id": "e84f671f-cbd3-45bb-a9c4-2f9ac28a7669",
  "household_id": "33e2a92a-7bdc-4df1-b697-87de0d8efee7",
  "role": "parent"
}
```

- `access_token`: JWT with `sub` (member id) + `hid` (household id), **60-minute** expiry.
- `refresh_token`: JWT with `sub`, `type: "refresh"`, a unique `jti`, **30-day** expiry.
- `role`: `"parent"` or `"teen"`.

---

## 1. `POST /v1/auth/register`

Creates a new **parent** and their **household** in one call — mirrors how `POST /v1/auth/apple`
doubles as signup. **Parent-only**: teens are minted by a parent via
`POST /v1/household/members/teens` (unchanged); a teen self-registering here would create a new
household rather than attaching to a parent-created profile, since no linking flow exists yet
(see `docs/api-reference.md` §5 and `docs/ios-prd.md` §16 open question 3).

**Request**

| Field | Type | Rules |
|---|---|---|
| `email` | string | Valid email (Pydantic `EmailStr`). Stored lowercased. Globally unique across all members, any provider. |
| `password` | string | Min 8 chars. |
| `display_name` | string | 1–100 chars. |

```json
{ "email": "priya@example.com", "password": "hunter2pass", "display_name": "Priya" }
```

**Response `201`** — `AuthTokens` (with `role: "parent"`).

**Errors**

| Status | When | Body |
|---|---|---|
| `409` | Email already registered | `{ "detail": "That email is already in use." }` |
| `422` | Missing field / invalid email / password too short | Standard Pydantic validation shape |
| `429` | Rate limit exceeded (§4) | `{ "detail": "Too many attempts. Try again later." }` |

---

## 2. `POST /v1/auth/login`

Authenticates an existing member with `auth_provider == "email"` (in practice, currently only
parents, since teen self-registration is out of scope).

**Request**

```json
{ "email": "priya@example.com", "password": "hunter2pass" }
```

Email is compared case-insensitively (lowercased both at registration and at lookup).

**Response `200`** — `AuthTokens` (with the member's actual `role`).

**Errors**

| Status | When | Body |
|---|---|---|
| `401` | Wrong email **or** password | `{ "detail": "Incorrect email or password." }` — one generic message for both, doesn't reveal which was wrong |
| `422` | Malformed body | Standard Pydantic validation shape |
| `429` | Rate limit exceeded (§4) | |

---

## 3. `POST /v1/auth/refresh`

Redeems a refresh token for a fresh token pair. **Rotates** the refresh token: the presented one
is single-use — a second redemption attempt of the same token is rejected, even if it hasn't
expired yet. This closes the gap noted in `docs/api-reference.md` §1.3 (previously, no endpoint
existed to redeem a `refresh_token` at all).

**Request**

```json
{ "refresh_token": "eyJhbGciOi..." }
```

**Response `200`** — a fresh `AuthTokens`, with a **new** `refresh_token` (new `jti`).

**Errors**

| Status | When | Body |
|---|---|---|
| `401` | Malformed, expired, or not-a-refresh-token (e.g. an access token passed here) | `{ "detail": "Invalid or expired refresh token." }` |
| `401` | Valid but already redeemed (replay of a rotated-out token) | `{ "detail": "Refresh token has already been used or is invalid." }` |
| `401` | Token valid but the member no longer exists | `{ "detail": "Member not found." }` |

**Implementation note:** rotation is enforced via Redis (`app/services/refresh_tokens.py`) — each
refresh token's `jti` is marked used on first redemption via `SET NX`, TTL'd to that token's own
remaining lifetime (from its `exp` claim), so the record never outlives the token it guards.

---

## 4. Rate limiting

Per-IP and per-email fixed-window counters (Redis `INCR`/`EXPIRE`,
`app/services/rate_limit.py`), applied to both `register` and `login`:

| Endpoint | Per-IP | Per-email |
|---|---|---|
| `POST /v1/auth/login` | 10 attempts / 15 min | 5 attempts / 15 min |
| `POST /v1/auth/register` | 5 attempts / hour | 3 attempts / hour |

Both the IP and email limits are checked on every request; whichever is hit first returns `429`.
IP is read from the request's client address (`request.client.host`) — if this backend is ever
deployed behind a reverse proxy/load balancer, this needs to switch to trusting a forwarded-for
header from a known proxy, or every client will appear to share the proxy's IP and rate-limit each
other.

---

## Rules carried over from the existing design (all upheld as specified)

- **Token claims/TTLs unchanged:** access JWT carries `sub` + `hid`, 60-min expiry; refresh 30
  days (now with a `jti` for rotation tracking, additive — doesn't change the TTL). Household ID
  is always derived from the token server-side — never a client parameter.
- **Tenant isolation:** nothing crosses household boundaries; a 404 (not 403) is returned for any
  id outside the caller's household (unrelated to auth, but unaffected by this change).
- **Teen email/password is out of scope for now**, for the same reason Apple-ID linking is:
  `register` is **parent-only**. Teens keep using shared-device/PIN
  (`POST /v1/household/members/teens`) until a linking flow exists.

## What changed in the data model

- `members.password_hash` (nullable `VARCHAR(255)`) — only set for `auth_provider == "email"`.
- A new **global** `UNIQUE(auth_provider, auth_subject)` constraint on `members`. Previously the
  only uniqueness constraint was per-household (`UNIQUE(household_id, auth_subject)`), but both
  Apple sign-in and email login look a member up by `(auth_provider, auth_subject)` with **no**
  household filter — so without a global constraint, a race (or a bug) could have created the same
  auth identity under two different households. This closes that gap for both auth methods, not
  just the new one.

---

## 5. Shared-device PIN verification *(implemented — `app/routers/household.py`, `app/auth/pins.py`)*

**Why this is needed.** The iOS shared-device / family-device mode (ios-prd §4.4) shows a teen
profile picker on a shared iPad; each profile is gated by the 4-digit PIN set at teen creation
(`POST /v1/household/members/teens`, `pin` field). The backend already **hashes and stores** that
PIN, but there is currently **no way to verify it**, and `pin_hash` is never returned by
`GET /v1/household/members`. As a result the iOS client can only verify PINs it set locally on the
same device — a teen profile created on a *different* device opens with **no PIN gate at all**. A
verify endpoint makes the gate work on any device.

This is explicitly **not a security boundary** (master PRD §6.1) — it's friction against casual
sibling access. So the endpoint is a simple match check, but it should still be rate-limited
(a 4-digit PIN is only 10,000 combinations).

### `POST /v1/household/members/{member_id}/verify-pin`

Authenticated — any member of the household (in practice the parent, whose token the shared device
holds). `member_id` must be in the caller's household, else `404` (as everywhere).

**Request**

```json
{ "pin": "1234" }
```

| Field | Type | Rules |
|---|---|---|
| `pin` | string | Exactly 4 digits. |

**Response `200`** — always `200` for a *check* (a wrong PIN is not an auth failure of the request
itself):

```json
{ "valid": true, "pin_set": true }
```

- `valid`: whether the supplied PIN matches the member's stored hash.
- `pin_set`: whether this member has a PIN at all. When `false`, `valid` is `false` and the client
  may open the profile without a gate.

**Errors**

| Status | When |
|---|---|
| `404` | `member_id` not in the caller's household |
| `422` | `pin` missing or not exactly 4 digits |
| `429` | Rate limit exceeded (below) |

**Rate limiting.** Per-`member_id` only, **5 attempts / 5 min**, checked before the `member_id`
lookup (so a request that never gets past rate limiting doesn't leak whether `member_id` exists).
Built on `app/services/rate_limit.py` — the same primitive used for `/v1/auth/login` and
`/v1/auth/register`. Deliberately **not** also rate-limited per-IP, unlike login/register: this is
a shared-device feature by design, so multiple teens' profiles get checked from the same iPad's IP
in normal use — an IP-wide limit would let one sibling's failed attempts lock out another's PIN
check, which per-`member_id` scoping avoids.

**Implementation note:** PIN hashing (`app/auth/pins.py`) reuses the unsalted SHA-256 scheme
already established at teen-profile creation (`POST /v1/household/members/teens`) rather than
introducing a second scheme — verification uses `hmac.compare_digest` for a constant-time
comparison. This is intentionally simpler than the argon2id used for account passwords (§ above);
the threat model here is a shared-device sibling, not credential theft, and the rate limit — not
the hash strength — is what actually protects the 10,000-combination PIN space.

### Companion change: expose `pin_set` on the member list

Add a boolean **`pin_set`** to each member in `GET /v1/household/members` (never the hash itself).
This lets the client render the lock badge and decide whether a PIN prompt is needed **without** a
verify round-trip per profile.

```json
[{ "id": "...", "role": "teen", "display_name": "Arjun", "pin_set": true, "...": "..." }]
```

### Client integration

- Replace the local `PINStore` verification with a `verify-pin` call (keep `PINStore` only as an
  offline fallback for on-device-created profiles, if desired).
- Read `pin_set` from the member list (now returned on every `MemberResponse`, not just this
  endpoint) to drive the lock badge / whether to present `PINEntryView`.
- Surface `429` as "Too many tries — wait a moment."
- This endpoint requires an authenticated request (the parent's token, held by the shared device)
  — it is not a way to authenticate the *device* itself, only to check a specific profile's PIN
  once already inside an authenticated household session.

---

## 6. Set / change / clear a member PIN *(proposed — not yet implemented)*

**Why this is needed.** A PIN can currently only be set **once**, at teen creation
(`POST /v1/household/members/teens`, `pin` field). There is no way for a parent to **change** a
teen's PIN later or **remove** it — needed for the Family → member detail "Change/Remove PIN"
action in the client. (The parent's *own* device passcode that gates leaving family mode is a
separate, client-only local secret — not this endpoint.)

### `PUT /v1/household/members/{member_id}/pin`

**Parent only.** `member_id` must be a teen in the caller's household, else `404`.

**Request**

```json
{ "pin": "1234" }
```

| Field | Type | Rules |
|---|---|---|
| `pin` | string \| null | Exactly 4 digits to set/change; **`null` to clear** the PIN. |

- Setting/changing: hashes with the same scheme as teen creation (§5 — unsalted SHA-256), replaces
  the stored hash.
- Clearing (`null`): removes the stored hash so `pin_set` becomes `false` (profile then opens with
  no gate).

**Response `200`** — the updated **member** object (so the client sees the new `pin_set`):

```json
{ "id": "...", "role": "teen", "display_name": "Arjun", "pin_set": true, "...": "..." }
```

**Errors**

| Status | When |
|---|---|
| `403` | Caller isn't a parent |
| `404` | `member_id` not a member of the caller's household |
| `422` | `pin` present but not exactly 4 digits |

### Client integration (already built, awaiting this endpoint)

| Client symbol | Calls | Sends |
|---|---|---|
| `HouseholdService.setMemberPIN(memberID:pin:)` / `LiveAPIClient` | `PUT /v1/household/members/{id}/pin` | `{ "pin": "1234" }` or `{ "pin": null }` |

The Family → member detail screen has **Set/Change PIN** and **Remove PIN** actions wired to this;
it also mirrors the new PIN into the local `PINStore` (the offline-verify fallback).

---

## 7. Change account password *(implemented — `POST /v1/auth/change-password` in `app/routers/auth.py`)*

**Why this is needed.** The client now separates a parent's **Account** (their login) from
**Household** settings. The Account screen offers "Change Password", which needs a server endpoint.
Requires the current password so a borrowed, already-unlocked phone can't silently change it.

### `POST /v1/auth/change-password`

**Authenticated.** Changes the caller's own password.

**Request**

```json
{ "current_password": "hunter2pass", "new_password": "newhunter3pass" }
```

| Field | Type | Rules |
|---|---|---|
| `current_password` | string | Must match the stored hash. |
| `new_password` | string | Min 8 chars. |

**Response `204`**, empty body. Sessions are **not** invalidated — the caller's current
access/refresh tokens (and any other outstanding ones) keep working exactly as before. There's no
per-member token-revocation mechanism today (refresh-token rotation is single-use-per-token, not
a member-wide kill switch), so "log out other devices on password change" isn't implemented — flag
if you actually need that, it's a bigger change (would need e.g. a `password_changed_at` timestamp
checked against each token's `iat`).

**Errors**

| Status | When | Body |
|---|---|---|
| `401` | `current_password` incorrect | `{ "detail": "Current password is incorrect." }` |
| `422` | `new_password` too short, or the account doesn't use a password (e.g. Apple-only) | Standard Pydantic validation shape / `{ "detail": "This account does not use a password." }` |
| `429` | Rate limited — **not** a reuse of the login limiter; a dedicated per-member limit (5 attempts / 15 min), since the caller is already authenticated and identified, so per-member is more precise than per-IP/per-email here | `{ "detail": "Too many attempts. Try again later." }` |

---

## 8. Delete account *(proposed — not yet implemented)*

**Why this is needed.** The Account screen offers "Delete Account". Because a household currently
has **exactly one parent/owner** (no second-parent or ownership-transfer yet — master PRD §6.1),
deleting the sole owner's account should **delete the whole household** and everything in it
(teens, definitions, instances, ledger, series). This is irreversible and the client gates it
behind an explicit confirmation.

### `DELETE /v1/account`

**Authenticated.** Deletes the caller's account.

- **Sole owner (today's only case):** cascade-delete the household and all its data.
- *(Future, once multiple parents exist: if other admins remain, delete only this member and
  retain the household. Out of scope now — document the sole-owner behavior.)*

**Response `204`.** After this the client discards its session and returns to sign-in; any tokens
for the deleted account must stop working immediately.

**Errors**

| Status | When |
|---|---|
| `401` | Not authenticated |
| `409` (optional) | If you decide to block deletion while other admins/teens exist and require an explicit flag instead — confirm the desired rule with the client team. |

### Client integration (already built, awaiting these endpoints)

| Client symbol | Calls | Sends |
|---|---|---|
| `AccountService.changePassword(_:)` | `POST /v1/auth/change-password` | `{ current_password, new_password }` |
| `AccountService.deleteAccount()` | `DELETE /v1/account` | — |

The **Account** screen (separate from Household settings) wires Change Password, Sign Out, and
Delete Account; on delete-success the client signs out locally and returns to the sign-in screen.

**Open question for the backend team:** confirm the **sole-owner delete = delete household**
semantics above (vs. blocking deletion while teens exist). The client copy currently warns the user
that deletion removes the household and all data.

---

## 9. Bug: deleting a member with history fails *(fixed — option 2, soft delete)*

**Fixed as of migration `0003_member_archived_at`.** `DELETE /v1/household/members/{member_id}`
now **always** soft-deletes (`204`, never a FK error), regardless of how much history the member
has — verified live against a teen with a completed task instance *and* a ledger entry.

Went with **option 2, soft delete**, not cascade: `created_by`/`reviewed_by`/`resolved_by` columns
(on `task_definitions`/`task_instances`/`ledger_entries`/`claims`) intentionally have no
`ON DELETE CASCADE` — the ledger must stay an immutable audit record (PRD §8.1), so a real cascade
delete would have to either destroy ledger rows or null out who-did-what history, neither
acceptable. `Member.archived_at` was added, matching the existing `TaskDefinition`/`Series` archive
pattern exactly.

**What changed, precisely:**
- `GET /v1/household/members` gained `?include_archived=false` (default) — archived members are
  excluded unless you ask for them.
- `MemberResponse` gained `archived_at: string | null`.
- All of the removed member's history stays queryable exactly as before — ledger, task instances,
  reports (§11) all keep working for an archived member; there's just no *un*-archive endpoint yet.

No client action needed beyond what was already built — the existing delete flow's `204` handling
just starts working. If the client wants to actively distinguish "archived" members anywhere (e.g.
greyed out in a rarely-used "all members ever" view), `archived_at` is there to check.

---

## 10. Edit a series *(implemented — `PATCH /v1/series/{series_id}` in `app/routers/series.py`)*

> Note: this doc has grown beyond auth into "client-needed endpoints". This entry is about Series.

**Why this is needed.** The client's Series screen now supports **edit** (tap a series) and
**delete** (swipe → archives via the existing `DELETE /v1/series/{id}`). Delete works today; edit
needs an update endpoint — there's currently only GET/POST/DELETE for series.

**Scope is actually broader than requested — read before locking client UI to name/bonus only.**
The implemented endpoint also accepts `payout_mode` and `window_type` (still excludes `assignee_id`
and `task_definition_ids`, for the same "structural change is semantically messy mid-window" reason
given below). If the client intentionally wants to keep those two locked in the UI regardless of
what the server permits, that's a fine product decision — just know it's a client-side choice, not
a server limitation, in case "let a parent fix a typo'd payout mode" ever comes up as a request.

**Structural fields still not editable:** assignee and the bundled `task_definition_ids` — changing
either mid-window is semantically messy (instances/progress already exist against the old bundle).
Archive + recreate is still the path for those.

### `PATCH /v1/series/{series_id}`

**Parent only.** Partial update; omitted fields are unchanged.

**Request**

```json
{ "name": "Weekend Reset", "bonus_points": 120 }
```

| Field | Type | Rules |
|---|---|---|
| `name` | string? | 1–200 chars. |
| `bonus_points` | int? | ≥ 1. |
| `payout_mode` | string? | `individual_plus_bonus` \| `all_or_nothing` — implemented, optional for the client to expose. |
| `window_type` | string? | `weekly` \| `monthly` \| `custom` — implemented, optional for the client to expose; note `custom` still isn't fully wired (`docs/api-reference.md` §9). |

**Response `200`** — the updated `Series` object.

Doesn't retroactively touch an already-`complete` `SeriesInstance`'s payout (that's already on the
ledger). A still-`active` instance picks up the new `bonus_points`/`payout_mode` immediately, since
neither is frozen onto the instance until it actually completes.

**Errors**

| Status | When |
|---|---|
| `403` | Caller isn't a parent |
| `404` | Series not in the caller's household |
| `422` | Validation (empty name / bonus < 1) |

### Client integration

| Client symbol | Calls | Sends |
|---|---|---|
| `SeriesService.updateSeries(id:_:)` | `PATCH /v1/series/{id}` | `{ name?, bonus_points? }` (and optionally `payout_mode?`/`window_type?`, both now live if ever wanted) |
| `SeriesService.archiveSeries(id:)` | `DELETE /v1/series/{id}` | — *(works today)* |

---

## 11. Reports *(implemented — `GET /v1/reports/{member_id}` in `app/routers/reports.py`)*

**Why this is needed.** The parent app now has a **Reports** tab (ios-prd §8.5) with three charts
per teen: completion-rate trend, points earned per week, and excuse frequency. The PRD deliberately
warns against approximating this client-side from paginated ledger fetches (won't scale), so this
is a **server-aggregated** endpoint.

**Read this before wiring the client model — the shipped response differs from what was asked for
in two ways, both worth a deliberate decision on your side, not just a Codable adjustment:**

1. **`completion_rate` is `null` for a no-data week, not `0`.** The spec above said "return `0` (or
   omit)"; the implementation uses JSON `null` instead, on purpose — `0` reads as "0% completion,"
   which is a materially different (worse) signal than "no tasks were even due that week." If your
   model declares `completionRate: Double`, this will fail to decode on any zero-activity week
   (which is common — a brand-new teen, or any week before their first task). **Declare it
   `Double?`** and render a gap/skip in the line chart for `nil`, not a dip to zero.
2. **The response is keyed by member, not bare `{ "weeks": [...] }`**, and each week carries two
   extra counts beyond what was asked for:

```json
{
  "member_id": "...",
  "weeks": [
    { "week_start": "2026-07-13", "completed": 5, "missed": 1,
      "completion_rate": 0.833, "excuse_count": 1, "points_earned": 180 },
    { "week_start": "2026-07-20", "completed": 0, "missed": 0,
      "completion_rate": null, "excuse_count": 0, "points_earned": 0 }
  ]
}
```

`completed`/`missed` are additive, not a breaking change — decode them or ignore them, whichever's
convenient; they exist because they're what `completion_rate` is actually computed from and may be
independently useful (e.g. "5 done" as small text under the rate ring). `member_id` at the top level
is redundant with the path param but harmless to ignore.

### `GET /v1/reports/{member_id}?weeks=12`

**Teen: own report only. Parent: any member in household** (spec above said parent-only; teens
being able to see their own report seemed like an obvious extension of "own data" access already
granted everywhere else — flag if you'd rather it stay parent-only). `weeks` defaults to 12, must
be 1–52 (`422` otherwise, not silently clamped). Computed in household-local time, Monday-start
weeks (matches series-window computation elsewhere).

| Field | Type | Definition |
|---|---|---|
| `week_start` | string | `YYYY-MM-DD`, household-local Monday. |
| `completed` / `missed` | int | Raw counts for the week. |
| `completion_rate` | float \| null | `completed / (completed + missed)`. `excused`/`cancelled` instances are excluded from **both** — PRD §8.2: excused "does not count as missed in reports." `null` when `completed + missed == 0`. |
| `excuse_count` | int | Bucketed by **when the excuse was submitted**, not the task's due date. |
| `points_earned` | int | Gross — sums only `task_completed`/`series_bonus`/`excused_partial` ledger entries, not claims/reversals/adjustments. Will not reconcile against a claim-heavy week's balance delta; that's intentional. |

Every week in the requested range is present (zero-filled), oldest first — safe to index directly
into a chart's x-axis with no client-side gap-filling.

**Errors:** `404` (member not in household), `403` (teen requesting another member's report),
`422` (`weeks` outside 1–52).

### Client integration

| Client symbol | Calls |
|---|---|
| `ReportService.report(memberID:weeks:)` | `GET /v1/reports/{member_id}?weeks=` |

The Reports screen (Swift Charts) shows a teen picker + 4/12-week toggle and renders all three
series — update the model per the two shape notes above before wiring it up for real.

---

## 12. List pending claims (for the parent inbox) *(implemented — `GET /v1/wallet/claims` in `app/routers/wallet.py`)*

**Matches the spec below exactly** — same query params, same response shape (including
`member_name`), same error behavior. Verified live: teen submits → parent sees it in
`?status=pending` → resolve → drops out of the pending list, balance debited.

**One adjacent bug fixed while implementing this:** `POST /v1/wallet/claims/{id}/resolve` had no
household check at all — it looked a claim up by id globally, so (in principle) a parent could
have resolved a claim belonging to a *different* household if they somehow had its id. Now scoped
to the caller's household like everywhere else (`404` if the claim isn't in it). No client-visible
change unless you were relying on the old behavior, which you shouldn't have been.

**Why this is needed.** Reward **claims a teen submits do not appear anywhere for the parent to
act on.** The inbox is built on `GET /v1/approvals`, which returns only `review_pending` completions
and `excuse_pending` excuses — **not claims** — and there is **no endpoint to list claims at all**
today (only `POST /v1/wallet/claims` to create and `.../resolve` to resolve a known id). So a
submitted claim is invisible: the parent never sees it, and can't fulfil or decline it.

Two things are needed: a **list endpoint**, and the client surfaces the results **in the inbox**
alongside task approvals (the inbox is "everything awaiting the parent").

### `GET /v1/wallet/claims?status=pending`

**Parent:** all claims across the household (optionally filtered by `status` and/or `member_id`).
**Teen:** their own claims only.

| Query | Type | Notes |
|---|---|---|
| `status` | string? | `pending` \| `fulfilled` \| `declined`. Omitted = all. |
| `member_id` | string? | Restrict to one teen. |

**Response `200`** — array of claims. **Include `member_name`** so the parent inbox can show who
asked without a second lookup (mirrors `assignee_name` on `/v1/approvals`):

```json
[{
  "id": "…", "member_id": "…", "member_name": "Arjun",
  "points": 500, "requested_item": "New headphones",
  "status": "pending", "parent_note": null,
  "requested_at": "2026-08-03T10:00:00Z", "resolved_at": null
}]
```

Resolving already exists — `POST /v1/wallet/claims/{id}/resolve` `{ "approve": bool, "parent_note"? }`
(`approve:true` fulfils + debits; `approve:false` declines, no debit).

**Errors:** `403` for a teen requesting another member's claims.

### Client integration (already built, awaiting this endpoint)

| Client symbol | Calls |
|---|---|
| `WalletService.pendingClaims()` | `GET /v1/wallet/claims?status=pending` |
| `WalletService.resolveClaim(claimID:_:)` | `POST /v1/wallet/claims/{id}/resolve` *(exists)* |

The Inbox now shows a **"Reward claims"** section (each with **Fulfil** / **Decline**) above task
approvals. The client **tolerates the endpoint's absence** (treats a failure as "no claims"), so the
inbox keeps working for task approvals until this ships. Adding `member_name` to the model was the
only new field. *(This also unblocks the Family "outstanding claims count" from ios-prd §8.2.)*
