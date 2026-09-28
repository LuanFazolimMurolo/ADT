#!/bin/sh

set -eu

section() {
    printf '\n============================================================\n'
    printf ' %s\n' "$1"
    printf '============================================================\n'
}

kv() {
    printf '%s=%s\n' "$1" "$2"
}

command_state() {
    if command -v "$1" >/dev/null 2>&1; then
        printf 'available'
    else
        printf 'unavailable'
    fi
}

section "ADT PRODUCTION READ-ONLY PERFORMANCE PROFILE"

kv "profile_mode" "read-only"
kv "timestamp_utc" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
kv "hostname" "$(hostname 2>/dev/null || printf 'unknown')"
kv "kernel" "$(uname -sr 2>/dev/null || printf 'unknown')"

section "HOST CAPACITY"

if command -v nproc >/dev/null 2>&1; then
    kv "host_cpu_logical" "$(nproc)"
else
    kv "host_cpu_logical" "unavailable"
fi

if [ -r /proc/meminfo ]; then
    awk '
        /^MemTotal:/     { print "host_mem_total_kib=" $2 }
        /^MemAvailable:/ { print "host_mem_available_kib=" $2 }
        /^SwapTotal:/    { print "host_swap_total_kib=" $2 }
        /^SwapFree:/     { print "host_swap_free_kib=" $2 }
    ' /proc/meminfo
else
    kv "host_memory" "unavailable"
fi

if [ -r /proc/loadavg ]; then
    set -- $(cat /proc/loadavg)
    kv "host_load_1m" "$1"
    kv "host_load_5m" "$2"
    kv "host_load_15m" "$3"
else
    kv "host_load" "unavailable"
fi

section "FILESYSTEM CAPACITY"

if [ -d /srv/adt/data ]; then
    kv "adt_data_dir_state" "present"
    df -Pk /srv/adt/data | awk '
        NR == 2 {
            print "adt_data_filesystem_kib=" $2
            print "adt_data_used_kib=" $3
            print "adt_data_available_kib=" $4
            print "adt_data_use_percent=" $5
        }
    '

    if command -v du >/dev/null 2>&1; then
        DATA_USAGE=$(du -sk /srv/adt/data 2>/dev/null | awk '{print $1}' || true)

        if [ -n "${DATA_USAGE:-}" ]; then
            kv "adt_data_tree_kib" "$DATA_USAGE"
        else
            kv "adt_data_tree_kib" "unavailable"
        fi
    fi
else
    kv "adt_data_dir_state" "absent"
fi

section "PROCESS OBSERVATION"

profile_process() {
    label="$1"
    pattern="$2"

    if command -v pgrep >/dev/null 2>&1; then
        pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    else
        pids=""
    fi

    if [ -z "$pids" ]; then
        kv "${label}_state" "not-running"
        return
    fi

    kv "${label}_state" "running"

    for pid in $pids; do
        kv "${label}_pid" "$pid"

        ps -p "$pid" \
            -o pid=,pcpu=,pmem=,rss=,etimes=,comm= \
            2>/dev/null \
            | awk -v prefix="$label" '
                NF {
                    print prefix "_sample_pid=" $1
                    print prefix "_sample_cpu_percent=" $2
                    print prefix "_sample_memory_percent=" $3
                    print prefix "_sample_rss_kib=" $4
                    print prefix "_sample_elapsed_seconds=" $5
                    print prefix "_sample_command=" $6
                }
            ' || true
    done
}

profile_process \
    "adt_api" \
    "python.*-m[[:space:]]+app\.main"

profile_process \
    "adt_market_supervisor" \
    "python.*-m[[:space:]]+app\.runtime\.market_data_supervisor"

profile_process \
    "adt_paper_supervisor" \
    "python.*-m[[:space:]]+app\.runtime\.paper_run_supervisor"

section "DOCKER OBSERVATION"

kv "docker_cli" "$(command_state docker)"

if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        kv "docker_daemon" "available"

        printf '%s\n' "--- docker ps ---"
        docker ps \
            --format 'container={{.Names}} image={{.Image}} status={{.Status}}' \
            2>/dev/null \
            || true

        printf '%s\n' "--- docker stats no-stream ---"
        docker stats \
            --no-stream \
            --format 'container={{.Name}} cpu={{.CPUPerc}} memory={{.MemUsage}} memory_percent={{.MemPerc}} pids={{.PIDs}}' \
            2>/dev/null \
            || true
    else
        kv "docker_daemon" "unavailable"
    fi
else
    kv "docker_daemon" "unavailable"
fi

section "DATABASE BOUND CONFIGURATION"

CONFIG_FILE="services/backend/app/core/config.py"

if [ -r "$CONFIG_FILE" ]; then
    grep -E \
        'database_pool|max_size|market_http_timeout|market_http_max_connections|market_http_retries|market_http_max_retry_after|market_max_fetch_candles' \
        "$CONFIG_FILE" \
        2>/dev/null \
        || true
else
    kv "backend_config" "unavailable"
fi

section "FRONTEND BUNDLE GUARD"

if [ -r apps/web/scripts/check-bundle-budget.mjs ]; then
    kv "frontend_bundle_budget_script" "present"
else
    kv "frontend_bundle_budget_script" "absent"
fi

if [ -r apps/web/package.json ]; then
    if grep -q '"check:bundle-budget"' apps/web/package.json; then
        kv "frontend_bundle_budget_command" "present"
    else
        kv "frontend_bundle_budget_command" "absent"
    fi
else
    kv "frontend_package_json" "unavailable"
fi

section "PROFILE INTERPRETATION"

cat <<'TEXT'
profile_note=single samples are observational evidence only
profile_note=sizing requires representative sustained and peak observation windows
profile_note=absence of a running ADT process is not a failure of this profiler
profile_note=docker unavailability is reported and does not trigger service startup
profile_note=this profiler grants no trading or operational authority
TEXT

section "PROFILE COMPLETE"

kv "profile_result" "completed"
