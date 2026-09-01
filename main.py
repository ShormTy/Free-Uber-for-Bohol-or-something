import re
import os
import time
import secrets
import hashlib
from typing import Optional
from datetime import datetime, timedelta
from collections import defaultdict, deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Field, Session, create_engine, select, update
from pydantic import BaseModel, field_validator

from models import User, Ride, Session as SessionToken, WSTicket

app = FastAPI()

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Set FRONTEND_ORIGINS as a comma-separated env var once you have a fixed
# domain (or capacitor://localhost / http://localhost for the packaged app).
# Falls back to "*" for local dev so nothing breaks before you set it.
#
# Note: the wildcard was fine from a security standpoint even before this —
# auth here is Bearer-token based (sent in an explicit header, not a cookie),
# so there's no CSRF-via-CORS exposure. This change is about tidiness /
# defense-in-depth once you have a real domain, not fixing a live hole.
_origins_env = os.environ.get("FRONTEND_ORIGINS", "*")
if _origins_env == "*":
    ALLOWED_ORIGINS = ["*"]
else:
    ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

engine = create_engine("sqlite:///app.db")

SESSION_LIFETIME = timedelta(days=30)
WS_TICKET_LIFETIME = timedelta(seconds=30)  # just long enough to open the socket

VALID_ROLES = {"student", "driver"}


def hash_password(password: str, salt: str) -> str:
    # PBKDF2-HMAC-SHA256, 200k iterations — free, stdlib, no external deps.
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 200_000
    ).hex()


def make_salt() -> str:
    return secrets.token_hex(16)


def create_session(session: Session, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    session.add(SessionToken(
        token=token,
        user_id=user_id,
        expires_at=datetime.utcnow() + SESSION_LIFETIME,
    ))
    session.commit()
    return token


def get_current_user(authorization: Optional[str] = Header(default=None)) -> User:
    """Resolves the Bearer token on every protected request into the
    actual User record. Nothing downstream trusts a client-supplied
    user_id/driver_id/student_id anymore — only this."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()

    with Session(engine) as session:
        record = session.get(SessionToken, token)
        if not record or record.expires_at < datetime.utcnow():
            raise HTTPException(status_code=401, detail="Session expired or invalid, please log in again")
        user = session.get(User, record.user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user


def get_current_user_token(authorization: Optional[str] = Header(default=None)) -> str:
    """Same resolution as get_current_user, but also hands back the raw
    token string — needed by /logout to know exactly which session row
    to delete."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return authorization.removeprefix("Bearer ").strip()


async def get_current_user_ws(websocket: WebSocket, ticket: str) -> Optional[User]:
    """WebSocket version — a short-lived, single-use ticket comes in as a
    query param since browsers can't set custom headers on the WS handshake.
    The ticket is minted via POST /ws-ticket (using the real 30-day Bearer
    token over HTTPS) and is deleted the moment it's redeemed here, so even
    if it ends up in a proxy/server access log, it's already spent and
    expires within seconds regardless."""
    with Session(engine) as session:
        record = session.get(WSTicket, ticket)
        if not record:
            return None
        session.delete(record)
        session.commit()
        if record.expires_at < datetime.utcnow():
            return None
        return session.get(User, record.user_id)


@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)


ride_connections: dict[int, list[WebSocket]] = {}
board_connections: list[WebSocket] = []  # drivers browsing the pending-rides board


# ---------------------------------------------------------------------------
# Rate limiting (in-memory — fine at current scale; resets on server
# restart/redeploy, and won't be shared across multiple instances if you
# ever horizontally scale. Revisit with Redis if/when that happens.)
# ---------------------------------------------------------------------------
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 60 * 5  # 5 minutes
_login_attempts: dict[str, deque] = defaultdict(deque)


def _client_key(request: Request) -> str:
    # Best-effort client identifier. Behind a reverse proxy, make sure
    # X-Forwarded-For is set and trusted, or every request will look like
    # it's coming from the proxy's IP.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_login_rate_limit(key: str):
    now = time.time()
    attempts = _login_attempts[key]
    while attempts and attempts[0] < now - LOGIN_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        retry_after = int(LOGIN_WINDOW_SECONDS - (now - attempts[0]))
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts. Try again in {max(retry_after, 1)} seconds.",
        )


def record_login_attempt(key: str):
    _login_attempts[key].append(time.time())


def clear_login_attempts(key: str):
    _login_attempts.pop(key, None)


