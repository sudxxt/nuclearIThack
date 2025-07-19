import re
import subprocess
from pathlib import Path
from typing import Callable, Any, Optional, Dict, Coroutine, List

import dateparser  # type: ignore
import whisper
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

import logging
import json, os
import httpx

from donor_bot.services.events import create_event, get_event_by_date, register_donor_for_event, cancel_registration
from donor_bot.services.broadcasts import send_broadcast
from donor_bot.config import settings
from donor_bot.models import Event
from donor_bot.services.donors import (
    get_donor_active_registration, 
    donor_stats, 
    get_donor_registrations, 
    get_donor_by_credentials
)

# ---------- LLM (OpenRouter) ----------
OPENROUTER_KEY = settings.OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY")

SYSTEM_PROMPT = """
Ты — продвинутый парсер голосовых команд для Telegram-бота доноров крови. Твоя задача — преобразовать неструктурированный русский текст в строгий JSON-формат. Отвечай ТОЛЬКО JSON.
Распознавай даты в любом формате (включая "послезавтра", "через неделю", "15-е августа"), имена, номера телефонов, ID.

--- ДОСТУПНЫЕ КОМАНДЫ (action и params) ---

## Для всех пользователей:
1.  register {date: "YYYY-MM-DD"} - Записаться на донацию.
    "запиши меня на 15-е августа", "хочу сдать кровь 20.09.2025"
2.  cancel {} - Отменить свою запись на ближайшее мероприятие.
    "отмени запись", "я не смогу прийти"
3.  get_my_card {} - Показать карточку донора (статистика).
    "моя карточка", "сколько у меня донаций", "статистика"
4.  get_my_registrations {} - Показать мои активные и прошлые записи.
    "мои записи", "куда я записан"
5.  list_events {} - Показать ближайшие мероприятия.
    "какие ближайшие акции", "покажи мероприятия"
6.  help {} - Показать справку по командам.
    "помощь", "что ты умеешь"

## Для администраторов:
7.  create_event {date: "YYYY-MM-DD", center: "str", slots?: int} - Создать день донора.
    "создай дд на 20 мая в ФМБА со 120 слотами"
8.  edit_event {date: "YYYY-MM-DD", updates: {slots?: int, center?: "str", new_date?: "YYYY-MM-DD"}} - Изменить мероприятие.
    "добавь 20 слотов на 20 мая", "перенеси дд 20 мая на 21.05", "измени центр крови 20.05 на СПК"
9.  get_event_details {date: "YYYY-MM-DD"} - Получить информацию о мероприятии.
    "сколько записалось на 20 мая", "покажи инфо по дд 20.05"
10. get_donor_details {phone?: "str", tg_id?: int} - Найти донора по телефону или TG ID.
    "найди донора +79991234567"
11. broadcast {tag: "str", text: "str"} - Сделать рассылку (tag: all, student, staff, dkm).
    "рассылка студенты завтра ждем вас в 9 утра"
12. edit_donor {tg_id: int, updates: {full_name?: "str", group?: "str", category?: "str"}} - Изменить данные донора.
    "измени донору 6433063981 группу на ИКБО-01-22"

Если смысл неясен, верни {"action": "unknown", "params": {}}.
"""


async def llm_intent(text: str) -> Optional[Dict[str, Any]]:
    if not OPENROUTER_KEY:
        logging.warning("OPENROUTER_API_KEY not set – LLM intent disabled")
        return None

    logging.info(f"[LLM] → {text!r}")

    payload = {
        "model": settings.OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "HTTP-Referer": "https://t.me/donorcentr_bot",
        "X-Title": "DonorBotIntent",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, http2=True) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            if "choices" not in data:
                # OpenRouter occasionally returns error objects or streaming chunks aggregated
                logging.error(f"[LLM] Unexpected response keys: {list(data.keys())}")
                return None
            content = data["choices"][0]["message"]["content"]
            logging.info(f"[LLM] ← {content}")
            # Очистка от markdown-блока, если он есть
            if content.strip().startswith("```json"):
                content = content.strip("```json \n")
            parsed_content = json.loads(content)
            logging.info(f"[LLM] Parsed: {parsed_content}")
            return parsed_content
    except Exception as e:
        logging.error(f"[LLM] ERROR: {e}")
        return None


