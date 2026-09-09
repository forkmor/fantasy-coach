# Fantasy Football Harness

A local Windows app with a React dashboard, Python/FastAPI backend, SQLite storage,
and interchangeable Grok and Gemini tool-calling agents. The managed scope is
ESPN league **656212638**, team **4**, season **2026**.

## Current capability and important limitation

The app supports credential setup, authenticated ESPN reads, provider tool loops,
policy-checked **dry-run proposals**, one active agent, persisted history, emergency
pause, and opt-in interval schedules.

Verified live ESPN transactions currently include an exact swap between one
starter and one eligible bench player, one unlocked free-agent add, or one
unlocked droppable roster-player drop. The owner can confirm these directly, or
explicitly enable Live mode so the AI coach may execute them within the saved
policy and current-period add/drop limits. Fieldhouse re-reads ESPN immediately
before one submission and performs authoritative roster readback. It never
automatically retries a mutation. Ambiguous responses are recorded as `unknown`
and must be checked on ESPN.

Waiver operations, paired atomic add/drop transactions, and trades remain
disabled. In Dry run mode, agent-created actions remain typed proposals. Direct
controls on **My team** require the owner to choose the named player(s) and approve
the exact preview within five minutes. The lineup live acceptance test completed
on September 9, 2026 with an HTTP-successful submission and authoritative
readback. Add and drop request contracts were captured from the authenticated
ESPN website with the requests aborted; no player was added or dropped.

To finish broader live management, an authenticated integration pass must verify
each remaining write contract, full league-specific legality, cap/budget
reservations, pending trade/waiver commitments, and authoritative outcome
reconciliation.
Never test a write by dropping players, spending FAAB, or sending trades without
the owner's explicit approval of that real action. This app must not be relied on
to set a lineup before kickoff while live integration remains blocked.

## Quick start (PowerShell)

Requirements: Windows, Google Chrome for ESPN browser sign-in, Python 3.14
(validated), and a current Node.js LTS release compatible with the frontend's Vite
version. Browser sign-in uses installed Chrome; no additional browser download is
required.

```powershell
Set-Location C:\source\espnff
.\setup.ps1
.\start.ps1
```

Setup creates a local virtual environment, installs the declared dependencies,
and builds the frontend. Startup binds to `127.0.0.1:8765` and opens a private local
launch link in your browser. The link contains a local login token in its fragment,
not in the server request URL. Keep it private. Restarting the app invalidates old
browser sessions and generates a new launch link.

If PowerShell blocks local scripts, use your organization's approved script policy;
do not disable machine-wide security controls.

## Publish on Hostinger

Hostinger Business/Cloud web hosting cannot run Fieldhouse's persistent FastAPI
service and scheduler. Use a Hostinger VPS and the included Docker Compose package.
The production configuration serves `https://fantasy-coach.tech` through Caddy
with automatic HTTPS, owner-password authentication, an encrypted Linux credential
vault, persistent SQLite storage, health checks, and automatic container restart.
Hosted deployments use a shared league password, then verify each manager's ESPN
cookies and isolate that team's credentials, settings, schedule, runs, and actions.

See [DEPLOYMENT.md](./DEPLOYMENT.md) for DNS, VPS, credential, backup, update, and
security instructions. Run `deploy/package.ps1` on Windows to create the uploadable
`dist/fieldhouse-hostinger-vps.zip`. Do not upload the project, `.env`, or database
to the shared-hosting `public_html` directory.

## Configure the app

1. In **Connections**, enter an **xAI API key** for Grok and/or a **Google Gemini API key**.
   A consumer chat subscription is not an API key and does not necessarily include
   API usage. Select one active provider and an explicit model ID available to that
   account; test the selected provider connection.
