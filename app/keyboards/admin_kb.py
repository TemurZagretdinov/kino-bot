from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Kino qo'shish"), KeyboardButton(text="📺 Serial qo'shish")],
            [KeyboardButton(text="📋 Kinolar ro'yxati"), KeyboardButton(text="🗑 Kino o'chirish")],
            [KeyboardButton(text="📢 Kanal qo'shish"), KeyboardButton(text="📋 Kanallar ro'yxati")],
            [KeyboardButton(text="❌ Kanal o'chirish"), KeyboardButton(text="📊 Statistika")],
            [KeyboardButton(text="📨 Reklama yuborish")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Admin bo'limini tanlang",
    )


def confirm_delete_movie_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Ha, o'chirish",
                    callback_data="confirm_delete_movie",
                ),
                InlineKeyboardButton(
                    text="Bekor qilish",
                    callback_data="cancel_delete_movie",
                ),
            ]
        ]
    )


def confirm_broadcast_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Yuborish",
                    callback_data="confirm_broadcast",
                ),
                InlineKeyboardButton(
                    text="Bekor qilish",
                    callback_data="cancel_broadcast",
                ),
            ]
        ]
    )
