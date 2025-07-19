from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field

class Ticket(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    donor_id: int = Field(index=True)
    question: str
    answer: Optional[str] = None
    status: str = Field(default="open")  # open | taken | answered
    taken_by: Optional[int] = None  # admin tg id
    created_at: datetime = Field(default_factory=datetime.utcnow)
    answered_at: Optional[datetime] = None 