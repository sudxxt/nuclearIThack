import dateparser  # type: ignore
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, asc
from datetime import date as _d

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from donor_bot.models.donor import Donor
from donor_bot.models.event import Event
from donor_bot.models.info_page import InfoPage
from donor_bot.utils.center import CANONICAL_CENTERS

from donor_bot.config import settings
from donor_bot.services.donors import update_donor_from_command, import_donors_from_xlsx
from donor_bot.services.events import create_event
from donor_bot.services.reports import make_report
from donor_bot.services.tickets import list_open_tickets
from donor_bot.services.donors import get_donor_by_tg_id
from donor_bot.services.broadcasts import send_broadcast
from donor_bot.services.reports import export_single_event
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram import Bot

from donor_bot.keyboards import calendar as calendar_widget
from aiogram_calendar import SimpleCalendarCallback

# Helper to append «Главное меню» exit row to any InlineKeyboardMarkup
def _with_exit_row(kb: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    # Avoid duplicating row
    if not any(btn.text.startswith("⬅️") for row in kb.inline_keyboard for btn in row):
        kb.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="admin_exit")])
    return kb


router = Router()
# All handlers in this router should be accessible only to admins
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))


class AdminStates(StatesGroup):
    waiting_for_event_date = State()
    waiting_for_event_center = State()
    waiting_for_event_start = State()
    waiting_for_event_end = State()
    waiting_for_broadcast_tag = State()
    waiting_for_broadcast_text = State()
    waiting_for_import_file = State()
    waiting_for_event_link = State()
    waiting_for_event_results_select = State()
    waiting_for_event_results_file = State()
    waiting_for_info_content = State()
    # donor edit GUI
    waiting_for_edit_phone = State()
    waiting_for_edit_field = State()
    waiting_for_edit_value = State()
    # admin mgmt
    waiting_for_new_admin_id = State()


# ---------- Админ: обзор и управление мероприятиями ----------


# Add new callback prefix constants
ACTIVE_TAB = "active"
DONE_TAB = "done"


async def build_admin_events_kb(session: AsyncSession, page: int, mode: str):
    """mode: active | done"""
    from donor_bot.utils.time import today_msk
    today = today_msk()
    stmt = select(Event)
    if mode == DONE_TAB:
        stmt = stmt.where(Event.date < today)  # type: ignore[arg-type]
    else:
        stmt = stmt.where(Event.date >= today)  # type: ignore[arg-type]

    events = (await session.execute(stmt.order_by(Event.date))).scalars().all()  # type: ignore[arg-type]
    start = page * PAGE_SIZE
    slice_events = events[start : start + PAGE_SIZE]

    rows = []
    for ev in slice_events:
        if mode == DONE_TAB:
            # кнопка открывает список регистраций
            label_btn = InlineKeyboardButton(text=_event_label(ev), callback_data=f"evt_finished:{ev.id}")
        else:
            label_btn = InlineKeyboardButton(text=_event_label(ev), callback_data="noop")
            del_btn = InlineKeyboardButton(text="🗑️", callback_data=f"evt_del:{ev.id}")
            rows.append([label_btn, del_btn])
            continue
        rows.append([label_btn])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"evt_pg:{mode}:{page-1}"))
    if start + PAGE_SIZE < len(events):
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"evt_pg:{mode}:{page+1}"))
    if nav_row:
        rows.append(nav_row)

    # Add button row
    if mode == ACTIVE_TAB:
        rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="evt_add")])

    # universal exit row
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="evt_exit")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- entry point ---


@router.message(F.text == "📅 Мероприятия")
async def events_overview_admin(message: Message, session: AsyncSession):
    from datetime import date as _d
    from donor_bot.utils.time import today_msk
    today = today_msk()
    top_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Актуальные", callback_data="evt_tab:active"),
        InlineKeyboardButton(text="Завершённые", callback_data="evt_tab:done"),
    ]])
    await message.answer("Выберите вкладку:", reply_markup=top_kb)


# Tab switch
@router.callback_query(F.data.startswith("evt_tab:"))
async def events_tab_switch(call: CallbackQuery, session: AsyncSession):
    data = call.data or ""
    if not data:
        await call.answer()
        return
    mode = data.split(":", 1)[1]
    page = 0
    kb = await build_admin_events_kb(session, page, mode)
    await call.message.edit_reply_markup(reply_markup=kb)  # type: ignore[attr-defined]
    await call.answer()


