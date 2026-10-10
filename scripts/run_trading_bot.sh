#!/usr/bin/env bash
set -euo pipefail

# Исходники встроены в образ; снаружи остаются только настройки и рабочие данные.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f .env ]]; then
    echo "❌ Не найден .env. Создайте его из .env.example и задайте API-ключи." >&2
    exit 1
fi
mkdir -p data/prices data/news data/logs charts
IMAGE_NAME="${SERVO_IMAGE_PREFIX:-localhost/servo-engine}-bot:${SERVO_IMAGE_TAG:-dev}"
podman build --format docker --layers -t "$IMAGE_NAME" .

run_args=(--rm --init --userns=keep-id --stop-timeout 45 --env-file .env)
[[ ! -t 0 || ! -t 1 ]] || run_args+=(-it)
# Переданная из shell настройка сохраняет прежний способ прямого запуска.
[[ -z "${SERVO_RUNTIME_SUPERVISOR:-}" ]] || run_args+=(-e "SERVO_RUNTIME_SUPERVISOR=$SERVO_RUNTIME_SUPERVISOR")
[[ "${SERVO_RUNTIME_SUPERVISOR:-1}" != 0 ]] || run_args+=(--no-healthcheck)

exec podman run "${run_args[@]}" \
    -v "$PROJECT_ROOT/data:/app/data:rw,z" \
    -v "$PROJECT_ROOT/charts:/app/charts:rw,z" \
    -v "$PROJECT_ROOT/config:/app/config:rw,z" \
    "$IMAGE_NAME"