2. Select **Sign in to ESPN** in Connections. A separate Chrome window
   opens on the Windows desktop. Complete ESPN's login and any verification
   yourself, then return to the app and select **Save ESPN session**. The backend
   captures only the required ESPN cookies, verifies ownership of the configured
   team, then encrypts and saves both cookies atomically. Passwords and verification
   codes are not collected by the app. Existing credentials remain unchanged if
   verification fails. The temporary browser closes after saving/cancelling and
   expires after 10 minutes; it does not use your usual browser profile.
   If ESPN does not permit the automated browser, use the manual cookie fields as
   a fallback: inspect `espn_s2` and `SWID` in your signed-in browser's developer
   tools and enter them only in the local app. Do not paste cookies, passwords, or
   keys into chat, source files, or screenshots. Missing/expired credentials and
   team-ownership mismatches are surfaced as errors, not treated as empty rosters.
3. Open **Coach settings** to choose permissions with checkboxes, set limits, and
   choose protected players by name. Suggested settings change a draft only:
   review the changes and explicitly save. Existing off-roster player preferences
   are retained rather than silently discarded. The model cannot edit these rules.
   In **Connections & setup**, enable **Deep player research** when you want each
   coach review to inspect every supported roster player's current ESPN news,
   injury/practice reports, NFL statistics, fantasy projection, lineup slot, and
   start percentage before making recommendations. Deep reviews take longer and
   send more sanitized football context to the selected model provider.
4. On **My team**, choose **Review my team**, select a goal, and confirm the plan
   in the guided dialog. If the coach is paused, resuming requires an explicit
   choice; enabled schedules would also resume. The **Decisions** page keeps the
   advice and walks through suggested moves without pretending to execute them.
5. Optionally enable **Check-in schedule** after completing setup. Frequency
   suggestions stay unsaved until the confirmation dialog is accepted. Only one manager can run
   at a time. Switching providers or credentials requires the current run to stop.

### Your fantasy clubhouse

- The home screen shows roster cards with player names, positions, named lineup
  slots, projected/scored fantasy points, ESPN start/roster percentages, and ESPN
  headshots/team logos. Lineup and roster-management selectors include projection
  and health context instead of showing names alone.
- The league pulse shows sanitized standings, records, points for/against, waiver
  priority, transaction counts, and current matchup data when ESPN supplies it.
- **Direct lineup control** supports only a mirrored starter/bench swap. It filters
  the bench list by slot eligibility and requires an immediate final confirmation.
- **Direct roster control** supports one free-agent add or one droppable player
  drop at a time. It loads at most ESPN's current top 50 available players, honors
  protected-player and weekly limits, and requires immediate final confirmation.
- A jersey number is displayed only when it comes from ESPN player metadata; a
  player ID is never used as a jersey. Missing scores appear as a dash, not zero.
- Player dialogs show current roster facts and a confirmed **Keep on my team**
  preference. This prevents coach drop/outgoing-trade suggestions; it is not an
  ESPN keeper-league designation or an ESPN roster move. They also show current
  ESPN NFL statistics, dated news, and injury/practice reports when supplied.
- Team/player presentation metadata is optional. If it cannot be retrieved, the
  roster remains visible with an explicit warning, and failed images fall back to
  initials or a team logo. Images load only from allowlisted ESPN CDN hosts with
  no referrer; no account credentials are sent to image hosts.
- Technical IDs, raw records, provider usage and event logs are available in
  collapsed troubleshooting sections instead of dominating the main workflow.
- The dialogs support keyboard focus, Escape to close, and returning focus to the
  control that opened them. Cancelling a draft or confirmation does not save it.

No provider key or ESPN session credential is included in tool outputs or prompts.
The selected model provider receives the owner's objective and sanitized football
context (including roster data). Do not put private information into the objective.
ESPN manager identities and session information must remain local.

Deep research uses fixed, read-only ESPN endpoints rather than unrestricted model
browsing. It retains at most eight current ESPN news items per player or defense
and fetches injury reports by NFL team to keep responses bounded. Missing news or
an absent injury entry is reported as missing evidence and is never interpreted as
proof that a player practiced or is healthy.

## Limits and scheduling semantics

- Schedules start disabled and use an interval from 5 minutes to 7 days. The IANA
  timezone controls how users interpret/display times; interval execution uses UTC
  instants, so daylight-saving changes do not duplicate runs.
- Next-run times, settings, policies and history persist. After a restart the next
  run is scheduled one interval ahead; obsolete missed runs are not replayed.
