from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, ReplyKeyboardRemove, KeyboardButton, ReplyKeyboardMarkup
from aiogram.types import FSInputFile
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, WebAppInfo

from donor_bot.keyboards import main_menu_kb, admin_menu_kb
from donor_bot.services.donors import get_donor_by_tg_id, create_donor
from donor_bot.config import settings

router = Router()


class RegistrationState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_agree = State()
    waiting_for_language = State()
    waiting_for_full_name = State()
    waiting_for_category = State()
    waiting_for_group = State()


@router.message(Command("start"))
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext):
    if not message.from_user:
        return
    await state.clear()
    donor = await get_donor_by_tg_id(session, message.from_user.id)
    if donor:
        kb = admin_menu_kb if message.from_user.id in settings.ADMIN_IDS else main_menu_kb
        gif_path = Path(__file__).resolve().parent.parent / "data" / "donor.gif"
        if gif_path.exists():
            try:
                await message.answer_animation(animation=FSInputFile(str(gif_path)), caption="С возвращением!", reply_markup=kb)
            except Exception:
                await message.answer("С возвращением!", reply_markup=kb)
        else:
            await message.answer("С возвращением!", reply_markup=kb)
    else:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        start_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚀 Начать", callback_data="reg:start")]]
        )
        welcome_text = (
            "Привет! Это официальный бот проекта «День Донора НИЯУ МИФИ». "
            "С его помощью вы сможете:\n"
            "• записаться на акцию\n"
            "• получать напоминания\n"
            "• посмотреть свою статистику\n\n"
            "Нажмите «Начать», чтобы пройти быструю регистрацию."
        )

        gif_path = Path(__file__).resolve().parent.parent / "data" / "donor.gif"
        if gif_path.exists():
            try:
                await message.answer_animation(animation=FSInputFile(str(gif_path)), caption=welcome_text, reply_markup=start_kb)
            except Exception:
                await message.answer(welcome_text, reply_markup=start_kb)
        else:
            await message.answer(welcome_text, reply_markup=start_kb)
        # ждём нажатия кнопки


@router.callback_query(F.data == "reg:start")
async def reg_start_cb(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.answer(  # type: ignore[union-attr]
        "Поделитесь номером телефона, нажав кнопку ниже:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
            resize_keyboard=True,
        )
    )
    await state.set_state(RegistrationState.waiting_for_phone)


@router.message(RegistrationState.waiting_for_phone, F.contact)
async def process_phone(message: Message, state: FSMContext):
    if not message.contact:
        return

    raw_phone = message.contact.phone_number
    import re
    # Допустимы +7XXXXXXXXXX или 8XXXXXXXXXX (11 цифр)
    if not re.match(r"^(\+?7\d{10}|8\d{10})$", raw_phone):
        await message.answer("Похоже, номер телефона имеет неверный формат. Попробуйте ещё раз или введите вручную в формате +7XXXXXXXXXX.")
        return

    # Унифицируем: приводим к +7XXXXXXXXXX
    digits = raw_phone.lstrip('+')
    if digits.startswith('8'):
        digits = '7' + digits[1:]
    normalized = '+' + digits

    await state.update_data(phone=normalized)
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    # Сразу переходим к выбору языка без мини-приложения
    lang_kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Русский", callback_data="reg:lang:ru"),
            InlineKeyboardButton(text="English", callback_data="reg:lang:en"),
        ]]
    )
    await message.answer("Выберите язык / Choose language:", reply_markup=lang_kb)
    await state.set_state(RegistrationState.waiting_for_language)


# --- Agreement accepted through WebApp ---


@router.message(RegistrationState.waiting_for_agree, F.web_app_data)
async def agree_pd_webapp(message: Message, state: FSMContext):
    """Handles data coming from the mini-app once the user ticks the checkboxes and presses «Принять»."""
    # We trust mini-app validation – any payload counts as consent
    await message.answer("Согласие получено ✅")

    # Ask language preference (reuse same flow as callback handler)
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    lang_kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Русский", callback_data="reg:lang:ru"),
            InlineKeyboardButton(text="English", callback_data="reg:lang:en"),
        ]]
    )

    await message.answer("Выберите язык / Choose language:", reply_markup=lang_kb)
    await state.set_state(RegistrationState.waiting_for_language)


@router.message(RegistrationState.waiting_for_full_name)
async def process_full_name(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, введите ФИО.")
        return
    # Простая валидация: минимум две буквы, минимум две части имени
    import re
    cleaned = " ".join(message.text.strip().split())  # убираем лишние пробелы
    if not re.match(r"^[А-Яа-яA-Za-z\- ]{5,}$", cleaned) or len(cleaned.split()) < 2:
        await message.answer("Похоже, ФИО введено некорректно. Пожалуйста, укажите полностью, без лишних символов.")
        return
    await state.update_data(full_name=cleaned)
    await message.answer(
        "Выберите вашу категорию:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Студент")],
                [KeyboardButton(text="Сотрудник")],
                [KeyboardButton(text="Гость")],
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(RegistrationState.waiting_for_category)


