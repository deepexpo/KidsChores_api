# Product Requirements Document
## KidsChores — Family Task & Rewards App

**Author:** KD Singh
**Date:** 31 July 2026
**Status:** Draft v1 — for review
**Target platforms:** iPhone + iPad (SwiftUI, single codebase)
**Backend:** Python (FastAPI + PostgreSQL)
**Target user age:** Teens, 13–17

---

## 1. Summary

A household app where a parent assigns tasks — one-time or recurring — each carrying a point value. When a teen completes a task, points land in their wallet. Points accumulate and can be claimed for real-world rewards (cash, privileges, purchases) at a time of the teen's choosing.

The app also handles the messy part of real households: tasks that don't get done. A teen can submit a reason, and the parent approves or denies it. Approved reasons can preserve streaks and, at the parent's discretion, still pay out.

Tasks can be bundled into **series** — a set of related tasks that pays a bonus only when the whole set is completed.

---

## 2. Problem statement

Existing chore apps fail teenagers in three predictable ways:

1. **They're built for young children.** Sticker charts, cartoon mascots, and "Great job!" confetti read as condescending to a 15-year-old, and the app gets deleted within a week.
2. **They ignore that life happens.** A task marked "missed" because a teen had a debate tournament creates resentment and makes the system feel arbitrary rather than fair.
3. **The ledger is untrustworthy.** Points appear and disappear without explanation, parents forget to pay out, and the teen concludes the whole thing is theatre.

The product thesis is that a rewards system for teenagers succeeds or fails on **perceived fairness**, not on gamification polish. Every design decision below should be read through that lens.

---

## 3. Goals and non-goals

### Goals

- Give parents a low-friction way to assign, track, and value recurring household responsibilities.
- Give teens a transparent, auditable record of what they've earned and why.
- Handle non-completion gracefully via a structured excuse-and-approval flow.
- Support multi-task series with bonus payouts to encourage sustained effort.
- Work equally well on a teen's iPhone and a parent's iPad.

### Non-goals (v1)

- **No real money movement.** The app never touches payment rails. Points are a record-keeping device; parents settle up however they already do. This avoids money-transmitter regulation, App Store IAP entanglement, and a large compliance burden.
- **No Android.** Revisit after product-market fit within families. (Note: this is a real constraint — mixed-platform households will be unable to use the app at all. See Open Questions.)
- **No social/leaderboard features across households.** Comparing kids to strangers' kids is a support-ticket generator.
- **No AI task suggestions, photo verification, or location-based auto-completion.** Deferred to v2+.

---

## 4. Personas

**Priya, 44 — the Parent/Admin.**
Manages a household of two teens. Sets up tasks on the iPad in a Sunday-evening planning session, then checks approvals from her phone during the week in 30-second bursts. Her failure mode is forgetting to approve things, which stalls the entire system. **The app must make approvals nearly frictionless and must nag her, not the kids.**

**Arjun, 15 — the Teen.**
Has his own iPhone and lives on it. Motivated by saving toward a specific purchase. Will absolutely test the boundaries of the excuse system to see what he can get away with. Will abandon the app instantly if it feels babyish or if he suspects points are being calculated unfairly. **He needs to see his balance and his progress toward a goal in one glance.**

**Meera, 13 — the Younger Teen.**
Shares a family iPad, no personal phone. Less strategic than Arjun; more likely to simply forget. **Needs reminders and a very short path from "open app" to "mark done."**

---

## 5. Core concepts (glossary)

| Term | Definition |
|---|---|
| **Household** | The top-level tenant. Contains members, tasks, and all data. Has a single timezone and currency-label setting. |
| **Member** | A user within a household, with role `parent` or `teen`. Parents have admin rights. |
| **Task Definition** | The reusable template: title, description, point value, assignee, schedule. Editing it affects future instances only. |
| **Task Instance** | A single occurrence of a Task Definition on a specific date, with its own status. This is what teens interact with. |
| **Series** | An ordered or unordered set of Task Definitions with a bonus payout on full completion within a window. |
| **Wallet** | A teen's point balance, derived from the ledger. Never stored as a mutable number. |
| **Ledger Entry** | An immutable record of a point change, with a reason and a link to its source. |
| **Claim** | A teen's request to convert points into a real-world reward. Requires parent fulfilment. |
| **Excuse** | A teen's written explanation for a task they could not complete, subject to parent review. |

