from pathlib import Path

from aiogram import Router, F, Bot
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from donor_bot.config import settings
from donor_bot.services.voice import transcribe_voice, process_voice_command

router = Router()


@router.message(F.text.in_({"🎙️ Голос", "🎙️ Голос(адм)"}))
async def start_voice_command(message: Message):
    await message.answer("Пришлите голосовое сообщение с вашей командой.")


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot, session: AsyncSession):
    if not message.voice or not message.from_user:
        return

    # temp folder inside package data directory
    temp_dir = Path(__file__).resolve().parent.parent / "data" / "temp_audio"
    temp_dir.mkdir(parents=True, exist_ok=True)
    ogg_path = temp_dir / f"{message.voice.file_id}.ogg"

    # download file to disk via Bot API
    file_info = await bot.get_file(message.voice.file_id)
    if file_info.file_path is None:
        await message.answer("Не удалось получить файл.")
        return
    await bot.download_file(file_info.file_path, destination=ogg_path)  # type: ignore[arg-type]

    try:
        text = transcribe_voice(ogg_path)
        if not text:
            await message.answer("Не удалось распознать речь. Попробуйте еще раз.")
            return

        await message.answer(f"<i>Распознано:</i> «{text}»", parse_mode="HTML")

        is_admin = message.from_user.id in settings.ADMIN_IDS
        response = await process_voice_command(text, session, bot, message.from_user.id, is_admin)

        if response:
            await message.answer(response)
        else:
            await message.answer("Не удалось распознать команду в вашем сообщении.")

    except Exception as e:
        await message.answer(f"Произошла ошибка при обработке голосового сообщения: {e}")

    finally:
        if ogg_path.exists():
            ogg_path.unlink()