@router.message(RegistrationState.waiting_for_category, F.text.in_({"Студент", "Сотрудник", "Гость"}))
async def process_category(message: Message, state: FSMContext, session: AsyncSession):
    if not message.from_user or not message.text:
        return
    category = message.text.lower()
    await state.update_data(category=category)
    if category == "студент":
        await message.answer("Введите номер вашей группы (например, ИКБО-01-22).", reply_markup=ReplyKeyboardRemove())
        await state.set_state(RegistrationState.waiting_for_group)
    elif category == "гость":
        data = await state.get_data()
        await create_donor(
            session=session,
            tg_id=message.from_user.id,
            phone=data["phone"],
            full_name=data["full_name"],
            category=data["category"],
            lang=data.get("lang", "ru"),
        )
        # Попробуем найти ближайшее мероприятие и отправить его external_link, если есть
        link_note = ""
        try:
            from donor_bot.services.events import get_upcoming_events
            events = await get_upcoming_events(session)
            if events and events[0].external_link:
                link_note = f"\nЗаполните форму: {events[0].external_link}"
        except Exception:
            pass

        await message.answer(
            "Так как вы внешний донор, необходимо также зарегистрироваться на проходной МИФИ." + link_note,
            reply_markup=main_menu_kb,
        )
        await state.clear()
    else:
        data = await state.get_data()
        await create_donor(
            session=session,
            tg_id=message.from_user.id,
            phone=data["phone"],
            full_name=data["full_name"],
            category=data["category"],
            lang=data.get("lang", "ru"),
        )
        await message.answer("Регистрация завершена!", reply_markup=main_menu_kb)
        await state.clear()


@router.message(RegistrationState.waiting_for_group)
async def process_group(message: Message, state: FSMContext, session: AsyncSession):
    if not message.from_user or not message.text:
        return

    import re
    group_clean = message.text.strip().upper()
    # Пример формата: ИКБО-01-22 или «MATH-101» – буквы/цифры/дефис
    if not re.match(r"^[A-ZА-Я0-9\-]{3,20}$", group_clean):
        await message.answer("Пожалуйста, введите номер группы в корректном формате (буквы/цифры, например ИКБО-01-22). Попробуйте ещё раз.")
        return

    await state.update_data(group=group_clean)
    data = await state.get_data()
    await create_donor(
        session=session,
        tg_id=message.from_user.id,
        phone=data["phone"],
        full_name=data["full_name"],
        category=data["category"],
        group=data["group"],
        lang=data.get("lang", "ru"),
    )
    await message.answer("Регистрация завершена!", reply_markup=main_menu_kb)
    await state.clear()


# --- Language selection during registration ---


@router.callback_query(RegistrationState.waiting_for_language, F.data.startswith("reg:lang:"))
async def reg_pick_language(call: CallbackQuery, state: FSMContext):
    lang = call.data.split(":", 2)[2]  # type: ignore
    await state.update_data(lang=lang)
    await call.message.edit_reply_markup(reply_markup=None)  # type: ignore[attr-defined]
    prompt = "Введите ваше ФИО полностью:" if lang == "ru" else "Please enter your full name:"
    await call.message.answer(prompt, reply_markup=ReplyKeyboardRemove())  # type: ignore[attr-defined]
    await state.set_state(RegistrationState.waiting_for_full_name)
    await call.answer()


# ---------- /help ----------


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Показывает краткую справку по возможностям бота (разные для пользователя и админа)."""
    user_lines = [
        "<b>Основные функции:</b>",
        "• 🩸 Моя карточка — статистика донаций, очки, уровень", 
        "• ℹ️ Информация — требования к донорам, ДКМ, процесс в МИФИ", 
        "• 📅 Мероприятия — запись (с календарём) на ближайшие акции", 
        "• 🎫 Тикет — задать вопрос организаторам", 
        "• 🏆 Рейтинг — топ‐доноры года", 
        "• ⚙️ Настройки — изменить личные данные (скоро)",
        "\nДополнительно:",
        "• /start — перезапуск приветствия", 
        "• /help — эта справка",
    ]

    # Если администратор — добавим расширенный раздел
    from donor_bot.config import settings as _s
    if message.from_user and message.from_user.id in _s.ADMIN_IDS:
        admin_lines = [
            "\n<b>Администратор:</b>",
            "• 📅 Мероприятия — вкладки Актуальные / Завершённые, добавление через календарь", 
            "    • в Завершённых — отметка ✅/❌ присутствия", 
            "• 💬 Рассылка — выбор сегмента и отправка сообщения", 
            "• 📥 Импорт ДД — загрузить результаты акции (.xlsx)",
            "• 📊 Экспорт — выгрузка списка доноров акции", 
            "• 📈 Отчёт — сводный Excel по всем акциям", 
            "• 🩸 Доноры — статистика и экспорт базы", 
            "• 🎫 Тикеты — вопросы пользователей", 
            "• /donor edit <id|phone> field=value — правка донора текстом", 
            "• /info list | /info edit <key> — редактирование информационных страниц", 
            "• /import — импорт доноров из Excel", 
            "• /donors_export — экспорт всей базы доноров", 
        ]
        user_lines.extend(admin_lines)

    await message.answer("\n".join(user_lines), parse_mode="HTML")


@router.message(RegistrationState.waiting_for_phone, F.text)
async def process_phone_text(message: Message, state: FSMContext):
    """Fallback when user enters phone number as plain text instead of sharing a contact."""
    if not message.text:
        return
    raw_phone = message.text.strip()

    import re
    if not re.match(r"^(\+?7\d{10}|8\d{10})$", raw_phone):
        await message.answer(
            "Похоже, номер телефона имеет неверный формат. Пожалуйста, укажите в формате +7XXXXXXXXXX или нажмите кнопку \"Поделиться контактом\"."
        )
        return

    # Унифицируем: приводим к +7XXXXXXXXXX
    digits = raw_phone.lstrip('+')
    if digits.startswith('8'):
        digits = '7' + digits[1:]
    normalized = '+' + digits

    await state.update_data(phone=normalized)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    # Сразу спрашиваем язык, минуя этап согласия
    lang_kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Русский", callback_data="reg:lang:ru"),
            InlineKeyboardButton(text="English", callback_data="reg:lang:en"),
        ]]
    )
    await message.answer("Выберите язык / Choose language:", reply_markup=lang_kb)
    await state.set_state(RegistrationState.waiting_for_language)
