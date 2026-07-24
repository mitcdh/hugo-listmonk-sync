#!/bin/sh
set -eu

image_name="${1:-hugo-listmonk-sync:smoke}"
smoke_port="${SMOKE_PORT:-18081}"

python3 tests/container_smoke_server.py "${smoke_port}" &
mock_pid=$!
trap 'kill "${mock_pid}" 2>/dev/null || true' EXIT INT TERM

docker run --rm \
  --add-host=host.docker.internal:host-gateway \
  --env "NEWSLETTER_JSON_URL=http://host.docker.internal:${smoke_port}/newsletter.json" \
  --env "LISTMONK_BASE_URL=http://host.docker.internal:${smoke_port}" \
  --env "LISTMONK_API_USERNAME=smoke-user" \
  --env "LISTMONK_API_TOKEN=smoke-token" \
  --env "LISTMONK_LIST_IDS=1" \
  --env "RUN_ONCE=true" \
  "${image_name}"

curl --fail --silent --show-error "http://127.0.0.1:${smoke_port}/health"
echo

