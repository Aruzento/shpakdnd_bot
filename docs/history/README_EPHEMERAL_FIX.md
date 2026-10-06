# Исправление открытия D&D Mini

Изменена только механика открытия личного меню из закрепа.

Было:
- публичный launcher пытался превращаться в ephemeral-overlay
  через `replace_callback_query_message=True`.

Стало:
- публичный launcher всегда остаётся на месте;
- по нажатию бот отправляет отдельное ephemeral-сообщение;
- его видит только нажавший пользователь;
- chat_id и topic_id берутся из зарегистрированного Mini-мира,
  а не из callback-сообщения;
- ошибки Telegram API теперь пишутся в systemd-журнал;
- при успехе кнопка показывает короткий toast `D&D Mini открыто`.

После распаковки поверх `/opt/shpakdnd-bot`:

```bash
cd /opt/shpakdnd-bot
chown -R shpakbot:shpakbot /opt/shpakdnd-bot
sudo -u shpakbot ./.venv/bin/python check_bot.py
sudo -u shpakbot ./.venv/bin/python -m compileall -q bot.py app
systemctl restart shpakdnd-bot
journalctl -u shpakdnd-bot -n 50 --no-pager
```

Старый закреп можно оставить: после рестарта лучше выполнить `/minipanel`
ещё раз, чтобы бот обновил кнопку до актуального callback.
