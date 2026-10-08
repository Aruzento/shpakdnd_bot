#!/usr/bin/env bash
# Interactive release deploy. The filesystem watcher remains a separate tool.
set -Eeuo pipefail

PROJECT="${SHPAKDND_PROJECT:-/opt/shpakdnd-bot}"
SERVICE="${SHPAKDND_SERVICE:-shpakdnd-bot.service}"
WATCHER="${SHPAKDND_WATCHER:-shpakdnd-bot-watch.path}"
UPDATE_SERVICE="${SHPAKDND_UPDATE_SERVICE:-shpakdnd-bot-update.service}"
BOT_USER="${SHPAKDND_BOT_USER:-shpakbot}"
PYTHON="${SHPAKDND_PYTHON:-$PROJECT/.venv/bin/python}"
DB="$PROJECT/shpakdnd.db"
BACKUP_DIR="${SHPAKDND_BACKUPS:-/opt/shpakdnd-backups}"
LOG_DIR="${SHPAKDND_LOGS:-/var/log/shpakdnd-deploy}"
LOCK="${SHPAKDND_LOCK:-/run/lock/shpakdnd-deploy.lock}"
RUNTIME_DIR="${SHPAKDND_RUNTIME_DIR:-/run}"
START_WAIT="${SHPAKDND_START_WAIT:-5}"
PREFLIGHT_DIR="${SHPAKDND_PREFLIGHT_EVIDENCE:-/var/lib/shpakdnd-preflight}"
WORK_DIR="${SHPAKDND_PREFLIGHT_WORK:-/var/tmp/shpakdnd-preflight}"
SOURCE_DIR="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")" && pwd)"
TOOLS="" OLD_HEAD="unknown" TARGET_HEAD="unknown" BACKUP="не создан" LOG=""
MAINTENANCE=0 DRY_RUN=0 PREFLIGHT_ONLY=0 FAILED_STEP="" TEST_COUNT="unknown" EVIDENCE="" PREFLIGHT_PID=""

as_bot() { runuser -u "$BOT_USER" -- "$@"; }
runtime_bot() { timeout --kill-after=5 60 runuser -u "$BOT_USER" -- "$@"; }
git_bot() { as_bot git -C "$PROJECT" "$@"; }
state() { systemctl is-active "$1" 2>/dev/null || true; }
head_now() { git_bot rev-parse HEAD 2>/dev/null || printf 'unknown\n'; }

summary() {
    printf '\nPrevious HEAD: %s\nTarget HEAD: %s\nCurrent HEAD: %s\nBackup: %s\n' "$OLD_HEAD" "$TARGET_HEAD" "$(head_now)" "$BACKUP"
    printf 'Bot service: %s\nWatcher: %s\nUpdate helper: %s\nLog: %s\n' "$(state "$SERVICE")" "$(state "$WATCHER")" "$(state "$UPDATE_SERVICE")" "${LOG:-не создан}"
}
on_exit() {
    local code=$?
    trap - EXIT
    if (( code != 0 && MAINTENANCE )); then
        printf '\nDeployment не завершён. Автоматический запуск не выполняется.\n'
        summary
    fi
    if (( code != 0 )) && [[ -n "$EVIDENCE" && -f "$EVIDENCE" ]]; then
        "$PYTHON" "$TOOLS/release_preflight.py" invalidate --evidence "$EVIDENCE" || true
    fi
    # This directory was created by mktemp for this process, contains only helper copies.
    if [[ -n "$TOOLS" && "$TOOLS" == "$RUNTIME_DIR"/shpakdnd-deploy.* ]]; then rm -rf -- "$TOOLS"; fi
}
interrupted() {
    if [[ -n "$PREFLIGHT_PID" ]]; then kill -TERM "$PREFLIGHT_PID" 2>/dev/null || true; wait "$PREFLIGHT_PID" 2>/dev/null || true; fi
    printf '\nDeployment прерван.\n'; summary; exit "$1"; }
trap on_exit EXIT
trap 'interrupted 130' INT
trap 'interrupted 143' TERM

