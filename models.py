from typing import Optional
from datetime import datetime, timedelta
from sqlmodel import SQLModel, Field

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    phone: str = Field(unique=True)
    role: str  # "student" or "driver"
    password: str
    salt: str
    current_lat: Optional[float] = None
    current_lng: Optional[float] = None
    # Free, no-SMS/email password reset: a security question the user picks
    # at signup. security_answer is stored hashed (same PBKDF2 scheme as
    # password) — never in plaintext.
    security_question: Optional[str] = None
    security_answer_hash: Optional[str] = None
    security_answer_salt: Optional[str] = None

class Ride(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="user.id")
    driver_id: Optional[int] = Field(default=None, foreign_key="user.id")
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    status: str = "requested"  # requested -> accepted -> ongoing -> completed -> cancelled

class Session(SQLModel, table=True):
    token: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime

class WSTicket(SQLModel, table=True):
    """Short-lived, single-use ticket for authenticating WebSocket connections.
    Unlike the 30-day Session token, this never needs to go in a query
    string long-term — it's minted right before connecting and expires in
    seconds, so even if a proxy/access-log captures it, it's useless shortly
    after."""
    ticket: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    expires_at: datetime

class PasswordResetToken(SQLModel, table=True):
    """Short-lived, single-use token issued after a security-question
    challenge is answered correctly. Redeemed once to set a new password."""
    token: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    used: bool = False