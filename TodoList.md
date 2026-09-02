# TODO

Running list of known gaps, planned fixes, and thesis-relevant design decisions. Not all of these need to be built right now — some are just documented so they don't get forgotten, or so they can be cited as "known limitation, addressed in future work" in a thesis defense.

## 🔴 High priority — security / privacy

- [x] **`GET /rides` had no auth check.** ~~Anyone (logged in or not) could hit this endpoint and get exact pickup/dropoff coordinates for every pending ride request.~~ Fixed: now requires an authenticated driver. Pre-acceptance responses (`GET /rides`, the `ride_added` board broadcast on request/release) return a rounded pickup coordinate (2 decimal places) with dropoff omitted entirely, via a shared `_board_ride_view()` helper. Exact coordinates are only returned once a driver has accepted (`POST /rides/{id}/accept`) and is accountable via the `Ride` record.
  - Follow-up: `BOARD_COORD_PRECISION = 2` was a reasonable starting guess, not a deliberately chosen blur radius. Worth revisiting for Bohol's geography specifically — rural roads may need a different "close enough" threshold than dense urban blocks.
- [ ] **Driver location never expires.** `current_lat`/`current_lng` is continuously overwritten via `watchPosition` with no stop condition tied to ride state. Even though `get_location` correctly restricts *who* can query it, the data itself persists indefinitely. Should be cleared or made stale shortly after a ride ends — reduces blast radius if access control ever has a bug.
- [ ] **No driver verification.** `role` is self-attested at signup — anyone can register as "driver" with no proof (license, plate, ID). This is the actual root cause of the "driver could stalk a student" concern — access control only matters if you trust who's on the other end.
  - Thesis-scope fix: document upload (license/OR-CR) + manual admin approval before a driver account can go live. No third-party KYC service needed.
- [ ] **No account deletion flow.** Users have no way to request their data be deleted. Plan: soft-delete (scrub personal identifiers — name, phone, location) rather than hard-delete, since ride records may need to persist (anonymized) for the other party's history.
- [ ] **No location retention/anonymization after ride completion.** Per Uber's own approach: address details are removed post-trip and only approximate pickup/dropoff remain in history. Consider downgrading stored ride coordinates to rounded/approximate after completion instead of keeping exact addresses indefinitely.

## 🟡 Medium priority — hardening

- [ ] Rate limiting on `/login` is in-memory — resets on server restart, won't scale across multiple instances. Fine for now; revisit if this ever needs horizontal scaling (Redis or similar).
- [ ] `/admin/reset-password` is an interim, operator-only password reset (no free SMS/email provider fits phone-only auth). Not a real self-service flow — document this clearly if pitching this project as production-ready.
- [ ] `ride_connections` / `board_connections` are in-memory dicts — fine at current scale, but won't survive a server restart mid-ride, and can't horizontally scale without moving to something like Redis pub/sub.
- [ ] No audit trail for location access — if a complaint about misuse ever came in, there's currently no record of who queried what location data and when.

## 🟢 Lower priority / polish

- [x] **Driver's active-ride map didn't render at all without a GPS fix.** ~~`updateActiveRideMap()` bailed out entirely (`if (!activeRide || !myLocation) return;`) if the driver's own location wasn't available yet, so an accepted ride showed no map whatsoever — no pickup/dropoff pins, nothing — with zero indication why.~~ Fixed: the map now initializes and shows pickup/dropoff pins as soon as there's an active ride, independent of GPS. The driver's own "You" marker and the route line still require a location fix, but now degrade gracefully instead of blocking the whole map. Also added a persistent warning toast if geolocation fails (denied permission / unsupported), instead of only logging to console.
- [ ] Tighten CORS (`FRONTEND_ORIGINS`) once there's a fixed deployed frontend domain — currently defaults to `*` for local dev.
- [ ] Consider adding a "View as Driver" style transparency feature (à la Uber) — let a student see exactly what a matched driver can see about them, at each stage of the ride. Cheap trust-building feature, good thesis talking point even if not fully built.
- [ ] Schema migrations: `SQLModel.metadata.create_all()` doesn't alter existing tables when `models.py` changes — currently requires deleting `app.db` and starting fresh. Fine for dev; would need a real migration tool (e.g. Alembic) before there's real user data worth keeping.

## 🛠️ Local dev / environment gotchas

Not app bugs — things that look like bugs during local testing but are actually machine/OS configuration. Documenting so future-you (or a labmate) doesn't waste an hour chasing a phantom code issue.

- **Windows: geolocation silently never resolves if the Windows Geolocation Service (`lfsvc`) is stopped.** `navigator.geolocation.watchPosition()` neither succeeds nor calls its error callback in this state — no console error, nothing — even if the browser itself has location permission granted for the site. This looks identical to "the driver map isn't showing anything" but is actually an OS-level service being off, not a code bug.
  - Fix: `Win+R` → `services.msc` → find **Geolocation Service** → set Startup type to **Automatic**, click **Start** → fully restart the browser (not just refresh).
  - Diagnostic tell: in DevTools console, typing `myLocation` returns `null` indefinitely, with no error ever logged from the `watchPosition` error callback.

## 💭 Thesis / design decisions to keep documented

These aren't bugs — they're deliberate scoping calls worth writing down now so the reasoning doesn't get lost by the time thesis writing starts.

- **Data minimization vs. right to erasure are two different things** — minimization (auto-expiring live location) protects users automatically; erasure (delete-my-account) requires the user to act. Both matter; don't conflate them in the writeup.
- **Access control vs. data retention are two different layers of defense.** Restricting *who* can query location data (access control) doesn't help if the data itself is never cleared (retention). A single bug or compromised account bypasses access control — minimizing retained data limits the damage even then.
- **Privacy and driver-trust/verification are related but distinct problems.** Don't collapse them into one "privacy policy" answer in a defense — access control questions and identity verification questions have different fixes.
- **Uber's own tradeoffs are a useful reference, not a template to copy.** E.g. their pre-acceptance destination visibility is inconsistent by market specifically because of a fairness/business tradeoff (driver cherry-picking) — not a pure privacy decision. Worth citing as an example of "some design choices balance privacy against a different concern," not proof that any specific choice is automatically correct.