### 5.1 Why Definition and Instance must be separate

This is the single most important structural decision in the document.

If "make your bed, daily, 5 points" is stored only as a template, the system cannot answer *"did Arjun make his bed on Tuesday?"* — there is nowhere to record it. It cannot support streaks, cannot show history, and cannot let a parent raise the value from 5 to 8 points without silently rewriting what past completions were worth.

Task Instances are generated from Definitions ahead of time (see §9.1). Once an instance is created, it is a historical fact and its point value is frozen at creation.

---

## 6. Feature requirements

Priorities: **P0** = required for launch, **P1** = fast-follow, **P2** = later.

### 6.1 Household & accounts

- **P0** — Parent signs up with Sign in with Apple or email; this creates the household and makes them admin.
- **P0** — Parent creates teen profiles. Teens 13+ may have their own login (Sign in with Apple or email + password) on their own device.
- **P0** — Shared-device mode: profiles on one iPad, protected by a 4-digit PIN per profile. Sufficient friction to prevent casual sibling mischief; not a security boundary.
- **P0** — Household timezone setting. Determines when "today" ends.
- **P1** — Second parent/guardian can be invited with full admin rights.
- **P2** — Household transfer of ownership; separated-parents mode with two households sharing a teen.

### 6.2 Task management (parent)

- **P0** — Create a Task Definition with: title, optional description, point value, assignee, schedule.
- **P0** — Schedule types: **one-time** (specific date), **daily**, **specific weekdays** (e.g. Mon/Wed/Fri), **weekly** (any day within the week).
- **P0** — Set a due time within the day; after this, an instance becomes overdue.
- **P0** — Edit a Definition. Changes apply only to instances not yet generated. Editing an active instance is a separate, explicit action.
- **P0** — Archive a Definition. Stops future generation; preserves history.
- **P0** — Assign a task to one teen. (Multi-assignee and "first to claim it" are **P2**.)
- **P1** — Duplicate a Definition, and a starter library of common household tasks.
- **P2** — Require photo proof on specified tasks.

### 6.3 Task completion (teen)

- **P0** — Today view listing everything due today, sorted by due time.
- **P0** — Mark a task complete in one tap.
- **P0** — Optionally attach a note when marking complete.
- **P0** — Submit an excuse on an overdue or upcoming task, with free-text reason (required, min. 10 characters).
- **P1** — Week view showing the full week's tasks and their statuses.
- **P1** — Propose a task to a parent ("can I do X for Y points?"). Meaningfully increases teen buy-in by giving them agency in the system rather than making them purely a recipient of instructions.

### 6.4 Approvals (parent)

- **P0** — Approval inbox: single list of everything awaiting parent action (completions if verification is on, plus all excuses).
- **P0** — Approve or deny with one tap; optional comment. Denial requires a comment.
- **P0** — Per-Definition setting: `auto_approve` (completion pays immediately) or `requires_review`.
- **P0** — Bulk approve.
- **P1** — Daily digest push notification summarising pending approvals.

### 6.5 Series

- **P0** — Create a Series: name, member Task Definitions, window (this week / this month / custom date range), bonus point value.
- **P0** — Payout mode, chosen per series:
  - `individual_plus_bonus` — each task pays its own value on completion, plus the bonus if all complete. *(Default.)*
  - `all_or_nothing` — member tasks pay nothing individually. On full completion, the teen receives the sum of all member task values **plus** the bonus, in a single ledger entry.
- **P0** — Progress indicator (e.g. "4 of 7 complete").
- **P1** — Recurring series that reset each week.

### 6.6 Wallet & claims

