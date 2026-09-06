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

class PasswordResetRequest(SQLModel, table=True):
    """A user-submitted 'please reset my password' request, for the admin
    to review and act on manually via POST /admin/reset-password. This is
    the free, phone-only-auth-compatible alternative to a self-service
    flow: NIST explicitly advises against security-question-based recovery
    (answers are guessable/scrapeable), so verification here is a human
    (the admin) confirming identity out-of-band before acting — not an
    automated challenge. This table just removes the need for the user to
    separately track the admin down to ask.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    phone: str  # not a foreign key — the phone may be mistyped, or belong
                # to no account at all; the admin sees the raw submission
    message: Optional[str] = None  # optional context from the user, e.g.
                                    # "it's Juan, my sister can vouch for me"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved: bool = False
    resolved_at: Optional[datetime] = None