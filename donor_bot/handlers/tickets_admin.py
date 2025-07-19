import logging  # added logging import
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy.ext.asyncio import AsyncSession

from donor_bot.services.tickets import (
    take_ticket,
    answer_ticket,
    close_ticket,
    list_open_tickets,
    get_ticket,
    ticket_text,
)
from donor_bot.services.donors import get_donor_by_tg_id
from donor_bot.config import settings

router = Router()
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))


def build_kb(ticket_id: int | None, status: str):
    """Формирует inline-кнопки для управления тикетом для админа."""
    if ticket_id is None or status == "closed":
        return None

    buttons: list[list[InlineKeyboardButton]] = []

    if status == "open":
        # Не взят: можно взять/ответить/закрыть
        buttons.append([
            InlineKeyboardButton(text="🙋‍♂️ Взять", callback_data=f"ticket:take:{ticket_id}"),
            InlineKeyboardButton(text="✍️ Ответить", callback_data=f"ticket:reply:{ticket_id}"),
            InlineKeyboardButton(text="✅ Закрыть", callback_data=f"ticket:close:{ticket_id}"),
        ])
    elif status == "taken":
        buttons.append([
            InlineKeyboardButton(text="✍️ Ответить", callback_data=f"ticket:reply:{ticket_id}"),
            InlineKeyboardButton(text="✅ Закрыть", callback_data=f"ticket:close:{ticket_id}"),
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_ticket_embed(
    bot: Bot,
    session: AsyncSession,
    ticket_id: int,
    dest_chat_id: int | None = None,
):
    """Отправить/показать карточку тикета администратору.

    Если dest_chat_id не указан, пробуем ADMIN_CHAT_ID, затем каждого из ADMIN_IDS.
    """

    ticket = await get_ticket(session, ticket_id)
    if not ticket:
        return

    donor = await get_donor_by_tg_id(session, ticket.donor_id)
    username = getattr(donor, "full_name", None) if donor else None
    text = ticket_text(ticket, username)
    assert ticket.id is not None
    kb = build_kb(ticket.id, ticket.status)

    target_chats: list[int] = []
    if dest_chat_id is not None:
        target_chats.append(dest_chat_id)
    elif settings.ADMIN_CHAT_ID:
        target_chats.append(settings.ADMIN_CHAT_ID)
    target_chats.extend(settings.ADMIN_IDS)

    for chat_id in target_chats:
        try:
            await bot.send_message(chat_id, text, reply_markup=kb)
            break  # успешно – выходим
        except Exception:
            continue


# ---------- callbacks ----------

@router.callback_query(F.data.startswith("ticket:take:"))
async def cb_take(call: CallbackQuery, session: AsyncSession):
    logging.info("cb_take triggered by user %s, data: %s", call.from_user.id, call.data)  # added logging
    ticket_id = int(call.data.split(":")[2])  # type: ignore
    ok = await take_ticket(session, ticket_id, call.from_user.id)  # type: ignore
    if ok:
        await call.answer("Тикет взят")
        await call.message.edit_reply_markup(reply_markup=build_kb(ticket_id, "taken"))  # type: ignore[attr-defined]
    else:
        await call.answer("Не удалось взять", show_alert=True)


@router.callback_query(F.data.startswith("ticket:close:"))
async def cb_close(call: CallbackQuery, session: AsyncSession):
    logging.info("cb_close triggered by user %s, data: %s", call.from_user.id, call.data)  # added logging
    ticket_id = int(call.data.split(":")[2])  # type: ignore
    ok = await close_ticket(session, ticket_id, call.from_user.id)  # type: ignore
    if ok:
        await call.answer("Закрыто")
        await call.message.edit_reply_markup(reply_markup=None)  # type: ignore[attr-defined]
    else:
        await call.answer("Не удалось", show_alert=True)


# ---- FSM for answer flow ----


class ReplyState(StatesGroup):
    waiting_for_answer = State()


@router.callback_query(F.data.startswith("ticket:reply:"))
async def cb_reply(call: CallbackQuery, state: FSMContext):
    logging.info("cb_reply triggered by user %s, data: %s", call.from_user.id, call.data)  # added logging
    if not call.data:
        await call.answer()
        return
    ticket_id = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(ReplyState.waiting_for_answer)
    await state.update_data(ticket_id=ticket_id)
    if isinstance(call.message, Message):
        await call.message.answer(
            f"Введите ответ на тикет {ticket_id}. Отправьте текст одним сообщением."
        )

@router.message(ReplyState.waiting_for_answer)
async def receive_reply(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
):
    logging.info("receive_reply triggered by user %s", message.from_user.id)  # added logging
    data = await state.get_data()
    ticket_id: int | None = data.get("ticket_id")  # type: ignore
    if ticket_id is None:
        await state.clear()
        return
    await answer_ticket(session, ticket_id, message.from_user.id, message.text or "")  # type: ignore[arg-type]
    ticket = await get_ticket(session, ticket_id)
    if ticket:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        reply_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Ответить", callback_data=f"user_ticket_reply:{ticket.id}")]
            ]
        )
        await bot.send_message(  # type: ignore[attr-defined]
            ticket.donor_id,
            f"Ответ на ваш тикет:\n{message.text}",
            reply_markup=reply_kb,
        )
    await message.answer("Ответ отправлен")
    await state.clear()


# ---------- command ----------

@router.message(Command("tickets"))
async def cmd_tickets(message: Message, session: AsyncSession):
    logging.info("cmd_tickets triggered by user %s", message.from_user.id)  # added logging
    tickets = await list_open_tickets(session)
    if not tickets:
        await message.answer("Открытых тикетов нет")
        return
    for t in tickets:
        if t.id is None:
            continue
        await message.answer(ticket_text(t, None), reply_markup=build_kb(t.id, t.status))