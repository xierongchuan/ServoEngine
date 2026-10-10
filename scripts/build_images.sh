#!/usr/bin/env bash
set -euo pipefail

# Оба Dockerfile используют корень репозитория как контекст сборки.
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE="podman"
PLATFORM=""
IMAGE_PREFIX="localhost/servo-engine"
TAG="dev"
PUSH=false

usage() {
    cat <<'EOF'
Сборка автономных образов бота и панели.

Использование: scripts/build_images.sh [параметры]
  --engine podman|docker                 Движок (по умолчанию podman)
  --platform linux/amd64|linux/arm64|linux/amd64,linux/arm64
                                        Платформы (по умолчанию нативная)
  --image-prefix PREFIX                 Префикс (по умолчанию localhost/servo-engine)
  --tag TAG                             Тег (по умолчанию dev)
  --push                                Опубликовать образы в registry
  -h, --help                            Показать справку

Имена образов: PREFIX-bot:TAG и PREFIX-panel:TAG.
Docker для двух платформ требует --push. Авторизацию в registry выполните заранее.
EOF
}

fail() {
    printf 'Ошибка: %s\n' "$*" >&2
    exit 1
}

require_value() {
    [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "Для $1 требуется значение."
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --engine)
            require_value "$@"
            ENGINE="$2"
            shift 2
            ;;
        --platform)
            require_value "$@"
            PLATFORM="$2"
            case "$PLATFORM" in
                linux/amd64|linux/arm64|linux/amd64,linux/arm64) ;;
                *) fail "Недопустимая платформа: $PLATFORM. См. --help." ;;
            esac
            shift 2
            ;;
        --image-prefix)
            require_value "$@"
            IMAGE_PREFIX="$2"
            shift 2
            ;;
        --tag)
            require_value "$@"
            TAG="$2"
            shift 2
            ;;
        --push)
            PUSH=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) fail "Неизвестный параметр: $1. См. --help." ;;
    esac
done

case "$ENGINE" in
    podman|docker) ;;
    *) fail "Движок должен быть podman или docker: $ENGINE." ;;
esac

# Префикс не содержит тег или digest; допускается registry с номером порта.
component='[a-z0-9]+(([._]|__|-+)[a-z0-9]+)*'
registry='[a-z0-9]+([.-][a-z0-9]+)*(:[0-9]+)?'
[[ "$IMAGE_PREFIX" =~ ^($registry/)?$component(/$component)*$ ]] \
    || fail "Некорректный префикс образов: $IMAGE_PREFIX. Используйте нижний регистр, без тега и digest."
[[ "$TAG" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]] \
    || fail "Некорректный тег: $TAG (1–128 символов: буквы, цифры, _, . и -)."

MULTI=false
[[ "$PLATFORM" != *,* ]] || MULTI=true
if [[ "$ENGINE" == docker && "$MULTI" == true && "$PUSH" == false ]]; then
    fail "Docker не может загрузить multi-platform образ через --load. Укажите --push или одну платформу."
fi

command -v "$ENGINE" >/dev/null 2>&1 || fail "Не найден движок $ENGINE."
for dockerfile in "$PROJECT_ROOT/Dockerfile" "$PROJECT_ROOT/src/telegram_panel/Dockerfile"; do
    [[ -f "$dockerfile" ]] || fail "Не найден Dockerfile: $dockerfile."
done
if [[ "$ENGINE" == docker ]]; then
    docker buildx version >/dev/null 2>&1 || fail "Для Docker требуется доступный плагин buildx."
fi

platform_args=()
[[ -z "$PLATFORM" ]] || platform_args=(--platform "$PLATFORM")

build_image() {
    local name="$1"
    local dockerfile="$2"
    local image="${IMAGE_PREFIX}-${name}:${TAG}"
    printf 'Сборка %s: движок %s, платформа %s\n' "$image" "$ENGINE" "${PLATFORM:-нативная}"

    if [[ "$ENGINE" == podman ]]; then
        if [[ "$MULTI" == true ]]; then
            # Временный manifest не конфликтует с single-platform тегом и не
            # заменяет прежний рабочий образ до успешного завершения сборки.
            local manifest="${IMAGE_PREFIX}-${name}-build-manifest:${TAG}"
            if podman manifest exists "$manifest"; then
                podman manifest rm "$manifest"
            else
                local status=$?
                [[ "$status" -eq 1 ]] || fail "Не удалось проверить manifest $manifest (код $status)."
            fi
            podman build --format docker --layers "${platform_args[@]}" --manifest "$manifest" --file "$dockerfile" "$PROJECT_ROOT"
            podman tag "$manifest" "$image"
            podman manifest rm "$manifest"
            if [[ "$PUSH" == true ]]; then
                podman manifest push --all "$image" "docker://$image"
            fi
        else
            podman build --format docker --layers "${platform_args[@]}" -t "$image" --file "$dockerfile" "$PROJECT_ROOT"
            if [[ "$PUSH" == true ]]; then
                podman push "$image" "docker://$image"
            fi
        fi
    else
        local output_arg=--load
        [[ "$PUSH" == false ]] || output_arg=--push
        docker buildx build "${platform_args[@]}" "$output_arg" -t "$image" --file "$dockerfile" "$PROJECT_ROOT"
    fi
    printf 'Готово: %s\n' "$image"
}

build_image bot "$PROJECT_ROOT/Dockerfile"
build_image panel "$PROJECT_ROOT/src/telegram_panel/Dockerfile"
