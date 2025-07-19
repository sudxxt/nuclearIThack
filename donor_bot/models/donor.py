from datetime import datetime, date
from typing import Optional

from sqlmodel import Field, SQLModel


class Donor(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tg_id: int = Field(index=True, unique=True)
    phone: str
    full_name: str
    category: str  # student | staff | external
    group: Optional[str] = None
    # --- New statistics fields for different blood centers ---
    gavrilova_count: int = 0  # Кол-во донаций в ЦК им. Гаврилова
    fmba_count: int = 0       # Кол-во донаций в ЦК ФМБА
    total_sum: int = 0        # Суммарное число донаций (можно рассчитывать, но храним для удобства)

    # Геймификация
    points: int = 0  # очки за донации и активности
    streak: int = 0  # текущая серия непрерывных донаций
    last_donation: Optional[date] = None  # дата последней донации (для расчёта серии)

    # Даты последних донаций по центрам
    last_gavrilova: Optional[date] = None
    last_fmba: Optional[date] = None

    # Доп. контакты
    social: Optional[str] = None  # Контакты соцсети

    dkm_member: bool = Field(default=False)
    pd_agreed: bool = Field(default=False)
    # Preferred language for the UI: 'ru' | 'en'
    lang: str = Field(default="ru", max_length=2)
    created_at: datetime = Field(default_factory=datetime.utcnow)
