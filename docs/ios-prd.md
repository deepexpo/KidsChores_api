# Product Requirements Document
## KidsChores for iOS — Client PRD

**Author:** KD Singh (client-scope companion, drafted with the KidsChores engineering assistant)
**Date:** 1 August 2026
**Status:** Draft v1 — for review
**Relationship to master PRD:** This document is the client-side companion to
[`docs/prd.md`](./prd.md) (§10 "Screens" and §11.4 "Client" in particular). It does not restate
business rules, the state machine, or backend architecture — read the master PRD first. This
document exists because the master PRD deliberately keeps client UI/UX at the level of a screen
inventory; everything below is the detail needed to actually build it. The concrete API this app
talks to is documented in [`docs/api-reference.md`](./api-reference.md).
**Target platforms:** iPhone + iPad, iOS 17+, SwiftUI.
**Status of `ios/`:** empty — this PRD describes the app to be built, not one that exists yet.

---

## 1. Why this document exists

The master PRD's design thesis is **perceived fairness, not gamification polish** — but for the
teen persona specifically, fairness alone doesn't win the war against the delete button. Section
2 of the master PRD names the failure mode directly: *"They're built for young children... the
app gets deleted within a week."* The brief for this client is to be the opposite of that: an app
a 15-year-old would not be embarrassed to have a friend see open on their phone.

"Interactive and modern" is the mandate for this document specifically. Concretely, that means:

- **Direct manipulation over menus.** Swipe, drag, long-press, and haptic-confirmed gestures are
  the primary interaction model for the teen surfaces — tapping into a form should be the
  exception, not the default, for anything a teen does more than once a day.
- **Motion with a job to do.** Every animation either confirms an action succeeded, shows spatial
  relationship (where did this card come from / go to), or communicates state change (points
  ticking up). No motion exists purely for delight — see §6.4.
- **Native-first, not custom-chrome.** Lean on SF Symbols, system materials, `NavigationSplitView`,
  and platform-standard components rather than hand-rolled UI. A modern app in 2026 looks
  *confidently plain*, not skinned — see the Things/Streaks/banking-app reference points from the
  master PRD §10.4, which this document treats as load-bearing, not decorative.
- **The wallet is the emotional center of the app**, not the task list. Every screen should make
  it easy to get back to "how close am I to my goal," per the master PRD's note that a savings
  goal is *"the strongest single retention feature for the teen persona."*

---

## 2. Personas (recap — full detail in master PRD §4)

| Persona | Device | Primary need from this client |
|---|---|---|
| **Arjun, 15** | Own iPhone | Balance + goal progress in one glance; will judge the app's legitimacy on its polish within the first 10 seconds. |
| **Meera, 13** | Shared family iPad | Shortest possible path from "open app" to "mark done"; needs the profile-picker/PIN flow to be fast, not a barrier. |
| **Priya, 44** | iPhone (approvals) + iPad (planning) | Approvals must be doable one-handed, in a queue, without leaving the inbox. |

---

## 3. Platform & technical foundation

| Concern | Choice | Why |
|---|---|---|
| UI framework | SwiftUI, iOS 17+ minimum | Master PRD §11.4. iOS 17 gives us `NavigationSplitView` maturity, `ScrollTransition`, `PhaseAnimator`, and Observation (`@Observable`) — all used below. |
| Local persistence | SwiftData | Backs offline-first Today/Week views and the optimistic completion queue (§12). |
| Networking | `URLSession` + `async/await`, generated/hand-written `Codable` models matching [`docs/api-reference.md`](./api-reference.md) | No third-party networking dependency needed at this scale. |
| Auth | Email + password (current), `POST /v1/auth/{register,login,refresh}` — [`docs/auth-endpoints.md`](./auth-endpoints.md) | Sign in with Apple is implemented server-side (master PRD §11.1) but **deferred client-side** to a later phase; see §5. |
| Design system | SF Symbols 6, Dynamic Type, system color roles + one brand accent | §6. |
| Haptics | `UIFeedbackGenerator` (`UINotificationFeedbackGenerator`, `UIImpactFeedbackGenerator`) | §6.4. |
| Push | APNs via `UserNotifications` + interactive notification actions | §9. Backend push-sending is a stub today (see API reference §12) — build the UI/registration path now, but don't block v0.1 on server delivery. |
| Widgets / Live Activities | WidgetKit, ActivityKit | §10. P1/P2 — flagged here because they inform data-model and background-refresh decisions made in v0.1. |

