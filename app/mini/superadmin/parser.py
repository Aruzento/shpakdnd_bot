import re
import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class SuperCommand:
    action: str
    chat_id: int
    thread_id: int
    target: str = ""
    flags: tuple[str,...] = ()
    value: str = ""


def parse_command(text):
    parts=shlex.split(text or "")
    if len(parts)<2:
        raise ValueError("Использование: /super... CHAT_ID:THEME_ID @user, numeric ID или ALL -c/-s/-p/-i VALUE")
    action=parts[0].split('@',1)[0].removeprefix('/')
    scope=re.fullmatch(r"(-?\d+):(\d+)",parts[1])
    if not scope:
        raise ValueError("Укажи мир строго как chatid:themeid.")
    chat_id,thread_id=map(int,scope.groups())
    if chat_id==0:
        raise ValueError("Некорректный chat ID.")
    if action=="superchars" and len(parts)==2:
        return SuperCommand(action,chat_id,thread_id)
    if len(parts)<3 or not re.fullmatch(r"(?:@[A-Za-z0-9_]+|[1-9]\d*|(?i:all))",parts[2]):
        raise ValueError("Target: @username, положительный numeric Telegram user ID или ALL.")
    target=parts[2].lower()
    if action=="superluck" and len(parts)==3:
        return SuperCommand(action,chat_id,thread_id,target)
    valid={"-c","-s","-p","-i"}
    if action=="superlook":
        flags=tuple(parts[3:]) or ("-c","-s","-p","-i")
        if len(set(flags))!=len(flags) or any(x not in valid for x in flags):
            raise ValueError("Флаги просмотра: -c -s -p -i.")
        return SuperCommand(action,chat_id,thread_id,target,flags)
    if action in {"superadd","superdel"} and len(parts)==5 and parts[3] in valid:
        value=parts[4]
        if parts[3] in {"-c","-s"} and not re.fullmatch(r"[1-9]\d*",value):
            raise ValueError("Количество должно быть целым положительным числом.")
        if not value or value.startswith('-'):
            raise ValueError("Укажи один ресурс и его значение.")
        return SuperCommand(action,chat_id,thread_id,target,(parts[3],),value)
    raise ValueError("Некорректный формат super-команды: мутация принимает ровно один флаг.")
