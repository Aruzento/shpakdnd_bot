from app.handlers.admin import router as admin_router
from app.handlers.characters import router as characters_router
from app.handlers.common import router as common_router
from app.handlers.inventory import router as inventory_router
from app.handlers.timers import router as timers_router


ROUTERS = [
    common_router,
    characters_router,
    inventory_router,
    admin_router,
    timers_router,
]
