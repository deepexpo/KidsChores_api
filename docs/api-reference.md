# KidsChores API Reference (for the iOS client)

This is the as-built contract between the backend and any client — written for the iOS app.
For the *why* behind these rules, see [`docs/prd.md`](./prd.md). For the client's own UI/UX
requirements, see [`docs/ios-prd.md`](./ios-prd.md).

Interactive OpenAPI docs are also always available at `{base_url}/docs` when the server runs
with `APP_DEBUG=true`, generated live from the same code this reference describes.

- **Base URL (local dev):** `http://localhost:8000`
- **All endpoints are versioned under `/v1`.**
- **All request/response bodies are JSON.** `Content-Type: application/json`.
- **All timestamps are ISO-8601 with a UTC offset**, e.g. `"2026-08-01T23:59:00Z"`. Convert to
  the household's local timezone (`GET /v1/household` → `timezone`, an IANA name like
  `"America/Los_Angeles"`) for display — never assume device timezone equals household timezone.
- **IDs are UUID strings** (36 chars, e.g. `"e9f43ead-b58e-4c00-94c2-a0a06ffe096b"`), not integers.

---

## 1. Authentication

**Email + password is the current auth method** (client phase — see `docs/ios-prd.md`). Sign in
with Apple is fully implemented server-side and remains available, but is deferred client-side to
a later phase. Both issue the identical `AuthTokens` response shape and share every rule below.

### 1.1 Email + password

Full spec, including error shapes and rate limits: [`docs/auth-endpoints.md`](./auth-endpoints.md).
Summary:

```
POST /v1/auth/register          { email, password, display_name }        → 201 AuthTokens (role: "parent")
POST /v1/auth/login             { email, password }                       → 200 AuthTokens
POST /v1/auth/refresh           { refresh_token }                         → 200 AuthTokens (rotated)
POST /v1/auth/change-password   { current_password, new_password }        → 204 (authenticated)
```

`register` is **parent-only** and doubles as signup (creates a new household, same as Apple
sign-in below) — there is no separate "create household" endpoint. `login`/`register` are
rate-limited per-IP and per-email; `change-password` is rate-limited per-member (it's already
authenticated, so per-member is more precise than per-IP/email) — 5 attempts / 15 min, guards
against brute-forcing `current_password` via a stolen-but-valid access token. A `429` from any of
these means back off, not retry immediately. `change-password` does **not** invalidate other
sessions/tokens — see `docs/auth-endpoints.md` §7 if you need that.

### 1.2 Sign in with Apple *(implemented, deferred client-side)*

```
POST /v1/auth/apple
```

Not authenticated. Body:

| Field | Type | Notes |
|---|---|---|
| `identity_token` | string | The identity token from `ASAuthorizationAppleIDCredential`. |
| `display_name` | string | 1–100 chars. Only used the *first* time this Apple ID is seen. |

The backend verifies the token against Apple's public keys (`https://appleid.apple.com/auth/keys`).
If this is the first time the Apple `sub` has been seen, it **creates a new household** and makes
this member its `parent`. There is no separate "create household" endpoint — sign-in *is* signup.

### 1.3 `AuthTokens` — shared response shape

Every endpoint in §1.1/§1.2 returns this shape:

```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "member_id": "e84f671f-cbd3-45bb-a9c4-2f9ac28a7669",
  "household_id": "33e2a92a-7bdc-4df1-b697-87de0d8efee7",
  "role": "parent"
}
```

`role` is `"parent"` or `"teen"` — branch your entire navigation stack on this.

### 1.4 Using the access token

Every other endpoint requires:

```
Authorization: Bearer <access_token>
```

The access token is a JWT containing `sub` (member id) and `hid` (household id), expiring after
**60 minutes** (`exp` claim — decode client-side to know when to pre-emptively refresh, don't wait
for a 401). The refresh token expires after **30 days** and is now redeemable via
`POST /v1/auth/refresh` (§1.1) — it **rotates** on every use (single-use; a replayed refresh token
is rejected with `401`), so always persist whichever `refresh_token` came back from the *most
recent* successful call, not the one from initial sign-in.