# pagination reuse (evt_pg)


@router.callback_query(F.data.startswith("evt_pg:"))
async def admin_events_pagination(call: CallbackQuery, session: AsyncSession):
    data = call.data or ""
    if not data:
        await call.answer()
        return
    parts = data.split(":")
    if len(parts) != 3:
        await call.answer()
        return
    _, mode, page_str = parts  # evt_pg:mode:page
    page = int(page_str)
    kb = await build_admin_events_kb(session, page, mode)
    await call.message.edit_reply_markup(reply_markup=kb)  # type: ignore[attr-defined]
    await call.answer()


# delete event


@router.callback_query(F.data.startswith("evt_del:"))
async def admin_event_delete(call: CallbackQuery, session: AsyncSession):
    if not call.data:
        await call.answer()
        return
    event_id = int(call.data.split(":")[1])
    ev = await session.get(Event, event_id)
    if ev:
        await session.delete(ev)
        await session.commit()
    # refresh current page (assume active tab page 0)
    kb = await build_admin_events_kb(session, 0, ACTIVE_TAB)
    await call.message.edit_reply_markup(reply_markup=kb)  # type: ignore[attr-defined]
    await call.answer("Удалено")


# add event button triggers existing flow


@router.callback_query(F.data == "evt_add")
async def admin_event_add_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    kb = await calendar_widget.start_calendar()
    await call.message.answer("Выберите дату мероприятия:", reply_markup=kb)  # type: ignore[attr-defined]
    await state.set_state(AdminStates.waiting_for_event_date)


# Handle calendar selection


@router.callback_query(SimpleCalendarCallback.filter())
async def process_simple_calendar_admin(call: CallbackQuery, callback_data: SimpleCalendarCallback, state: FSMContext):
    selected, date_obj = await calendar_widget.process_selection(call, callback_data)
    if not selected or not date_obj:
        return  # calendar will update automatically

    from donor_bot.utils.time import today_msk
    from datetime import datetime as _dt
    date_only = date_obj.date() if isinstance(date_obj, _dt) else date_obj  # type: ignore[arg-type]
    if date_only < today_msk():
        await call.answer("Нельзя выбрать прошедшую дату", show_alert=True)
        return
    await state.update_data(event_date=date_only)
    await call.message.answer("Введите время начала акции (HH:MM):")  # type: ignore[attr-defined]
    await state.set_state(AdminStates.waiting_for_event_start)


@router.message(AdminStates.waiting_for_event_start)
async def add_event_start_time(message: Message, state: FSMContext, session: AsyncSession):
    # Allow user to cancel with the "Назад" button while selecting center
    if message.text and (message.text.startswith("🔙") or message.text.startswith("⬅️")):
        await state.clear()
        await message.answer("Создание мероприятия отменено.")
        return
    if not message.text:
        return
    import re
    if not re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", message.text.strip()):
        await message.answer("Время должно быть в формате HH:MM. Попробуйте ещё раз.")
        return
    await state.update_data(event_start_time=message.text.strip())
    from donor_bot.keyboards.donor import back_button as _bb
    await message.answer("Введите время окончания акции (HH:MM):", reply_markup=_bb)  # type: ignore[attr-defined]
    await state.set_state(AdminStates.waiting_for_event_end)


@router.message(AdminStates.waiting_for_event_end)
async def add_event_end_time(message: Message, state: FSMContext, session: AsyncSession):
    if message.text and (message.text.startswith("🔙") or message.text.startswith("⬅️")):
        await state.clear()
        await message.answer("Создание мероприятия отменено.")
        return
    if not message.text:
        return
    import re, datetime as _dt
    end_time_str = message.text.strip()
    if not re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", end_time_str):
        await message.answer("Время должно быть в формате HH:MM. Попробуйте ещё раз.")
        return

    await state.update_data(event_end_time=end_time_str)

    # --- Offer predefined centers via inline buttons ---
    center_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=center, callback_data=f"center_pick:{center}")]
                         for center in sorted(CANONICAL_CENTERS)]
    )

    await message.answer("Выберите центр крови:", reply_markup=center_kb)
    await state.set_state(AdminStates.waiting_for_event_center)


# --- Center pick via inline button ---