- **P0** — Current balance, prominently displayed.
- **P0** — Full transaction history with a human-readable reason for every entry.
- **P0** — Teen submits a Claim: amount of points + what they want in return (free text or from a parent-defined reward menu).
- **P0** — Parent marks a Claim fulfilled, which debits the points. Points are **not** debited when the claim is submitted, only on fulfilment — otherwise an unfulfilled claim traps the teen's balance.
- **P0** — Parent can make a manual adjustment (positive or negative) with a mandatory reason. Every override is visible to the teen.
- **P1** — Savings goal: teen names a target ("AirPods — 2,000 pts") and sees a progress bar. This is the strongest single retention feature for the teen persona; it converts an abstract balance into something they care about.
- **P1** — Parent-defined reward menu with fixed prices.

### 6.7 Notifications

- **P0** — Teen: reminder at a configurable time before a task's due time.
- **P0** — Teen: excuse approved or denied.
- **P0** — Parent: new excuse submitted.
- **P1** — Parent: daily nudge if approvals are pending more than 24 hours. *Parent inaction is the primary way these systems die; this notification is doing more work than it appears.*
- **P1** — Teen: series about to expire with tasks outstanding.
- **P2** — Quiet hours; per-notification-type toggles.

---

## 7. Task lifecycle state machine

Every Task Instance occupies exactly one state.

```
                    ┌────────────────────────────────────────┐
                    │              PENDING                   │
                    │  (created, due time not yet passed)    │
                    └────────────────────────────────────────┘
                       │              │                │
        marks complete │              │ submits        │ due time
       (auto_approve)  │              │ excuse         │ passes
                       ▼              ▼                ▼
                 ┌──────────┐   ┌──────────┐    ┌──────────┐
                 │ COMPLETE │   │ EXCUSE_  │    │ OVERDUE  │
                 │ +points  │   │ PENDING  │    │          │
                 └──────────┘   └──────────┘    └──────────┘
                                  │      │          │     │
                        approved  │      │ denied   │     │ submits
                                  ▼      ▼          │     │ excuse
                            ┌────────┐ ┌────────┐   │     ▼
                            │EXCUSED │ │ MISSED │◄──┘  (back to
                            │        │ │        │       EXCUSE_PENDING)
                            └────────┘ └────────┘
                                            ▲
                              grace period  │ expires with
                                            │ no action
                                            │
        marks complete      ┌──────────────────────┐
       (requires_review) ──►│  REVIEW_PENDING      │
                            └──────────────────────┘
                               │            │
                     approved  │            │ denied
                               ▼            ▼
                         ┌──────────┐  ┌──────────┐
                         │ COMPLETE │  │ PENDING  │
                         │ +points  │  │ (retry)  │
                         └──────────┘  └──────────┘
```

### 7.0 Complete transition table

The diagram above shows the main paths; this table is authoritative and should be what the implementation is tested against.

| From | Trigger | To |
|---|---|---|
| `pending` | teen completes, `auto_approve` | `complete` |
| `pending` | teen completes, `requires_review` | `review_pending` |
| `pending` | teen submits excuse | `excuse_pending` |
| `pending` | due time passes | `overdue` |
| `pending` | parent cancels | `cancelled` |
| `overdue` | teen completes within grace, `auto_approve` | `complete` |
| `overdue` | teen completes within grace, `requires_review` | `review_pending` |
| `overdue` | teen submits excuse | `excuse_pending` |
| `overdue` | grace period expires | `missed` |
| `overdue` | parent cancels | `cancelled` |
| `review_pending` | parent approves | `complete` |
| `review_pending` | parent denies, before due time | `pending` |
| `review_pending` | parent denies, after due time | `overdue` |
| `excuse_pending` | parent approves | `excused` |
| `excuse_pending` | parent denies, grace remaining | `overdue` |
| `excuse_pending` | parent denies, grace expired | `missed` |
| `complete` | parent reverses approval | `overdue` or `missed`, plus a `reversal` ledger entry |

`missed`, `excused`, and `cancelled` are terminal. `complete` is terminal except via explicit parent reversal.

Note that `review_pending` and `excuse_pending` **suspend the grace-period clock** — a teen must not be penalised for a parent's slow response. Resume the clock from where it paused on denial.

### 7.1 State definitions

