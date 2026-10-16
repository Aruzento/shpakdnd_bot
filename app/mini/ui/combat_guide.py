"""Five live plain-text chapters backed by existing glossary codes."""
from aiogram.types import InlineKeyboardMarkup
from app.mini.editorial import text,glossary,screen
from app.mini.editorial.buttons import button
CHAPTERS=("factions","damage","range","classes","traits")
TERMS=(
 ("faction",("commoners","beasts","monsters","warriors","dark")),
 ("damage",("slashing","piercing","bludgeoning","magic")),
 ("range",("melee","ranged")),
 ("class",("warrior","guardian","sneaky","healer","technical","mage","beast","none")),
 ("trait",("undead","construct","flying","poisonous","holy","demonic","armored","none")),
)
@screen
def guide_screen(world_id,user_id,page=0):
    if page not in range(5):raise ValueError("Invalid combat guide page")
    chapter=CHAPTERS[page];group,codes=TERMS[page]
    parameters={code:glossary(group+"."+code,decorated=True) for code in codes}
    body=text("guide."+chapter+".body",title=text("guide."+chapter+".title"),**parameters)
    def b(label,target):
        return button(text=label,callback_data=f"mini:guide:{world_id}:{user_id}:{target}")
    rows=[]
    if page:rows.append([b(text("guide.previous"),page-1)])
    if page<4:rows.append([b(text("guide.next"),page+1)])
    rows.append([button(text=text("guide.rules"),callback_data=f"mini:rules:{world_id}:{user_id}")])
    return body,InlineKeyboardMarkup(inline_keyboard=rows)