@router.callback_query(F.data.startswith("center_pick:"))
async def admin_center_pick(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not call.data:
        await call.answer()
        return

    center = call.data.split(":", 1)[1]
    await state.update_data(event_center=center)

    # Remove keyboard and ask next question
    try:
        await call.message.edit_reply_markup(reply_markup=None)  # type: ignore[attr-defined]
    except Exception:
        pass

    from donor_bot.keyboards.donor import back_button as _bb

    prompt_text = (
        "Если для внешних доноров требуется ссылка регистрации, отправьте её. "
        "Иначе напишите 'нет'."
    )

    # `call.message` can be `None` for inline queries in some contexts, so we fall back
    # to a direct `send_message` call when it is unavailable to satisfy the type checker
    # and avoid runtime errors.
    from typing import cast
    from aiogram.types import Message as _Msg

    if call.message is not None:
        msg = cast(_Msg, call.message)
        await msg.answer(prompt_text, reply_markup=_bb)
    else:
        await bot.send_message(chat_id=call.from_user.id, text=prompt_text, reply_markup=_bb)
    await state.set_state(AdminStates.waiting_for_event_link)
    await call.answer(f"Вы выбрали: {center}")


# --- Manual text entry fallback ---


@router.message(AdminStates.waiting_for_event_center)
async def add_event_center_text(message: Message, state: FSMContext):
    """Fallback when admin types center name manually instead of clicking a button."""
    if message.text and (message.text.startswith("🔙") or message.text.startswith("⬅️")):
        await state.clear()
        await message.answer("Создание мероприятия отменено.")
        return

    if not message.text:
        return

    await state.update_data(event_center=message.text.strip())
    from donor_bot.keyboards.donor import back_button as _bb
    await message.answer(
        "Если для внешних доноров требуется ссылка регистрации, отправьте её. Иначе напишите 'нет'.",
        reply_markup=_bb,
    )
    await state.set_state(AdminStates.waiting_for_event_link)


# --- Receive external link and create event ---


@router.message(AdminStates.waiting_for_event_link)
async def add_event_link(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text:
        return

    link_raw = message.text.strip()
    link: str | None = None
    if link_raw.lower() not in {"нет", "no", "-"}:
        if not link_raw.startswith("http://") and not link_raw.startswith("https://"):
            await message.answer("Ссылка должна начинаться с http:// или https://. Попробуйте ещё раз или напишите 'нет'.")
            return
        link = link_raw

    data = await state.get_data()
    from datetime import time as _time
    try:
        start_parts = str(data.get("event_start_time", "10:00")).split(":")
        end_parts = str(data.get("event_end_time", "13:00")).split(":")
        start_time = _time(int(start_parts[0]), int(start_parts[1]))
        end_time = _time(int(end_parts[0]), int(end_parts[1]))
    except Exception:
        start_time = None
        end_time = None

    try:
        event = await create_event(
            session,
            data["event_date"],
            data["event_center"],
            external_link=link,
            start_time=start_time,
            end_time=end_time,
        )
    except ValueError as e:
        await message.answer(str(e))
        await state.clear()
        return

    txt = (
        f"Мероприятие создано: {event.date.strftime('%d.%m.%Y')} в {event.blood_center}\n"
        f"Время: {event.start_time.strftime('%H:%M') if event.start_time else '?'}–{event.end_time.strftime('%H:%M') if event.end_time else '?'}"
    )
    if event.external_link:
        txt += f"\nСсылка для внешних: {event.external_link}"

    await message.answer(txt)
    await state.clear()

# --------- Экспорт конкретного мероприятия ---------


PAGE_SIZE = 5


def _event_label(ev):
    return f"{ev.blood_center} {ev.date.strftime('%d.%m.%Y')}"


async def build_events_page_kb(session: AsyncSession, page: int, cb_prefix: str):
    events = (await session.execute(select(Event).order_by(asc(getattr(Event, "date"))))).scalars().all()
    start = page * PAGE_SIZE
    slice_events = events[start : start + PAGE_SIZE]
    buttons = [[InlineKeyboardButton(text=_event_label(ev), callback_data=f"{cb_prefix}:{ev.id}")] for ev in slice_events]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{cb_prefix}_pg:{page-1}"))
    if start + PAGE_SIZE < len(events):
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"{cb_prefix}_pg:{page+1}"))
    if nav_row:
        buttons.append(nav_row)
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return _with_exit_row(kb)


