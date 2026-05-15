"""
Decorators for handlers to inject db_user from middleware data.
"""
import logging
from functools import wraps
from aiogram.types import Message, CallbackQuery
import database as db

logger = logging.getLogger(__name__)


def inject_db_user(handler):
    """
    Decorator to inject db_user from handler middleware data.
    In aiogram 3.x, middleware data doesn't automatically inject into handler parameters,
    so this decorator ensures db_user is available to handlers that need it.
    """
    @wraps(handler)
    async def wrapper(*args, **kwargs):
        # If db_user is already in kwargs (from middleware), use it
        if "db_user" not in kwargs:
            # Extract from the event (Message or CallbackQuery)
            for arg in args:
                if isinstance(arg, Message):
                    db_user = await db.get_user(arg.from_user.id)
                    if db_user:
                        kwargs["db_user"] = db_user
                    break
                elif isinstance(arg, CallbackQuery):
                    db_user = await db.get_user(arg.from_user.id)
                    if db_user:
                        kwargs["db_user"] = db_user
                    break
        
        return await handler(*args, **kwargs)
    return wrapper
