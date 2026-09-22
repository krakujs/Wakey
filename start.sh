#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Local quickstart: preserve configuration and volumes; never install host software.
set -eu

if [ "${1:-}" = "--help" ]; then
    printf '%s\n' 'Usage: sh start.sh' \
        'Requires Docker with Compose. Starts Wakey and issues a fresh login token.' \
        'Re-running invalidates an unused setup token, but preserves data and sessions.'
    exit 0
fi
if [ "$#" -ne 0 ]; then
    printf '%s\n' 'Unknown option. Use: sh start.sh --help' >&2
    exit 2
fi
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Docker is required: https://docs.docker.com/get-started/get-docker/' >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    printf '%s\n' 'Install Docker Compose v2 or newer, then run this command again.' >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    printf '%s\n' 'Docker is not reachable. Start Docker and check your Docker permissions.' >&2
    exit 1
fi
printf '%s\n' 'Starting Wakey. The first build downloads dependencies; later starts reuse them.'
if ! docker compose up --build --wait --wait-timeout 180 wakey; then
    printf '%s\n' 'Wakey did not become healthy. Check: docker compose logs --tail 80 wakey' \
        'Check port 8477 availability and Docker resources. Your data volume was preserved.' >&2
    exit 1
fi
printf '\n%s\n' 'Open http://localhost:8477 and enter this single-use login token:'
docker compose exec -T wakey wakey setup-token --force
printf '\n%s\n' 'Next: connect GitHub, register your service, and send the sample log.' \
    'Shared-App setup requires the operator-published WAKEY_CONNECT_URL in .env.' \
    'Without GitHub configuration, this is a local demo—not a live GitHub connection.' \
    'On a remote server, use an SSH tunnel; do not expose port 8477 publicly.' \
    'Stop safely: docker compose stop    Start again: sh start.sh' \
    'Never use docker compose down -v unless you intend to erase your Wakey data.'
