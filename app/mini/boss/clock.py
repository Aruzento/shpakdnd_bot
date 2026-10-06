


from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def coerce_utc(value: datetime | None) -> datetime:
    if value is None:
        return utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def db_time(value: datetime) -> str:
    return coerce_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def parse_db_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            result = datetime.strptime(text, fmt)
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
            return result.astimezone(timezone.utc)
        except ValueError:
            continue
    return None