@router.message(F.text == "📊 Экспорт")
async def export_select_event(message: Message, session: AsyncSession):
    kb = await build_events_page_kb(session, 0, "export_evt")  # builder already includes exit row
    if not kb.inline_keyboard:
        await message.answer("Нет мероприятий для экспорта.")
        return
    await message.answer("Выберите мероприятие для экспорта:", reply_markup=kb)


@router.callback_query(F.data.startswith("export_evt_pg:"))
async def export_pagination(call: CallbackQuery, session: AsyncSession):
    if not call.data:
        await call.answer()
        return
    page = int(call.data.split(":")[1])  # type: ignore
    kb = await build_events_page_kb(session, page, "export_evt")
    await call.message.edit_reply_markup(reply_markup=kb)  # type: ignore[attr-defined]
    await call.answer()


@router.callback_query(F.data.startswith("export_evt:"))
async def export_event_excel(call: CallbackQuery, session: AsyncSession):
    if not call.data:
        await call.answer()
        return
    event_id = int(call.data.split(":")[1])  # type: ignore
    from tempfile import NamedTemporaryFile
    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    await export_single_event(session, event_id, tmp.name)
    input_file = FSInputFile(tmp.name, filename=f"event_{event_id}.xlsx")
    await call.message.answer_document(document=input_file, caption="Экспорт готов")  # type: ignore[attr-defined]
    await call.answer("Файл сформирован")


@router.message(Command("donor"))
async def donor_command(message: Message, session: AsyncSession):
    if not message.text:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1] not in ["add", "edit", "delete"]:
        await message.answer("Использование:\n"
                             "/donor add key=value ...\n"
                             "/donor edit <id> key=value ...\n"
                             "/donor delete <id>")
        return

    command, args = parts[1], parts[2]
    if command == "edit":
        response = await update_donor_from_command(session, args)
    elif command == "add":
        from donor_bot.services.donors import add_donor_from_command
        response = await add_donor_from_command(session, args)
    elif command == "delete":
        from donor_bot.services.donors import delete_donor_by_id
        response = await delete_donor_by_id(session, args)
    else:
        response = "Неизвестная команда."

    await message.answer(response)


@router.message(F.text == "💬 Рассылка")
async def broadcast_start(message: Message, state: FSMContext):
    # Кнопки с готовыми сегментами аудитории
    tag_buttons = [
        ("all", "Всем"),
        ("student", "Студенты"),
        ("staff", "Сотрудники"),
        ("external", "Гости"),
        ("dkm", "ДКМ"),
        ("upcoming_registered", "Записавшиеся (ближайшее)"),
        ("upcoming_not_registered", "Не записавшиеся (ближайшее)"),
        ("noshow_last", "No-show (прошлый ДД)"),
    ]

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"broadcast_tag:{tag}")]
            for tag, label in tag_buttons
        ]
    )
    _with_exit_row(keyboard)

    await message.answer("Выберите аудиторию для рассылки:", reply_markup=keyboard)


# --- Выбор тега через кнопку ---