---

## 4. Information architecture

The app is **one codebase, two experiences**, switched on `role` from the sign-in response — not
two separate apps, not a settings toggle. A parent who is also occasionally checking their own
household as a "player" is out of scope for v1 (see master PRD §14 Q3 territory — roles are fixed
per member).

### 4.1 iPhone — Teen

```
TabView
├── Today      (default)
├── Week
├── Wallet
└── (no Inbox tab — teens never see approvals)
```

### 4.2 iPhone — Parent

```
TabView
├── Inbox      (default — the parent's whole job starts here)
├── Family
├── Tasks
└── Reports
```

### 4.3 iPad — Parent (management surface)

```
NavigationSplitView
├── Sidebar: Inbox / Family / Tasks / Reports
├── Content column: list for the selected section
└── Detail pane: task/series form, member detail, or report chart
```

Not a stretched phone layout (master PRD §10.3 is explicit and non-negotiable on this point) —
the sidebar is always visible on iPad in regular width; it collapses to a tab bar only in
compact-width multitasking (Slide Over / narrow Split View).

### 4.4 iPad — Teen (shared-device surface)

```
Launch → Profile picker (avatar grid, one per teen in the household)
       → PIN entry (4-digit, per profile — only shown when that
         profile's pin_set is true)
       → TabView (same Today/Week/Wallet as iPhone, in a 2-column
         layout: task list | detail, using the extra width instead
         of stretching single-column content)
```

Distinct from iPhone-teen: this surface must support **fast profile switching** (a "Switch
Profile" affordance always reachable from the tab bar, not buried in settings) since siblings
share the device.

**PIN verification is server-side**, via `POST /v1/household/members/{id}/verify-pin`
(`docs/auth-endpoints.md` §5) — call it, don't try to verify the PIN against any locally-cached
hash. This matters specifically because a teen profile can be created on one device (the parent's
phone, during setup) and then unlocked on a different device (the shared iPad) — a local-only
verification scheme would silently fail for every profile not created on that exact iPad. Read
`pin_set` off the profile-picker's member list to decide whether to show the PIN screen at all for
a given avatar (skip straight through for a profile with no PIN set). The endpoint is rate-limited
(5 attempts/5 min) — surface a `429` as "Too many tries — wait a moment," not a raw error.

---

## 5. Onboarding & auth flow

**Current phase: email + password.** Sign in with Apple is implemented server-side
(`POST /v1/auth/apple`) and this section's flow is written so swapping the credential step for a
`SignInWithAppleButton` later is a drop-in replacement — everything downstream of "I now have an
`AuthTokens`" is identical either way. Don't build any Apple-specific UI yet.

1. **Launch, no session:** Single screen. App name/mark, one line of copy (not marketing-speak —
   something closer to *"Track what needs doing. Get paid for it."*), then two paths: **Sign In**
   and **Create Account** (segmented control or two buttons — this is a real signup/login pair
   now, not a single Apple button). Both lead to an email + password form
   (`SecureField` for password, standard `.textContentType(.password)` /
   `.textContentType(.newPassword)` for Password AutoFill support — don't skip this, it's the
   single biggest friction-reducer for a plain email/password form).
   - **Create Account** additionally asks for display name. Submits to `POST /v1/auth/register`
     (`docs/auth-endpoints.md` §1) — client-side validate password ≥ 8 chars before submit to
     avoid a round-trip for the common case, but still handle the server's `422`.
   - **Sign In** submits to `POST /v1/auth/login`. On `401`, show one generic inline error
     ("Incorrect email or password") — never indicate which field was wrong, matching the
     backend's own generic message.
   - On `429` from either (rate-limited), show a calm "Too many attempts — try again in a few
     minutes" message, not a raw error. This is expected to be rare for a real user and common
     only under credential-stuffing load, so don't over-engineer the copy here.
2. On successful **register** → backend creates a household and this person as `parent`
   (`docs/auth-endpoints.md` §1). Show a **3-screen setup flow**, skippable after screen 1:
   - Household name + timezone (default to device timezone, editable).
   - "Add your first teen" (name + birthdate + optional PIN) — can be skipped and done later from
     Family tab; don't force it, Priya's real flow is a deliberate Sunday-evening session per
     master PRD §4, not a rushed first-run wizard.
   - Points label ("What do you want to call points in your house?" — text field, default
     "points") with a live preview of a task row using the chosen label.
3. On successful **login** (returning user) → straight to Today (teen) or Inbox (parent). No
   interstitial, no "welcome back" screen — the whole point of a fast return is that it's fast.
4. **Shared-device / teen-PIN flow** is a *separate* entry point on iPad only, reached via "This
   is a family device" on the sign-in screen (parent still authenticates once via email + password
   to set the device into that mode; individual teen profiles then unlock via a PIN checked
   server-side against the parent's authenticated session, via `verify-pin` — §4.4 — without
   re-entering the parent's credentials; the PIN is a local-friction gate, not a security boundary,
   per master PRD §6.1).
