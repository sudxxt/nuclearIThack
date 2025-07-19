from datetime import date, time
from typing import Optional

from sqlmodel import Field, SQLModel


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: date
    blood_center: str
    slots_total: int = 100
    slots_taken: int = 0
    external_link: Optional[str] = None  # ссылка для внешних доноров
    start_time: time | None = None  # время начала акции
    end_time: time | None = None    # время окончания акции

