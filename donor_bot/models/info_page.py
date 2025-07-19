from datetime import datetime

from sqlmodel import Field, SQLModel


class InfoPage(SQLModel, table=True):
    key: str = Field(primary_key=True)  # 'blood' | 'dkm' | 'mifi'
    content: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)
