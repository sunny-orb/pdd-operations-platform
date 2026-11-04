#!/usr/bin/env bash
# 标准输入为构建清单JSON；无路径/账号/镜像环境变量覆盖入口。
set -euo pipefail
readonly project=/home/songbing/pdd-operations-platform
[[ "$(id -un)" == songbing && "$(cd -- "$(dirname -- "$0")" && pwd -P)" == "$project" ]]
[[ ! -L /home && ! -L /home/songbing && ! -L "$project" && ! -L "$project/manage.py" ]]
exec /usr/bin/python3 -I -B "$project/manage.py" release
