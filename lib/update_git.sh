if ! is_checkpoint_done "update_git_synced"; then


    # ─── Git Stash + Pull (CRITICAL BLINDSPOT FIX) ───────────────────────────
    echo -e "${BLUE}  → Checking for local changes...${NC}"
    # Save pre-update HEAD for reliable redeploy detection after git operations.
    # Priority: 1) env var from re-exec (survives exec boundary),
    #           2) stale file from failed previous update (survives process death),
    #           3) current HEAD (normal first run).
    PRE_UPDATE_HEAD=""
    if [ -n "${SMSLY_PRE_UPDATE_HEAD:-}" ]; then
        PRE_UPDATE_HEAD="$SMSLY_PRE_UPDATE_HEAD"
    elif [ -f "$INSTALL_DIR/.pre-update-head" ] && [ -s "$INSTALL_DIR/.pre-update-head" ]; then
        PRE_UPDATE_HEAD="$(cat "$INSTALL_DIR/.pre-update-head"  || true)"
        echo -e "${YELLOW}  ⚠ Recovering pre-update baseline from prior incomplete run (${PRE_UPDATE_HEAD:0:7})${NC}"
    else
        PRE_UPDATE_HEAD="$(git rev-parse HEAD  || true)"
    fi
    echo "$PRE_UPDATE_HEAD" > "$INSTALL_DIR/.pre-update-head"  || true
    ensure_local_ignores
    if [ -n "$(git status --porcelain )" ]; then
        echo -e "${YELLOW}  ⚠ Local changes detected — stashing before pull${NC}"
        git stash push --include-untracked -m "install-update-$(date +%s)"
        touch "$INSTALL_DIR/.git-stash-marker"
    fi

    echo -e "${BLUE}  → Force-pulling latest code from GitHub ($SMSLY_BRANCH)...${NC}"

    # Track if git update succeeded
    GIT_UPDATE_OK=true

    if ! git fetch origin "$SMSLY_BRANCH" ; then
        echo -e "${RED}  ✗ Git fetch failed for $SMSLY_BRANCH. SSL verification is always enforced — check network or CA certificates.${NC}"
        GIT_UPDATE_OK=false
    fi

    if [ "$GIT_UPDATE_OK" = "true" ]; then
        if ! git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" ; then
            echo -e "${RED}  ✗ Git checkout failed for $SMSLY_BRANCH.${NC}"
            GIT_UPDATE_OK=false
        else
            git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH"  || true
        fi
    fi

    # Fallback if git failed but a local bundle was provided
    if [ "$GIT_UPDATE_OK" = "false" ]; then
        if [ -n "${SMSLY_INSTALL_WORKDIR:-}" ] && [ -d "${SMSLY_INSTALL_WORKDIR}" ]; then
            echo -e "${BLUE}  → Fallback: Synchronizing from pre-uploaded source bundle...${NC}"
            # Use rsync if available, otherwise cp. Exclude .git to preserve local repo state if any.
            if command -v rsync ; then
                rsync -rtv --exclude='.git' "${SMSLY_INSTALL_WORKDIR}/" "$INSTALL_DIR/"
            else
                cp -rv "${SMSLY_INSTALL_WORKDIR}/"* "$INSTALL_DIR/"  || true
            fi
            echo -e "${GREEN}  ✓ Fallback synchronization complete.${NC}"
        else
            echo -e "${RED}✗ Git update failed and no local fallback bundle available. Update may be incomplete.${NC}"
        fi
    fi
    set_checkpoint "update_git_synced"
fi

    # ─── Self-Update Check ──────────────────────────────────────────────────
    # If the installer itself was updated, we MUST re-execute it to pick up
    # new service names (e.g., celery-deploy) and self-healing logic.
    if [[ "${SMSLY_REEXEC:-}" != "1" ]]; then
        echo -e "${GREEN}  → Installer updated. Re-executing for safe synchronization...${NC}"
        export SMSLY_REEXEC=1
        export NO_SCREEN=true
        export SKIP_SCREEN=1
        # Preserve pre-update HEAD across re-exec so the SHA comparison
        # uses the TRUE baseline commit (before git pull), not the
        # already-updated HEAD (which would prevent redeploy detection).
        export SMSLY_PRE_UPDATE_HEAD="$PRE_UPDATE_HEAD"
        # Release the lock before re-exec so the new process can acquire it.
        # Closing FD 9 releases the flock.
        exec 9>&-  || true
        exec env SMSLY_REEXEC=1 NO_SCREEN=true SKIP_SCREEN=1 SMSLY_PRE_UPDATE_HEAD="$PRE_UPDATE_HEAD" PATH="/usr/local/bin:$PATH" bash "$SCRIPT_PATH" --no-screen "$@"
    fi
