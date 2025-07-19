from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

admin_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Мероприятия"), KeyboardButton(text="🩸 Доноры"), KeyboardButton(text="🎫 Тикеты")],
        [KeyboardButton(text="💬 Рассылка"), KeyboardButton(text="📈 Отчёт"), KeyboardButton(text="📊 Экспорт")],
        [KeyboardButton(text="👑 Админы")],
    ],
    resize_keyboard=True,
)


def answer_kb(donor_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ответить донору", callback_data=f"answer:{donor_id}")]
        ]
    )
