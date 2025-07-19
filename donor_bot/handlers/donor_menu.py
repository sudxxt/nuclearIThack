from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession
# from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback  # calendar hidden (registration via event list)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram import Bot

from donor_bot.keyboards import info_kb, history_kb, main_menu_kb, answer_kb, back_button  # type: ignore
from donor_bot.services.donors import get_donor_by_tg_id
from donor_bot.services.donors import get_donor_history
from donor_bot.services.events import (
    register_donor_for_event,
    cancel_registration,
)
from donor_bot.models import InfoPage
from donor_bot.config import settings
from donor_bot.models.event import Event
from sqlalchemy import select, asc
from datetime import date

router = Router()
# calendar removed – регистрация теперь через кнопки в списке мероприятий

# ---------- Просмотр всех мероприятий ----------

PAGE_SIZE = 5


def _event_label(ev: Event) -> str:
    return f"{ev.date.strftime('%d.%m.%Y')} – {ev.blood_center}"


async def build_events_kb(session: AsyncSession, page: int):
    events = (
        await session.execute(
            select(Event)
            .where(getattr(Event, "date") >= date.today())
            .order_by(asc(getattr(Event, "date")))
        )
    ).scalars().all()
    start = page * PAGE_SIZE
    slice_events = events[start : start + PAGE_SIZE]
    buttons = [[InlineKeyboardButton(text=_event_label(ev), callback_data=f"evt_info:{ev.id}")] for ev in slice_events]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"evt_pg:{page-1}"))
    if start + PAGE_SIZE < len(events):
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"evt_pg:{page+1}"))
    if nav_row:
        buttons.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(F.text == "📅 Мероприятия")
async def list_events(message: Message, session: AsyncSession):
    kb = await build_events_kb(session, 0)
    if not kb.inline_keyboard:
        await message.answer("Нет доступных мероприятий.")
        return
    await message.answer("Ближайшие мероприятия:", reply_markup=kb)


@router.callback_query(F.data.startswith("evt_pg:"))
async def events_pagination(call: CallbackQuery, session: AsyncSession):
    if not call.data:
        await call.answer()
        return
    page = int(call.data.split(":")[1])
    kb = await build_events_kb(session, page)
    await call.message.edit_reply_markup(reply_markup=kb)  # type: ignore[attr-defined]
    await call.answer()


@router.callback_query(F.data.startswith("evt_info:"))
async def event_info(call: CallbackQuery, session: AsyncSession):
    if not call.data:
        await call.answer()
        return
    event_id = int(call.data.split(":")[1])
    event = await session.get(Event, event_id)
    if not event:
        await call.answer("Мероприятие не найдено", show_alert=True)
        return
    from donor_bot.models import Registration

    # Корректное число занятых мест – по реальным записям (кроме отменённых)
    from sqlalchemy import func
    occupied: int = (
        await session.scalar(
            select(func.count())
            .select_from(Registration)
            .where(Registration.event_id == event.id)  # type: ignore[arg-type]
            .where(Registration.status != "cancelled")  # type: ignore[arg-type]
        )
    ) or 0

    free_slots = max(event.slots_total - occupied, 0)
    time_note = ""
    if event.start_time and event.end_time:
        time_note = f"\nВремя: {event.start_time.strftime('%H:%M')}–{event.end_time.strftime('%H:%M')}"
    text = (
        f"<b>{event.date.strftime('%d.%m.%Y')}</b> – {event.blood_center}{time_note}\n"
        f"Свободно слотов: {free_slots} из {event.slots_total}"
    )

    # Проверяем регистрацию текущего пользователя
    kb = None
    if call.from_user:
        donor = await get_donor_by_tg_id(session, call.from_user.id)
        reg: Registration | None = None
        if donor and donor.id:
            reg = (
                await session.execute(
                    select(Registration)
                    .where(Registration.donor_id == donor.id)  # type: ignore[arg-type]
                    .where(Registration.event_id == event.id)  # type: ignore[arg-type]
                )
            ).scalars().first()

        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        buttons = []
        if not reg:
            if free_slots > 0:
                buttons.append([InlineKeyboardButton(text="Записаться", callback_data=f"evt_reg:{event.id}")])
        elif reg.status == "registered":
            buttons.append([InlineKeyboardButton(text="Отменить запись", callback_data=f"evt_cancel:{event.id}")])

        if buttons:
            kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await call.message.answer(text, parse_mode="HTML", reply_markup=kb)  # type: ignore[attr-defined]
    await call.answer()


