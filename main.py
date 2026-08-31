from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Field, Session, create_engine, select, update
from models import User, Ride, Session as SessionToken
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional
import hashlib
import secrets

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

engine = create_engine("sqlite:///app.db")

SESSION_LIFETIME = timedelta(days=30)

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

async def get_current_user_ws(websocket: WebSocket, token: str) -> Optional[User]:
    """WebSocket version — token comes in as a query param since browsers
    can't set custom headers on the WS handshake."""
    with Session(engine) as session:
        record = session.get(SessionToken, token)
        if not record or record.expires_at < datetime.utcnow():
            return None
        return session.get(User, record.user_id)

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

ride_connections: dict[int, list[WebSocket]] = {}
board_connections: list[WebSocket] = []  # drivers browsing the pending-rides board

@app.websocket("/ws/rides/{ride_id}")
async def ride_websocket(websocket: WebSocket, ride_id: int, token: str):
    user = await get_current_user_ws(websocket, token)
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
async def board_websocket(websocket: WebSocket, token: str):
    """Drivers connect here while browsing the pending-rides list (not yet
    tied to a specific ride). Used to push 'a ride disappeared' events —
    e.g. the student cancelled before any driver had accepted, so there's
    no per-ride socket to notify anyone through yet."""
    user = await get_current_user_ws(websocket, token)
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

@app.post("/signup")
def signup(user: User):
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.phone == user.phone)).first()
        if existing:
            return {"error": "That phone number is already registered. Try logging in instead."}
        user.salt = make_salt()
        user.password = hash_password(user.password, user.salt)
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_session(session, user.id)
        return {"id": user.id, "name": user.name, "role": user.role, "token": token}

class LoginRequest(BaseModel):
    phone: str
    password: str

@app.post("/login")
def login(credentials: LoginRequest):
    with Session(engine) as session:
        user = session.exec(select(User).where(User.phone == credentials.phone)).first()
        if not user or user.password != hash_password(credentials.password, user.salt):
            return {"error": "Invalid phone or password"}
        token = create_session(session, user.id)
        return {"id": user.id, "name": user.name, "role": user.role, "token": token}

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