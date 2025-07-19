from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🩸 Моя карточка")],
        [KeyboardButton(text="ℹ️ Информация"), KeyboardButton(text="📅 Мероприятия"), KeyboardButton(text="🎫 Тикет")],
        [KeyboardButton(text="🏆 Рейтинг"), KeyboardButton(text="⚙️ Настройки")],
    ],
    resize_keyboard=True,
)

info_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Требования к донорам", callback_data="info:blood")],
        [InlineKeyboardButton(text="Что такое костный мозг?", callback_data="info:dkm")],
        [InlineKeyboardButton(text="Как проходит донация в МИФИ?", callback_data="info:mifi")],
    ]
)

# Back button for simple FSM cancel
back_button = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True, one_time_keyboard=True
)

history_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="История донаций", callback_data="history_pg_1")]
    ]
)
