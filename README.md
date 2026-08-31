This is purely vibecoded.

# Free-Uber-for-Bohol-or-something

A lightweight ride-hailing web app built for Bohol — students can request rides, drivers can accept and complete them, and both sides get live updates over WebSockets. No third-party ride app fees, just a simple self-hosted matcher.

## Features

- **Accounts** — sign up and log in as a student (rider) or a driver
- **Ride lifecycle** — request a ride, a driver accepts it, starts it, then completes it (or cancels/releases it back)
- **Live updates** — WebSocket connections push ride status changes in real time to both the rider and the driver, plus a live "board" view of all open ride requests
- **Location tracking** — drivers and students can share and view live location during an active ride
- **Simple web frontend** — static HTML pages for login, the student view, and the driver board

## Tech stack

- **Backend:** FastAPI + Uvicorn
- **Database:** SQLModel (SQLAlchemy) with SQLite
- **Realtime:** native WebSockets
- **Frontend:** static HTML/CSS served from `/static`

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/ShormTy/Free-Uber-for-Bohol-or-something.git
cd Free-Uber-for-Bohol-or-something
```

### 2. Create a virtual environment

```bash
python -m venv venv
```

Activate it:

- **Windows (PowerShell):**
  ```powershell
  venv\Scripts\Activate.ps1
  ```
- **macOS/Linux:**
  ```bash
  source venv/bin/activate
  ```

### 3. Install dependencies

```bash
pip install fastapi uvicorn sqlmodel requests
```

### 4. Run the server

```bash
uvicorn main:app --reload
```

The app will start at **http://127.0.0.1:8000**

A SQLite database file (`app.db`) is created automatically on first run.

## Usage

Once the server is running, open your browser to:

- **`/static/login.html`** — sign up or log in
- **`/static/student.html`** — request a ride, track your driver in real time
- **`/static/driver.html`** — view and accept nearby ride requests, manage active rides

## API overview

| Method | Endpoint | Description |
|---|---|---|
| POST | `/signup` | Create a new account |
| POST | `/login` | Log in |
| POST | `/rides` | Request a new ride |
| GET | `/rides` | List rides |
| GET | `/rides/{ride_id}` | Get a specific ride |
| POST | `/rides/{ride_id}/accept` | Driver accepts a ride |
| POST | `/rides/{ride_id}/start` | Driver starts a ride |
| POST | `/rides/{ride_id}/complete` | Mark a ride complete |
| POST | `/rides/{ride_id}/cancel` | Cancel a ride |
| POST | `/rides/{ride_id}/release` | Driver releases a ride back to the pool |
| POST | `/users/me/location` | Update your live location |
| GET | `/users/{user_id}/location` | Get a user's live location |
| GET | `/drivers/me/active-ride` | Get the driver's current active ride |
| GET | `/students/me/active-ride` | Get the student's current active ride |
| WS | `/ws/rides/{ride_id}` | Live updates for a specific ride |
| WS | `/ws/board` | Live feed of all open ride requests |

## Notes

- This project is a personal/local build — not affiliated with Uber or any commercial ride service.
- `app.db`, `venv/`, `.venv/`, and `__pycache__/` are git-ignored and won't be included when you clone the repo — you'll generate a fresh database the first time you run it.