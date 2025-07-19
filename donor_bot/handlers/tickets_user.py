from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy.ext.asyncio import AsyncSession

from donor_bot.services.tickets import create_ticket, answer_ticket  # type: ignore
from donor_bot.services.donors import get_donor_by_tg_id
from donor_bot.config import settings

# Reply keyboard
from donor_bot.keyboards.donor import back_button, main_menu_kb  # type: ignore

router = Router()


class TicketCreateState(StatesGroup):
    waiting_for_subject = State()
    waiting_for_message = State()


class TicketReplyState(StatesGroup):
    waiting_for_reply = State()


# -------- создание тикета ---------

@router.message(F.text == "🎫 Тикет")
async def ticket_start(message: Message, state: FSMContext):
    await message.answer("Введите тему тикета:", reply_markup=back_button)
    await state.set_state(TicketCreateState.waiting_for_subject)


@router.message(TicketCreateState.waiting_for_subject)
async def ticket_subject(message: Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        await message.answer("Отменено.", reply_markup=main_menu_kb)
        await state.clear()
        return
    if not message.text:
        return

    subj = message.text.strip()
    if len(subj) < 3:
        await message.answer("Тема слишком короткая. Укажите не менее 3 символов.")
        return

    await state.update_data(subject=subj)
    await message.answer("Опишите проблему или вопрос одним сообщением (не менее 10 символов):")
    await state.set_state(TicketCreateState.waiting_for_message)


@router.message(TicketCreateState.waiting_for_message)
async def ticket_body(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    if message.text == "⬅️ Назад":
        await message.answer("Отменено.", reply_markup=main_menu_kb)
        await state.clear()
        return
    if not message.text or not message.from_user:
        return

    body = message.text.strip()
    if len(body) < 10:
        await message.answer("Описание слишком короткое. Попробуйте подробнее описать вопрос (мин. 10 символов).")
        return

    data = await state.get_data()
    subject = data.get("subject", "Без темы")
    ticket_text = f"{subject}\n\n{body}"
    ticket = await create_ticket(session, message.from_user.id, ticket_text)
    await message.answer("Тикет создан ✅", reply_markup=main_menu_kb)

    # Уведомляем админов
    from donor_bot.handlers.tickets_admin import send_ticket_embed  # lazy import to avoid cycle
    await send_ticket_embed(bot, session, ticket.id)  # type: ignore[arg-type]
    await state.clear()


# -------- ответ пользователя на ответ администратора ---------

@router.callback_query(F.data.startswith("user_ticket_reply:"))
async def user_ticket_reply_start(call: CallbackQuery, state: FSMContext):
    if not call.data:
        return
    ticket_id = int(call.data.split(":")[1])
    await state.update_data(ticket_id=ticket_id)
    await call.message.answer("Введите ваш ответ:")  # type: ignore[attr-defined]
    await state.set_state(TicketReplyState.waiting_for_reply)
    await call.answer()


@router.message(TicketReplyState.waiting_for_reply)
async def process_user_reply(message: Message, state: FSMContext, bot: Bot, session: AsyncSession):
    data = await state.get_data()
    ticket_id: int | None = data.get("ticket_id")  # type: ignore
    if ticket_id is None or not message.text or not message.from_user:
        await state.clear()
        return

    # Сохраняем ответ в истории (как answer), не меняя статус
    await answer_ticket(session, ticket_id, 0, message.text)  # admin_id 0 для отличия?

    # Пересылаем администратору
    from donor_bot.services.donors import get_donor_by_tg_id
    donor = await get_donor_by_tg_id(session, message.from_user.id)
    donor_name = getattr(donor, "full_name", None) or f"user {message.from_user.id}"
    text_to_admin = (
        f"Ответ пользователя {donor_name} по тикету {ticket_id}:\n{message.text}"
    )

    delivered = False
    if settings.ADMIN_CHAT_ID:
        try:
            await bot.send_message(settings.ADMIN_CHAT_ID, text_to_admin)
            delivered = True
        except Exception:
            pass
    if not delivered:
        for admin_id in settings.ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text_to_admin)
                delivered = True
            except Exception:
                continue

    await message.answer("Ваш ответ отправлен.")
    await state.clear() 