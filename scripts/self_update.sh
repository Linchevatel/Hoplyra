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
HEALTH_CHECK_RETRIES="${HEALTH_CHECK_RETRIES:-10}"
HEALTH_CHECK_SLEEP="${HEALTH_CHECK_SLEEP:-2}"
export PYTHON_BIN

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

CANONICAL_REPO_DIR=$(pwd -P)
INSTALL_ID_FILE="$(git rev-parse --git-path hoplyra_install_id 2>/dev/null || echo '.git/hoplyra_install_id')"
INSTALL_ID=""
if [ -f "$INSTALL_ID_FILE" ]; then
    INSTALL_ID=$(cat "$INSTALL_ID_FILE" 2>/dev/null || true)
fi
if [ -z "$INSTALL_ID" ]; then
    INSTALL_ID=$(python3 -c 'import uuid; print(uuid.uuid4())' 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "$(date +%s%N)-$$")
    mkdir -p "$(dirname "$INSTALL_ID_FILE")" 2>/dev/null || true
    echo "$INSTALL_ID" > "$INSTALL_ID_FILE" 2>/dev/null || true
fi

STAGE_DIR=""
RELEASE_VENV=""
VENV_OLD_DIR=""
ORIG_VENV_IS_SYMLINK=false
ORIG_VENV_TARGET=""
VENV_OLD_MOVED=false
VENV_SWITCHED=false
CHECKED_OUT=false
SUCCESS=false

