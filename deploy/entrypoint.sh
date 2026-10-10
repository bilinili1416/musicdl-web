#!/bin/sh
set -eu
musicdl_data_path="${MUSICDL_DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ]; then
    # Railway volumes mount as root. Prepare only application-owned directories,
    # then run Python as the unprivileged application user.
    install -d -o musicdl -g musicdl "$musicdl_data_path/musicdl_outputs" "$musicdl_data_path/.musicdl-web"
    exec gosu musicdl "$@"
fi
exec "$@"