@app.websocket("/ws/rides/{ride_id}")
async def ride_websocket(websocket: WebSocket, ride_id: int, ticket: str):
    user = await get_current_user_ws(websocket, ticket)
    if not user:
        await websocket.close(code=4401)  # custom app code: unauthenticated
        return

    with Session(engine) as session:
        ride = session.get(Ride, ride_id)

    if not ride or user.id not in (ride.student_id, ride.driver_id):
        await websocket.close(code=4403)  # custom app code: forbidden
        return

    await websocket.accept()
    ride_connections.setdefault(ride_id, []).append(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            for connection in ride_connections.get(ride_id, []):
                if connection != websocket:
                    await connection.send_json(data)
    except WebSocketDisconnect:
        ride_connections[ride_id].remove(websocket)


@app.websocket("/ws/board")
async def board_websocket(websocket: WebSocket, ticket: str):
    """Drivers connect here while browsing the pending-rides list (not yet
    tied to a specific ride). Used to push 'a ride disappeared' events —
    e.g. the student cancelled before any driver had accepted, so there's
    no per-ride socket to notify anyone through yet."""
    user = await get_current_user_ws(websocket, ticket)
    if not user or user.role != "driver":
        await websocket.close(code=4403)
        return

    await websocket.accept()
    board_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()  # drivers don't send anything; just keep the connection open
    except WebSocketDisconnect:
        board_connections.remove(websocket)


async def broadcast_to_board(message: dict):
    for connection in board_connections:
        await connection.send_json(message)


@app.get("/")
def read_root():
    return {"message": "My app is alive!"}


PHONE_PATTERN = re.compile(r"^\+?[0-9]{7,15}$")


class SignupRequest(BaseModel):
    """Dedicated signup shape instead of accepting the raw User table
    model — the client should only ever be able to set these four fields.
    Anything else (id, current_lat/lng, salt, an already-hashed password,
    etc.) is simply not part of this schema, so it can't be smuggled in."""
    name: str
    phone: str
    password: str
    role: str

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}")
        return v

    @field_validator("name", "password")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("phone")
    @classmethod
    def phone_must_be_valid(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        # Strip common formatting characters people type by habit —
        # spaces, dashes, parens — before validating and storing, so
        # "0912-345-6789" and "09123456789" aren't treated as different
        # phone numbers.
        cleaned = re.sub(r"[\s\-().]", "", v)
        if not PHONE_PATTERN.match(cleaned):
            raise ValueError("must be a valid phone number (digits only, 7-15 digits, optional leading +)")
        return cleaned


@app.post("/signup")
def signup(body: SignupRequest):
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.phone == body.phone)).first()
        if existing:
            return {"error": "That phone number is already registered. Try logging in instead."}
        salt = make_salt()
        user = User(
            name=body.name,
            phone=body.phone,
            role=body.role,
            password=hash_password(body.password, salt),
            salt=salt,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_session(session, user.id)
        return {"id": user.id, "name": user.name, "role": user.role, "token": token}


class LoginRequest(BaseModel):
    phone: str
    password: str

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        # Same cleanup as signup, so "0912-345-6789" typed at login still
        # matches the "09123456789" stored from signup.
        return re.sub(r"[\s\-().]", "", v or "")


@app.post("/login")
def login(credentials: LoginRequest, request: Request):
    key = _client_key(request)
    check_login_rate_limit(key)

    with Session(engine) as session:
        user = session.exec(select(User).where(User.phone == credentials.phone)).first()
        if not user or user.password != hash_password(credentials.password, user.salt):
            record_login_attempt(key)
            return {"error": "Invalid phone or password"}
        clear_login_attempts(key)
        token = create_session(session, user.id)
        return {"id": user.id, "name": user.name, "role": user.role, "token": token}


@app.post("/logout")
def logout(token: str = Depends(get_current_user_token)):
    """Revokes the current session server-side. localStorage.clear() alone
    only forgets the token client-side — the token itself stayed valid for
    up to 30 days if it leaked. This deletes the session row so it can't be
    replayed."""
    with Session(engine) as session:
        record = session.get(SessionToken, token)
        if record:
            session.delete(record)
            session.commit()
    return {"status": "logged out"}


@app.post("/ws-ticket")
def issue_ws_ticket(current_user: User = Depends(get_current_user)):
    """Mint a short-lived, single-use ticket for opening a WebSocket. Called
    with the real Bearer token over a normal (HTTPS) request; the ticket
    that goes into the WS query string afterward expires in ~30s and is
    deleted on first use, so it's not a meaningful thing to have captured
    even if it ends up in a proxy access log."""
    ticket = secrets.token_urlsafe(24)
    with Session(engine) as session:
        session.add(WSTicket(
            ticket=ticket,
            user_id=current_user.id,
            expires_at=datetime.utcnow() + WS_TICKET_LIFETIME,
        ))
        session.commit()
    return {"ticket": ticket}


class RideRequest(BaseModel):
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float


@app.post("/rides")
async def request_ride(body: RideRequest, current_user: User = Depends(get_current_user)):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Only students can request rides")
    with Session(engine) as session:
        existing = session.exec(
            select(Ride).where(
                Ride.student_id == current_user.id,
                Ride.status.in_(["requested", "accepted", "ongoing"])
            )
        ).first()
        if existing:
            return {"error": f"You already have an active ride (#{existing.id}, status: {existing.status})."}
        ride = Ride(
            student_id=current_user.id,  # always the authenticated user, never client-supplied
            pickup_lat=body.pickup_lat,
            pickup_lng=body.pickup_lng,
            dropoff_lat=body.dropoff_lat,
            dropoff_lng=body.dropoff_lng,
        )
        session.add(ride)
        session.commit()
        session.refresh(ride)

    await broadcast_to_board({"type": "ride_added", "ride": ride.model_dump(mode="json")})
    return ride


@app.get("/rides")
def list_rides():
    with Session(engine) as session:
        rides = session.exec(select(Ride).where(Ride.status == "requested")).all()
        return rides


@app.get("/rides/{ride_id}")
def get_ride(ride_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        ride = session.get(Ride, ride_id)
        if not ride:
            return {"error": "Ride not found"}
        if current_user.id not in (ride.student_id, ride.driver_id):
            raise HTTPException(status_code=403, detail="Not your ride")
        return ride


@app.post("/rides/{ride_id}/accept")
async def accept_ride(ride_id: int, current_user: User = Depends(get_current_user)):
    if current_user.role != "driver":
        raise HTTPException(status_code=403, detail="Only drivers can accept rides")
    with Session(engine) as session:
        # Atomic conditional update: only succeeds if the ride is still
        # "requested" at the moment SQLite executes the write. This closes
        # the check-then-write race where two drivers could both pass the
        # Python-level status check before either commits.
        result = session.exec(
            update(Ride)
            .where(Ride.id == ride_id, Ride.status == "requested")
            .values(driver_id=current_user.id, status="accepted")
        )
        session.commit()

        if result.rowcount == 0:
            ride = session.get(Ride, ride_id)
            if not ride:
                return {"error": "Ride not found"}
            return {"error": f"Ride is already {ride.status}"}

        ride = session.get(Ride, ride_id)

    for connection in ride_connections.get(ride_id, []):
        await connection.send_json({"type": "status", "status": "accepted", "driver_id": current_user.id})
    await broadcast_to_board({"type": "ride_removed", "ride_id": ride_id})

    return ride


@app.post("/rides/{ride_id}/start")
async def start_ride(ride_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        ride = session.get(Ride, ride_id)
        if not ride:
            return {"error": "Ride not found"}
        if ride.driver_id != current_user.id:
            raise HTTPException(status_code=403, detail="You aren't the driver on this ride")
        if ride.status != "accepted":
            return {"error": f"Ride must be accepted first (currently {ride.status})"}
        ride.status = "ongoing"
        session.add(ride)
        session.commit()
        session.refresh(ride)

    for connection in ride_connections.get(ride_id, []):
        await connection.send_json({"type": "status", "status": "ongoing"})

    return ride


@app.post("/rides/{ride_id}/complete")
async def complete_ride(ride_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        ride = session.get(Ride, ride_id)
        if not ride:
            return {"error": "Ride not found"}
        if ride.driver_id != current_user.id:
            raise HTTPException(status_code=403, detail="You aren't the driver on this ride")
        if ride.status != "ongoing":
            return {"error": f"Ride must be ongoing first (currently {ride.status})"}
        ride.status = "completed"
        session.add(ride)
        session.commit()
        session.refresh(ride)

    for connection in ride_connections.get(ride_id, []):
        await connection.send_json({"type": "status", "status": "completed"})

    return ride


class LocationUpdate(BaseModel):
    lat: float
    lng: float


@app.post("/users/me/location")
def update_location(location: LocationUpdate, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        user = session.get(User, current_user.id)
        user.current_lat = location.lat
        user.current_lng = location.lng
        session.add(user)
        session.commit()
        return {"status": "updated"}


@app.get("/users/{user_id}/location")
def get_location(user_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        # Only allow this if the caller shares an active ride with user_id —
        # e.g. a student checking their assigned driver's live position.
        shared_ride = session.exec(
            select(Ride).where(
                Ride.status.in_(["accepted", "ongoing"]),
                (
                    ((Ride.student_id == current_user.id) & (Ride.driver_id == user_id)) |
                    ((Ride.driver_id == current_user.id) & (Ride.student_id == user_id))
                )
            )
        ).first()
        if not shared_ride:
            raise HTTPException(status_code=403, detail="No active ride with this user")

        user = session.get(User, user_id)
        if not user or user.current_lat is None:
            return {"error": "Location not available"}
        return {"lat": user.current_lat, "lng": user.current_lng}


@app.get("/drivers/me/active-ride")
def get_active_ride_for_driver(current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        ride = session.exec(
            select(Ride).where(
                Ride.driver_id == current_user.id,
                Ride.status.in_(["accepted", "ongoing"])
            )
        ).first()
        if not ride:
            return {"error": "No active ride"}
        return ride


@app.get("/students/me/active-ride")
def get_active_ride_for_student(current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        ride = session.exec(
            select(Ride).where(
                Ride.student_id == current_user.id,
                Ride.status.in_(["requested", "accepted", "ongoing"])
            )
        ).first()
        if not ride:
            return {"error": "No active ride"}
        return ride


@app.post("/rides/{ride_id}/cancel")
async def cancel_ride(ride_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        ride = session.get(Ride, ride_id)
        if not ride:
            return {"error": "Ride not found"}
        if ride.student_id != current_user.id:
            raise HTTPException(status_code=403, detail="You aren't the student on this ride")
        if ride.status not in ["requested", "accepted"]:
            return {"error": f"Can't cancel a ride that's already {ride.status}"}
        ride.status = "cancelled"
        session.add(ride)
        session.commit()
        session.refresh(ride)

    for connection in ride_connections.get(ride_id, []):
        await connection.send_json({"type": "cancelled", "by": "student"})
    await broadcast_to_board({"type": "ride_removed", "ride_id": ride_id})

    return ride


@app.post("/rides/{ride_id}/release")
async def release_ride(ride_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        ride = session.get(Ride, ride_id)
        if not ride:
            return {"error": "Ride not found"}
        if ride.driver_id != current_user.id:
            raise HTTPException(status_code=403, detail="You aren't the driver on this ride")
        if ride.status != "accepted":
            return {"error": f"Can't release a ride that's {ride.status}"}
        ride.driver_id = None
        ride.status = "requested"
        session.add(ride)
        session.commit()
        session.refresh(ride)

    for connection in ride_connections.get(ride_id, []):
        await connection.send_json({"type": "released", "by": "driver"})
    await broadcast_to_board({"type": "ride_added", "ride": ride.model_dump(mode="json")})

    return ride


# ---------------------------------------------------------------------------
# Password reset — admin-assisted (free-tier friendly)
# ---------------------------------------------------------------------------
# There's no budget for a paid SMS/email provider, and this app currently
# authenticates by phone number with no email on file, so a self-service
# "forgot password" flow isn't buildable for free without adding real cost
# or an insecure workaround. This endpoint is an interim stand-in: you (the
# operator) run it yourself, protected by a secret only you have, when a
# user asks you directly to reset their password. It also revokes all of
# that user's existing sessions, since a password reset should invalidate
# anything issued under the old password.
#
# Set ADMIN_RESET_SECRET in your environment before deploying. If it's
# unset, this endpoint refuses to run at all rather than defaulting open.
ADMIN_RESET_SECRET = os.environ.get("ADMIN_RESET_SECRET")


class AdminPasswordReset(BaseModel):
    phone: str
    new_password: str
    admin_secret: str


@app.post("/admin/reset-password")
def admin_reset_password(body: AdminPasswordReset):
    if not ADMIN_RESET_SECRET:
        raise HTTPException(status_code=503, detail="Admin reset is not configured on this server")
    if not secrets.compare_digest(body.admin_secret, ADMIN_RESET_SECRET):
        raise HTTPException(status_code=403, detail="Invalid admin secret")
    if not body.new_password or len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    with Session(engine) as session:
        user = session.exec(select(User).where(User.phone == body.phone)).first()
        if not user:
            return {"error": "No account with that phone number"}

        salt = make_salt()
        user.salt = salt
        user.password = hash_password(body.new_password, salt)
        session.add(user)

        # Revoke every existing session for this user — a password reset
        # should log out any device currently using the old credentials.
        old_sessions = session.exec(
            select(SessionToken).where(SessionToken.user_id == user.id)
        ).all()
        for s in old_sessions:
            session.delete(s)

        session.commit()
        return {"status": f"Password reset for {user.name} ({user.phone})"}