find_active_venv() {
    local bin="$1"
    if [ -z "$bin" ]; then
        return 1
    fi
    # Determine venv strictly by sys.prefix and sys.base_prefix of the passed interpreter
    local venv_path
    venv_path=$("$bin" -c 'import sys
prefix = sys.prefix
base_prefix = getattr(sys, "base_prefix", sys.prefix)
if prefix != base_prefix:
    print(prefix)
    sys.exit(0)
sys.exit(1)
' 2>/dev/null) || return 1

    if [ -n "$venv_path" ] && [ -e "$venv_path" ] && [ -f "$venv_path/pyvenv.cfg" ]; then
        echo "$venv_path"
        return 0
    fi
    return 1
}

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

    # 1. Clean up temporary worktree if created
    if [ -n "$STAGE_DIR" ] && [ -d "$STAGE_DIR" ]; then
        echo "Cleaning up staging directory: $STAGE_DIR"
        git worktree remove --force "$STAGE_DIR" 2>/dev/null || rm -rf "$STAGE_DIR" || true
    fi

    # If update succeeded, clean up old venv backup and finish cleanly
    if [ "$SUCCESS" = "true" ]; then
        if [ -n "$VENV_OLD_DIR" ] && [ -d "$VENV_OLD_DIR" ]; then
            echo "Cleaning up old backup venv after successful update: $VENV_OLD_DIR"
            rm -rf "$VENV_OLD_DIR" || true
        fi
        if [ "$ORIG_VENV_IS_SYMLINK" = "true" ] && [ -n "$ORIG_VENV_TARGET" ] && [ -d "$ORIG_VENV_TARGET" ] && [ "$ORIG_VENV_TARGET" != "$RELEASE_VENV" ]; then
            local target_canon repo_canon
            target_canon=$(cd "$ORIG_VENV_TARGET" 2>/dev/null && pwd -P || true)
            repo_canon="${CANONICAL_REPO_DIR:-}"
            [ -z "$repo_canon" ] && repo_canon=$(cd "$REPO_DIR" 2>/dev/null && pwd -P || true)

            local is_external=true
            if [ -n "$target_canon" ] && [ -n "$repo_canon" ] && [[ "$target_canon" == "$repo_canon/"* ]]; then
                is_external=false
            fi

            if [ "${PRUNE_OLD_VENVS:-0}" = "1" ]; then
                # Pruning explicitly requested as a separate manual maintenance operation
                local marker_file="$target_canon/.hoplyra_updater_managed"
                local can_prune=false
                if [ "$is_external" = "false" ] && [ -f "$marker_file" ]; then
                    local marker_install_id marker_repo_dir
                    marker_install_id=$(grep '^install_id=' "$marker_file" 2>/dev/null | cut -d= -f2- || true)
                    marker_repo_dir=$(grep '^repo_dir=' "$marker_file" 2>/dev/null | cut -d= -f2- || true)
                    if [ -n "${INSTALL_ID:-}" ] && [ "$marker_install_id" = "$INSTALL_ID" ] && [ "$marker_repo_dir" = "$repo_canon" ]; then
                        can_prune=true
                    fi
                fi

                if [ "$can_prune" = "true" ]; then
                    echo "Pruning previous updater-managed venv target (explicit PRUNE_OLD_VENVS=1): $ORIG_VENV_TARGET"
                    rm -rf "$ORIG_VENV_TARGET" || true
                else
                    echo "Preserving target venv: cannot prune unmanaged, external, or foreign venv: $ORIG_VENV_TARGET"
                fi
            else
                if [ "$is_external" = "true" ]; then
                    echo "Preserving unmanaged/external target venv (exclusive ownership not confirmed): $ORIG_VENV_TARGET"
                else
                    echo "Preserving previous target venv (automatic deletion disabled to protect shared references; exclusive ownership not confirmed): $ORIG_VENV_TARGET"
                fi
            fi
        fi
        return 0
    fi

    echo "CRITICAL: Update failed (exit code $exit_code). Initiating cleanup and rollback..."

    local rollback_failed=false
    local needs_service_restart=false

    # 2. Roll back main repository if checked out
    if [ "$CHECKED_OUT" = "true" ]; then
        echo "Rolling back main repo to $PREV_REF..."
        if ! git checkout "$PREV_REF"; then
            echo "ERROR: git checkout $PREV_REF failed during rollback!" >&2
            rollback_failed=true
        else
            ROLLBACK_HEAD=$(git rev-parse HEAD)
            if [ "$ROLLBACK_HEAD" != "$PREV_REF" ]; then
                echo "ERROR: Rollback HEAD mismatch! Expected $PREV_REF, got $ROLLBACK_HEAD" >&2
                rollback_failed=true
            fi
        fi

        # Restore static bundle from reverted commit with error checking
        local bundle_restored=false
        if [ "${MOCK_FAIL_STATIC_RESTORE:-0}" = "1" ]; then
            echo "MOCK ERROR: Simulated static bundle restore failure" >&2
        elif [ -d "$REPO_DIR/backend/hoplyra/static" ] && [ -f "$REPO_DIR/backend/hoplyra/static/index.html" ]; then
            if sync_static_bundle "$REPO_DIR/backend/hoplyra/static"; then
                bundle_restored=true
            fi
        elif [ -d "$REPO_DIR/ui/dist" ] && [ -f "$REPO_DIR/ui/dist/index.html" ]; then
            if sync_static_bundle "$REPO_DIR/ui/dist"; then
                bundle_restored=true
            fi
        fi

        if [ "$bundle_restored" != "true" ]; then
            echo "ERROR: Static web bundle restoration failed during rollback!" >&2
            rollback_failed=true
        fi
        needs_service_restart=true
    fi

    # 3. Roll back Python virtual environment regardless of checkout state or static bundle errors
    if [ "$VENV_OLD_MOVED" = "true" ] && [ "$VENV_SWITCHED" != "true" ]; then
        echo "Second mv failed; restoring $ACTIVE_VENV from $VENV_OLD_DIR..."
        rm -f "$ACTIVE_VENV.tmp" 2>/dev/null || true
        if [ -d "$VENV_OLD_DIR" ] && [ ! -e "$ACTIVE_VENV" ]; then
            if [ "${MOCK_FAIL_ROLLBACK_MV:-0}" = "1" ]; then
                echo "MOCK ERROR: Simulated rollback mv failure" >&2
                rollback_failed=true
            elif ! mv "$VENV_OLD_DIR" "$ACTIVE_VENV"; then
                echo "ERROR: Failed to restore $ACTIVE_VENV from $VENV_OLD_DIR" >&2
                rollback_failed=true
            fi
        else
            rollback_failed=true
        fi
        needs_service_restart=true
    elif [ "$VENV_SWITCHED" = "true" ]; then
        echo "Restoring previous Python venv..."
        rm -f "$ACTIVE_VENV.tmp" 2>/dev/null || true
        if [ "$ORIG_VENV_IS_SYMLINK" = "true" ] && [ -n "$ORIG_VENV_TARGET" ]; then
            ln -sfn "$ORIG_VENV_TARGET" "$ACTIVE_VENV.tmp"
            if [ "${MOCK_FAIL_ROLLBACK_MV:-0}" = "1" ]; then
                echo "MOCK ERROR: Simulated rollback mv failure" >&2
                rm -f "$ACTIVE_VENV.tmp" 2>/dev/null || true
                rollback_failed=true
            elif ! mv -Tf "$ACTIVE_VENV.tmp" "$ACTIVE_VENV"; then
                echo "ERROR: Failed to restore symlink $ACTIVE_VENV -> $ORIG_VENV_TARGET" >&2
                rollback_failed=true
            fi
        elif [ -n "$VENV_OLD_DIR" ] && [ -d "$VENV_OLD_DIR" ]; then
            local rollback_symlink_backup="$ACTIVE_VENV.rollback_backup.$$"
            if mv -T "$ACTIVE_VENV" "$rollback_symlink_backup" 2>/dev/null; then
                if [ "${MOCK_FAIL_ROLLBACK_MV:-0}" = "1" ]; then
                    echo "MOCK ERROR: Simulated rollback mv failure" >&2
                    mv -Tf "$rollback_symlink_backup" "$ACTIVE_VENV" 2>/dev/null || true
                    rollback_failed=true
                elif ! mv "$VENV_OLD_DIR" "$ACTIVE_VENV"; then
                    echo "ERROR: Failed to restore directory $ACTIVE_VENV from $VENV_OLD_DIR" >&2
                    mv -Tf "$rollback_symlink_backup" "$ACTIVE_VENV" 2>/dev/null || true
                    rollback_failed=true
                else
                    rm -f "$rollback_symlink_backup" 2>/dev/null || true
                fi
            else
                rollback_failed=true
            fi
        fi
        needs_service_restart=true
    fi

    # Clean up unpromoted release venv only if active venv was safely restored or never switched
    if [ -n "$RELEASE_VENV" ] && [ -d "$RELEASE_VENV" ]; then
        local can_cleanup_release_venv=false

        if [ "$VENV_SWITCHED" != "true" ]; then
            can_cleanup_release_venv=true
        else
            if [ "$ORIG_VENV_IS_SYMLINK" = "true" ]; then
                local current_active_target
                current_active_target=$(readlink -f "$ACTIVE_VENV" 2>/dev/null || true)
                if [ -n "$current_active_target" ] && [ "$current_active_target" = "$ORIG_VENV_TARGET" ] && [ "$current_active_target" != "$RELEASE_VENV" ]; then
                    can_cleanup_release_venv=true
                fi
            else
                if [ -d "$ACTIVE_VENV" ] && [ ! -L "$ACTIVE_VENV" ] && [ ! -e "$VENV_OLD_DIR" ]; then
                    can_cleanup_release_venv=true
                fi
            fi
        fi

        if [ "$can_cleanup_release_venv" = "true" ]; then
            echo "Cleaning up unpromoted release venv: $RELEASE_VENV"
            rm -rf "$RELEASE_VENV" || true
        else
            echo "WARNING: Active venv does not point to original environment (may still point to release venv); preserving $RELEASE_VENV to avoid dangling link." >&2
        fi
    fi

    # 4. Restart service and verify health if environment or code was touched
    if [ "$needs_service_restart" = "true" ]; then
        if [ "${MOCK_SERVICE_RESTART:-0}" != "1" ]; then
            if ! systemctl restart hoplyra; then
                echo "ERROR: systemctl restart hoplyra failed during rollback!" >&2
                rollback_failed=true
            fi
        fi

        # Verify rollback service health using common parsed check
        echo "Verifying restored service health after rollback..."
        ROLLBACK_OK=false
        for i in $(seq 1 "$HEALTH_CHECK_RETRIES"); do
            if check_service_health; then
                ROLLBACK_OK=true
                break
            fi
            sleep "$HEALTH_CHECK_SLEEP"
        done

        if [ "$ROLLBACK_OK" != "true" ]; then
            echo "ERROR: Restored service failed health check on ${HEALTH_SCHEME}://127.0.0.1:${HEALTH_PORT}!" >&2
            rollback_failed=true
        fi
    fi

    if [ "$rollback_failed" = "true" ]; then
        echo "FATAL: One or more rollback operations failed!" >&2
        exit 2
    fi

    echo "Rollback to $PREV_REF verified and completed."
    exit 1
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

# Prepare and verify Python dependencies in an isolated staging environment
STAGE_REQ=""
if [ -f "$STAGE_DIR/backend/requirements.txt" ]; then
    STAGE_REQ="$STAGE_DIR/backend/requirements.txt"
elif [ -f "$STAGE_DIR/requirements.txt" ]; then
    STAGE_REQ="$STAGE_DIR/requirements.txt"
fi

ACTIVE_VENV=$(find_active_venv "$PYTHON_BIN" || true)

if [ -n "$STAGE_REQ" ]; then
    echo "Verifying and preparing Python release dependencies from $STAGE_REQ..."
    if [ -z "$ACTIVE_VENV" ] || [ ! -e "$ACTIVE_VENV" ]; then
        echo "ERROR: Cannot reliably identify active virtual environment for service (passed interpreter is not in a virtualenv or ACTIVE_VENV is missing). Aborting update before pip install." >&2
        exit 1
    fi

    RELEASE_VENV=$(mktemp -d "$(dirname "$ACTIVE_VENV")/.venv_app_${TARGET_TAG}_XXXXXX")
    chmod 755 "$RELEASE_VENV"
    echo "Preparing isolated release venv at permanent unique path $RELEASE_VENV..."
    cp -a "$ACTIVE_VENV"/. "$RELEASE_VENV/"

    # Write updater ownership marker AFTER copying to ensure it is not overwritten
    # by an old marker or a marker from another migrated installation.
    rm -f "$RELEASE_VENV/.hoplyra_updater_managed" 2>/dev/null || true
    cat <<EOF > "$RELEASE_VENV/.hoplyra_updater_managed"
repo_dir=$CANONICAL_REPO_DIR
install_id=$INSTALL_ID
EOF
    chmod 644 "$RELEASE_VENV/.hoplyra_updater_managed"

    # Fix shebangs in bin/ to point to the permanent path $RELEASE_VENV/bin/python
    if [ -d "$RELEASE_VENV/bin" ]; then
        for script in "$RELEASE_VENV"/bin/*; do
            if [ -f "$script" ] && [ ! -L "$script" ] && [ -x "$script" ]; then
                if head -n 1 "$script" 2>/dev/null | grep -q '^#!.*python'; then
                    sed -i "1s|^#!.*python.*$|#!$RELEASE_VENV/bin/python|" "$script" 2>/dev/null || true
                fi
            fi
        done
    fi

    if ! "$RELEASE_VENV/bin/python" -m pip install --no-cache-dir -r "$STAGE_REQ"; then
        echo "ERROR: Failed to install Python dependencies from $STAGE_REQ" >&2
        exit 1
    fi
fi

echo "Staged verification succeeded for $TARGET_TAG."
git worktree remove --force "$STAGE_DIR" 2>/dev/null || true
STAGE_DIR=""

# 3. Apply release to working repo
if [ -n "$ACTIVE_VENV" ] && [ -e "$ACTIVE_VENV" ] && [ -n "$RELEASE_VENV" ] && [ -d "$RELEASE_VENV" ]; then
    echo "Switching active Python environment to release version ($RELEASE_VENV)..."
    if [ -L "$ACTIVE_VENV" ]; then
        ORIG_VENV_IS_SYMLINK=true
        ORIG_VENV_TARGET=$(readlink -f "$ACTIVE_VENV" 2>/dev/null || true)
    else
        ORIG_VENV_IS_SYMLINK=false
        ORIG_VENV_TARGET=""
        VENV_OLD_DIR=$(mktemp -d "$(dirname "$ACTIVE_VENV")/.venv_old_${PREV_REF:0:12}_XXXXXX")
        rmdir "$VENV_OLD_DIR"
        mv "$ACTIVE_VENV" "$VENV_OLD_DIR"
        VENV_OLD_MOVED=true
    fi

    ln -sfn "$RELEASE_VENV" "$ACTIVE_VENV.tmp"
    if [ "${MOCK_FAIL_SECOND_MV:-0}" = "1" ]; then
        echo "MOCK ERROR: Simulated second mv failure" >&2
        rm -f "$ACTIVE_VENV.tmp" 2>/dev/null || true
        exit 1
    fi
    mv -Tf "$ACTIVE_VENV.tmp" "$ACTIVE_VENV"
    VENV_SWITCHED=true

    echo "Verifying active environment and console commands..."
    if ! "$ACTIVE_VENV/bin/python" --version >/dev/null 2>&1; then
        echo "ERROR: Active Python interpreter failed verification at $ACTIVE_VENV/bin/python" >&2
        exit 1
    fi
    if [ -x "$ACTIVE_VENV/bin/pip" ]; then
        if ! "$ACTIVE_VENV/bin/pip" --version >/dev/null 2>&1; then
            echo "ERROR: Active venv console command (pip) failed execution at $ACTIVE_VENV/bin/pip" >&2
            exit 1
        fi
    fi
fi

echo "Checking out $TARGET_TAG in main repository..."
if [ "${MOCK_FAIL_CHECKOUT:-0}" = "1" ]; then
    echo "MOCK ERROR: Simulated git checkout failure" >&2
    exit 1
fi
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

# 4. Update container images cache on control machine (local host only)
if [ "${MOCK_BUILD_IMAGES:-0}" != "1" ] && [ -f "$REPO_DIR/backend/hoplyra/images/build-all.sh" ]; then
    echo "Updating container images cache on control machine..."
    (cd "$REPO_DIR/backend" && bash hoplyra/images/build-all.sh) || {
        echo "WARNING: Failed to build/update container images, continuing..."
    }
fi

# 5. Restart service and verify health
if [ "${MOCK_SERVICE_RESTART:-0}" != "1" ]; then
    echo "Restarting hoplyra service..."
    systemctl restart hoplyra
fi

# Health check probe using actual scheme and port
echo "Verifying service health at ${HEALTH_SCHEME}://127.0.0.1:${HEALTH_PORT}..."
sleep "$HEALTH_CHECK_SLEEP"
HEALTH_OK=false

for i in $(seq 1 "$HEALTH_CHECK_RETRIES"); do
    if check_service_health; then
        HEALTH_OK=true
        break
    fi
    sleep "$HEALTH_CHECK_SLEEP"
done

if [ "$HEALTH_OK" != "true" ]; then
    echo "ERROR: Service failed health check on ${HEALTH_SCHEME}://127.0.0.1:${HEALTH_PORT}! Initiating rollback..."
    exit 1
fi

SUCCESS=true
echo "=== Hoplyra successfully updated to $TARGET_TAG at $(date -u) ==="
