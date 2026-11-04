#!/usr/bin/env bash
set -euo pipefail
readonly project=/home/songbing/pdd-operations-platform
[[ "$(id -un)" == songbing && "$(cd -- "$(dirname -- "$0")" && pwd -P)" == "$project" ]]
[[ ! -L /home && ! -L /home/songbing && ! -L "$project" && ! -L "$project/manage.py" ]]
exec /usr/bin/python3 -I -B "$project/manage.py" rollback