| State | Meaning | Pays points? |
|---|---|---|
| `pending` | Live, not yet due | — |
| `review_pending` | Teen marked complete; awaiting parent verification | Not yet |
| `complete` | Done and accepted | **Yes**, full value |
| `overdue` | Due time passed, no action taken. Still actionable during grace period | — |
| `excuse_pending` | Teen submitted a reason; awaiting parent | Not yet |
| `excused` | Parent accepted the reason | **Per policy** (see §8.2) |
| `missed` | Not done, no accepted excuse | No |
| `cancelled` | Parent voided the instance (e.g. family was away) | No, and doesn't count against streaks |

### 7.2 Grace period

An overdue instance remains actionable for a configurable grace period (default: until end of the following day) before it hard-locks to `missed`. Without a grace period, a teen who does the dishes at 9:05pm for an 9:00pm deadline gets nothing, and the app is immediately perceived as petty.

---

## 8. Business rules

These are the decisions that determine whether the system feels fair. Several are deliberately flagged as configurable because the right answer varies by family.

### 8.1 Point award idempotency

A Task Instance may generate **at most one** `task_completed` ledger entry, ever. Enforced at the database level with a unique constraint on `(task_instance_id, entry_type)`, not in application code.

If a task moves from `complete` back to another state (parent reverses an approval), the original entry is **not deleted**. A compensating `reversal` entry is written instead. The ledger is append-only. A teen who watches points silently vanish from their history stops trusting the app permanently.

### 8.2 Do excused tasks pay out?

Configurable per household, default **`excused_pays_nothing`**:

- `excused_pays_nothing` *(default)* — the task pays no points, but does not break a streak and does not count as missed in reports.
- `excused_pays_partial` — pays 50% of value.
- `excused_pays_full` — pays full value.

The default is deliberate. If an accepted excuse pays the same as doing the work, the rational teen strategy is to write excuses instead of doing tasks, and the parent is placed in the position of having to deny reasonable-sounding explanations to keep the system functioning. Decoupling *"you're not in trouble"* from *"you get paid"* lets the parent be generous with the first without undermining the second.

### 8.3 Series completion

- A series completes when every member task instance within the window reaches `complete`.
- **Excused tasks do not block series completion** — the series completes on the remaining tasks. Under `all_or_nothing`, the payout excludes the excused task's value but pays the bonus in full. (An excused task should not cost the teen the bonus; it should only cost them that task's own value.)
- A series with *every* member task excused does not complete and pays nothing.
- If a series expires with tasks outstanding, no bonus is paid; individually earned points (under `individual_plus_bonus`) are retained.
- A series bonus writes a single `series_bonus` ledger entry, subject to the same idempotency constraint on `(series_instance_id, entry_type)`.

### 8.4 Value freezing

A Task Instance stores its own `point_value`, copied from the Definition at generation time. Changing a Definition's value never alters instances that already exist. This makes history stable and prevents retroactive re-pricing of work already done.

### 8.5 Timezone and day boundaries

All timestamps stored in UTC. All scheduling logic — "today," "this week," due times, grace periods — evaluated in the **household's** timezone. A single misapplied timezone here produces tasks that appear a day early for half the year, which is a hard bug to diagnose after launch.

### 8.6 Anti-gaming considerations

Given the teen persona, assume good-faith gaming:

- Excuse text has a minimum length and is permanently visible in history, so patterns are apparent to the parent.
- A teen cannot edit a submitted excuse, cannot edit any task's point value, and cannot approve anything.
- `requires_review` exists for tasks where self-reporting is untrustworthy.
- Report view: excuse frequency per teen over time, so a parent can notice a trend without policing individual instances.

---

## 9. Data model