# --- States ---
class AskQuestionState(StatesGroup):
    waiting_for_question = State()


@router.message(F.text == "🩸 Моя карточка")
async def show_card(message: Message, session: AsyncSession):
    if not message.from_user:
        return
    donor = await get_donor_by_tg_id(session, message.from_user.id)
    if not donor:
        await message.answer("Сначала нужно зарегистрироваться. Введите /start")
        return

    history = await get_donor_history(session, donor.tg_id)
    donated_regs = [h for h in history if h[0].status == "donated"]
    if donated_regs:
        last_event = donated_regs[-1][1]
        last_date = f"{last_event.date.strftime('%d.%m.%Y')} — {last_event.blood_center}"
    else:
        last_date = "-"
    from donor_bot.services.donors import compute_level

    level_name = compute_level(donor.points or 0)
    liters = donor.total_sum * 0.45 if donor.total_sum else 0.0
    card_text = (
        f"<b>ФИО:</b> {donor.full_name}\n"
        f"<b>Категория:</b> {donor.category}\n"
        f"<b>Всего донаций:</b> {len(donated_regs)} ({liters:.2f} л)\n"
        f"<b>Уровень:</b> {level_name} ({donor.points} очков)\n"
        f"<b>Текущий стрик:</b> {donor.streak} \n"
        f"<b>Последняя донация:</b> {last_date}\n"
        f"<b>В регистре ДКМ:</b> {'Да' if donor.dkm_member else 'Нет'}"
    )
    await message.answer(card_text, parse_mode="HTML", reply_markup=history_kb)


# Удалён маршрут "📆 Записаться" и календарь. Теперь регистрация через кнопки в разделе мероприятий.


# --------- Регистрация / отмена через кнопки ---------


@router.callback_query(F.data.startswith("evt_reg:"))
async def evt_register_cb(call: CallbackQuery, session: AsyncSession):
    if not call.from_user or not call.data:
        await call.answer()
        return
    event_id = int(call.data.split(":")[1])
    success, msg = await register_donor_for_event(session, call.from_user.id, event_id)
    if success:
        # Обновляем сообщение, чтобы сразу показать результат записи
        try:
            await call.message.edit_text(msg, parse_mode="HTML")  # type: ignore[attr-defined]
        except Exception:
            await call.answer(msg, show_alert=True)

        # Send Google Calendar button
        event = await session.get(Event, event_id)
        if event and event.start_time and event.end_time:
            from datetime import datetime as _dt
            start_dt = _dt.combine(event.date, event.start_time)
            end_dt = _dt.combine(event.date, event.end_time)
            start_str = start_dt.strftime("%Y%m%dT%H%M00")
            end_str = end_dt.strftime("%Y%m%dT%H%M00")
            cal_url = (
                "https://www.google.com/calendar/render?action=TEMPLATE"
                f"&text=День+Донора+{event.blood_center.replace(' ','+')}"
                f"&dates={start_str}/{end_str}"
                f"&details=Регистрация+из+бота"
                f"&location=МИФИ"
                "&sf=true&output=xml"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Добавить в Google Calendar", url=cal_url)]]
            )
            await call.message.answer("Добавьте напоминание в календарь:", reply_markup=kb)  # type: ignore[attr-defined]
    else:
        await call.answer(msg, show_alert=True)