5. **Teens do not have their own registration path yet** — per `docs/api-reference.md` §5,
   `register` always creates a `parent`. Don't put a "Create Account" entry point in front of a
   teen; teen profiles are created by a parent from the Family tab and used only in shared-device
   mode until a linking flow exists (§16 open question 3).

---

## 6. Design language

### 6.1 Color

System-role-driven, not a hardcoded custom palette — this is what makes dark mode correct for
free and keeps the app feeling native rather than skinned.

| Role | Light | Dark | Usage |
|---|---|---|---|
| Accent | A single brand accent (recommend a desaturated indigo/teal — deliberately *not* the primary color of any big kids'-app competitor) | Same hue, adjusted for contrast | Primary actions, selected tab, progress rings. |
| Status: pending/overdue | `.orange` (overdue), `.secondary` (pending) | Same, system-adjusted | Task status badges. |
| Status: complete/excused | `.green` | Same | |
| Status: missed | `.red`, low-emphasis (never alarming-red — a missed task is a fact, not a punishment) | Same | |
| Surfaces | `.systemBackground` / `.secondarySystemBackground` grouped list styling | Same | Never a custom off-white/off-black — use system materials so appearance settings (increased contrast, reduced transparency) are respected automatically. |
| Points | Rendered in the accent color, **never green-and-dollar-sign styled** | Same | Master PRD §10.4: points must never look like literal currency. |

**Dark mode is not an afterthought.** Given the teen persona lives on their phone at night, ship
with dark mode as a first-class, explicitly designed mode from the first screen built, not a
post-hoc `.preferredColorScheme` pass.

### 6.2 Typography

Dynamic Type throughout, San Francisco (no custom font — a modern app in this reference class
uses the system font well rather than importing a display face). Suggested scale usage:

| Style | Usage |
|---|---|
| `.largeTitle` / `.title` (bold, rounded design) | Balance number on Wallet — this is the one number in the app that gets weight and rounded-digit treatment; everywhere else is standard. |
| `.headline` | Task titles, screen titles. |
| `.subheadline` / `.body` | Descriptions, ledger reasons. |
| `.caption` | Timestamps, point deltas on list rows. |
| `.monospacedDigit()` | Any point value or countdown that updates in place, so digits don't reflow the layout as they change width. |

### 6.3 Iconography

SF Symbols exclusively for chrome/status; no custom icon set for v1. Suggested mapping:

| Concept | Symbol |
|---|---|
| Complete a task | `checkmark.circle` → `checkmark.circle.fill` (fills on completion) |
| Excuse | `text.bubble` |
| Overdue | `exclamationmark.circle` |
| Series | `link.circle` or `rosette` for the bonus |
| Wallet / balance | `creditcard` is explicitly wrong (currency framing) — use `star.circle` or a custom minimal coin-free glyph; points_label copy does the explaining, the icon should not | 
| Claim | `gift` |
| Savings goal | `target` |
| Approve / deny | `checkmark` / `xmark` (never a thumbs-up/down — reads younger) |

### 6.4 Motion & haptics — the "interactive" half of the brief

Motion budget is deliberately restrained (master PRD §10.4: *"No cartoon mascots, no confetti
animations"*) but not absent — restraint is a design choice, not a lack of interactivity.

| Interaction | Motion | Haptic |
|---|---|---|
| Swipe-to-complete on a task row | Row slides, checkmark fills in with a spring, row collapses out of the list (`ScrollTransition`/`withAnimation(.spring)`), balance in the header ticks up with rolling digits | `UINotificationFeedbackGenerator.success` on release past threshold |
| Swipe-to-excuse | Row reveals an excuse composer sheet on swipe (not a destructive-red swipe action — excusing isn't a failure state) | `UIImpactFeedbackGenerator(.light)` on swipe-open |
| Parent approve/deny in Inbox | Card swipes off-screen in the direction of the action (right=approve/green, left=deny/red), next card animates up | `.success` / `.warning` respectively |
| Series completes | The series progress ring (§6.5) completes its fill with a spring and the bonus amount appears as a small "+N" that flies toward the tab bar's Wallet icon | `.success`, slightly stronger impact than a normal completion — this is the one moment worth a beat of extra weight |
| Balance updates anywhere | Rolling-digit counter animation (never an instant cut) — this is what makes "every number has a reason attached to it" *feel* true in real time, not just in the ledger | none (visual only) |
| Pull-to-refresh | Standard system refresh control — no custom refresh animation | none |

No screen transition should exceed ~300ms. No looping/idle animations anywhere (a common teen
complaint about "childish" apps is idle bounce/wiggle on icons — never do this).

### 6.5 Core reusable components

Build these once, use everywhere — consistency across Today/Week/Inbox is what makes the app feel
coherent rather than screen-by-screen bespoke:

- **`TaskRow`** — leading status glyph, title + due time, trailing point pill, swipe actions.
  Variants: teen (complete/excuse swipe) and parent-viewing-teen (read-only, tap for detail).
- **`PointPill`** — `"+40"` / `"−20"` in a capsule, colored by sign, `monospacedDigit`.
- **`StatusBadge`** — small label + color per `TaskStatus` (§4 of the API reference — use the
  status enum verbatim as the source of truth for badge text, not a re-derived string).
- **`SeriesProgressRing`** — circular progress (complete / total), center label `"4 of 7"`,
  animates on change.
- **`ApprovalCard`** — used in Inbox: task/excuse context, teen's note/excuse text, approve/deny
  buttons, swipe-to-decide.
- **`GoalProgressBar`** — linear, balance vs. `target_points`, with the remaining-points delta
  as trailing text ("860 to go").
- **`EmptyStateView`** — icon + headline + subline, reused per §11 (never a bare "No data").

---

## 7. Screens — Teen

### 7.1 Today *(default tab)*

**Purpose:** the single most important screen in the app — the whole product lives or dies on
this being a 3-second glance-and-tap experience.

**Layout:** Balance pill in the navigation bar (tap → Wallet). List grouped by due time, sorted
ascending. Overdue items pinned in a distinct section at the top with an excuse affordance visible
without opening the row (per master PRD §10.1).

**Row content:** status glyph, title (client-joined from the cached `/v1/definitions` response —
see API reference §7 known gap), due time, point pill. `completion_note`/series membership shown
as small secondary badges when present.

**Primary interactions:**
- Swipe right → complete (full swipe or tap the revealed button; both must work — don't require
  gesture precision from a distracted teen).
- Swipe left / long-press → excuse composer sheet.
- Tap row → Task Detail (§7.4).

**States:**
- *Empty (nothing due today):* not a sad-empty-state — this is a *good* outcome. Friendly-but-not-
  childish copy ("Nothing due today.") plus a subtle link to Week view.
- *Loading:* skeleton rows (shimmer), not a spinner — the list shape should be visible immediately.
- *Offline:* a small, non-blocking banner ("Showing saved tasks — will sync when back online"),
  cached content fully interactive (§12).
- *All complete:* the completed rows stay visible (crossed-through style, not removed) for the
  rest of the day so the teen can see the day's work — don't make completion make things vanish.

### 7.2 Week

**Layout:** 7-day horizontal-scroll-snap header (today centered/highlighted) above a vertically
scrolling list of that day's tasks, mirroring Today's row style. Series progress cards inserted
inline on the day a series window starts.

**Interaction:** tap a day in the header to jump; the list section for that day scrolls into
view (not a full-screen swap) so the teen keeps spatial context across the week.

### 7.3 Wallet

**Layout (top to bottom):**
1. Balance — large, rounded digits, `points_label` beneath it.
2. Active savings goal card (`GoalProgressBar` + "N to go") if one exists; if not, an inline
   "Set a goal" prompt (not a nag — one line, dismissible for the session).
3. "Claim points" button (primary, always visible — this is a core action, not buried in a menu).
4. Full transaction history (`LedgerEntryResponse` list), `reason` rendered verbatim, newest first,
   infinite-scroll paginated via `limit`/`offset`.

**Interaction:** tap a ledger row for a detail sheet (full reason, timestamp, linked task if any —
tap through to Task Detail). Tap "Claim points" → claim composer sheet (amount + free-text item,
or picked from a reward menu once that P1 feature exists server-side).

### 7.4 Task Detail

Reached from any task row. Description, point value, due time, this recurring task's history
("You've done this 12 times, missed it twice" — computed client-side from ledger + instance
history, a nice-to-have but genuinely useful trust-building detail), and the same
complete/excuse actions as the row swipe, as full-width buttons for accessibility/discoverability.

### 7.5 Excuse composer

Presented as a sheet, not a full push — this is a quick, in-context action. Text editor
(min-10-chars enforced client-side with a live counter before hitting the API's validation),
character-count-down only appearing once close to the minimum (don't nag from character 1).
Submit button disabled until valid. On submit: sheet dismisses, row updates to `excuse_pending`
state immediately (optimistic), no confirmation alert needed — the state change on the row *is*
the confirmation.

### 7.6 Claim composer

Sheet: points amount (stepper or text field, clamped ≥ 1 and — client-side warning, not a hard
block — flagged if it exceeds current balance, since the backend doesn't reject over-balance
claims itself, see §6.6 of the master PRD on claims not being debited at submission) and a
free-text "what do you want" field. Submit → success state shows "Sent to [parent name] for
approval" (not "pending" jargon).

---

## 8. Screens — Parent

### 8.1 Inbox *(default tab)*

**Purpose:** master PRD §4 is explicit that this must be *"nearly frictionless"* — Priya's failure
mode is forgetting to approve things, so friction here is the single highest-leverage thing this
app can remove.

**Layout:** `ApprovalCard` stack, oldest-first. Each card: type icon (completion vs. excuse vs.
reward claim), teen's name + avatar, task title, point value, and for excuses, the full excuse
text inline (no tap-to-expand — reading it is the whole point of the screen).

**Reward claims belong in this stack too, not a separate screen.** `GET /v1/approvals` only
returns task completions/excuses — claims come from a second call,
`GET /v1/wallet/claims?status=pending` (`docs/api-reference.md` §10), and the client merges the
two client-side into one oldest-first stack. A claim card shows the requested item + point cost in
place of a task title; swipe right calls `POST /v1/wallet/claims/{id}/resolve` with
`approve: true` (fulfil, debits points) instead of the task-review endpoint. This keeps "everything
awaiting the parent" genuinely unified — a separate claims list would recreate exactly the kind of
easy-to-forget side-screen the Inbox exists to eliminate.

**Interactions:**
- Swipe right on a card → approve (with undo toast for ~4s before it's final client-side; the
  server call still fires immediately per §2 idempotency behavior, but the toast gives Priya a
  last-second correction window without a second server round trip on undo within the window —
  implement via optimistic local removal + delayed API call, canceling the pending call if undo
  is tapped).
- Swipe left → opens a comment field inline (required for deny — API reference §7), then confirms.
- **Multi-select mode** (long-press any card, or an explicit "Select" button) → checkboxes appear,
  a bottom action bar offers "Approve N" / "Deny N", which calls `POST /v1/approvals/bulk`
  (API reference §8) — this is the client surface for the master PRD's P0 "Bulk approve"
  requirement (§6.4).
- Empty state: **this is the success state** (master PRD §10.2, verbatim) — a genuinely
  celebratory-but-not-childish empty state (a simple checkmark glyph + "You're all caught up.").

### 8.2 Family

Per-teen summary cards: avatar, balance, this-week completion rate (ring or bar), outstanding
claims count (tappable → filtered claims list). "This-week completion rate" is one call to
`GET /v1/reports/{member_id}?weeks=1` (§8.5) — take the single returned week's `completion_rate`
directly rather than deriving it from a separate ledger/task-instance fetch. Tap a teen → their
Today/Week as the parent would see it (read view, using `member_id` param per API reference §7),
plus a "View wallet" link and an "Adjust points" action (opens the manual-adjustment sheet —
mandatory reason field, per master PRD §6.6, always visible to the teen afterward via the ledger).

### 8.3 Tasks (definition management)

List of `TaskDefinition`s, grouped by assignee, with archived ones filtered out by default (toggle
to show). Tap → edit form. "+" → creation form:

- Title, description, assignee (picker), point value (stepper, not free-text, to avoid fat-finger
  10,000-point typos), schedule type (segmented control: One-time / Daily / Weekdays / Weekly),
  conditional weekday picker (7-day toggle row) when Weekdays is selected, start/end date pickers,
  due-time picker, `requires_review` toggle with an inline explainer ("Off: pays automatically
  when marked done. On: you approve first.").
- "Create as part of a series" affordance links out to Series creation rather than duplicating
  series fields into this form (series bundles *existing* definitions per the API, so the flow is:
  create definitions first, then bundle into a series).

### 8.4 Series (reached from Tasks, or its own row in the iPad sidebar)

List of series with `SeriesProgressRing` per active instance. Creation form: name, assignee, bonus
points, payout mode (segmented, with a one-line explainer of each mode drawn straight from master
PRD §6.5 — teens/parents should not need to read the PRD to understand the difference), window
type (Weekly / Monthly — omit Custom from the picker per the known gap in the API reference §9),
multi-select list of the assignee's existing definitions to bundle.

### 8.5 Reports

P1 per master PRD roadmap. **The backend now exists** — `GET /v1/reports/{member_id}?weeks=`
(`docs/api-reference.md` §11) returns exactly what this screen needs in one call: a week-bucketed
array of `{ completed, missed, completion_rate, excuse_count, points_earned }`, oldest first,
zero-filled for empty weeks. This unblocks building the Reports tab for real, not just planning
against it.

- **Completion-rate trend** — line chart, per teen, toggle range 4/12 weeks (pass `weeks=4` or
  `weeks=12`). Plot `completion_rate` directly; skip/gap a week where it's `null` rather than
  plotting it as 0% — a `null` week means no complete-or-missed tasks were due that week at all,
  which is a different situation from a 0% completion week and shouldn't look the same on the
  chart.
- **Excuse frequency** — bar chart per teen, `excuse_count` per week. Master PRD §8.6: *"so a
  parent can notice a trend without policing individual instances"* — render as informational,
  not accusatory: neutral color, no red/warning styling, regardless of how high the count gets.
- **Points earned per week** — bar chart, `points_earned` per week. This is gross earning
  (`task_completed`/`series_bonus`/`excused_partial` ledger entries only), not net balance change
  — it will not reconcile against the wallet's week-over-week balance delta on a week with claims,
  and that's by design (a claim isn't a report-worthy dip in "how much did they earn").
- **Excluded from completion rate by design:** `excused` and `cancelled` instances never enter
  either the numerator or denominator (PRD §8.2) — a teen with several accepted excuses should not
  show a depressed completion rate for it.

The one thing still missing server-side: nothing computes month-over-month or longer-range
aggregates — `weeks` is capped at 52 and every call re-buckets from raw rows, so a very large
range (`weeks=52`) is a heavier call than a small one. Fine at this scale; worth knowing if this
screen ever needs a "since the beginning" view.

### 8.6 Household settings

Reached via a gear icon (iPhone: top of Family tab; iPad: sidebar footer). Household name,
timezone, points label, excused-payout-policy (segmented with one-line explainer per option,
drawn from master PRD §8.2 — this is a genuinely consequential setting and deserves in-context
explanation, not just an enum picker), grace period hours (stepper, 1–72).

---

## 9. Notifications

Server-side push sending is not implemented yet (API reference §12) — this section specs the
*client* contract so the app is ready the moment it is.

| Notification | Recipient | Interactive actions (notification-content-extension) |
|---|---|---|
| Task due soon (configurable lead time) | Teen | "Mark Done" (calls complete directly from the notification, no app launch) |
| Excuse approved/denied | Teen | Tap → Task Detail |
| New excuse submitted | Parent | "Approve" / "Deny" (deny opens the app to the comment field — can't fully resolve from the banner since a comment is required) |
| Daily pending-approvals nudge (P1, master PRD §6.7) | Parent | Tap → Inbox |

Interactive "Mark Done" / "Approve" actions are the concrete expression of "interactive" for this
surface — a modern-feeling app in 2026 should not require opening it for a single-tap decision.
Register a `UNNotificationCategory` per row above at launch; wire the action handlers to call the
same idempotent endpoints as the in-app buttons, generating a fresh idempotency key per action.

---

## 10. Widgets & Live Activities *(P1/P2 — flagged for data-model awareness now)*

- **Home Screen widget** (small/medium): today's remaining task count + current balance. Refreshes
  on a `WidgetKit` timeline tied to the same cached SwiftData store the app uses — no separate
  network stack needed in the widget extension.
- **Live Activity** for a task in its grace period: a countdown to `missed`, with a "Mark Done" /
  "Excuse" action pair directly on the Lock Screen / Dynamic Island. This is a strong fit for the
  product's own grace-period concept (master PRD §7.2) and a genuinely modern, teen-native
  interaction pattern (Live Activities are widely used in apps this persona already has installed)
  — recommended as the first P1 build after v1.0 ships, ahead of Reports.

---

## 11. Auth & session handling detail

`POST /v1/auth/refresh` exists (`docs/auth-endpoints.md` §3) and **rotates** the refresh token on
every call — the response's `refresh_token` is a new one, and the token just presented is now
single-use-consumed. The client's session layer (a `TokenProvider`/`AuthService`-shaped object)
must:

1. Decode the access JWT locally on receipt to read `exp`.
2. Schedule a proactive `POST /v1/auth/refresh` a few minutes before expiry, not reactively on
   401. **Always overwrite the stored `refresh_token` with the one from the refresh response** —
   because rotation makes the old one single-use, holding onto a stale refresh token after a
   successful refresh will make the *next* refresh attempt fail with 401 ("already used").
3. On an unexpected 401 mid-session (e.g. token expired sooner than expected, or the app was
   backgrounded past the proactive-refresh window), attempt one `refresh` call before surfacing
   any UI — only show a "Sign in again" screen if that also fails (refresh token itself expired
   at 30 days, or was already rotated out by a refresh that happened on another device/session).
4. Serialize concurrent refresh attempts — if multiple in-flight requests 401 at once, only issue
   *one* `POST /v1/auth/refresh` and have the others await its result, rather than racing several
   refresh calls against the same (single-use) refresh token, where only the first would succeed
   and the rest would incorrectly look like session-expiry failures.
5. Never persist the raw access/refresh tokens in `UserDefaults` — use Keychain.

Sign in with Apple's client-specific re-auth workaround (silently re-invoking
`ASAuthorizationAppleIDProvider`) is **not needed** for the current email/password phase — normal
refresh-token rotation per above is sufficient. Revisit this section when Apple sign-in is
reintroduced client-side; the session layer shouldn't need structural changes, just an additional
credential-acquisition path feeding the same `AuthTokens` handling.

---

## 12. Offline & sync design

Per master PRD §11.4: *"Offline completion matters more than it sounds — kids do chores in
basements and garages."*

- **Read path:** Today/Week/Wallet are backed by a SwiftData store synced from the API on
  foreground + pull-to-refresh. The UI always renders from the local store, never blocks on a
  network call — a cold launch with no connectivity shows the last-synced state immediately.
- **Write path:** `complete`/`excuse`/`cancel` write an optimistic local status change *and*
  enqueue the corresponding API call (with its generated idempotency key persisted alongside the
  queued action) in a local outbox. The outbox drains on connectivity regain, in FIFO order per
  task instance. If a queued call ultimately fails with a `422` (illegal transition — e.g. the
  task was cancelled by a parent while the teen was offline), surface a **non-blocking**
  correction: revert the optimistic state and show a small inline notice on that row, not a
  modal interrupt.
- **Conflict rule:** the idempotency key is generated once, at the moment of the optimistic local
  write, and reused for every retry attempt of that same queued action — this is what makes it
  safe to retry indefinitely without double-completing (API reference §2).
- **What does *not* need offline support in v0.1:** parent-side creation/editing of definitions
  and series, Reports, claims resolution. These are lower-frequency, typically-at-a-desk actions;
  scope offline specifically to the teen's daily complete/excuse loop and the parent's inbox
  approve/deny loop, which are the two flows explicitly called out as needing to be frictionless.

---

## 13. Accessibility

- Full Dynamic Type support, tested up to AX5. `PointPill`/`StatusBadge` must not truncate — the
  point/status is never less important than the title.
- VoiceOver labels on swipe actions must state the action *and* target ("Complete, Wash dishes"),
  not just the icon's default label. Provide the swipe actions as an accessibility action set as
  well as a gesture, since swipe-to-reveal is not reliably discoverable by VoiceOver users.
- Color is never the only signal for status — every `StatusBadge` pairs color with a glyph and
  text label (already true by construction in §6.5, called out here so it isn't regressed later).
- Respect Reduce Motion: swap the spring/fly animations in §6.4 for simple cross-fades; haptics
  are unaffected by this setting and remain the primary confirmation channel when motion is
  reduced.
- Minimum tap target 44×44pt on every swipe-revealed button and every row in a shared-device
  context (Meera's iPad use case specifically — younger/less precise taps).

---

## 14. Empty, loading, and error-state philosophy

A consistent rule across every screen: **the empty state must communicate whether "empty" is good
or bad**, because in this app both occur constantly and look superficially similar (no tasks today
= good; no approvals to act on = good; no ledger history yet for a brand new teen = neutral; no
network connection = bad but not the teen's fault). Concretely:

| State | Treatment |
|---|---|
| Good-empty (Today done, Inbox clear) | Checkmark glyph, calm/affirming one-liner, no CTA needed. |
| Neutral-empty (new user, no history yet) | Explainer one-liner + a single clear next action (e.g. "Ask a parent to add your first task"). |
| Error (network/server) | Icon + plain-language message + explicit "Retry" button. Never a raw error code or stack trace in teen-facing UI; parent-facing debug detail (if any) stays behind a "Details" disclosure. |
| Loading | Skeleton content matching the eventual layout, not a centered spinner, for any list screen (Today/Week/Inbox/Wallet history). A centered spinner is acceptable only for full-screen transitional states (initial sign-in, first sync). |

---

## 15. Success metrics (client-specific instrumentation)

Building on master PRD §13's household/teen-level metrics, this client should locally log (and
eventually pipe to whatever analytics stack is chosen — not specified yet, flagged as an open
question in §16) at minimum:

- Time from app foreground to first meaningful interaction on Today (proxy for "3-second glance"
  design goal in §7.1).
- Swipe-to-complete vs. tap-to-complete ratio (validates whether the gesture-first bet in §1 is
  actually landing with teens, or whether they default to tapping).
- Time-to-decision in Inbox, per card (client-side proxy feeding the master PRD's "median
  parent time-to-approval" metric, §13).
- Offline-queue drain success rate (validates §12 isn't silently losing completions).

---

## 16. Open questions (client-specific, in addition to master PRD §14)

1. **Analytics vendor.** Not chosen. Affects whether §15's events are first-party-only (simplest,
   most privacy-aligned given master PRD §12's data-minimization stance) or routed through a
   third-party SDK (would need its own privacy review given the minors-focused audience).
2. **Widget/Live Activity priority vs. Reports.** §10 recommends Live Activities as the first P1
   build ahead of Reports on the theory that it's higher-leverage for teen retention than
   parent-facing analytics — worth validating against actual usage data once v1.0 has real
   households on it, rather than committing now.
3. **Teen-owned-device linking flow.** API reference §5 flags that there is currently no way for
   a teen to link their own credentials (Apple ID, or email/password) to a parent-created profile
   — this blocks the "Arjun has his own iPhone" persona's real usage pattern (master PRD §4) and
   needs a backend endpoint + a client flow ("Enter the code your parent gave you" or similar)
   before v1.0, not just shared-device mode. Applies equally regardless of which auth method ends
   up being primary at that point.
4. **Reward menu UI** (master PRD §6.6, P1) — once the backend has a parent-defined fixed-price
   reward menu, does the claim composer become a picker-first, free-text-fallback UI, or stay
   free-text-first with the menu as a shortcut row above it? Depends on how populated menus turn
   out to be in practice; deferred until the feature exists server-side.
