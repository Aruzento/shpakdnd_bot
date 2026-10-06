class BossError(ValueError):
    pass


class BossRegistrationClosed(BossError):
    pass


class BossNotEnoughPlayers(BossError):
    pass


class BossCombatError(BossError):
    pass


class BossNotYourTurn(BossCombatError):
    pass


class BossNotParticipant(BossCombatError):
    pass

