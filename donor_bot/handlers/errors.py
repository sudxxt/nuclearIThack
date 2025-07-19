import logging
from aiogram import Router
from aiogram.types.error_event import ErrorEvent

errors_router = Router()

@errors_router.errors()
async def handle_errors(event: ErrorEvent):
    """
    Handles errors and exceptions.
    """
    logging.exception(
        "Cause an exception: %s, on update: %s",
        event.exception,
        event.update.model_dump_json(indent=2, exclude_none=True),
    )