@router.callback_query(F.data.startswith("broadcast_tag:"))
async def broadcast_tag_cb(call: CallbackQuery, state: FSMContext):
    tag = call.data.split(":", 1)[1]  # type: ignore
    await state.update_data(tag=tag)

    try:
        await call.message.edit_text(  # type: ignore[union-attr]
            f"Аудитория выбрана: <b>{tag}</b>\n\nВведите текст рассылки:",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        # fallback to new message if редактирование не удалось
        await call.message.answer("Введите текст рассылки:")  # type: ignore[attr-defined]

    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await call.answer()


@router.message(AdminStates.waiting_for_broadcast_text)
async def broadcast_text(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text or not message.bot:
        return
    data = await state.get_data()
    success, fail = await send_broadcast(message.bot, session, data["tag"], message.text)
    await message.answer(f"Рассылка завершена. Успешно: {success}, ошибок: {fail}")
    await state.clear()


@router.message(Command("import"))
async def import_start(message: Message, state: FSMContext):
    await message.answer("Пришлите файл .xlsx для импорта доноров.")
    await state.set_state(AdminStates.waiting_for_import_file)


@router.message(AdminStates.waiting_for_import_file, F.document)
async def import_file(message: Message, state: FSMContext, session: AsyncSession):
    if not message.document or not message.document.file_name or not message.bot:
        await message.answer("Произошла ошибка с файлом.")
        return
    if not message.document.file_name.endswith(".xlsx"):
        await message.answer("Пожалуйста, пришлите файл в формате .xlsx")
        return
        
    file_info = await message.bot.get_file(message.document.file_id)
    if not file_info.file_path:
        await message.answer("Не удалось получить информацию о файле.")
        return
    
    file_path = f"data/{message.document.file_id}.xlsx"
    await message.bot.download_file(file_info.file_path, destination=file_path)

    response = await import_donors_from_xlsx(session, file_path)
    await message.answer(response)
    await state.clear()


# --------- Управление информационными страницами ---------


@router.message(Command("info"))
async def info_admin(message: Message, state: FSMContext, session: AsyncSession):
    """Команда для администраторов: управление разделами информации.

    Доступные подкоманды:
        /info list – показать существующие страницы.
        /info edit <key> – отредактировать страницу (дальше прислать новый markdown-текст).
    """

    if not message.text:
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) == 1 or parts[1] == "help":
        await message.answer("Использование:\n/info list – список страниц\n/info edit <key> – редактировать страницу")
        return

    subcmd = parts[1].lower()

    if subcmd == "list":
        # Собираем ключи и отображаем кнопками
        from pathlib import Path
        rows: list[str] = []
        db_keys = [p.key for p in (await session.execute(select(InfoPage))).scalars().all()]
        rows.extend(db_keys)
        data_folder = Path(__file__).resolve().parent.parent / "data"
        rows.extend([p.stem for p in data_folder.glob("*.md")])
        unique_keys = sorted(set(rows))

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=k, callback_data=f"info_edit:{k}")] for k in unique_keys
            ]
        )

        await message.answer("Выберите страницу для редактирования:", reply_markup=keyboard)
        return

    if subcmd == "edit" and len(parts) >= 3:
        key = parts[2].strip()
        await state.update_data(info_key=key)
        await message.answer(
            f"Пришлите новый markdown-текст для страницы <b>{key}</b>. Отправьте 'Отмена' для отмены.",
            parse_mode="HTML",
        )
        await state.set_state(AdminStates.waiting_for_info_content)
        return

    await message.answer("Неверная команда. Используйте /info help для справки.")


# --- Callback для выбора страницы инфо ---


@router.callback_query(F.data.startswith("info_edit:"))
async def info_edit_cb(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":", 1)[1]  # type: ignore
    await state.update_data(info_key=key)
    await call.message.answer(  # type: ignore[union-attr]
        f"Пришлите новый markdown-текст для страницы <b>{key}</b>. Отправьте 'Отмена' для отмены.",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.waiting_for_info_content)
    await call.answer()