confirm() {
    local answer default="${2:-N}"
    # Complete the line before read: the log redactor streams whole lines.
    printf '%s [%s]:\n' "$1" "$([[ "$default" == Y ]] && printf Y/n || printf y/N)"
    # EOF is never confirmation, including at the default-yes launch prompt.
    if ! IFS= read -r answer; then printf '\nВвод закрыт; подтверждение не получено.\n'; return 1; fi
    [[ -z "$answer" ]] && answer="$default"
    [[ "$answer" == y || "$answer" == Y || "$answer" == yes || "$answer" == д || "$answer" == Д ]]
}
require_clean() {
    local changes
    changes="$(git_bot status --porcelain --untracked-files=all)" || return 1
    if [[ -n "$changes" ]]; then
        printf '❌ Есть server-local изменения; deployment остановлен:\n%s\n' "$changes" >&2
        return 1
    fi
}
verify_head() { [[ "$(head_now)" == "$1" ]]; }
unit_loaded() { [[ "$(systemctl show -p LoadState --value "$1")" == loaded ]]; }
safe_control_paths() {
    local physical_project physical_directory directory
    physical_project="$(readlink -f -- "$PROJECT")" || return 1
    for directory in "$PREFLIGHT_DIR" "$WORK_DIR" "$RUNTIME_DIR" "$LOG_DIR" "$BACKUP_DIR" "$LOCK"; do
        physical_directory="$(readlink -m -- "$directory")" || return 1
        [[ "$physical_directory" != "$physical_project" && "$physical_directory" != "$physical_project/"* && "$physical_project" != "$physical_directory/"* ]] || { printf '❌ Temporary/evidence paths intersect production.\n'; return 1; }
    done
    [[ ! -L "$LOCK" && ! "$LOCK" -ef "$DB" ]] || { printf '❌ Deployment lock aliases protected DB.\n'; return 1; }
}

