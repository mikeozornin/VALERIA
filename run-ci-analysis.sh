#!/usr/bin/env bash
# Запуск задачи разбора упавшего CI-пайплайна и опрос её статуса.
set +x
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Использование:
  bash run-ci-analysis.sh <URL пайплайна или job GitLab>
  bash run-ci-analysis.sh --status <RUN_ID>

Скрипт запросит логин и пароль Valeria. Нужны curl и jq.
Учётке должны быть назначены workspace и доступ к таске.

Необязательные переменные окружения:
  VALERIA_BASE_URL   Адрес Valeria (по умолчанию http://127.0.0.1:8080)
  VALERIA_LOGIN      Логин, чтобы не вводить его каждый раз
  VALERIA_TASK_ID    ID задачи разбора CI (обязательная переменная)
  IDEMPOTENCY_KEY  Ключ повторной отправки того же запроса после сбоя

Опрос выполняется каждые 5 секунд. Ctrl+C прекращает только опрос:
сама задача продолжает работать. Возобновить опрос можно через --status.
Коды завершения: 0 — completed, 1 — failed/cancelled, 2 — ошибка клиента/API.
GET статуса не возвращает текст отчёта агента.
EOF
}

die() { printf '%s\n' "$*" >&2; exit 2; }

MODE=run
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --status)
    [[ $# == 2 && -n "$2" ]] || { usage >&2; exit 2; }
    MODE=status
    RUN_ID=$2
    [[ "$RUN_ID" =~ ^[a-zA-Z0-9_-]+$ ]] || die 'Некорректный RUN_ID.'
    ;;
  *)
    [[ $# == 1 ]] || { usage >&2; exit 2; }
    PIPELINE_URL=$1
    [[ "$PIPELINE_URL" =~ ^https?://[^[:space:]]+$ ]] || die 'Нужна HTTP(S)-ссылка GitLab.'
    ;;
esac

for command in curl jq; do
  command -v "$command" >/dev/null || die "Не установлена команда: $command"
done

BASE=${VALERIA_BASE_URL:-http://127.0.0.1:8080}
BASE=${BASE%/}
TASK_ID=${VALERIA_TASK_ID:?VALERIA_TASK_ID is required}
[[ "$TASK_ID" =~ ^[a-zA-Z0-9_-]+$ ]] || die 'Некорректный TASK_ID.'
COOKIE_FILE=$(mktemp)
trap 'rm -f "$COOKIE_FILE"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
CURL=(curl --fail-with-body --silent --show-error --connect-timeout 10 --max-time 60)

LOGIN=${VALERIA_LOGIN:-}
if [[ -z "$LOGIN" ]]; then
  read -r -p 'Логин Valeria: ' LOGIN || die 'Не удалось прочитать логин.'
fi
read -r -s -p 'Пароль Valeria: ' PASSWORD || die 'Не удалось прочитать пароль.'
printf '\n'

# Пароль передаётся через stdin, не через аргументы внешних процессов.
if ! AUTH=$(
  printf '%s' "$PASSWORD" |
    jq -Rs --arg login "$LOGIN" '{login: $login, password: .}' |
    "${CURL[@]}" -c "$COOKIE_FILE" \
      -H 'Content-Type: application/json' \
      --data-binary @- "$BASE/api/auth/login"
); then
  unset PASSWORD
  die "Ошибка входа: $AUTH"
fi
unset PASSWORD
CSRF=$(jq -er '.csrf_token | strings | select(length > 0)' <<< "$AUTH") || die 'Нет CSRF-токена в ответе.'
unset AUTH

if [[ "$MODE" == run ]]; then
  REQUEST_KEY=${IDEMPOTENCY_KEY:-$(cat /proc/sys/kernel/random/uuid)}
  printf 'TASK_ID: %s\nIdempotency-Key: %s\n' "$TASK_ID" "$REQUEST_KEY"
  printf 'При сбое POST повторите с тем же логином, URL и настройками:\n'
  printf 'IDEMPOTENCY_KEY=%q bash %q %q\n' "$REQUEST_KEY" "$0" "$PIPELINE_URL"

  if ! RUN=$(
    jq -n --arg url "$PIPELINE_URL" '{user_input: $url}' |
      "${CURL[@]}" -b "$COOKIE_FILE" \
        -H 'Content-Type: application/json' \
        -H "X-CSRF-Token: $CSRF" \
        -H "Idempotency-Key: $REQUEST_KEY" \
        --data-binary @- "$BASE/api/tasks/$TASK_ID/runs"
  ); then
    die "Не удалось подтвердить запуск. Ответ: $RUN"
  fi
  RUN_ID=$(jq -er '.id | strings | select(length > 0)' <<< "$RUN") || die 'Нет ID запуска в ответе.'
fi

printf 'RUN_ID: %s\n' "$RUN_ID"
printf 'Возобновить опрос под той же учёткой:\nbash %q --status %q\n' "$0" "$RUN_ID"

while true; do
  if ! STATE=$("${CURL[@]}" -b "$COOKIE_FILE" "$BASE/api/task-runs/$RUN_ID"); then
    die "Ошибка получения статуса. Задача могла продолжить выполнение. Ответ: $STATE"
  fi
  STATUS=$(jq -er '.status | strings | select(length > 0)' <<< "$STATE") || die 'Нет статуса в ответе.'
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$STATUS"
  case "$STATUS" in
    completed)
      printf 'Разбор завершён. Метаданные запуска:\n'
      jq . <<< "$STATE"
      exit 0
      ;;
    failed|cancelled)
      jq . <<< "$STATE"
      exit 1
      ;;
    queued|dispatching|running|cancelling) sleep 5 ;;
    *) die "Неизвестный статус: $STATUS" ;;
  esac
done
