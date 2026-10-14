"""Public invitation, hidden per-user choices, and recoverable ephemeral lobby."""
import secrets
from aiogram import F, Router
from aiogram.types import InlineKeyboardMarkup,EphemeralMessageParameters
from app.mini.editorial import text, glossary, screen
from app.mini.editorial.buttons import button
from app.mini.ui.context import load_extended_context, load_world, load_player
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.events import pvp
from app.mini.events.pvp_publication import publish

router=Router(name="mini_events_pvp")
CODES={"s":"saint","d":"demon","v":"villager"}

def _button(wid,uid,label,payload):
    return button(text=label,callback_data=f"mini:pvp:{wid}:{uid}:{payload}")

def _name(member):
    return str(member.get("character_name") or text("general.player"))[:100]

@screen
def public_screen(session):
    sid=session["id"];wid=session["world_id"]
    names={m["player_id"]:_name(m) for m in session["members"]}
    rows=[]
    if session["status"]=="open":
        body=text("pvp.public.open",name=names[session["creator_id"]])
        rows=[[_button(wid,0,text("pvp.accept"),f"join.{sid}")]]
    elif session["status"]=="choosing":
        body=text("pvp.public.choosing",first=names[session["creator_id"]],second=names[session["acceptor_id"]])
        # Owner=0 is an open-your-own-screen entry, never a choice on behalf
        # of another participant. Membership is checked by the service.
        rows=[[_button(wid,0,text("pvp.open_choice"),f"view.{sid}")]]
    elif session["status"]=="finished":
        members=session["members"]
        choices="\n".join(text("pvp.public.choice",name=_name(m),choice=glossary("rps."+m["choice"])) for m in members)
        body=text("pvp.public.draw",choices=choices) if session["outcome"]=="draw" else text(
            "pvp.public.win",choices=choices,winner=names[session["winner_id"]])
    else:
        body=text("pvp.public.closed")
    return body,InlineKeyboardMarkup(inline_keyboard=rows)

@screen
def personal_screen(world_id,user_id,player_id,session):
    sid=session["id"];rows=[]
    member=next((m for m in session["members"] if m["player_id"]==player_id),None)
    if not member:raise pvp.PvpError(text("pvp.error.member"))
    if session["status"]=="open":
        body=text("pvp.personal.open")
        rows.append([_button(world_id,user_id,text("pvp.cancel"),f"cancel.{sid}")])
    elif session["status"]=="choosing":
        if member["choice"] is None:
            body=text("pvp.personal.choose")
            rows += [[_button(world_id,user_id,glossary("rps."+choice,decorated=True),f"pick.{sid}.{code}")]
                     for code,choice in CODES.items()]
        else:
            body=text("pvp.personal.wait")
    else:
        body=public_screen(session)[0]
        if session["status"]=="publication_failed":body=text("pvp.personal.publication_failed")
    rows.append([_button(world_id,user_id,text("pvp.refresh"),f"view.{sid}")])
    rows.append([_button(world_id,user_id,text("navigation.back"),"home")])
    return body,InlineKeyboardMarkup(inline_keyboard=rows)

@screen
def lobby_screen(world_id,user_id,player_id,active,challenges):
    if active:return personal_screen(world_id,user_id,player_id,active)
    rows=[[_button(world_id,user_id,text("pvp.create"),"create."+secrets.token_hex(4))]]
    for session in challenges:
        creator=next(m for m in session["members"] if m["player_id"]==session["creator_id"])
        rows.append([_button(world_id,user_id,text("pvp.lobby.challenge",name=_name(creator)),f"join.{session['id']}")])
    rows.append([button(text=text("navigation.back"),callback_data=f"mini:ev:{world_id}:{user_id}:rps")])
    return text("pvp.lobby"),InlineKeyboardMarkup(inline_keyboard=rows)

@screen
async def _show(callback,world,player,session=None):
    if session:
        body,markup=personal_screen(world["id"],callback.from_user.id,player["id"],session)
    else:
        active,challenges=pvp.lobby(player["id"],world["id"])
        body,markup=lobby_screen(world["id"],callback.from_user.id,player["id"],active,challenges)
    await send_private_text_from_callback(callback,world,body,markup)

@router.callback_query(F.data.startswith("mini:pvp:"))
@screen
async def action(callback):
    # Public invitations intentionally have no predetermined owner.
    parts=(callback.data or "").split(":",4)
    try:
        if len(parts)!=5:raise ValueError
        wid,owner=int(parts[2]),int(parts[3])
        payload=parts[4].split(".")
        if owner==0:
            if payload[0] not in {"join","view"} or len(payload)!=2:raise ValueError
            world=load_world(wid,callback)
            player=load_player(wid,callback) if world else None
            if not world or not player:raise ValueError
        else:
            context=await load_extended_context(callback,"pvp")
            if context is None:return
            world,player,_=context
        act=payload[0];session=None
        if act=="home" and len(payload)==1:pass
        elif act=="create" and len(payload)==2:
            if len(payload[1])!=8 or any(c not in "0123456789abcdef" for c in payload[1]):raise ValueError
            session=pvp.create(player["id"],wid,"button:"+payload[1])
            await publish(callback.bot,session["id"])
            session=pvp.get_session(wid,session["id"])
        elif act in {"join","view","cancel"} and len(payload)==2:
            sid=int(payload[1])
            if act=="join":
                before=pvp.get_session(wid,sid)
                session=pvp.accept(player["id"],wid,sid)
                if before["status"]=="open" and session["status"]=="choosing":
                    creator=next(m for m in session["members"] if m["player_id"]==session["creator_id"])
                    body,markup=personal_screen(wid,creator["telegram_user_id"],creator["player_id"],session)
                    try:
                        await callback.bot.send_message(chat_id=world["chat_id"],message_thread_id=world["thread_id"] or None,
                            text=body,reply_markup=markup,ephemeral_message_parameters=EphemeralMessageParameters(
                                receiver_user_id=creator["telegram_user_id"]))
                    except Exception as error:
                        # A missed ephemeral can always be recovered via Events
                        # or the public open-choice button until the DB deadline.
                        print(f"PvP choice notice: {type(error).__name__}: {error}")
            elif act=="cancel":session=pvp.cancel(player["id"],wid,sid)
            else:session=pvp.get_session(wid,sid)
            if not any(m["player_id"]==player["id"] for m in session["members"]):
                raise pvp.PvpError(text("pvp.error.member"))
            await publish(callback.bot,sid)
        elif act=="pick" and len(payload)==3 and owner!=0:
            session=pvp.choose(player["id"],wid,int(payload[1]),CODES[payload[2]])
            await publish(callback.bot,session["id"])
        else:raise ValueError
    except (ValueError,KeyError) as error:
        await callback.answer(str(error)[:180] or text("pvp.error.callback"),show_alert=True)
        return
    await callback.answer()
    await _show(callback,world,player,session)