preflight() {
    [[ -d "$PROJECT" && -x "$PYTHON" && -f "$DB" && -f "$PROJECT/.env" ]] || { printf '❌ Отсутствует project/python/DB/.env.\n'; return 1; }
    id "$BOT_USER" >/dev/null || return 1
    as_bot test -r "$DB" || return 1
    as_bot test -r "$PROJECT/.env" || return 1
    as_bot test -w "$PROJECT" || return 1
    as_bot "$PYTHON" -c 'import dotenv' || return 1
    [[ "$(git_bot rev-parse --show-toplevel)" == "$(cd "$PROJECT" && pwd -P)" ]] || { printf '❌ PROJECT не является корнем repository.\n'; return 1; }
    for unit in "$SERVICE" "$WATCHER" "$UPDATE_SERVICE"; do
        unit_loaded "$unit" || { printf '❌ systemd unit отсутствует: %s\n' "$unit"; return 1; }
    done
    [[ "$(systemctl show -p User --value "$SERVICE")" == "$BOT_USER" ]] || { printf '❌ User сервиса не совпадает с BOT_USER.\n'; return 1; }
    [[ "$(systemctl show -p WorkingDirectory --value "$SERVICE")" == "$PROJECT" ]] || { printf '❌ WorkingDirectory сервиса не совпадает с PROJECT.\n'; return 1; }
    [[ "$(systemctl show -p ExecStart --value "$SERVICE")" == *"$PYTHON"* ]] || { printf '❌ ExecStart использует другой Python.\n'; return 1; }
    [[ "$(systemctl show -p Triggers --value "$WATCHER")" == "$UPDATE_SERVICE" ]] || { printf '❌ Watcher вызывает неожиданный service.\n'; return 1; }
    [[ "$START_WAIT" =~ ^[0-9]+$ ]] || return 1
    require_clean || return 1
}
stop_stack() {
    systemctl stop "$WATCHER" || return 1
    # Stop a pending/running oneshot too: stopping the path alone does not cancel it.
    systemctl stop "$UPDATE_SERVICE" || return 1
    systemctl stop "$SERVICE" || return 1
    for unit in "$WATCHER" "$UPDATE_SERVICE" "$SERVICE"; do
        [[ "$(state "$unit")" == inactive ]] || { printf '❌ Unit не остановлен: %s\n' "$unit"; return 1; }
    done
}
protected_target() {
    local paths
    paths="$(git_bot ls-tree -r --name-only "${1:-$TARGET_HEAD}")" || return 1
    if printf '%s\n' "$paths" | grep -Eq '^(\.env$|shpakdnd\.db($|[-.])|timers\.db($|[-.])|\.venv(/|$))'; then
        printf '❌ Target отслеживает production env/DB/venv. Checkout запрещён.\n'; return 1
    fi
}
checkout_head() {
    require_clean || return 1
    git_bot switch --no-overwrite-ignore -C main "$1" || return 1
    verify_head "$1" || { printf '❌ Checkout HEAD не совпадает с подтверждённым target.\n'; return 1; }
}
notice() { as_bot "$PYTHON" "$TOOLS/telegram-deploy-notice.py" --project "$PROJECT" --text "$1"; }
finished_notice() {
    if ! notice "✅ Технический перерыв завершён.
D&D Mini снова работает.
Версия: $(head_now | cut -c1-7)"; then printf 'WARNING: Финальное сообщение Telegram не отправлено; bot и watcher active.\n'; fi
}
check_step() {
    local name="$1"; shift
    printf '\n[%s] %s\n' "$(date -Is)" "$name"
    if "$@"; then printf '%-18s OK\n' "$name ........"; return 0; fi
    FAILED_STEP="$name"
    printf '❌ Проверка не пройдена: %s\n' "$name" >&2
    return 1
}
full_preflight() {
    install -d -o root -g root -m 700 "$PREFLIGHT_DIR"
    install -d -o root -g root -m 711 "$WORK_DIR"
    EVIDENCE="$PREFLIGHT_DIR/preflight_$(date '+%Y%m%d_%H%M%S')_$$_${TARGET_HEAD:0:12}.json"
    "$PYTHON" "$TOOLS/release_preflight.py" run --project "$PROJECT" --db "$DB" \
        --old "$OLD_HEAD" --target "$TARGET_HEAD" --evidence "$EVIDENCE" --workspace "$WORK_DIR" \
        --python "$PYTHON" --bot-user "$BOT_USER" --service "$SERVICE" --watcher "$WATCHER" --update-service "$UPDATE_SERVICE" &
    PREFLIGHT_PID=$!
    local result=0
    wait "$PREFLIGHT_PID" || result=$?
    PREFLIGHT_PID=""
    (( result == 0 )) || return "$result"
    local validated
    validated="$("$PYTHON" "$TOOLS/release_preflight.py" validate --evidence "$EVIDENCE" --old "$OLD_HEAD" --target "$TARGET_HEAD")" || return 1
    printf '%s\n' "$validated"
    TEST_COUNT="$(printf '%s\n' "$validated" | sed -n 's/.*tests=\([0-9][0-9]*\).*/\1/p')"
    [[ "$TEST_COUNT" =~ ^[1-9][0-9]*$ ]] || return 1
}
validate_preflight() {
    "$PYTHON" "$TOOLS/release_preflight.py" validate --evidence "$EVIDENCE" --old "$OLD_HEAD" --target "$TARGET_HEAD" "$@"
}
checks() {
    local expected="$1"
    # Only bounded runtime/schema/integrity checks during maintenance. All
    # compileall, check_bot, validators, guard and unittest ran on copied TARGET.
    check_step runtime_DB runtime_bot "$PYTHON" "$TOOLS/sqlite-deploy.py" config --project "$PROJECT" --db "$DB" || return 1
    check_step runtime_init runtime_bot "$PYTHON" "$TOOLS/release_preflight.py" runtime --short --project "$PROJECT" --db "$DB" --sha "$expected" || return 1
    check_step database runtime_bot "$PYTHON" "$TOOLS/sqlite-deploy.py" check --db "$DB" || return 1
    check_step HEAD verify_head "$expected" || return 1
    check_step worktree require_clean || return 1
}
start_stack() {
    local started pid restarts journal
    started="$(date '+%Y-%m-%d %H:%M:%S')"
    systemctl start "$SERVICE" || return 1
    pid="$(systemctl show -p MainPID --value "$SERVICE")" || return 1
    restarts="$(systemctl show -p NRestarts --value "$SERVICE")" || return 1
    sleep "$START_WAIT"
    systemctl is-active --quiet "$SERVICE" || return 1
    [[ "$pid" =~ ^[1-9][0-9]*$ && "$(systemctl show -p MainPID --value "$SERVICE")" == "$pid" && "$(systemctl show -p NRestarts --value "$SERVICE")" == "$restarts" ]] || { printf '❌ PID/restart count изменился при startup.\n'; return 1; }
    journal="$(journalctl -u "$SERVICE" --since "$started" -n 100 --no-pager -o cat)" || return 1
    if printf '%s\n' "$journal" | grep -Eq 'Traceback|ModuleNotFoundError|ImportError|RuntimeError|SyntaxError|Failed to start|Main process exited|Scheduled restart job|Start request repeated too quickly'; then
        printf '❌ Startup failure найден в свежем journal.\n%s\n' "$journal"; return 1
    fi
    systemctl start "$WATCHER" || return 1
    systemctl is-active --quiet "$WATCHER" || return 1
    sleep "$START_WAIT"
    systemctl is-active --quiet "$SERVICE" || return 1
    [[ "$(systemctl show -p MainPID --value "$SERVICE")" == "$pid" && "$(systemctl show -p NRestarts --value "$SERVICE")" == "$restarts" ]] || return 1
    systemctl is-active --quiet "$WATCHER" || return 1
    journal="$(journalctl -u "$SERVICE" --since "$started" -n 100 --no-pager -o cat)" || return 1
    if printf '%s\n' "$journal" | grep -Eq 'Traceback|ModuleNotFoundError|ImportError|RuntimeError|SyntaxError|Failed to start|Main process exited|Scheduled restart job|Start request repeated too quickly'; then return 1; fi
    printf 'bot .............. active\nwatcher .......... active\n'
}
failed_start() {
    printf '\n❌ Bot/watcher startup failed.\n'
    systemctl --no-pager status "$SERVICE" "$WATCHER" || true
    journalctl -u "$SERVICE" -n 50 --no-pager || true
    # Do not leave an unhealthy Restart=always process looping.
    stop_stack || return 1
}
rollback() {
    validate_preflight --rollback-db "$DB" || { printf '❌ Rollback запрещён: совместимость/схема не подтверждена.\n'; return 1; }
    printf 'Code rollback: %s. DB НЕ восстанавливается из backup.\n' "$OLD_HEAD"
    stop_stack || return 1
    if ! verify_head "$OLD_HEAD"; then checkout_head "$OLD_HEAD" || return 1; fi
    checks "$OLD_HEAD" || return 1
    if ! start_stack; then failed_start; return 1; fi
    finished_notice
    printf '✅ Предыдущая версия восстановлена и запущена.\n'
    summary
}
failure_choice() {
    local choice
    summary
    printf '\n[1] Оставить бот остановленным\n[2] Вернуть предыдущий Git HEAD и запустить предыдущую версию\nВыбор [1]:\n'
    IFS= read -r choice || choice=1
    if [[ "$choice" == 2 ]]; then
        if ! rollback; then printf '❌ Rollback не завершён; нужен ручной разбор.\n'; return 1; fi
    else printf 'Бот оставлен остановленным. Watcher не запускается.\n'; fi
    return 1  # The requested target deployment failed, even if rollback succeeded.
}
main() {
    case "${1:-}" in
        '') ;;
        --dry-run) DRY_RUN=1 ;;
        --preflight) PREFLIGHT_ONLY=1 ;;
        --help) printf 'sudo deploy-shpakdnd [--dry-run|--preflight]\n'; return 0 ;;
        *) printf 'Неизвестный аргумент. Используй --dry-run, --preflight или --help.\n'; return 2 ;;
    esac
    [[ "$#" -le 1 ]] || return 2
    [[ "$(id -u)" == 0 ]] || { printf 'Запусти через sudo deploy-shpakdnd.\n'; return 1; }
    for command in git flock runuser systemctl journalctl install mktemp tee timeout readlink; do command -v "$command" >/dev/null || return 1; done
    safe_control_paths || return 1
    # Opening a lock must never truncate any existing file.
    exec 9>>"$LOCK"
    if ! flock -n 9; then printf 'Deployment уже выполняется.\n'; return 1; fi
    preflight || return 1
    cd "$PROJECT"
    # Freeze the small helper bundle: it also survives a rollback to older code.
    TOOLS="$(mktemp -d "$RUNTIME_DIR/shpakdnd-deploy.XXXXXXXX")"
    chmod 755 "$TOOLS"
    for helper in deploy_helpers.py telegram-deploy-notice.py sqlite-deploy.py release_preflight.py preflight_data.py deploy-shpakdnd.sh; do
        install -m 644 "$SOURCE_DIR/$helper" "$TOOLS/$helper"
    done
    as_bot "$PYTHON" "$TOOLS/sqlite-deploy.py" config --project "$PROJECT" --db "$DB" || return 1
    if (( ! DRY_RUN )); then
        install -d -m 700 "$LOG_DIR"
        LOG="$LOG_DIR/deploy_$(date '+%Y%m%d_%H%M%S')_$$.log"
        touch "$LOG"; chmod 600 "$LOG"
        exec > >(as_bot "$PYTHON" "$TOOLS/deploy_helpers.py" --redact-env "$PROJECT/.env" | tee -a "$LOG") 2>&1
    fi
    printf 'D&D Mini Deploy — %s\n' "$(date -Is)"
    printf 'git fetch origin (bot пока не останавливается)...\n'
    git_bot fetch origin || return 1
    require_clean || return 1
    OLD_HEAD="$(git_bot rev-parse --verify HEAD)" || return 1
    TARGET_HEAD="$(git_bot rev-parse --verify 'origin/main^{commit}')" || return 1
    protected_target "$OLD_HEAD" || return 1
    protected_target "$TARGET_HEAD" || return 1
    printf '\nТекущий HEAD: %s\norigin/main: %s\nBot: %s; watcher: %s\n' "$OLD_HEAD" "$TARGET_HEAD" "$(state "$SERVICE")" "$(state "$WATCHER")"
    if (( DRY_RUN )); then printf 'Dry run: OK. Notice/stop/checkout/migrations/tests/restart не выполнялись.\n'; return 0; fi
    if ! check_step preflight full_preflight; then return 1; fi
    if (( PREFLIGHT_ONLY )); then printf 'Preflight завершён; production не изменён. Evidence: %s\n' "$EVIDENCE"; return 0; fi
    if [[ "$OLD_HEAD" == "$TARGET_HEAD" ]]; then
        printf 'Production уже находится на актуальном origin/main.\n'
        confirm 'Всё равно выполнить проверки/restart?' || return 0
    else confirm 'Обновить production до origin/main?' || return 0; fi
    validate_preflight || return 1
    require_clean && verify_head "$OLD_HEAD" || return 1
    [[ "$(state "$SERVICE")" == active && "$(state "$WATCHER")" == active && "$(state "$UPDATE_SERVICE")" == inactive ]] || return 1
    if ! notice '🛠 Технический перерыв'; then
        printf 'WARNING: Не удалось отправить сообщение в Telegram.\n'
        confirm 'Продолжить deployment?' || return 0
    fi
    MAINTENANCE=1
    stop_stack || return 1
    install -d -o root -g "$BOT_USER" -m 770 "$BACKUP_DIR"
    BACKUP="$BACKUP_DIR/shpakdnd_$(date '+%Y%m%d_%H%M%S').db"
    if ! check_step backup runtime_bot "$PYTHON" "$TOOLS/sqlite-deploy.py" backup --db "$DB" --destination "$BACKUP"; then failure_choice || true; return 1; fi
    printf '\nCurrent: %s\nTarget: %s\nBackup: %s\n' "$OLD_HEAD" "$TARGET_HEAD" "$BACKUP"
    if ! confirm "Установить версию ${TARGET_HEAD:0:7} из origin/main?"; then
        printf 'Код не изменён.\n'
        if confirm 'Снова запустить прежнюю версию?' Y; then
            if ! verify_head "$OLD_HEAD" || ! start_stack; then failed_start; return 1; fi
            finished_notice
            summary
            return 0
        fi
        printf 'Бот и watcher оставлены остановленными по выбору пользователя.\n'; summary; return 2
    fi
    if ! validate_preflight --live-db "$DB"; then FAILED_STEP=evidence; failure_choice || true; return 1; fi
    if ! checkout_head "$TARGET_HEAD"; then FAILED_STEP=checkout; failure_choice || true; return 1; fi
    if ! checks "$TARGET_HEAD"; then failure_choice || true; return 1; fi
    if ! validate_preflight --live-db "$DB"; then FAILED_STEP=evidence; failure_choice || true; return 1; fi
    printf '\n✅ Все проверки пройдены.\nHEAD: %s\nTests: %s tests, OK\nDB: OK\n' "$TARGET_HEAD" "$TEST_COUNT"
    if ! confirm 'Запустить новую версию?' Y; then
        printf 'Проверенный код установлен; bot и watcher остаются остановленными.\n'; summary; return 2
    fi
    if ! start_stack; then failed_start; failure_choice || true; return 1; fi
    finished_notice
    printf '\n✅ Deployment завершён.\n'
    summary
}
# Parsed before checkout; never read a modified source script after main returns.
main "$@"; exit $?