```
Household
  id, name, timezone, points_label, excused_payout_policy,
  grace_period_hours, created_at

Member
  id, household_id, role (parent|teen), display_name, avatar,
  auth_provider, auth_subject, pin_hash (nullable), birthdate,
  push_token, created_at

TaskDefinition
  id, household_id, assignee_id, title, description,
  point_value, schedule_type (one_time|daily|weekdays|weekly),
  weekday_mask (nullable), start_date, end_date (nullable),
  due_time, requires_review (bool), series_id (nullable),
  archived_at (nullable), created_by, created_at

TaskInstance
  id, definition_id, assignee_id, due_at (UTC),
  point_value          -- frozen at generation
  status               -- see §7.1
  completed_at, completion_note,
  excuse_text, excuse_submitted_at,
  reviewed_by, reviewed_at, review_comment,
  series_instance_id (nullable)
  UNIQUE (definition_id, due_at)   -- generation idempotency

Series
  id, household_id, name, assignee_id, bonus_points,
  payout_mode (individual_plus_bonus|all_or_nothing),
  window_type (weekly|monthly|custom), archived_at

SeriesInstance
  id, series_id, window_start, window_end, status, completed_at
  UNIQUE (series_id, window_start)

LedgerEntry
  id, member_id, delta (signed int), balance_after,
  entry_type (task_completed | series_bonus | excused_partial |
              claim_fulfilled | manual_adjustment | reversal),
  task_instance_id (nullable), series_instance_id (nullable),
  claim_id (nullable), reason, created_by, created_at
  UNIQUE (task_instance_id, entry_type) WHERE task_instance_id IS NOT NULL
  UNIQUE (series_instance_id, entry_type) WHERE series_instance_id IS NOT NULL

Claim
  id, member_id, points, requested_item, status (pending|fulfilled|declined),
  parent_note, requested_at, resolved_at, resolved_by

SavingsGoal          -- P1
  id, member_id, title, target_points, created_at, achieved_at
```

**Balance** = `SELECT SUM(delta) FROM ledger_entry WHERE member_id = ?`. The `balance_after` column is a denormalised convenience for rendering history; it is never the source of truth and should be verifiable against the running sum in a periodic integrity check.

### 9.1 Instance generation

A scheduled job runs nightly per household (at local 00:05) and materialises Task Instances for a rolling 14-day horizon. The `UNIQUE (definition_id, due_at)` constraint makes the job safely re-runnable — important, because it will be re-run, after outages and during backfills.

Same job transitions `pending` → `overdue` for elapsed due times, and `overdue` → `missed` for expired grace periods.

**Why 14 days and not lazy generation:** a rolling horizon lets teens see the week ahead, lets parents plan, and makes the "what's coming up" query a simple indexed read rather than a recurrence calculation at request time.

---

## 10. Screens

### 10.1 Teen — iPhone

| Screen | Contents |
|---|---|
| **Today** *(default)* | Balance in the header. List of today's tasks with point values, grouped by due time. Swipe to complete. Overdue items pinned to top with an excuse affordance. |
| **Week** | Seven-day scroll, each day showing tasks and status. Series progress cards. |
| **Wallet** | Balance, savings-goal progress bar, full transaction history, "Claim points" button. |
| **Task detail** | Description, value, due time, history of this recurring task, complete / excuse actions. |

### 10.2 Parent — iPhone

| Screen | Contents |
|---|---|
| **Inbox** *(default)* | Pending approvals with context: what the task was, what the teen said, one-tap approve/deny. Empty state is the success state. |
| **Family** | Per-teen summary: balance, completion rate this week, outstanding claims. |
| **Tasks** | Definition list, add/edit, archive. |
| **Reports** | Completion rate over time, excuse frequency, points earned per week. |

### 10.3 iPad

Use `NavigationSplitView` throughout, not a stretched phone layout — the difference is immediately obvious to users and is a common reason iPad apps feel unfinished.

- **Parent iPad** is the *management* surface: sidebar (Inbox / Family / Tasks / Reports), list column, detail pane. Task creation is a proper form in the detail pane, and the Sunday-planning session Priya does is a first-class flow here — bulk task creation, week-at-a-glance across all teens.
- **Teen iPad** is the *shared-device* surface: profile picker on launch, PIN entry, then the same Today/Week/Wallet structure in a two-column layout.

### 10.4 Visual direction

Explicitly **not** a children's app. No cartoon mascots, no confetti animations, no "Great job, superstar!" microcopy. The reference points should be Things, Streaks, or a banking app — clean typography, restrained colour, information-dense. Teen users read visual infantilisation as disrespect, and it is the most likely single cause of teen-side abandonment.

Points should be presented with the household's chosen label (default "points") and never styled to look like literal currency, which sets an expectation the app cannot fulfil.