# --- Intent parsing ---

async def handle_donor_register(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    from datetime import date as _d
    date_str = args.get("date")
    if not date_str:
        return "Не удалось распознать дату в команде."
        
    try:
        event_date = _d.fromisoformat(date_str)
    except (ValueError, TypeError):
        parsed_dt = dateparser.parse(date_str, languages=["ru"])
        if not parsed_dt:
            return "Неверный формат даты."
        event_date = parsed_dt.date()

    event = await get_event_by_date(session, event_date)
    if not event or not event.id:
        return f"Мероприятие на {event_date.strftime('%d.%m.%Y')} не найдено."
    success, message_text = await register_donor_for_event(session, user_id, event.id)

    # Даже если регистрация прошла успешно, нам может понадобиться отправить
    # кнопку добавления в Google Calendar (как это сделано в inline-кнопках
    # user-flow `evt_reg`).  Здесь нет доступа к объекту `Message`, поэтому
    # создадим новое сообщение через `bot.send_message`.

    if success and event.start_time and event.end_time:
        from datetime import datetime as _dt
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        start_dt = _dt.combine(event.date, event.start_time)
        end_dt = _dt.combine(event.date, event.end_time)
        start_str = start_dt.strftime("%Y%m%dT%H%M00")
        end_str = end_dt.strftime("%Y%m%dT%H%M00")

        cal_url = (
            "https://www.google.com/calendar/render?action=TEMPLATE"
            f"&text=День+Донора+{event.blood_center.replace(' ','+')}"
            f"&dates={start_str}/{end_str}"
            "&details=Регистрация+из+бота"
            "&location=МИФИ"
            "&sf=true&output=xml"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Добавить в Google Calendar", url=cal_url)]]
        )

        try:
            await bot.send_message(user_id, "Добавьте напоминание в календарь:", reply_markup=kb)
        except Exception:
            # не прерываем основную логику, если не удалось отправить кнопку
            pass

    return message_text

async def handle_donor_cancel(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    
    active_reg = await get_donor_active_registration(session, user_id)
    if not active_reg:
        return "У вас нет активных записей для отмены."
        
    success = await cancel_registration(session, active_reg.donor_id, active_reg.event_id)
    if success:
        event = await session.get(Event, active_reg.event_id)
        if event:
            return f"Ваша запись на {event.date.strftime('%d.%m.%Y')} успешно отменена."
        else:
            return "Ваша запись успешно отменена." # Fallback if event somehow not found
    else:
        return "Не удалось отменить запись. Пожалуйста, попробуйте позже."

async def handle_get_my_card(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    # Используем donor_stats, которую мы уже создали
    return await donor_stats(session, user_id)

async def handle_get_my_registrations(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    registrations = await get_donor_registrations(session, user_id)
    if not registrations:
        return "У вас нет записей."

    response_lines = ["Ваши записи:"]
    for reg in registrations:
        event_date = reg.event.date.strftime('%d.%m.%Y')
        status = "✅ Активна" if reg.status == 'registered' else '❌ Отменена'
        response_lines.append(f"  - {event_date} в {reg.event.blood_center} ({status})")
    
    return "\n".join(response_lines)

# ---------- NEW: list upcoming events ----------

async def handle_list_events(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    from donor_bot.services.events import get_upcoming_events
    events = await get_upcoming_events(session)
    if not events:
        return "Ближайших мероприятий пока нет."
    lines = ["Ближайшие мероприятия:"]
    for ev in events[:5]:
        free = ev.slots_total - ev.slots_taken
        lines.append(f"• {ev.date.strftime('%d.%m.%Y')} – {ev.blood_center} (свободно {free})")
    return "\n".join(lines)


async def handle_edit_event(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    from donor_bot.services.events import get_event_by_date
    from datetime import date as _d

    date_str = args.get("date")
    updates = args.get("updates", {})
    if not date_str or not updates:
        return "Не указана дата мероприятия или параметры для изменения."

    try:
        event_date = _d.fromisoformat(date_str)
    except (ValueError, TypeError):
        return "Неверный формат даты."

    event = await get_event_by_date(session, event_date)
    if not event:
        return f"Мероприятие на {event_date.strftime('%d.%m.%Y')} не найдено."

    updated_fields = []
    if "slots" in updates:
        event.slots_total = int(updates["slots"])
        updated_fields.append("количество слотов")
    if "center" in updates:
        event.blood_center = updates["center"]
        updated_fields.append("центр крови")
    if "new_date" in updates:
        try:
            event.date = _d.fromisoformat(updates["new_date"])
            updated_fields.append("дата")
        except (ValueError, TypeError):
            return "Неверный формат новой даты."
    
    if not updated_fields:
        return "Не указаны параметры для изменения (slots, center, new_date)."

    session.add(event)
    await session.commit()
    
    return f"В мероприятии {date_str} обновлено: {', '.join(updated_fields)}."


async def handle_get_event_details(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    from datetime import date as _d
    date_str = args.get("date")
    if not date_str:
        return "Не удалось распознать дату в команде."
        
    try:
        event_date = _d.fromisoformat(date_str)
    except (ValueError, TypeError):
        return "Неверный формат даты от LLM."

    event = await get_event_by_date(session, event_date)
    if not event:
        return f"Мероприятие на {event_date.strftime('%d.%m.%Y')} не найдено."
        
    return (
        f"Статистика по мероприятию {event.date.strftime('%d.%m.%Y')} в '{event.blood_center}':\n"
        f"  - Всего слотов: {event.slots_total}\n"
        f"  - Занято слотов: {event.slots_taken}\n"
        f"  - Свободно слотов: {event.slots_total - event.slots_taken}"
    )

async def handle_get_donor_details(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    tg_id = args.get("tg_id")
    phone = args.get("phone")

    if not tg_id and not phone:
        return "Необходимо указать Telegram ID или номер телефона донора."

    donor = await get_donor_by_credentials(session, tg_id=tg_id, phone=phone)
    if not donor:
        return "Донор не найден."

    return await donor_stats(session, donor.tg_id)


async def handle_edit_donor(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    tg_id = args.get("tg_id")
    updates = args.get("updates", {})

    if not tg_id or not updates:
        return "Необходимо указать Telegram ID донора и параметры для изменения."

    donor = await get_donor_by_credentials(session, tg_id=int(tg_id))
    if not donor:
        return f"Донор с ID {tg_id} не найден."

    updated_fields = []
    if "full_name" in updates:
        donor.full_name = updates["full_name"]
        updated_fields.append("ФИО")
    if "group" in updates:
        donor.group = updates["group"]
        updated_fields.append("группа/курс")
    if "category" in updates:
        donor.category = updates["category"]
        updated_fields.append("категория")

    if not updated_fields:
        return "Не указаны параметры для изменения (full_name, group, category)."

    session.add(donor)
    await session.commit()

    return f"Данные донора {tg_id} обновлены: {', '.join(updated_fields)}."


async def handle_show_leaderboard(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    """Return top donors of current year."""
    from datetime import datetime
    from donor_bot.services.donors import get_year_leaderboard
    year = datetime.now().year
    top = await get_year_leaderboard(session, year, limit=10)
    if not top:
        return "Пока нет данных рейтинга за этот год."
    lines = [f"🏆 Топ‐доноры {year}:"]
    for idx, (d, cnt) in enumerate(top, start=1):
        lines.append(f"{idx}. {d.full_name} — {cnt}×")
    return "\n".join(lines)

async def handle_my_history(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    from donor_bot.services.donors import get_donor_history
    hist = await get_donor_history(session, user_id)
    if not hist:
        return "История пуста."
    lines = ["История донаций:"]
    for reg, ev in hist:
        icon = "✅" if reg.status == "donated" else "🚫" if reg.status == "cancelled" else "🕓"
        lines.append(f"{icon} {ev.date.strftime('%d.%m.%Y')} – {ev.blood_center} ({reg.status})")
    return "\n".join(lines)


patterns_donor: List[tuple[str, Callable[[AsyncSession, Bot, int, Dict[str, Any]], Coroutine[Any, Any, str]]]] = [
    # записаться на дату
    (r"(?:запис|запиш)(?:[ьисяь]*)\s*(?:меня|нас|ся)?\s*(?:на|к)\s+(?P<date>[\d\.]+)", handle_donor_register),
    # хочу сдать кровь 20.05.2025
    (r"хочу\s+(?:сдать\s+кровь|сдать|сдавать).*?(?P<date>[\d\.]+)", handle_donor_register),
    # отменить запись
    (r"отмен[ит][ь]?\s+запис", handle_donor_cancel),
]

# --- admin regex helpers ---

async def handle_edit_event_regex(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    """Wrapper that converts regex groups to the expected 'updates' dict."""
    base: Dict[str, Any] = {"date": args.get("date"), "updates": {}}
    if "slots" in args and args["slots"]:
        base["updates"]["slots"] = int(args["slots"])
    if "new_date" in args and args["new_date"]:
        base["updates"]["new_date"] = args["new_date"]
    if not base["updates"]:
        return "Не удалось распознать параметры изменения мероприятия."
    return await handle_edit_event(session, bot, user_id, base)

async def handle_admin_create_event(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    from datetime import date as _d
    date_str = args.get("date")
    center = args.get("center")
    slots = int(args.get("slots", 100))
    start_time = None
    end_time = None
    if "start" in args:
        try:
            from datetime import time as _time
            hh, mm = map(int, args["start"].split(":"))
            start_time = _time(hh, mm)
        except Exception:
            pass
    if "end" in args:
        try:
            from datetime import time as _time
            hh, mm = map(int, args["end"].split(":"))
            end_time = _time(hh, mm)
        except Exception:
            pass

    if not date_str or not center:
        return "Не удалось распознать дату или центр крови из команды."

    try:
        # LLM should provide date in ISO format
        event_date = _d.fromisoformat(date_str)
    except (ValueError, TypeError):
        # Fallback for regex or other formats
        parsed_dt = dateparser.parse(date_str, languages=["ru"])
        if not parsed_dt:
            return "Неверный формат даты."
        event_date = parsed_dt.date()

    try:
        event = await create_event(session, event_date, center, slots, start_time=start_time, end_time=end_time)
    except ValueError as e:
        return str(e)
    return f"Мероприятие создано: {event.date.strftime('%d.%m.%Y')} в {event.blood_center}"

async def handle_admin_broadcast(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    tag = args.get("tag")
    text = args.get("text")
    if not tag or not text:
        return "Не указан тег или текст для рассылки."
    success, fail = await send_broadcast(bot, session, tag, text)
    return f"Рассылка завершена. Успешно: {success}, ошибок: {fail}"

async def handle_stats(session: AsyncSession, bot: Bot, user_id: int, args: Dict[str, Any]) -> str:
    return await donor_stats(session, user_id)

async def handle_help(_: AsyncSession, __: Bot, ___: int, ____: Dict[str, Any]) -> str:
    return (
        "Голосовые команды:\n"
        "• запишись / записаться на 15 мая\n"
        "• создай день донора 10.06 в центре ФМБА (или голосом словами)\n"
        "• рассылка all Завтра акция!\n"
        "• сколько у меня донаций / статистика\n"
        "• отменить запись\n"
        "• какие ближайшие акции\n"
    )

patterns_admin: List[tuple[str, Callable[[AsyncSession, Bot, int, Dict[str, Any]], Coroutine[Any, Any, str]]]] = [
    # примеры:  "создай дд 20.05 в ФМБА", 
    #           "создай день донора на 10 мая в центре FMBA",
    #           "создать день донора 05.06 в СПК МИФИ"
    (
        r"созд(ай|ать|айте)?[^\d]*(?:дд|день\s+донора)?[^\d]*(?P<date>[\d]{1,2}[\.\s][\d]{1,2}(?:[\.\s][\d]{2,4})?)[^a-zA-ZА-Яа-я0-9]+(?:в\s*(?:центре)?\s*)?(?P<center>.+)",
        handle_admin_create_event,
    ),
    # варианты: "рассылку all текст ...", "сделай рассылку студентам завтра ...", "рассылку для всех привет"
    (
        r"рассыл[каку]+\s*(?:для|по)?\s*(?P<tag>all|всем|студент(?:ам|ы)?|staff|сотрудник(?:ам|и)?|dkm|дкм|external|гост[ьяи]?)(?:\s+|:)(?P<text>.+)",
        handle_admin_broadcast,
    ),
    (r"рассылк[а]? (?P<tag>\w+) (?P<text>.+)", handle_admin_broadcast),
    # сколько записалось на 20.05
    (r"сколько\s+записал[ао]сь\s+на\s+(?P<date>[\d\.]+)", handle_get_event_details),
    # перенести дату мероприятия: перенеси 15.08 на 19.08
    (r"перенес[и]?[^\d]*(?P<date>[\d\.]+)[^\d]+на\s+(?P<new_date>[\d\.]+)", handle_edit_event_regex),
    # добавить слоты: добавь 20 слотов на 15.08
    (r"добав[ь]?[\s\S]*?(?P<slots>\d+)\s+слот[а-я]*[\s\S]*?на\s+(?P<date>[\d\.]+)", handle_edit_event_regex),
]

patterns_common: List[tuple[str, Callable[[AsyncSession, Bot, int, Dict[str, Any]], Coroutine[Any, Any, str]]]] = [
    (r"статист", handle_stats),
    (r"рейтинг|топ", handle_show_leaderboard),
    (r"истори|мо[яе]\s+донаци", handle_my_history),
    (r"мероприяти|акци[яи]|ближайш", handle_list_events),
    (r"помощ", handle_help),
    # моя карточка
    (r"моя\s+карточк|сколько\s+у\s+меня\s+дон", handle_get_my_card),
]


async def process_voice_command(
    text: str, session: AsyncSession, bot: Bot, user_id: int, is_admin: bool
) -> Optional[str]:
    # 1) LLM intent (if key present)
    intent = await llm_intent(text)
    if intent and "action" in intent:
        action = intent["action"]
        params = intent.get("params", {})
        
        handler_map = {
            "create_event": handle_admin_create_event,
            "register": handle_donor_register,
            "cancel": handle_donor_cancel,
            "broadcast": handle_admin_broadcast,
            "stats": handle_stats,
            "help": handle_help,
            "list_events": handle_list_events,
            "get_my_card": handle_get_my_card,
            "get_my_registrations": handle_get_my_registrations,
            "edit_event": handle_edit_event,
            "get_event_details": handle_get_event_details,
            "get_donor_details": handle_get_donor_details,
            "edit_donor": handle_edit_donor,
        }

        if action in handler_map:
            return await handler_map[action](session, bot, user_id, params)
        elif action == "unknown":
            pass  # Fall through to regex
        else:
            logging.warning(f"Unknown LLM action: {action}")

    # 2) fallback regex (общие + специфичные)
    patterns = patterns_common + (patterns_admin if is_admin else patterns_donor)
    for pattern, handler in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            args = match.groupdict()
            return await handler(session, bot, user_id, args)

    # 3) heuristic
    from dateparser.search import search_dates  # local import to avoid global cost
    dates_found = search_dates(text, languages=["ru"])
    if dates_found and "центр" in text.lower():
        date_obj = dates_found[0][1].date()
        # центр = всё после слова "центр"
        lower = text.lower()
        idx = lower.find("центр")
        center_name = text[idx + 5 :].strip()
        params = {"date": date_obj.strftime("%Y-%m-%d"), "center": center_name}
        return await handle_admin_create_event(session, bot, user_id, params)

    return "Не удалось распознать команду в вашем сообщении."


# --- Whisper processing ---

model = whisper.load_model(settings.WHISPER_MODEL)

def transcribe_voice(file_path: Path) -> str:
    wav_path = file_path.with_suffix(".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-i", str(file_path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav_path)],
            check=True, capture_output=True, text=True
        )
        result = model.transcribe(str(wav_path), language='ru')
        text_out = result.get("text", "")
        if isinstance(text_out, list):
            text_out = " ".join(map(str, text_out))
        return str(text_out)
    finally:
        if file_path.exists():
            file_path.unlink()
        if wav_path.exists():
            wav_path.unlink()
