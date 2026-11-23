#!/bin/bash
set -euo pipefail

LOG_FILE="${LOG_FILE:-/var/log/hoplyra_update.log}"
LOCK_FILE="${LOCK_FILE:-/tmp/hoplyra_update.lock}"

exec >> "$LOG_FILE" 2>&1
echo "=== Starting Hoplyra Update at $(date -u) ==="

# 1. Single owner of execution lockfile
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "ERROR: Another update process is already running."
    exit 1
fi

REPO_DIR="${1:-/root/Hoplyra}"
TARGET_TAG="${2:-}"
PYTHON_BIN="${3:-python3}"
HEALTH_PORT="${4:-8787}"
HEALTH_SCHEME="${5:-https}"

if [ -z "$TARGET_TAG" ]; then
    echo "ERROR: TARGET_TAG argument is required."
    exit 1
fi

if [[ ! "$TARGET_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
    echo "ERROR: Invalid tag format: $TARGET_TAG"
    exit 1
fi

cd "$REPO_DIR"

if [ ! -d ".git" ]; then
    echo "ERROR: $REPO_DIR is not a git repository."
    exit 1
fi

PREV_REF=$(git rev-parse HEAD)
echo "Current HEAD: $PREV_REF"

STAGE_DIR=""
CHECKED_OUT=false
SUCCESS=false

# Unified, robust health check parsing HTTP status code and JSON field "status" == "ok"
check_service_health() {
    local url="${HEALTH_SCHEME}://127.0.0.1:${HEALTH_PORT}/api/health"
    local raw_out=""
    local http_code=""
    local body=""

    raw_out=$(curl -sk --max-time 4 -w "\n%{http_code}" "$url" 2>/dev/null) || return 1
    if [ -z "$raw_out" ]; then
        return 1
    fi

    http_code=$(echo "$raw_out" | tail -n 1)
    body=$(echo "$raw_out" | sed '$d')

    if [ "$http_code" != "200" ]; then
        return 1
    fi

    # Parse JSON status field via python
    if ! "$PYTHON_BIN" -c "import sys, json; data=json.loads(sys.argv[1]); sys.exit(0 if data.get('status') == 'ok' else 1)" "$body" 2>/dev/null; then
        return 1
    fi

    return 0
}

sync_static_bundle() {
    local src_dir="$1"
    if [ ! -d "$src_dir" ] || [ ! -f "$src_dir/index.html" ]; then
        echo "ERROR: Source static bundle $src_dir is invalid or missing index.html"
        return 1
    fi

    if [ -d "$REPO_DIR/ui" ]; then
        mkdir -p "$REPO_DIR/ui/dist"
        if ! cp -r "$src_dir"/* "$REPO_DIR/ui/dist/"; then
            echo "ERROR: Failed to copy static files to ui/dist"
            return 1
        fi
        if [ ! -f "$REPO_DIR/ui/dist/index.html" ]; then
            echo "ERROR: Failed to verify index.html in ui/dist"
            return 1
        fi
    fi

    if [ -d "$REPO_DIR/hoplyra" ]; then
        mkdir -p "$REPO_DIR/hoplyra/static"
        if ! cp -r "$src_dir"/* "$REPO_DIR/hoplyra/static/"; then
            echo "ERROR: Failed to copy static files to hoplyra/static"
            return 1
        fi
        if [ ! -f "$REPO_DIR/hoplyra/static/index.html" ]; then
            echo "ERROR: Failed to verify index.html in hoplyra/static"
            return 1
        fi
    fi

    return 0
}

cleanup_and_rollback() {
    local exit_code=$?
    # 1. Clean up temporary worktree if it was created
    if [ -n "$STAGE_DIR" ] && [ -d "$STAGE_DIR" ]; then
        echo "Cleaning up staging directory: $STAGE_DIR"
        git worktree remove --force "$STAGE_DIR" 2>/dev/null || rm -rf "$STAGE_DIR" || true
    fi

    # 2. Roll back main repository if modified and execution did not finish cleanly
    if [ "$CHECKED_OUT" = "true" ] && [ "$SUCCESS" != "true" ]; then
        echo "CRITICAL: Update failed after checkout. Rolling back main repo to $PREV_REF..."
        if ! git checkout "$PREV_REF"; then
            echo "FATAL: git checkout $PREV_REF failed during rollback!"
            exit 2
        fi

        ROLLBACK_HEAD=$(git rev-parse HEAD)
        if [ "$ROLLBACK_HEAD" != "$PREV_REF" ]; then
            echo "FATAL: Rollback HEAD mismatch! Expected $PREV_REF, got $ROLLBACK_HEAD"
            exit 2
        fi

        # Restore static bundle from reverted commit with strict error checking
        local bundle_restored=false
        if [ -d "$REPO_DIR/backend/hoplyra/static" ] && [ -f "$REPO_DIR/backend/hoplyra/static/index.html" ]; then
            if sync_static_bundle "$REPO_DIR/backend/hoplyra/static"; then
                bundle_restored=true
            fi
        elif [ -d "$REPO_DIR/ui/dist" ] && [ -f "$REPO_DIR/ui/dist/index.html" ]; then
            if sync_static_bundle "$REPO_DIR/ui/dist"; then
                bundle_restored=true
            fi
        fi

        if [ "$bundle_restored" != "true" ]; then
            echo "FATAL: Static web bundle restoration failed during rollback!"
            exit 2
        fi

        if [ "${MOCK_SERVICE_RESTART:-0}" != "1" ]; then
            if ! systemctl restart hoplyra; then
                echo "FATAL: systemctl restart hoplyra failed during rollback!"
                exit 2
            fi
        fi

        # Verify rollback service health using common parsed check
        echo "Verifying restored service health after rollback..."
        ROLLBACK_OK=false
        for i in {1..10}; do
            if check_service_health; then
                ROLLBACK_OK=true
                break
            fi
            sleep 2
        done

        if [ "$ROLLBACK_OK" != "true" ]; then
            echo "FATAL: Restored service failed health check on ${HEALTH_SCHEME}://127.0.0.1:${HEALTH_PORT}!"
            exit 2
        fi

        echo "Rollback to $PREV_REF verified and completed."
        exit 1
    fi
}
trap cleanup_and_rollback EXIT

# Fetch target tag directly from origin if remote exists
if git remote get-url origin >/dev/null 2>&1; then
    echo "Fetching target tag $TARGET_TAG from origin..."
    git fetch --depth=1 origin tag "$TARGET_TAG" || git fetch --tags origin
fi

if ! git rev-parse -q --verify "refs/tags/$TARGET_TAG" >/dev/null; then
    echo "ERROR: Target tag $TARGET_TAG not found after fetch."
    exit 1
fi

TARGET_COMMIT=$(git rev-parse "refs/tags/$TARGET_TAG^{commit}")
echo "Target tag $TARGET_TAG resolved to commit $TARGET_COMMIT"

# 2. Stage into an isolated temporary worktree for verification
STAGE_DIR=$(mktemp -d "/tmp/hoplyra_stage_XXXXXX")
echo "Creating isolated worktree at $STAGE_DIR for tag $TARGET_TAG..."
git worktree add --detach "$STAGE_DIR" "$TARGET_TAG"

STAGE_ENTRY=""
if [ -f "$STAGE_DIR/backend/run.py" ]; then
    STAGE_ENTRY="$STAGE_DIR/backend/run.py"
elif [ -f "$STAGE_DIR/run.py" ]; then
    STAGE_ENTRY="$STAGE_DIR/run.py"
else
    echo "ERROR: run.py entrypoint not found in release $TARGET_TAG."
    exit 1
fi

echo "Verifying Python syntax in staged release ($STAGE_ENTRY)..."
"$PYTHON_BIN" -m py_compile "$STAGE_ENTRY"

STAGE_STATIC=""
if [ -d "$STAGE_DIR/backend/hoplyra/static" ] && [ -f "$STAGE_DIR/backend/hoplyra/static/index.html" ]; then
    STAGE_STATIC="$STAGE_DIR/backend/hoplyra/static"
elif [ -d "$STAGE_DIR/ui/dist" ] && [ -f "$STAGE_DIR/ui/dist/index.html" ]; then
    STAGE_STATIC="$STAGE_DIR/ui/dist"
fi

if [ -z "$STAGE_STATIC" ]; then
    echo "ERROR: Valid prebuilt static bundle with index.html not found in release $TARGET_TAG."
    exit 1
fi

echo "Staged verification succeeded for $TARGET_TAG."
git worktree remove --force "$STAGE_DIR" 2>/dev/null || true
STAGE_DIR=""

# 3. Apply release to working repo
echo "Checking out $TARGET_TAG in main repository..."
git checkout "$TARGET_TAG"
CHECKED_OUT=true

ACTUAL_HEAD=$(git rev-parse HEAD)
if [ "$ACTUAL_HEAD" != "$TARGET_COMMIT" ]; then
    echo "ERROR: Checkout HEAD mismatch. Expected $TARGET_COMMIT, got $ACTUAL_HEAD"
    exit 1
fi

# Sync static bundle with strict failure check
if [ -d "$REPO_DIR/backend/hoplyra/static" ] && [ -f "$REPO_DIR/backend/hoplyra/static/index.html" ]; then
    if ! sync_static_bundle "$REPO_DIR/backend/hoplyra/static"; then
        echo "ERROR: sync_static_bundle failed for backend/hoplyra/static"
        exit 1
    fi
elif [ -d "$REPO_DIR/ui/dist" ] && [ -f "$REPO_DIR/ui/dist/index.html" ]; then
    if ! sync_static_bundle "$REPO_DIR/ui/dist"; then
        echo "ERROR: sync_static_bundle failed for ui/dist"
        exit 1
    fi
else
    echo "ERROR: No valid static web bundle found in checkout"
    exit 1
fi

if [ "${MOCK_SERVICE_RESTART:-0}" != "1" ]; then
    echo "Restarting hoplyra service..."
    systemctl restart hoplyra
fi

# Health check probe using actual scheme and port
echo "Verifying service health at ${HEALTH_SCHEME}://127.0.0.1:${HEALTH_PORT}..."
sleep 2
HEALTH_OK=false

for i in {1..10}; do
    if check_service_health; then
        HEALTH_OK=true
        break
    fi
    sleep 2
done

if [ "$HEALTH_OK" != "true" ]; then
    echo "ERROR: Service failed health check on ${HEALTH_SCHEME}://127.0.0.1:${HEALTH_PORT}! Initiating rollback..."
    exit 1
fi

SUCCESS=true
echo "=== Hoplyra successfully updated to $TARGET_TAG at $(date -u) ==="
