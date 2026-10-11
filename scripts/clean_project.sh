#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
IMAGE="${SERVO_CLEANUP_IMAGE:-localhost/servo-engine-bot:dev}"
mode=cache
apply=false

usage() {
    cat <<'EOF'
Безопасная очистка проекта — Python работает только в Podman.

scripts/clean_project.sh [--mode cache|reset] [--apply|--dry-run]
  cache    Восстановимые свечи, новости, графики, Python/test/build-кэш.
  reset    Также история, логи, control-файлы и весь rate limiter; всё в архив.
  --apply  Применить после остановки bot/panel и остальных писателей.
По умолчанию только просмотр. .env, config и trading state сохраняются.
В reset cooldown сбрасывается; при следующем запуске rate limiter создастся заново.
Архив reset: .cache/cleanup-backups/. Узнать подробности: CLEANUP.md.
Образ можно выбрать через SERVO_CLEANUP_IMAGE; .env не передаётся, сеть отключена.
EOF
}

fail() { printf '❌ %s\n' "$*" >&2; exit 1; }
arguments=("$@")
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            [[ $# -ge 2 ]] || fail 'Для --mode нужно cache или reset.'
            mode="$2"
            [[ "$mode" == cache || "$mode" == reset ]] || fail 'Режим должен быть cache или reset.'
            shift 2 ;;
        --apply) apply=true; shift ;;
        --dry-run) shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Неизвестный параметр: $1. См. --help." ;;
    esac
done
command -v podman >/dev/null 2>&1 || fail 'Не найден Podman.'
podman image exists "$IMAGE" || fail "Нет образа $IMAGE. Сначала выполните scripts/build_images.sh."

inventory='[]'
access=ro
if [[ "$apply" == true ]]; then
    # Проверяются все контейнеры, а не только стандартные Compose-имена.
    ids="$(podman ps -aq)" || fail 'Не удалось получить список контейнеров.'
    if [[ -n "$ids" ]]; then
        # IDs приходят от Podman; разделение по пробелам здесь намеренное.
        records="$(podman inspect --format '{"State":{{json .State}},"Mounts":{{json .Mounts}}}' $ids)" \
            || fail 'Не удалось проверить контейнеры; очистка запрещена.'
        # Не передаём Config.Env: там могут быть ключи других работающих сервисов.
        inventory="[${records//$'\n'/,}]"
    fi
    access=rw
fi

# Это не compose run: сервисы не запускаются, ключи не загружаются.
printf '%s\n' "$inventory" | podman run --rm -i --network none --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,size=128m --userns=keep-id --cap-drop ALL \
    --security-opt no-new-privileges --no-healthcheck \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$PROJECT_ROOT:/project:$access,z" -w /project \
    "$IMAGE" python scripts/cleanup.py --root /project --host-root "$PROJECT_ROOT" \
    --inventory-stdin "${arguments[@]}"
