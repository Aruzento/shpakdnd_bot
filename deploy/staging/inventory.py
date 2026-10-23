"""Frozen check inventory. All commands run ONLY in an offline container copy."""
CHECKS = [
    ['deploy/staging/unittest_policy.py'],
    ['check_bot.py'], ['-m', 'compileall', '-q', 'bot.py', 'app', 'deploy/staging'],
    ['-m', 'app.mini.combat.hero_abilities.validate'],
    ['-m', 'app.mini.boss.boss_abilities.validate'], ['-m', 'app.mini.tower.validate'],
    ['-m', 'app.mini.validate_v142'], ['-m', 'app.mini.editorial.validator'],
    ['tools/validate_editorial_release.py'], ['tools/validate_pvp_migration.py'],
    ['tools/verify_editorial_mechanics.py'],
    ['deploy/audit-v142-ui.py', '--output', '.staging-checks/ui-audit'],
]
MANUAL = [
    'Registration/deep link, duplicate callback, TEST chat/topic isolation',
    'Home/collection/cards/images, filters, equipment and navigation on phone',
    'Gacha x1/x10/coupons: one debit, restart and duplicate callbacks',
    'Tower/Boss/Village: active session, worker state, retry and restart',
    'PvP: join race, hidden choice, expiry/refund, publication/edit failures, pause-new',
    'Editorial hot reload, invalid JSON fallback, emoji, stable PID',
    'Complete docs/ui/v1.4.3-staging.md A-X and V1.4.2 screen matrix with evidence',
]