@router.message(AdminStates.waiting_for_info_content)
async def save_info_content(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text:
        return
    if message.text.lower() == "отмена":
        await message.answer("Редактирование отменено.")
        await state.clear()
        return

    data = await state.get_data()
    key = data.get("info_key")
    if not key:
        await message.answer("Ошибка: ключ страницы потерян. Попробуйте ещё раз.")
        await state.clear()
        return

    # upsert InfoPage
    page = await session.get(InfoPage, key)
    if page:
        page.content = message.text
    else:
        page = InfoPage(key=key, content=message.text)
    session.add(page)
    await session.commit()

    await message.answer(f"Страница <b>{key}</b> обновлена ✅", parse_mode="HTML")
    await state.clear()


@router.message(F.text == "📈 Отчёт")
async def report_start(message: Message, session: AsyncSession):
    file_path, summary = await make_report(session)
    if not file_path:
        await message.answer(summary)
        return
    
    input_file = FSInputFile(file_path, filename="report.xlsx")
    await message.answer_document(document=input_file, caption=summary)


# --------- Тикеты ---------


async def build_tickets_list_kb(session: AsyncSession, tickets):
    buttons = []
    for t in tickets:
        donor = await get_donor_by_tg_id(session, t.donor_id)
        label = getattr(donor, "full_name", None) or f"{t.donor_id}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"ticket:show:{t.id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return _with_exit_row(kb)


@router.message(F.text == "🎫 Тикеты")
async def tickets_overview(message: Message, session: AsyncSession):
    tickets = await list_open_tickets(session)
    if not tickets:
        await message.answer("Открытых тикетов нет")
        return
    kb = await build_tickets_list_kb(session, tickets[:5])
    await message.answer("Открытые тикеты:", reply_markup=kb)


@router.callback_query(F.data.startswith("ticket:show:"))
async def show_ticket_cb(call: CallbackQuery, session: AsyncSession, bot: Bot):
    ticket_id = int(call.data.split(":")[2])  # type: ignore
    from donor_bot.handlers.tickets_admin import send_ticket_embed  # lazy import
    dest_chat = call.message.chat.id if call.message else None  # type: ignore[attr-defined] 
    await send_ticket_embed(bot, session, ticket_id, dest_chat_id=dest_chat)
    await call.answer()


# ------- donors info -------


@router.message(F.text == "🩸 Доноры")
async def donors_info(message: Message, session: AsyncSession):
    """Показать краткую статистику по донорам."""
    from collections import Counter

    result = await session.execute(select(Donor))
    donors = result.scalars().all()
    if not donors:
        await message.answer("Список доноров пуст.")
        return

    categories = Counter(d.category or "unknown" for d in donors)
    lines = [f"Всего доноров: {len(donors)}"]
    for cat, count in categories.items():
        lines.append(f"{cat}: {count}")

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📤 Экспорт", callback_data="donors_export")]])
    await message.answer("\n".join(lines), reply_markup=kb)


# ---------- Экспорт доноров ----------


@router.message(Command("donors_export"))
async def donors_export_cmd(message: Message, session: AsyncSession):
    from tempfile import NamedTemporaryFile
    from donor_bot.services.reports import export_donors

    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    await export_donors(session, tmp.name)
    input_file = FSInputFile(tmp.name, filename="donors.xlsx")
    await message.answer_document(document=input_file, caption="Экспорт доноров готов")


@router.message(F.text == "📥 Импорт ДД")
async def import_results_select_event(message: Message, session: AsyncSession, state: FSMContext):
    kb = await build_events_page_kb(session, 0, "imp_evt")
    if not kb.inline_keyboard:
        await message.answer("Нет мероприятий для импорта.")
        return
    await message.answer("Выберите мероприятие для импорта результатов:", reply_markup=kb)
    await state.set_state(AdminStates.waiting_for_event_results_select)


@router.callback_query(F.data.startswith("imp_evt_pg:"))
async def import_evt_pagination(call: CallbackQuery, session: AsyncSession):
    if not call.data:
        await call.answer()
        return
    page = int(call.data.split(":")[1])
    kb = await build_events_page_kb(session, page, "imp_evt")
    await call.message.edit_reply_markup(reply_markup=kb)  # type: ignore[attr-defined]
    await call.answer()


@router.callback_query(F.data.startswith("imp_evt:"))
async def import_event_choose(call: CallbackQuery, state: FSMContext):
    if not call.data:
        await call.answer()
        return
    event_id = int(call.data.split(":")[1])
    await state.update_data(import_event_id=event_id)
    await call.message.edit_text("Пришлите .xlsx файл с результатами (ФИО, Телефон, Статус, ДКМ).", reply_markup=None)  # type: ignore[attr-defined]
    await state.set_state(AdminStates.waiting_for_event_results_file)
    await call.answer()


@router.message(AdminStates.waiting_for_event_results_file, F.document)
async def import_event_file(message: Message, state: FSMContext, session: AsyncSession):
    if not message.document or not message.document.file_name or not message.bot:
        await message.answer("Произошла ошибка с файлом.")
        return
    if not message.document.file_name.endswith(".xlsx"):
        await message.answer("Пожалуйста, пришлите файл .xlsx")
        return

    data = await state.get_data()
    event_id: int | None = data.get("import_event_id")  # type: ignore
    if event_id is None:
        await message.answer("Сессия импорта потеряна, начните сначала.")
        await state.clear()
        return

    file_info = await message.bot.get_file(message.document.file_id)
    if not file_info.file_path:
        await message.answer("Не удалось получить файл.")
        return

    file_path = f"data/event_{event_id}_{message.document.file_id}.xlsx"
    await message.bot.download_file(file_info.file_path, destination=file_path)

    from donor_bot.services.events import import_event_results
    summary = await import_event_results(session, event_id, file_path)

    await message.answer("Импорт завершён:\n" + summary)
    await state.clear()


@router.callback_query(F.data == "donors_export")
async def donors_export_cb(call: CallbackQuery, session: AsyncSession):
    from tempfile import NamedTemporaryFile
    from donor_bot.services.reports import export_donors

    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    await export_donors(session, tmp.name)
    input_file = FSInputFile(tmp.name, filename="donors.xlsx")
    await call.message.answer_document(document=input_file, caption="Экспорт доноров готов")  # type: ignore[attr-defined]
    await call.answer()


# ---------- Редактирование доноров (GUI) ----------


@router.callback_query(F.data == "donors_edit_start")
async def donors_edit_start_cb(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.answer("Введите телефон (+7…) или TG ID донора, которого хотите изменить:")  # type: ignore[attr-defined]
    await state.set_state(AdminStates.waiting_for_edit_phone)


@router.message(AdminStates.waiting_for_edit_phone)
async def donors_edit_phone(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text:
        return
    identifier = message.text.strip()
    from donor_bot.services.donors import get_donor_by_credentials
    donor = None
    if identifier.isdigit():
        donor = await get_donor_by_credentials(session, tg_id=int(identifier))
    else:
        donor = await get_donor_by_credentials(session, phone=identifier)

    if not donor or donor.id is None:
        await message.answer("Донор не найден. Попробуйте ещё раз или отправьте /cancel.")
        return

    await state.update_data(edit_donor_id=donor.id)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ФИО", callback_data="edit_field:full_name")],
            [InlineKeyboardButton(text="Группа", callback_data="edit_field:group")],
            [InlineKeyboardButton(text="Категория", callback_data="edit_field:category")],
            [InlineKeyboardButton(text="Телефон", callback_data="edit_field:phone")],
            [InlineKeyboardButton(text="Соцсеть", callback_data="edit_field:social")],
            [InlineKeyboardButton(text="DKM toggle", callback_data="edit_field:dkm_member")],
        ]
    )
    await message.answer("Выберите поле для изменения:", reply_markup=kb)
    await state.set_state(AdminStates.waiting_for_edit_field)


@router.callback_query(F.data.startswith("edit_field:"))
async def donor_edit_choose_field(call: CallbackQuery, state: FSMContext):
    if not call.data:
        await call.answer()
        return
    field = call.data.split(":")[1]
    await state.update_data(edit_field=field)
    await call.answer()

    if field == "dkm_member":
        from donor_bot.models import Donor
        data = await state.get_data()
        donor_id = data.get("edit_donor_id")
        if donor_id:
            session: AsyncSession = state.middleware_data["session"]  # type: ignore
            donor = await session.get(Donor, donor_id)
            if donor:
                donor.dkm_member = not donor.dkm_member
                session.add(donor)
                await session.commit()
                await call.message.answer(f"Флаг ДКМ теперь: {'Да' if donor.dkm_member else 'Нет'}")  # type: ignore[attr-defined]
        await state.clear()
        return

    await call.message.answer("Введите новое значение:")  # type: ignore[attr-defined]
    await state.set_state(AdminStates.waiting_for_edit_value)


@router.message(AdminStates.waiting_for_edit_value)
async def donor_edit_set_value(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    donor_id: int | None = data.get("edit_donor_id")  # type: ignore
    field: str | None = data.get("edit_field")  # type: ignore
    if donor_id is None or field is None:
        await message.answer("Сессия редактирования потеряна.")
        await state.clear()
        return

    from donor_bot.models import Donor
    donor = await session.get(Donor, donor_id)
    if not donor:
        await message.answer("Донор не найден.")
        await state.clear()
        return

    if not message.text:
        await message.answer("Пустое значение не принято.")
        return
    value = message.text.strip()

    # телефон валидация
    if field == "phone":
        import re
        if not re.match(r"^(\+?7\d{10}|8\d{10})$", value):
            await message.answer("Телефон должен быть в формате +7XXXXXXXXXX")
            return
        digits = value.lstrip('+')
        if digits.startswith('8'):
            digits = '7' + digits[1:]
        value = '+' + digits

    if field == "category" and value not in {"student", "staff", "external"}:
        await message.answer("Категория должна быть: student, staff или external")
        return

    setattr(donor, field, value)
    session.add(donor)
    await session.commit()
    await message.answer("Данные обновлены ✅")
    await state.clear()

# --------- Finished event: registrations list ---------


REG_PAGE_SIZE = 10


def _reg_label(reg, donor):
    icon = "✅" if reg.status == "donated" else "❌" if reg.status == "no-show" else "🕓"
    return f"{icon} {donor.full_name}"


async def build_regs_kb(session: AsyncSession, event_id: int, page: int):
    from donor_bot.models import Registration, Donor
    regs = (
        await session.execute(
            select(Registration, Donor)
            .join(Donor, Registration.donor_id == Donor.id)  # type: ignore[arg-type]
            .where(Registration.event_id == event_id)  # type: ignore[arg-type]
            .order_by(Donor.full_name)
        )
    ).all()

    start = page * REG_PAGE_SIZE
    slice_regs = regs[start : start + REG_PAGE_SIZE]

    rows = [
        [InlineKeyboardButton(text=_reg_label(r, d), callback_data=f"reg_toggle:{r.id}")]
        for r, d in slice_regs
    ]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"reg_pg:{event_id}:{page-1}"))
    if start + REG_PAGE_SIZE < len(regs):
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"reg_pg:{event_id}:{page+1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="evt_tab:done")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("evt_finished:"))
async def finished_event_regs(call: CallbackQuery, session: AsyncSession):
    data = call.data or ""
    if not data:
        await call.answer()
        return
    event_id = int(data.split(":")[1])
    kb = await build_regs_kb(session, event_id, 0)
    await call.message.edit_text("Участники:", reply_markup=kb)  # type: ignore[attr-defined]
    await call.answer()


@router.callback_query(F.data.startswith("reg_pg:"))
async def regs_pagination(call: CallbackQuery, session: AsyncSession):
    if not call.data:
        await call.answer()
        return
    _, event_id_str, page_str = call.data.split(":")
    event_id = int(event_id_str)
    page = int(page_str)
    kb = await build_regs_kb(session, event_id, page)
    await call.message.edit_reply_markup(reply_markup=kb)  # type: ignore[attr-defined]
    await call.answer()


@router.callback_query(F.data.startswith("reg_toggle:"))
async def reg_toggle_status(call: CallbackQuery, session: AsyncSession):
    if not call.data:
        await call.answer()
        return
    reg_id = int(call.data.split(":")[1])
    from donor_bot.models import Registration
    reg = await session.get(Registration, reg_id)
    if not reg:
        await call.answer("Регистрация не найдена", show_alert=True)
        return

    # toggle logic
    if reg.status == "donated":
        reg.status = "no-show"
    else:
        reg.status = "donated"

    session.add(reg)
    await session.commit()

    # rebuild current regs kb (assume stays on same page 0)
    event_id = reg.event_id
    kb = await build_regs_kb(session, event_id, 0)
    await call.message.edit_reply_markup(reply_markup=kb)  # type: ignore[attr-defined]
    await call.answer("Статус обновлён")


@router.callback_query(F.data == "evt_exit")
async def admin_events_exit(call: CallbackQuery):
    from donor_bot.keyboards import admin_menu_kb
    await call.message.answer("Главное меню:", reply_markup=admin_menu_kb)  # type: ignore[attr-defined]
    await call.answer()


# ---- universal exit handler ----


@router.callback_query(F.data == "admin_exit")
async def admin_menu_exit(call: CallbackQuery):
    from donor_bot.keyboards import admin_menu_kb  # local import to avoid cycle
    await call.message.answer("Главное меню:", reply_markup=admin_menu_kb)  # type: ignore[attr-defined]
    await call.answer()


# ---------- Управление администраторами ----------


@router.message(F.text == "👑 Админы")
async def admins_overview(message: Message, session: AsyncSession, state: FSMContext):
    from donor_bot.models.admin import Admin as _Admin
    admins = (await session.execute(select(_Admin))).scalars().all()
    ids = [str(a.tg_id) for a in admins] if admins else []
    current = ", ".join(ids) if ids else "(нет)"
    await message.answer(f"Текущие админы: {current}\n\nОтправьте ID пользователя, которого нужно сделать админом:")
    await state.set_state(AdminStates.waiting_for_new_admin_id)


@router.message(AdminStates.waiting_for_new_admin_id)
async def add_new_admin(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text or not message.text.isdigit():
        await message.answer("Пожалуйста, отправьте числовой Telegram ID.")
        return
    new_id = int(message.text)

    from donor_bot.models.admin import Admin as _Admin
    from sqlalchemy import select as _sel
    exists = (await session.execute(_sel(_Admin).where(getattr(_Admin, "tg_id") == new_id))).scalars().first()
    if exists:
        await message.answer("Этот ID уже есть в списке админов.")
        await state.clear()
        return

    session.add(_Admin(tg_id=new_id))
    await session.commit()

    # Обновляем runtime-настройку
    settings.ADMIN_IDS.add(new_id)  # type: ignore[attr-defined]

    await message.answer(f"Пользователь {new_id} добавлен в админы ✅")
    await state.clear()