**Household ID is never sent by the client.** It's derived from the token server-side on every
request — there is no `household_id` parameter anywhere in this API. Don't try to pass one.

### 1.5 Authorization model (enforced server-side, but the client should mirror it in UI)

- A **teen** may read only their own tasks/wallet/ledger, and may only write: complete own task,
  excuse own task, submit own claim, create own savings goal.
- A **parent** may read and write everything within their household, including other members' data.
- Nothing ever crosses household boundaries — a 404 is returned for any ID outside the caller's
  household, never a 403 (this avoids confirming a resource exists in someone else's household).

---

## 2. Idempotency (required for every mutating task action)

Mobile networks are flaky. `CompleteTaskRequest`, `ExcuseTaskRequest`, `ReviewRequest`,
`CancelTaskRequest`, and `BulkApproveRequest` all require an `idempotency_key: string` field.

**Client contract:** generate a UUID **once per user action** (e.g. once when the "Complete"
button is tapped), and reuse the *same* UUID for every retry of that specific action — never
generate a new one on retry. The server keys on `(member_id, idempotency_key)` for 24 hours; a
replayed request with the same key returns the **original** response instead of re-running the
mutation, so a double-tap or a retried timeout cannot double-award points. Do **not** reuse a key
across different logical actions (e.g. don't reuse the same key for completing two different
tasks).

```swift
// Sketch: one id per attempted action, persisted with the optimistic local write,
// reused verbatim if the request is retried.
let idempotencyKey = UUID().uuidString
```

This key is currently **only** wired up for the five task/approval mutation endpoints listed
above. `POST /v1/claims`, `/v1/claims/{id}/resolve`, `/v1/wallet/{id}/adjust`,
`/v1/definitions`, and `/v1/series` do **not** accept or enforce one yet — build simple
tap-to-disable-button retry protection client-side for those in the meantime.

---

## 3. Errors

Standard FastAPI error shape:

```json
{ "detail": "A comment is required when denying a completion." }
```

or, for Pydantic validation failures (422 on request body itself):

```json
{ "detail": [ { "loc": ["body", "point_value"], "msg": "...", "type": "..." } ] }
```

| Status | Meaning |
|---|---|
| `401` | Missing/invalid/expired token. Re-authenticate (see §1.4). |
| `403` | Authenticated, but this role/member can't do this (e.g. teen editing another teen's task). |
| `404` | Not found, or belongs to a different household (these are indistinguishable by design). |
| `422` | Validation failure, or a state-machine transition that isn't legal from the current status. |

