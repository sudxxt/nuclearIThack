from typing import Optional
from sqlmodel import Field, SQLModel

class Admin(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tg_id: int = Field(unique=True, index=True) 