---

## 11. Technical architecture

### 11.1 Backend

| Concern | Choice | Rationale |
|---|---|---|
| Framework | **FastAPI** | Async, native Pydantic validation, generated OpenAPI spec that the Swift client can codegen against. |
| ORM | **SQLAlchemy 2.0** + Alembic | Mature migrations; the schema will change a lot in the first months. |
| Database | **PostgreSQL** | Needs partial unique indexes (§9) and reliable transactional integrity for ledger writes. |
| Jobs | **Celery + Redis** | Instance generation, state transitions, notification fan-out. *If operational simplicity matters more than scale, APScheduler in-process is a defensible v1 shortcut — one fewer service to run.* |
| Auth | **Sign in with Apple**, JWT access + refresh | Apple ID works for 13+; avoids storing passwords. |
| Push | **APNs** via `aioapns` | |
| Hosting | Fly.io or Railway for v1 | Managed Postgres, minimal ops burden at this scale. |

### 11.2 API shape

REST, versioned under `/v1`. Household ID is derived from the authenticated token and never accepted as a client parameter — this is the primary tenant-isolation boundary and must be enforced in a dependency, not per-endpoint.

```
POST   /v1/auth/apple
GET    /v1/household
GET    /v1/members

GET    /v1/tasks/today?member_id=
GET    /v1/tasks/week?start=
POST   /v1/task-instances/{id}/complete
POST   /v1/task-instances/{id}/excuse
POST   /v1/task-instances/{id}/review     {approve: bool, comment}

GET    /v1/definitions
POST   /v1/definitions
PATCH  /v1/definitions/{id}
DELETE /v1/definitions/{id}               -- archives

GET    /v1/series
POST   /v1/series

GET    /v1/wallet/{member_id}
GET    /v1/wallet/{member_id}/ledger
POST   /v1/claims
POST   /v1/claims/{id}/resolve
POST   /v1/wallet/{member_id}/adjust

GET    /v1/approvals                      -- parent inbox
```

> **Note:** the illustrative paths above were adjusted slightly during implementation (e.g. task-instance actions live under `/v1/tasks/instances/{id}/...`, claims under `/v1/wallet/claims`). See [`docs/api-reference.md`](./api-reference.md) for the authoritative, as-built API contract.

All mutating endpoints accept an idempotency key. Mobile clients retry on flaky networks, and a double-tapped "complete" must not be able to produce two ledger entries — the database constraint is the backstop, but the API should not rely on it firing.

### 11.3 Authorization rules

- A teen may read only their own tasks, wallet, and ledger.
- A teen may write only: complete own task, excuse own task, submit own claim, propose task.
- A parent may read and write everything within their household.
- Nothing crosses household boundaries, ever.

### 11.4 Client

Single SwiftUI codebase, iOS 17+ minimum. Adaptive layout via `NavigationSplitView` with size-class-driven collapse. Local cache in SwiftData or Core Data so the Today view renders instantly offline; completions queue and sync when connectivity returns. Offline completion matters more than it sounds — kids do chores in basements and garages.

> **Note:** the client-specific product requirements — screen-by-screen UI/UX, design language, interaction and motion principles, and the offline/sync design — are expanded in [`docs/ios-prd.md`](./ios-prd.md).

---

## 12. Privacy, legal, and App Store

Ages 13–17 keeps this materially simpler than a younger-children product, but several constraints still apply.

- **COPPA does not apply** at 13+. Do not accept users under 13; collect a birthdate at teen-profile creation and block below 13, or make the household explicitly parent-managed with no independent teen account.
- **Not the Kids Category.** The App Store Kids Category is for 11-and-under and brings heavy restrictions. Target a **12+ age rating** in the standard category instead.
- **State minor-privacy laws** (notably in California and several other US states) impose duties around minors' data and default privacy settings. Worth a lawyer's read before launch if targeting the US.
- **Data minimisation.** Collect no more than display name, birthdate, and auth identifier. No location, no contacts, no photos in v1. This is both good practice and a materially smaller compliance surface.
- **No third-party ad SDKs.** Non-negotiable for a product used by minors.
- **No IAP for points.** If parents could buy points with real money, the app becomes a virtual-currency product with a different regulatory and App Store posture entirely. Subscription for the app itself is fine; selling points is not.
- **Parental visibility.** Parents can see all teen activity within the app. This should be stated plainly in onboarding rather than discovered — surprise surveillance damages trust with the teen user badly.

