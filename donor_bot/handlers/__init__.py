from .common import router as common_router
from .donor_menu import router as donor_menu_router
from .admin_menu import router as admin_menu_router
from .tickets_admin import router as tickets_admin_router
from .tickets_user import router as tickets_user_router

__all__ = [
    "common_router",
    "donor_menu_router",
    "admin_menu_router",
    "tickets_admin_router",
    "tickets_user_router",
]