For `422` on a task action specifically, `detail` is a human-readable sentence (e.g. *"Cannot
complete a task in status 'missed'. Task must be pending or overdue."*) — safe to surface directly
in an alert, but don't pattern-match on the string; branch on the HTTP status and the task's
`status` field instead.

---

## 4. Enums

Exact wire values (all lowercase snake_case strings — model as a `String`-backed Swift `enum`
with a case for each, plus consider a default/`unknown` fallback case for forward-compatibility):

```
MemberRole            parent | teen
TaskStatus            pending | review_pending | complete | overdue | excuse_pending
                       | excused | missed | cancelled
ScheduleType           one_time | daily | weekdays | weekly
ExcusedPayoutPolicy    excused_pays_nothing | excused_pays_partial | excused_pays_full
SeriesPayoutMode       individual_plus_bonus | all_or_nothing
SeriesWindowType       weekly | monthly | custom
SeriesStatus           active | complete | expired
LedgerEntryType        task_completed | series_bonus | excused_partial
                       | claim_fulfilled | manual_adjustment | reversal
ClaimStatus            pending | fulfilled | declined
```

`TaskStatus` drives almost all of the UI (badge color, available actions, empty states) — see the
full transition table in [`docs/prd.md §7`](./prd.md#7-task-lifecycle-state-machine).

---

## 5. Household & Members

### `GET /v1/household`
Any member. Returns the caller's household.

```json
{
  "id": "33e2a92a-...", "name": "Test Family", "timezone": "America/Los_Angeles",
  "points_label": "points", "excused_payout_policy": "excused_pays_nothing",
  "grace_period_hours": 24, "created_at": "2026-08-01T14:50:56Z"
}
```

`points_label` is the household's chosen word for points (default `"points"`) — always render
this instead of a hardcoded string; per the PRD's design thesis, never style it like currency.

### `PATCH /v1/household`
Parent only. Partial update — send only fields you're changing.

Body (all optional): `name`, `timezone`, `points_label`, `excused_payout_policy`,
`grace_period_hours` (int, 1–72).

### `GET /v1/household/members?include_archived=false`
Any member. Lists everyone in the household (parents and teens). Archived (removed) members are
excluded by default.

```json
[{ "id": "...", "household_id": "...", "role": "teen", "display_name": "Arjun",
   "avatar": null, "birthdate": "2011-01-01", "pin_set": true, "archived_at": null,
   "created_at": "..." }]
```

`pin_set` is a computed convenience (never the hash itself) — use it to decide whether to render a
lock badge / prompt for a PIN, without a round-trip to `verify-pin` per profile. `archived_at` is
non-null for a removed member — only visible with `include_archived=true`.

### `POST /v1/household/members/teens`
Parent only. Creates a teen profile. `201`.

| Field | Type | Notes |
|---|---|---|
| `display_name` | string | 1–100 chars. |
| `birthdate` | string | `YYYY-MM-DD`. Server rejects under-13 (COPPA), returns `422`. |
| `pin` | string? | Exactly 4 digits. Shared-device PIN, not a security boundary. |

The new teen has **no login of their own yet** — `auth_subject` is a server-generated placeholder,
and `password_hash` is unset. The profile works in **shared-device mode** immediately (parent mints
the profile, teen unlocks it with the PIN on the family iPad/iPhone). To let the teen log in
independently on their own device, use the linking flow below — a teen completing
`POST /v1/auth/apple` or `POST /v1/auth/register` directly would instead create a **second,
separate** household (`register` always mints a *parent* role), so don't route teens through those.

### `POST /v1/household/members/{member_id}/link-code`
Parent only. Generates a short-lived code the teen can redeem to attach their own email/password
login to this profile. `404` if the member isn't found; `422` if it isn't a teen profile; `409` if
this teen already has real credentials (only usable once, before linking).

```json
{ "code": "482913", "expires_at": "2026-08-09T15:19:47Z" }
```

- Code is **6 digits**, valid for **24 hours**, and **single-use** — redeeming it (successfully or
  not) consumes it.
- Generating a new code for the same teen invalidates any code generated earlier for them (only one
  valid code per teen at a time).
- Deliver the code out-of-band — read it to the teen, text it, whatever's convenient. There's no
  in-app delivery mechanism (no push/SMS integration for this).

### `POST /v1/auth/link`
Unauthenticated — this is how the teen redeems the code, typically during their own app's
onboarding ("I have a code from my parent" instead of "Create account").

```json
{ "code": "482913", "email": "arjun@example.com", "password": "..." }
```

Returns the same `TokenResponse` shape as `/v1/auth/register` / `/v1/auth/login` — the teen is
immediately logged in. Critically, `member_id` and `household_id` in the response are the
**existing** teen profile and the **existing** household — no new household is created, no data
carries over from a prior placeholder state because there wasn't any (the profile's history, if
any exists by the time it's linked, is untouched).

`404` for an invalid/expired/already-used code. `409` if the email is already in use by another
account, or if this profile was already linked by an earlier redemption. Rate-limited per-IP (10 /
15 min) and per-code (5 / 15 min) — a 6-digit code is brute-forceable given enough attempts, so the
per-code limit matters more than the per-IP one here.

### `POST /v1/household/members/{member_id}/verify-pin`
Any member (in practice, the parent's token, held by the shared device). Full spec:
[`docs/auth-endpoints.md`](./auth-endpoints.md) §5.

```json
{ "pin": "1234" }
```

Always `200` for a well-formed request — a wrong PIN is a normal check result, not a request
failure:

```json
{ "valid": true, "pin_set": true }
```

Rate-limited (5 attempts / 5 min, per-`member_id` — deliberately not per-IP, since a shared device
means several teens' profiles are legitimately checked from the same IP) — this is friction against
casual sibling access on a shared device (master PRD §6.1), not a real auth boundary, so the rate
limit (not hash strength) is what protects the 10,000-combination PIN space.

### `DELETE /v1/household/members/{member_id}`
Parent only. `204`. A parent cannot remove themself.

**This archives, it does not hard-delete.** A member with any activity (a completed task, a
ledger entry, a reviewed excuse...) is referenced by `created_by`/`reviewed_by`/`resolved_by`
columns that intentionally have no `ON DELETE CASCADE` — cascading those would null out or
destroy ledger/audit history, which the ledger's append-only design (§8.1 of the master PRD)
forbids. So this sets `archived_at` instead (same pattern as `TaskDefinition`/`Series`): the
member disappears from `GET /v1/household/members` by default, but all their history —
task instances, ledger entries, everything — is untouched and still queryable
(`GET /v1/wallet/{member_id}/ledger`, `GET /v1/reports/{member_id}`, etc. all keep working for an
archived member; there's just no path back to *un*-archive one yet).

---

## 6. Task Definitions (parent-managed templates)

### `GET /v1/definitions?include_archived=false`
Any member.

```json
{
  "id": "...", "household_id": "...", "assignee_id": "...",
  "title": "Wash dishes", "description": null, "point_value": 40,
  "schedule_type": "daily", "weekday_mask": null,
  "start_date": "2026-08-01", "end_date": null, "due_time": "23:59",
  "requires_review": false, "series_id": null,
  "archived_at": null, "created_at": "..."
}
```

`weekday_mask`: only meaningful when `schedule_type == "weekdays"`. 7-bit mask, **bit 0 = Monday
… bit 6 = Sunday** (`1 << Calendar.Component.weekday` needs remapping — Swift's `Weekday` is
Sun=1…Sat=7, so don't pass it through directly).

### `POST /v1/definitions`
Parent only. `201`.

| Field | Type | Notes |
|---|---|---|
| `assignee_id` | string | Must be a member of the same household. |
| `title` | string | 1–200 chars. |
| `description` | string? | |
| `point_value` | int | 1–10,000. |
| `schedule_type` | enum | `one_time` \| `daily` \| `weekdays` \| `weekly`. |
| `weekday_mask` | int? | 0–127. Required (in practice) when `schedule_type == "weekdays"`. |
| `start_date` | string | `YYYY-MM-DD`. |
| `end_date` | string? | `YYYY-MM-DD`. |
| `due_time` | string | `HH:MM`, 24h, default `"20:00"`. |
| `requires_review` | bool | Default `false` (auto-approve). |
| `series_id` | string? | Usually left unset — `POST /v1/series` links definitions itself. |

Creating a definition materializes an instance **immediately** for the relevant date, so the
teen doesn't have to wait on the nightly job to see it:
- `schedule_type: "one_time"` → its single instance is created right away, for `start_date`,
  regardless of whether that date is today or in the future.
- Recurring types (`daily` / `weekdays` / `weekly`) → today's instance is created immediately if
  today falls on the schedule; otherwise there's nothing due yet, so nothing to create.

Everything beyond that (future recurring occurrences, the rolling 14-day horizon) still comes from
the nightly generation run (server-side, §9.1 of the PRD) — this on-demand path only ever creates
the one instance that's immediately relevant, sharing the exact same generation logic
(`upsert_instance_for_date` in `app/workers/generate_instances.py`) so there's no drift between
the two paths, and no duplicate rows (`UNIQUE(definition_id, due_at)` makes it safe either way).

### `PATCH /v1/definitions/{id}`
Parent only. Partial update. Only affects **future, not-yet-generated** instances — never
retroactively changes instances that already exist (PRD §8.4). Cannot change `assignee_id`,
`start_date`, or `series_id` via this endpoint.

### `DELETE /v1/definitions/{id}`
Parent only. `204`. Archives (soft-delete) — stops future generation, preserves history. Not
reversible via the API today.

---

## 7. Task Instances (what teens actually interact with)

All under `/v1/tasks/...`. `TaskInstanceResponse` shape (returned by every endpoint in this
section):

```json
{
  "id": "0db50cbe-303a-44d8-8729-881e6094e07f",
  "definition_id": "a28dd6af-...",
  "assignee_id": "e9f43ead-...",
  "due_at": "2026-08-01T23:59:00Z",
  "point_value": 40,
  "status": "complete",
  "completed_at": "2026-08-01T14:55:00.788742Z",
  "completion_note": null,
  "excuse_text": null,
  "review_comment": null,
  "series_instance_id": null,
  "title": null,
  "description": null
}
```

> **⚠️ Known gap:** `title` and `description` are declared on the response model as a
> "denormalised convenience" but **the backend does not currently populate them** — they are
> always `null` on every endpoint in this section. Until that's fixed server-side, the client
> must separately fetch `GET /v1/definitions` and join on `definition_id` client-side (e.g. cache
> definitions by id and look up the title when rendering a `TaskInstanceResponse`). This is the
> single most important gap to know about before building the Today screen — see
> `docs/ios-prd.md` for the recommended client-side join pattern.

### `GET /v1/tasks/today?member_id=`
Any member. Teen: returns their own tasks due today (household-local "today", not device-local).
Parent: pass `member_id` to view a specific teen's day; omitting it returns **the parent's own**
tasks (which for a parent-only household will be empty — the UI should treat a parent account
without `member_id` as "pick a teen" rather than showing an empty state as if it were the teen's).

### `GET /v1/tasks/week?start=YYYY-MM-DD&member_id=`
Same auth rules as above. `start` is the first day of a 7-day window in household-local time.

### `POST /v1/tasks/instances/{id}/complete`
Teen (own task only) or parent. Requires idempotency key (§2).

```json
{ "idempotency_key": "…", "note": "optional string" }
```

Returns the updated instance. If the definition has `requires_review: true`, status becomes
`review_pending` and **no points are awarded yet** — don't show a balance-increase animation
until you see `status == "complete"` in the response (which happens immediately for
non-review tasks, or later via the parent's review action for review-gated ones).

If this instance belongs to a series and completing it finishes the whole series window, the
bonus is awarded server-side as part of this same call — there's no separate "check series"
step the client needs to perform, but the *series bonus ledger entry* won't be visible until you
next fetch `GET /v1/wallet/{id}/ledger`; consider re-fetching the wallet after any completion that
has a non-null `series_instance_id`.

### `POST /v1/tasks/instances/{id}/excuse`
Teen (own task only) or parent. Requires idempotency key.

```json
{ "idempotency_key": "…", "excuse_text": "min 10 characters" }
```

Only legal from `pending` or `overdue`. Moves to `excuse_pending`; the grace-period clock pauses
server-side the instant this succeeds.

### `POST /v1/tasks/instances/{id}/review`
Parent only. Requires idempotency key. Handles **both** `review_pending` (a completion awaiting
verification) and `excuse_pending` (an excuse awaiting a decision) — the server branches on the
instance's current status, so the client doesn't need two different endpoints.

```json
{ "idempotency_key": "…", "approve": true, "comment": "optional unless approve=false" }
```

`comment` is **required** when `approve: false` — validated both client-request-shape-side (422 if
missing) and business-logic-side; always show a comment field before enabling the "Deny" button.

### `POST /v1/tasks/instances/{id}/cancel`
Parent only. Requires idempotency key. Voids a `pending` or `overdue` instance (e.g. "family was
away") — pays no points, doesn't count against the teen in reports.

```json
{ "idempotency_key": "…", "reason": "optional string, stored as review_comment" }
```

---

## 8. Approvals (parent inbox)

### `GET /v1/approvals`
Parent only. Every `review_pending` completion and `excuse_pending` excuse across the household,
oldest-first (PRD: "the parent sees what needs attention most urgently at the top").

```json
[{
  "type": "excuse",
  "task_instance_id": "2de3d656-...",
  "task_title": "Wash dishes",
  "assignee_name": "Arjun",
  "point_value": 40,
  "submitted_at": "2026-08-02T10:00:00Z",
  "excuse_text": "Have a debate tournament that day, will make up for it after"
}]
```

Unlike `TaskInstanceResponse`, `task_title` **is** populated here — this endpoint is the
recommended source of truth for anything inbox-related; don't reconstruct it from
`/v1/tasks/today`. `type` is `"completion"` or `"excuse"` — branch your row UI on it (a completion
card shows the optional `completion_note`... actually note this endpoint doesn't return
`completion_note`; if you need it, fetch the instance directly). An empty array is the **success
state** — design it as such, not as a blank/error state (PRD §10.2).

### `POST /v1/approvals/bulk`
Parent only. Requires idempotency key. Approve/deny multiple items in one call.

```json
{
  "idempotency_key": "…",
  "items": [
    { "task_instance_id": "...", "approve": true },
    { "task_instance_id": "...", "approve": false, "comment": "Not clean enough" }
  ]
}
```

Response — **always `200`**, even if individual items failed; check each item's `success`:

```json
{
  "results": [
    { "task_instance_id": "...", "success": true, "status": "complete", "error": null },
    { "task_instance_id": "...", "success": false, "status": null, "error": "A comment is required when denying a completion." }
  ]
}
```

Design the bulk-approve UI (e.g. a "select all, swipe to approve" gesture in the inbox) to show a
per-item result afterward rather than a single pass/fail toast — a batch of 10 with 1 failure is
the expected case, not an edge case.

---

## 9. Series

### `GET /v1/series?include_archived=false`
Any member.

```json
{
  "id": "...", "household_id": "...", "name": "Weekend Reset",
  "assignee_id": "...", "bonus_points": 100,
  "payout_mode": "individual_plus_bonus", "window_type": "weekly",
  "archived_at": null, "created_at": "..."
}
```

### `POST /v1/series`
Parent only. `201`.

| Field | Type | Notes |
|---|---|---|
| `name` | string | 1–200 chars. |
| `assignee_id` | string | |
| `bonus_points` | int | ≥ 1. |
| `payout_mode` | enum | `individual_plus_bonus` \| `all_or_nothing`. |
| `window_type` | enum | `weekly` \| `monthly` \| `custom` (⚠️ `custom` currently falls back to a 7-day window server-side — there's no way to pass explicit custom dates yet; don't offer "custom" as a picker option in v1 UI). |
| `task_definition_ids` | [string] | ≥ 1 item. Existing definitions to bundle; they're re-parented onto this series. |

Creates the series **and** its first `SeriesInstance` window in one call.

### `PATCH /v1/series/{id}`
Parent only. Partial update — send only fields you're changing.

Body (all optional): `name`, `bonus_points`, `payout_mode`, `window_type`. Deliberately excludes
`assignee_id` and `task_definition_ids` (bundle membership) — reassigning a series or changing
its member definitions isn't exposed here, same as `PATCH /v1/definitions/{id}` excluding
`assignee_id`.

Doesn't affect an already-`complete` `SeriesInstance`'s payout — that's already written to the
ledger. A still-`active` instance picks up the new `bonus_points`/`payout_mode` immediately, since
neither is frozen onto the instance until it actually completes and pays out.

### `GET /v1/series/{id}/instances`
Any member. Each window with a computed progress string.

```json
[{ "id": "...", "series_id": "...", "window_start": "...", "window_end": "...",
   "status": "active", "completed_at": null, "progress": "2 of 3 complete" }]
```

`progress` is `null` if no task instances have been generated into this window yet (nightly job
hasn't run since the series was created). `status` transitions to `"complete"` automatically the
moment the last eligible task instance completes — there's no explicit "finish series" call.

### `DELETE /v1/series/{id}`
Parent only. `204`. Archives — existing instances/history untouched.

---

## 10. Wallet, Ledger, Claims, Savings Goals

### `GET /v1/wallet/{member_id}`
Teen: own wallet only. Parent: any member in household.

```json
{
  "member_id": "...", "balance": 170, "points_label": "points",
  "active_savings_goal": { "id": "...", "member_id": "...", "title": "AirPods",
                            "target_points": 2000, "created_at": "...", "achieved_at": null }
}
```

`balance` is always server-computed as `SUM(delta)` — never derive or cache it client-side beyond
a single screen's lifetime; re-fetch after any action that could plausibly change it.
`active_savings_goal` is the most recent goal not yet achieved, or `null`.

### `GET /v1/wallet/{member_id}/ledger?limit=50&offset=0`
Same access rule as above. Paginated, newest first.

```json
[{ "id": "...", "delta": 40, "balance_after": 40, "entry_type": "task_completed",
   "reason": "Completed: Wash dishes", "created_at": "..." }]
```

`reason` is a pre-formatted, human-readable string generated server-side (e.g. `"Completed: Wash
dishes"`, `"Series bonus: Weekend Reset"`, `"Excused (50%): ..."`, `"Reversal: <parent's reason>"`)
— render it directly, no client-side formatting needed. `delta` is signed; render red/negative for
`claim_fulfilled` and `reversal` types, green/positive otherwise (a negative `manual_adjustment` is
also possible — see PRD §14 Open Question 3 on whether this should be a first-class "deduction"
feature or stay deliberately unceremonious in the UI).

### `POST /v1/wallet/{member_id}/adjust`
Parent only. Requires `member_id` to be in the parent's household.

```json
{ "member_id": "...", "delta": -20, "reason": "min 5 chars, mandatory" }
```

Note the request body **also** contains `member_id` (redundant with the path parameter — the path
value is authoritative; the body field is currently ignored server-side). Just mirror the path
value into the body to satisfy the schema; don't rely on the body field having any effect.

### `GET /v1/wallet/claims?status=&member_id=`
Parent: all claims across the household, optionally filtered by `status`
(`pending`/`fulfilled`/`declined`) and/or `member_id`. Teen: own claims only (`403` if `member_id`
is passed and isn't their own). **This is the parent inbox's source for reward claims** — they do
**not** appear in `GET /v1/approvals`, which is task-completions/excuses only; the client is
expected to merge the two into one inbox UI, not treat this as a second, separate screen.

```json
[{ "id": "...", "member_id": "...", "member_name": "Arjun", "points": 500,
   "requested_item": "New headphones", "status": "pending", "parent_note": null,
   "requested_at": "2026-08-03T10:00:00Z", "resolved_at": null }]
```

`member_name` is included on every claim response (here and on `POST /v1/wallet/claims` /
`.../resolve` below) so the inbox can render who asked without a second lookup.

### `POST /v1/wallet/claims`
Teen (submits for self) or parent. `201`. **Does not debit points** — submission just records
intent; only `resolve` debits.

```json
{ "points": 500, "requested_item": "New headphones" }
```

### `POST /v1/wallet/claims/{claim_id}/resolve`
Parent only. `claim_id` must belong to the caller's household (`404` otherwise).

```json
{ "approve": true, "parent_note": "optional" }
```

`approve: true` debits `points` from the ledger and sets `status: "fulfilled"`. `approve: false`
sets `status: "declined"` — **no debit**, the teen's balance is untouched.

### `POST /v1/wallet/{member_id}/goals`
Teen (own) or parent. `201`.

```json
{ "title": "AirPods", "target_points": 2000 }
```

There's no endpoint to mark a goal achieved or to delete/edit one yet — `achieved_at` is a column
that exists on the model but nothing currently sets it. Treat savings goals as create-and-display
only for now; compute "progress toward goal" client-side as `balance / target_points`.

---

## 11. Reports

### `GET /v1/reports/{member_id}?weeks=12`
Teen: own report only. Parent: any member in household. `weeks` defaults to 12, must be 1–52
(`422` otherwise). Backs the master PRD §10.2 "Reports" screen — completion rate, excuse
frequency, and points earned, bucketed into household-local weeks (Monday-start).

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

Weeks are returned **oldest first**, and every week in the requested range is present even if
empty (zero-filled, not omitted) — safe to index directly into a chart's x-axis without gap-filling
client-side.

- **`completion_rate`** = `completed / (completed + missed)`. `excused` and `cancelled` instances
  are deliberately excluded from both the numerator and denominator — PRD §8.2: an excused task
  "does not count as missed in reports." `null` (not `0`) when a week has no complete-or-missed
  tasks at all, so don't render `null` as a 0% bar.
- **`excuse_count`** buckets by when the excuse was *submitted* (`excuse_submitted_at`), not the
  task's due date — an overdue task excused days later shows up in the week it was actually
  excused.
- **`points_earned`** sums only `task_completed` / `series_bonus` / `excused_partial` ledger
  entries — not claims, reversals, or manual adjustments. This is gross earning, not net balance
  change; it will not reconcile against a claim-heavy week's ending balance, by design.
- Both the task-instance and ledger queries are scoped by the *requested week range*, not the
  member's full history — a `weeks=52` call is bounded, not "everything ever."

---

## 12. Endpoint summary

| Method | Path | Auth | Idempotent? |
|---|---|---|---|
| POST | `/v1/auth/register` | none | no (rate-limited) |
| POST | `/v1/auth/login` | none | no (rate-limited) |
| POST | `/v1/auth/refresh` | none (bearer refresh token in body) | — (single-use by design) |
| POST | `/v1/auth/change-password` | any (authenticated) | no (rate-limited) |
| POST | `/v1/auth/apple` | none | — |
| POST | `/v1/auth/link` | none | — (rate-limited, code single-use) |
| GET | `/v1/household` | any | — |
| PATCH | `/v1/household` | parent | no |
| GET | `/v1/household/members` | any | — |
| POST | `/v1/household/members/teens` | parent | no |
| POST | `/v1/household/members/{id}/link-code` | parent | no |
| POST | `/v1/household/members/{id}/verify-pin` | any | — (rate-limited) |
| DELETE | `/v1/household/members/{id}` | parent | — |
| GET | `/v1/definitions` | any | — |
| POST | `/v1/definitions` | parent | no |
| PATCH | `/v1/definitions/{id}` | parent | no |
| DELETE | `/v1/definitions/{id}` | parent | — |
| GET | `/v1/tasks/today` | any | — |
| GET | `/v1/tasks/week` | any | — |
| POST | `/v1/tasks/instances/{id}/complete` | teen(own)/parent | **yes** |
| POST | `/v1/tasks/instances/{id}/excuse` | teen(own)/parent | **yes** |
| POST | `/v1/tasks/instances/{id}/review` | parent | **yes** |
| POST | `/v1/tasks/instances/{id}/cancel` | parent | **yes** |
| GET | `/v1/approvals` | parent | — |
| POST | `/v1/approvals/bulk` | parent | **yes** |
| GET | `/v1/series` | any | — |
| POST | `/v1/series` | parent | no |
| PATCH | `/v1/series/{id}` | parent | no |
| GET | `/v1/series/{id}/instances` | any | — |
| DELETE | `/v1/series/{id}` | parent | — |
| GET | `/v1/wallet/{member_id}` | teen(own)/parent | — |
| GET | `/v1/wallet/{member_id}/ledger` | teen(own)/parent | — |
| POST | `/v1/wallet/{member_id}/adjust` | parent | no |
| GET | `/v1/wallet/claims` | teen(own)/parent | — |
| POST | `/v1/wallet/claims` | any | no |
| POST | `/v1/wallet/claims/{id}/resolve` | parent | no |
| POST | `/v1/wallet/{member_id}/goals` | teen(own)/parent | no |
| GET | `/v1/reports/{member_id}` | teen(own)/parent | — |

---

## 13. Recommended client architecture notes

- **Offline-first Today view.** Cache the day's `TaskInstanceResponse` list locally (SwiftData).
  On `complete`/`excuse`/`cancel`, write the optimistic state locally immediately, queue the
  request with its idempotency key, and reconcile on response — see `docs/ios-prd.md §12` for the
  full offline/sync design.
- **Poll, don't assume push works yet.** No push-notification-sending code exists server-side yet
  beyond a `print()` stub in the Celery worker (`notify_digest.py`) — build the inbox/today views
  to refresh on foreground and via pull-to-refresh; don't architect around push arriving reliably
  in v0.1.
- **Cache `GET /v1/definitions` client-side** and join on `definition_id` to work around the
  `title`/`description` gap (§7) — refresh this cache whenever a definition-management screen is
  used, since it changes rarely compared to task instances.
- **Decode the JWT client-side** to read `exp` and proactively call `POST /v1/auth/refresh`
  (§1.4) before expiry rather than reactively handling 401s mid-flow, which is a worse UX for a
  teen mid-task-completion. Always persist the rotated `refresh_token` from the refresh response.
