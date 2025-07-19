from datetime import datetime
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from donor_bot.models import Ticket

# --- CRUD helpers ---

async def create_ticket(session: AsyncSession, donor_id: int, question: str) -> Ticket:
    ticket = Ticket(donor_id=donor_id, question=question)
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket

async def get_ticket(session: AsyncSession, ticket_id: int) -> Optional[Ticket]:
    return await session.get(Ticket, ticket_id)

async def take_ticket(session: AsyncSession, ticket_id: int, admin_id: int) -> bool:
    ticket = await get_ticket(session, ticket_id)
    if not ticket or ticket.status != "open":
        return False
    ticket.status = "taken"
    ticket.taken_by = admin_id
    session.add(ticket)
    await session.commit()
    return True

async def answer_ticket(session: AsyncSession, ticket_id: int, admin_id: int, answer_text: str) -> bool:
    """Сохраняет ответ администратора, оставляя статус тикета неизменным (open/taken).

    Возвращает True, если операция прошла успешно."""
    ticket = await get_ticket(session, ticket_id)
    if not ticket or ticket.status == "closed":
        return False
    ticket.answer = answer_text
    ticket.taken_by = admin_id
    ticket.answered_at = datetime.utcnow()
    # Не меняем status, чтобы диалог мог продолжаться
    session.add(ticket)
    await session.commit()
    return True

async def close_ticket(session: AsyncSession, ticket_id: int, admin_id: int) -> bool:
    """Переводит тикет в статус closed."""
    ticket = await get_ticket(session, ticket_id)
    if not ticket or ticket.status == "closed":
        return False
    ticket.status = "closed"
    ticket.taken_by = admin_id
    ticket.answered_at = datetime.utcnow()
    session.add(ticket)
    await session.commit()
    return True

async def list_open_tickets(session: AsyncSession) -> List[Ticket]:
    """Возвращает все тикеты кроме закрытых."""
    result = await session.execute(
        select(Ticket).where(Ticket.status != "closed").order_by(Ticket.created_at)  # type: ignore[arg-type]
    )
    return list(result.scalars().all())


# --- helpers for bot messages ---

def ticket_text(ticket: Ticket, donor_username: str | None = None) -> str:
    header = f"#Тикет {ticket.id} – {ticket.status.upper()}"
    user_part = f"@{donor_username}" if donor_username else f"donor {ticket.donor_id}"
    return f"{header}\nВопрос от {user_part}:\n{ticket.question}" 