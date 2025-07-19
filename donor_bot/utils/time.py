from datetime import datetime
from zoneinfo import ZoneInfo

def today_msk():
    """Return current date in Europe/Moscow timezone."""
    return datetime.now(ZoneInfo("Europe/Moscow")).date() 