@router.callback_query(F.data.startswith("evt_cancel:"))
async def evt_cancel_cb(call: CallbackQuery, session: AsyncSession):
    if not call.from_user or not call.data:
        await call.answer()
        return
    event_id = int(call.data.split(":")[1])
    # resolve donor
    donor = await get_donor_by_tg_id(session, call.from_user.id)
    if not donor or not donor.id:
        await call.answer("Не удалось найти данные пользователя.", show_alert=True)
        return
    ok = await cancel_registration(session, donor.id, event_id)
    cancel_msg = "Запись отменена." if ok else "Не удалось отменить запись."
    try:
        await call.message.edit_text(cancel_msg)  # type: ignore[attr-defined]
    except Exception:
        await call.answer(cancel_msg, show_alert=True)


@router.message(F.text == "ℹ️ Информация")
async def show_info(message: Message):
    await message.answer("Выберите интересующий раздел:", reply_markup=info_kb)


@router.callback_query(F.data.startswith("info:"))
async def send_info_page(callback_query: CallbackQuery, session: AsyncSession):
    if not callback_query.data or not callback_query.message:
        return
    key = callback_query.data.split(":")[1]
    info_page = await session.get(InfoPage, key)
    if info_page and info_page.content:
        await callback_query.message.answer(info_page.content, parse_mode="Markdown")  # type: ignore[union-attr]
        await callback_query.answer()
        return

    # Fallback: read markdown from data folder
    from pathlib import Path
    md_path = Path(__file__).resolve().parent.parent / "data" / f"{key}.md"
    if md_path.exists():
        await callback_query.message.answer(md_path.read_text(encoding="utf-8"), parse_mode="Markdown")  # type: ignore[attr-defined]
        await callback_query.answer()
    else:
        await callback_query.answer("Информация не найдена.", show_alert=True)


# ---------  Вопрос организатору  ---------


@router.message(F.text == "❓ Вопрос")
async def ask_question(message: Message, state: FSMContext):
    await message.answer("Напишите ваш вопрос, мы передадим его организаторам.", reply_markup=back_button)
    await state.set_state(AskQuestionState.waiting_for_question)


@router.message(AskQuestionState.waiting_for_question)
async def forward_question(message: Message, state: FSMContext, bot: Bot):
    if message.text == "⬅️ Назад":
        await message.answer("Отменено.", reply_markup=main_menu_kb)
        await state.clear()
        return

    question_text = (
        f"Вопрос от @{getattr(message.from_user, 'username', 'user')} ({message.from_user.id if message.from_user else ''}):\n{message.text}"
    )
    delivered = False
    # 1) пробуем групповой чат, если указан
    if settings.ADMIN_CHAT_ID:
        try:
            await bot.send_message(settings.ADMIN_CHAT_ID, question_text, reply_markup=answer_kb(message.from_user.id if message.from_user else 0))
            delivered = True
        except Exception:
            pass

    # 2) если не получилось – личные сообщения каждому ADMIN_IDS
    if not delivered:
        for admin_id in settings.ADMIN_IDS:
            try:
                await bot.send_message(admin_id, question_text)
                delivered = True
            except Exception:
                continue

    if delivered:
        await message.answer("Вопрос отправлен организаторам ✅", reply_markup=main_menu_kb)
    else:
        await message.answer("Не удалось доставить вопрос. Попробуйте позже.")
        import logging; logging.error("send_question_error: no recipients available")
    await state.clear()

# ---------  Настройки  ---------


class SettingsState(StatesGroup):
    waiting_for_option = State()
    waiting_for_full_name = State()


def _settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить ФИО", callback_data="settings:fullname")],
            [InlineKeyboardButton(text="🌐 Язык / Language", callback_data="settings:lang")],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="settings:close")],
        ]
    )


@router.message(F.text.in_({"⚙️ Настройки", "⚙️ Settings"}))
async def settings_start(message: Message, state: FSMContext):
    await message.answer("Настройки:", reply_markup=_settings_kb())
    await state.set_state(SettingsState.waiting_for_option)


