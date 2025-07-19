from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, UniqueConstraint


class Registration(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("donor_id", "event_id", name="unique_donor_event"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    donor_id: int = Field(foreign_key="donor.id")
    event_id: int = Field(foreign_key="event.id")
    status: str = Field(default="registered")  # donated | no-show | cancelled
    no_show_reason: Optional[str] = None  # medical | personal | changed_mind etc.
    created_at: datetime = Field(default_factory=datetime.utcnow)
