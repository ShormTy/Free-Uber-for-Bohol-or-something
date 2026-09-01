This is purely vibecoded.

# Free-Uber-for-Bohol-or-something

A lightweight ride-hailing web app built for Bohol — students can request rides, drivers can accept and complete them, and both sides get live updates over WebSockets. No third-party ride app fees, just a simple self-hosted matcher.

## Features

- **Accounts** — sign up and log in as a student (rider) or a driver, with real session-token authentication (no more trusting client-supplied user IDs)
- **Ride lifecycle** — request a ride, a driver accepts it, starts it, then completes it (or cancels/releases it back)
- **Live updates** — WebSocket connections push ride status changes in real time to both the rider and the driver, plus a live "board" view of all open ride requests for drivers browsing pending rides
- **Location tracking** — drivers and students can share and view live location during an active ride, restricted to only those who currently share a ride together
- **Session security** — logging out revokes the session server-side, not just client-side; repeated failed logins are rate-limited; WebSocket connections authenticate with short-lived, single-use tickets instead of putting the long-lived session token in a URL
- **Input validation** — signup rejects invalid roles and malformed phone numbers instead of silently creating broken accounts
- **Simple web frontend** — static HTML pages for login, the student view, and the driver board, with in-app toast notifications instead of browser alerts (including clear errors for invalid credentials, rate limiting, and network failures)

## Tech stack

- **Backend:** FastAPI + Uvicorn
- **Database:** SQLModel (SQLAlchemy) with SQLite
- **Auth:** Bearer token sessions, PBKDF2-HMAC-SHA256 password hashing with per-user salts
- **Realtime:** native WebSockets, authenticated via short-lived single-use tickets
- **Frontend:** static HTML/CSS served from `/static`

## Setup

### 1. Clone the repo

```
git clone https://github.com/ShormTy/Free-Uber-for-Bohol-or-something.git
cd Free-Uber-for-Bohol-or-something
```

### 2. Create a virtual environment

```
python -m venv venv
```

Activate it:

- **Windows (PowerShell):**
```
venv\Scripts\Activate.ps1
```
- **macOS/Linux:**
```
source venv/bin/activate
```

### 3. Install dependencies

```
pip install fastapi uvicorn sqlmodel requests websockets
```

### 4. (Optional) Configure environment variables

These are optional — the app runs fine without them for local development. Set them once you're ready to deploy somewhere real:

| Variable             | Purpose                                                                                          | Default            |
| --------------------- | -------------------------------------------------------------------------------------------------- | -------------------- |
| `FRONTEND_ORIGINS`    | Comma-separated list of allowed CORS origins (e.g. your deployed domain or Capacitor app scheme) | `*` (allow all)    |
| `ADMIN_RESET_SECRET`  | A long random string required to call the admin password-reset endpoint                          | unset (endpoint disabled) |

### 5. Run the server

```
uvicorn main:app --reload
```

The app will start at **http://127.0.0.1:8000**

A SQLite database file (`app.db`) is created automatically on first run.

> **Note on schema changes:** `app.db` is only created if it doesn't already exist — editing `models.py` later won't automatically add new columns to an existing database file. If you pull an update that changes `models.py` and the server throws `no such column: ...` errors, delete `app.db` and restart to regenerate it from the current schema. This is fine for local/dev use; once there's real user data worth keeping, use a proper migration tool (e.g. [Alembic](https://alembic.sqlalchemy.org/)) instead of wiping the database.

## Usage

Once the server is running, open your browser to:

- **`/static/login.html`** — sign up or log in (returns and stores a session token)
- **`/static/student.html`** — request a ride, track your driver in real time
- **`/static/driver.html`** — view and accept nearby ride requests, manage active rides

Signup and login return a `token` alongside the user's info — the frontend stores this and sends it as `Authorization: Bearer <token>` on every request afterward. Logging out calls `POST /logout`, which revokes that session server-side (not just clearing it from the browser).

WebSocket connections don't reuse that long-lived token. Instead, the frontend first calls `POST /ws-ticket` (using its normal Bearer token) to get a short-lived, single-use ticket, then opens the socket with `?ticket=...`. The ticket expires in ~30 seconds and is deleted the moment it's used, so even if it ends up in a proxy or server access log, it's not meaningful afterward.

## API overview

| Method | Endpoint                          | Description                                                                 |
| ------ | ----------------------------------- | ------------------------------------------------------------------------------ |
| POST   | `/signup`                         | Create a new account, returns a session token *(validates role and phone number format)* |
| POST   | `/login`                          | Log in, returns a session token *(rate-limited after repeated failed attempts)* |
| POST   | `/logout`                         | Revoke the current session token *(authenticated)*                          |
| POST   | `/ws-ticket`                      | Mint a short-lived, single-use ticket for opening a WebSocket *(authenticated)* |
| POST   | `/rides`                          | Request a new ride *(student only, authenticated)*                          |
| GET    | `/rides`                          | List pending ride requests                                                  |
| GET    | `/rides/{ride_id}`                | Get a specific ride *(only the student or driver on it)*                    |
| POST   | `/rides/{ride_id}/accept`         | Driver accepts a ride *(atomic — prevents two drivers double-accepting)*    |
| POST   | `/rides/{ride_id}/start`          | Driver starts a ride *(only that ride's driver)*                            |
| POST   | `/rides/{ride_id}/complete`       | Mark a ride complete *(only that ride's driver)*                            |
| POST   | `/rides/{ride_id}/cancel`         | Student cancels a ride *(only that ride's student)*                         |
| POST   | `/rides/{ride_id}/release`        | Driver releases a ride back to the pool *(only that ride's driver)*         |
| POST   | `/users/me/location`              | Update your own live location                                               |
| GET    | `/users/{user_id}/location`       | Get a user's live location *(only if you share an active ride with them)*   |
| GET    | `/drivers/me/active-ride`         | Get the logged-in driver's current active ride                             |
| GET    | `/students/me/active-ride`        | Get the logged-in student's current active ride                            |
| POST   | `/admin/reset-password`           | Admin-only password reset, requires `ADMIN_RESET_SECRET` *(interim tool — see Notes)* |
| WS     | `/ws/rides/{ride_id}?ticket=...`  | Live updates for a specific ride *(only its student/driver may connect)*    |
| WS     | `/ws/board?ticket=...`            | Live feed of all open ride requests *(drivers only)*                        |

## Notes

- This project is a personal/local build — not affiliated with Uber or any commercial ride service.
- `app.db`, `venv/`, `.venv/`, and `__pycache__/` are git-ignored and won't be included when you clone the repo — you'll generate a fresh database the first time you run it.
- Endpoints that used to accept a `user_id`/`driver_id`/`student_id` directly from the client now infer the caller's identity from their session token instead — this closes an earlier gap where anyone could act as any user by editing values sent from the browser.
- There's no budget for a paid email/SMS provider, and the app authenticates by phone number rather than email, so a self-service "forgot password" flow isn't buildable for free right now. `/admin/reset-password` is a stand-in: the operator runs it directly (protected by `ADMIN_RESET_SECRET`) when a user needs a reset. It also revokes all of that user's existing sessions. This is a known gap, not a finished feature — revisit if the user base grows past what admin-assisted resets can handle.
- Login rate limiting is in-memory and resets on server restart — fine at current scale, but won't be shared across multiple server instances if this ever needs to scale horizontally.