---

## 13. Success metrics

**Primary:** percentage of households still recording task completions in week 6. Everything else is secondary; family apps die of quiet abandonment, not of any single measurable failure.

Supporting:

- Median parent time-to-approval (target: under 12 hours; above 48 hours predicts household churn).
- Teen weekly active rate — the teen is the fragile side of the market.
- Task completion rate trend within a household (should be flat or rising; a decline predicts abandonment).
- Excuse rate (a sharp rise suggests the point values or task load are miscalibrated).
- Claims fulfilled vs. submitted (unfulfilled claims are a direct trust failure).

---

## 14. Open questions

1. **Android.** Mixed-platform households — one parent on Android, or a teen with a Pixel — cannot use the app at all. This may be a larger share of the addressable market than iOS-first instinct suggests. Worth validating before committing to SwiftUI.
2. **Point-to-money conversion.** Should the household set an explicit exchange rate (100 pts = $1)? It makes value legible, but converts the app into an allowance ledger with the expectations that carries.
3. **Negative points.** Should parents be able to deduct for infractions? It's requested often and it reliably poisons the dynamic — the app stops being about earning and becomes about punishment. Manual adjustment already permits it; the question is whether to make it a first-class feature or leave it deliberately awkward.
4. **Streaks.** Motivating, but a broken streak after 40 days is demoralising enough that some users quit rather than restart. If included, consider streak freezes.
5. **Who defines point values?** Parent-set is simplest. Negotiated values would increase teen buy-in substantially but add real product complexity.
6. **Business model.** Subscription per household? Free with paid tier? Undecided, and it affects onboarding design.

---

## 15. Roadmap

### v0.1 — Internal (4–6 weeks)
Household + profiles, one-time and recurring definitions, instance generation, complete flow, ledger, basic wallet. Parent and teen on iPhone only. Dogfood with one real family.

### v1.0 — Launch (10–14 weeks)
Add: excuse and approval flows, series with both payout modes, claims, notifications, iPad layouts, reports. Full state machine. App Store submission.

### v1.1 — Retention
Savings goals, teen task proposals, parent nudge notifications, reward menu, recurring series.

### v2.0
Photo proof, multi-assignee and competitive tasks, second-parent accounts, richer analytics, possible Android.

---

## Appendix A — Worked example

**Setup.** Priya creates a series, *"Weekend Reset,"* window = this week, bonus = 100 pts, mode = `individual_plus_bonus`, assignee = Arjun. Members: Mow lawn (40 pts, Sat), Clean room (30 pts, Sat), Wash car (50 pts, Sun).

**Saturday.** Arjun mows the lawn and marks it complete. `auto_approve` is on, so a `task_completed` entry of +40 is written immediately. Balance: 40. He cleans his room: +30. Balance: 70.

**Sunday.** He has a friend's birthday and doesn't wash the car. At 9pm the due time passes; the instance moves to `overdue`. He submits an excuse: *"Was at Dev's birthday all afternoon, will do it Monday."* Instance → `excuse_pending`. Priya gets a push notification.

**Sunday night.** Priya approves. Household policy is `excused_pays_nothing`, so the instance → `excused` with no ledger entry. But because excused tasks don't block series completion, the *Weekend Reset* series completes on the remaining two tasks. Under `individual_plus_bonus` the bonus pays in full: `series_bonus` +100. Balance: 170. (Had the series been `all_or_nothing`, this would instead be a single entry of 40 + 30 + 100 = 170, with the excused car wash's 50 pts excluded.)

**Monday.** Arjun opens the Wallet tab and sees:

```
  +100   Weekend Reset — series bonus          Sun 9:41pm
    +30   Clean room                            Sat 4:12pm
    +40   Mow lawn                               Sat 11:03am
```

Every number has a reason attached to it. That is the entire product.
