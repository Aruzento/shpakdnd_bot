"""The sole Mini owner is pinned by numeric identity, never by username.

Telegram ID 694384548 was explicitly confirmed by the owner for V1.3.
There is no role transfer, database assignment or dynamic allowlist.
"""
SUPERADMIN_USER_ID: int | None = 694384548


def is_superadmin(user_id: int) -> bool:
    return type(SUPERADMIN_USER_ID) is int and SUPERADMIN_USER_ID > 0 and type(user_id) is int and user_id == SUPERADMIN_USER_ID


def require_superadmin(user_id: int):
    if not is_superadmin(user_id):
        raise ValueError("Эта команда доступна только владельцу Mini (numeric Telegram ID).")
