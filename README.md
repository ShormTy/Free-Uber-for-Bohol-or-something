This is purely vibecoded.

# Free-Uber-for-Bohol-or-something

A lightweight ride-hailing web app built for Bohol — students can request rides, drivers can accept and complete them, and both sides get live updates over WebSockets. No third-party ride app fees, just a simple self-hosted matcher.

## Features

- **Accounts** — sign up and log in as a student (rider) or a driver, with real session-token authentication (no more trusting client-supplied user IDs)
- **Ride lifecycle** — request a ride, a driver accepts it, starts it, then completes it (or cancels/releases it back)
- **Live updates** — WebSocket connections push ride status changes in real time to both the rider and the driver, plus a live "board" view of all open ride requests for drivers browsing pending rides
- **Location tracking** — drivers and students can share and view live location during an active ride, restricted to only those who currently share a ride together
- **Simple web frontend** — static HTML pages for login, the student view, and the driver board, with in-app toast notifications instead of browser alerts

## Tech stack

- **Backend:** FastAPI + Uvicorn
- **Database:** SQLModel (SQLAlchemy) with SQLite
- **Auth:** Bearer token sessions, PBKDF2-HMAC-SHA256 password hashing with per-user salts
- **Realtime:** native WebSockets
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

### 4. Run the server

```
uvicorn main:app --reload
```

The app will start at **http://127.0.0.1:8000**

A SQLite database file (`app.db`) is created automatically on first run.

## Usage

Once the server is running, open your browser to:

- **`/static/login.html`** — sign up or log in (returns and stores a session token)
- **`/static/student.html`** — request a ride, track your driver in real time
- **`/static/driver.html`** — view and accept nearby ride requests, manage active rides

Signup and login return a `token` alongside the user's info — the frontend stores this and sends it as `Authorization: Bearer <token>` on every request afterward. WebSocket connections pass the same token as a `?token=` query parameter, since browsers can't set custom headers during the WS handshake.

## API overview

| Method | Endpoint                        | Description                                                              |
| ------ | -------------------------------- | ------------------------------------------------------------------------- |
| POST   | `/signup`                       | Create a new account, returns a session token                            |
| POST   | `/login`                        | Log in, returns a session token                                          |
| POST   | `/rides`                        | Request a new ride *(student only, authenticated)*                       |
| GET    | `/rides`                        | List pending ride requests                                               |
| GET    | `/rides/{ride_id}`              | Get a specific ride *(only the student or driver on it)*                 |
| POST   | `/rides/{ride_id}/accept`       | Driver accepts a ride *(atomic — prevents two drivers double-accepting)* |
| POST   | `/rides/{ride_id}/start`        | Driver starts a ride *(only that ride's driver)*                         |
| POST   | `/rides/{ride_id}/complete`     | Mark a ride complete *(only that ride's driver)*                         |
| POST   | `/rides/{ride_id}/cancel`       | Student cancels a ride *(only that ride's student)*                      |
| POST   | `/rides/{ride_id}/release`      | Driver releases a ride back to the pool *(only that ride's driver)*      |
| POST   | `/users/me/location`            | Update your own live location                                            |
| GET    | `/users/{user_id}/location`     | Get a user's live location *(only if you share an active ride with them)* |
| GET    | `/drivers/me/active-ride`       | Get the logged-in driver's current active ride                          |
| GET    | `/students/me/active-ride`      | Get the logged-in student's current active ride                         |
| WS     | `/ws/rides/{ride_id}?token=...` | Live updates for a specific ride *(only its student/driver may connect)* |
| WS     | `/ws/board?token=...`           | Live feed of all open ride requests *(drivers only)*                     |

## Notes

- This project is a personal/local build — not affiliated with Uber or any commercial ride service.
- `app.db`, `venv/`, `.venv/`, and `__pycache__/` are git-ignored and won't be included when you clone the repo — you'll generate a fresh database the first time you run it.
- Endpoints that used to accept a `user_id`/`driver_id`/`student_id` directly from the client now infer the caller's identity from their session token instead — this closes an earlier gap where anyone could act as any user by editing values sent from the browser.