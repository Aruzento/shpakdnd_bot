"""Live help registry; legacy import names remain available."""
from app.mini.editorial.registry import LiveMapping, text
SECTIONS = LiveMapping({'start': ('rules.start.title', 'rules.start.body'), 'heroes': ('rules.heroes.title', 'rules.heroes.body'), 'bosses': ('rules.bosses.title', 'rules.bosses.body'), 'factions': ('rules.factions.title', 'rules.factions.body'), 'tower': ('rules.tower.title', 'rules.tower.body'), 'equipment': ('rules.equipment.title', 'rules.equipment.body'), 'money': ('rules.money.title', 'rules.money.body'), 'village': ('rules.village.title', 'rules.village.body'), 'duels': ('rules.duels.title', 'rules.duels.body'), 'streak': ('rules.streak.title', 'rules.streak.body')})
def rules_text():
    return text("rules.overview")
def __getattr__(name):
    if name == "RULES_TEXT":
        return rules_text()
    raise AttributeError(name)