- A scheduler tick that cannot start a run records a skip/error rather than
  overlapping an active manager. Failures are visible in the activity log.
- The backend must remain running and the computer must remain awake and online.
  Closing the terminal stops the app. For optional Windows Task Scheduler startup,
  run `start.ps1` under the same Windows user, only after you have reviewed the
  configured schedule. DPAPI-protected secrets do not transfer to another account.
- Daily agent-run limits use UTC calendar days. Output-token limits are per model
  response; tool calls and run time are bounded. Usage reports are provider-reported
  data, not a dollar-cost guarantee. Provider errors, rate limits, and cancellations
  may still consume billable usage.
- Weekly move caps and waiver/trade budgets are **configured intent limits**, not
  functioning live budget accounting yet. Proposals check known constraints and
  explicitly list incomplete league accounting. Live mode remains blocked.
- Owner-supplied trade values are deterministic reference inputs, not objective
  market prices. Agents cannot change them. Unknown values block applicable
  proposal checks. Trade acceptance requires fetching and validating the actual
  offer before it could ever be enabled.
- Emergency pause blocks subsequent tools and cancels an active agent request.
  It cannot undo a request already received by an external service.
- Policy changes cancel the active run so a lowered limit takes effect immediately.

## Tools available to both agents

| Tool | Purpose |
| --- | --- |
| `get_team` | Fresh roster and league context |
| `search_available_players` | Bounded player search |
| `get_transactions` | Read transaction context |
| `get_policy` | Read owner limits and capability status |
| `plan_action` | Record a typed proposal with denials and unresolved checks |
| `execute_action` | Records only a policy-checked agent dry-run or denial; never mutates ESPN |

The agent can execute the same constrained lineup swap only when Live mode and the
saved `set_lineup` permission are both enabled. Unsupported action types cannot be
enabled indirectly by switching modes.
| `get_action_status` | Read an intent belonging to this run |

The provider adapters use documented HTTPS REST protocols through `httpx` rather
than adding two separate SDK dependencies. This keeps shared tool validation and
timeouts in the harness; Gemini conversation metadata is preserved by its adapter.
There is no general shell, browser, filesystem, arbitrary HTTP, or policy-edit tool.

## Local data and operation

- Runtime data lives in `data\harness.sqlite3`, outside source control.
- Keys and cookies are encrypted with Windows DPAPI for the current Windows user;
  settings endpoints expose only whether a credential exists. Database backups
  contain encrypted secrets and still contain private team/run history: protect them.
- Local cookie authentication, CSRF protection, Host/Origin validation and a
  restrictive content security policy protect the browser-to-backend interface.
  Do not reverse-proxy or expose this app to a LAN or the internet.
- An OS-held instance lock and SQLite active-run uniqueness prevent competing
  managers using the same data directory. Do not run multiple data directories
  against the same real team when live integration is eventually enabled.
- An interrupted run is marked interrupted, not silently restarted. Interrupted
  submissions are marked unknown and require reconciliation, not blind retries.
- `HARNESS_PORT` changes the loopback port; `HARNESS_DATA_DIR` selects an explicit
  local data directory; `HARNESS_NO_BROWSER=1` disables automatic browser opening.

## Development and validation

```powershell
.\.venv\Scripts\python.exe -m pytest .\backend\tests -q
Set-Location .\frontend
npm.cmd run build
npm.cmd test
```

Frontend development uses the Vite `/api` proxy. Start the backend with
`HARNESS_DEV=1` to allow the explicit local development origin on port 5173.
Production startup serves the built UI directly from the backend.

Tests use synthetic credentials, sanitized fixtures and mocked provider/ESPN
responses. They do not submit ESPN writes or prove live account connectivity.
Real provider/ESPN checks require keys/session credentials entered locally.

## References

- [Managed team](https://fantasy.espn.com/football/team?leagueId=656212638&teamId=4&seasonId=2026)
- [Community ESPN read library](https://github.com/cwendt94/espn-api)
- [xAI function calling](https://docs.x.ai/developers/tools/function-calling)
- [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling)
