from app.mini.titles.handlers import router as mini_titles_router
from app.handlers.create import router as create_router
from app.mini.superadmin.handlers import router as superadmin_router
from app.handlers.admin import router as admin_router
from app.handlers.characters import router as characters_router
from app.handlers.common import router as common_router
from app.handlers.inventory import router as inventory_router
from app.handlers.timers import router as timers_router
from app.mini.boss.handlers import router as mini_boss_router
from app.mini.events.handlers import router as mini_events_router
from app.mini.handlers import router as mini_router


ROUTERS = [
    common_router,
    mini_titles_router,
    mini_boss_router,
    mini_events_router,
    mini_router,
    create_router,
    superadmin_router,
    characters_router,
    inventory_router,
    admin_router,
    timers_router,
]