@router.callback_query(SettingsState.waiting_for_option, F.data.startswith("settings:"))
async def settings_option(call: CallbackQuery, state: FSMContext):
    action = call.data.split(":", 1)[1]  # type: ignore
    if action == "fullname":
        await call.message.answer("Введите новое ФИО:", reply_markup=back_button)  # type: ignore[attr-defined]
        await state.set_state(SettingsState.waiting_for_full_name)
    elif action == "lang":
        lang_kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="Русский", callback_data="settings_lang:ru"),
                InlineKeyboardButton(text="English", callback_data="settings_lang:en"),
            ]]
        )
        await call.message.answer("Выберите язык / Choose language:", reply_markup=lang_kb)  # type: ignore[attr-defined]
    elif action == "close":
        await call.message.answer("Ок", reply_markup=main_menu_kb)  # type: ignore[attr-defined]
        await state.clear()
    await call.answer()


@router.callback_query(F.data.startswith("settings_lang:"))
async def settings_set_lang(call: CallbackQuery, session: AsyncSession):
    lang = call.data.split(":", 1)[1]  # type: ignore
    if not call.from_user:
        await call.answer()
        return
    donor = await get_donor_by_tg_id(session, call.from_user.id)
    if donor:
        donor.lang = lang
        session.add(donor)
        await session.commit()
        text = "Language updated ✅" if lang == "en" else "Язык обновлён ✅"
        await call.message.answer(text)  # type: ignore[attr-defined]
    await call.answer()


@router.message(SettingsState.waiting_for_full_name)
async def settings_change_name(message: Message, state: FSMContext, session: AsyncSession):
    if message.text == "⬅️ Назад":
        await message.answer("Отменено.", reply_markup=main_menu_kb)
        await state.clear()
        return
    if not message.text or not message.from_user:
        return

    import re
    cleaned = " ".join(message.text.strip().split())
    if not re.match(r"^[А-Яа-яA-Za-z\- ]{5,}$", cleaned) or len(cleaned.split()) < 2:
        await message.answer("Похоже, ФИО введено некорректно. Попробуйте ещё раз.")
        return

    donor = await get_donor_by_tg_id(session, message.from_user.id)
    if donor:
        donor.full_name = cleaned
        session.add(donor)
        await session.commit()

    await message.answer("ФИО обновлено ✅", reply_markup=main_menu_kb)
    await state.clear()


@router.callback_query(F.data.startswith("history_pg_"))
async def show_history(callback_query: CallbackQuery, session: AsyncSession):
    if not callback_query.from_user:
        return
    history = await get_donor_history(session, callback_query.from_user.id)
    if not history:
        await callback_query.answer("Записей не найдено", show_alert=True)
        return
    lines = []
    for reg, event in history:
        status_icon = {
            "donated": "✅",
            "no-show": "❌",
            "registered": "🕓",
            "cancelled": "🚫",
        }.get(reg.status, "•")
        lines.append(f"{status_icon} {event.date.strftime('%d.%m.%Y')} – {event.blood_center} ({reg.status})")
    await callback_query.message.answer("\n".join(lines))  # type: ignore[attr-defined]
    await callback_query.answer()

# ---------  Лидерборд  ---------

@router.message(F.text == "🏆 Рейтинг")
async def show_leaderboard(message: Message, session: AsyncSession):
    from datetime import datetime
    from donor_bot.services.donors import get_year_leaderboard

    year = datetime.now().year
    top = await get_year_leaderboard(session, year, limit=10)
    if not top:
        await message.answer("Пока нет данных по донациям в этом году.")
        return

    lines = [f"🏆 <b>Топ-доноры {year} года</b>:"]
    for idx, (donor, count) in enumerate(top, start=1):
        liters = count * 0.45
        lines.append(f"{idx}. {donor.full_name} — {count}× (≈ {liters:.2f} л)")

    await message.answer("\n".join(lines), parse_mode="HTML")
