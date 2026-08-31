#!/usr/bin/env python3
"""Claude Code PreToolUse guard — blocks system-modifying Bash commands.

The Console's Claude CLI runs headless (`--print --permission-mode dontAsk`), so it
cannot ask for interactive approval and would otherwise execute write operations
autonomously (the reported incident: a `systemctl restart` on a production DB host
via SSH). This hook enforces read-only-by-default at the tool layer.

Policy — DENY when:
  * a system-level operation appears (service control, package mgmt, reboot,
    dd/mkfs, iptables, user mgmt, docker/kubectl mutations, git push, …) — anywhere,
    including inside `ssh host '…'`; OR
  * a file-modifying operation (rm/mv/cp/chmod/chown/sed -i/tee/`>` redirect/…)
    targets a REMOTE host (ssh/scp/rsync present) or a SYSTEM path (/etc, /var, …).
ALLOW: read-only diagnostics (df, cat, journalctl, `systemctl status`, docker ps,
ssh '<read cmd>') and local file writes inside the workspace (/home/yolo/workspaces,
/tmp, relative paths) so the agent can still save reports/scripts.

Wired via managed-settings.json → hooks.PreToolUse (matcher "Bash"), admin-scoped.
"""
import json
import re
import sys
import time

# System-level, always destructive — block regardless of path/target.
_SYSTEM_OP = re.compile(
    r"""(?ix)
    \b(systemctl|service)\s+\S*\s*(restart|stop|start|reload|enable|disable|mask|unmask|kill)\b
    | \b(reboot|shutdown|halt|poweroff|init)\b
    | \b(apt|apt-get|aptitude|yum|dnf|zypper|snap)\s+(install|remove|purge|upgrade|autoremove|dist-upgrade)\b
    | \b(pip3?|npm|yarn|pnpm|gem|cargo)\s+(install|uninstall|add|remove)\b
    | \bdocker\s+(run|rm|stop|start|restart|kill|rmi|pull|push|compose)\b
    | \bkubectl\s+(apply|delete|edit|scale|patch|create|replace|rollout|cordon|drain)\b
    | \bgit\s+(push|reset\s+--hard|clean)\b
    | \b(useradd|userdel|usermod|groupadd|passwd|chpasswd)\b
    | \b(iptables|nft|ufw|firewall-cmd)\b
    | \b(mount|umount|swapoff|swapon|mkfs\w*|mkswap|fdisk|parted|dd)\b
    | \bsysctl\s+-w\b
    | \b(kill|pkill|killall)\b
    | \bcrontab\b
    """,
    re.VERBOSE,
)

# File-modifying operations — dangerous only against remote hosts or system paths.
_FILE_WRITE = re.compile(
    r"""(?ix)
    \b(rm|rmdir|shred|unlink|truncate|mv|cp|chmod|chown|chgrp|ln|tee)\b
    | \bsed\s+-i | \bperl\s+-i | \bawk\s+-i\b
    | \b(nano|vim?|vi|emacs)\b
    | >>?\s*(?!(&\s*\d|/dev/null|/dev/stderr|/dev/stdout))\S
    """,
    re.VERBOSE,
)

_REMOTE = re.compile(r"(?ix)\b(ssh|scp|rsync|sshpass)\b")
_SYSTEM_PATH = re.compile(
    r"(?i)(^|[\s'\":=])/(etc|var|usr|boot|root|bin|sbin|lib|lib64|sys|proc|opt|srv|run)(/|\b)"
)


def _is_write(cmd: str) -> bool:
    if _SYSTEM_OP.search(cmd):
        return True
    if _FILE_WRITE.search(cmd):
        # Remote target (production) or a local system path → block.
        if _REMOTE.search(cmd) or _SYSTEM_PATH.search(cmd):
            return True
    return False


#: Where the backend records a user-granted, time-limited write approval. It lives in
#: /opt because that directory is root-owned and the agent runs as yolo — the agent
#: therefore cannot forge its own approval, which is the whole point: consent has to
#: come from the human through the CentralStation UI, not from the conversation the
#: agent itself controls.
APPROVAL_FILE = "/opt/cs-write-approval.json"


def _approval() -> dict | None:
    """Return the active write approval, or None when absent/expired/unreadable."""
    try:
        with open(APPROVAL_FILE) as fh:
            data = json.load(fh)
        if float(data.get("expires_at", 0)) > time.time():
            return data
    except Exception:
        pass
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # unreadable input → do not interfere
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    if _is_write(cmd):
        granted = _approval()
        if granted:
            # The user opened a write window in the CentralStation console. Let the
            # command through and leave a trace of whose approval was used.
            sys.stderr.write(
                "cs-readonly-guard: Schreibfreigabe aktiv (erteilt von "
                f"{granted.get('granted_by', '?')}, gültig bis "
                f"{granted.get('expires_at_iso', '?')}) — Befehl zugelassen.\n"
            )
            sys.exit(0)
        reason = (
            "READ-ONLY-MODUS: Dieser Befehl verändert ein System "
            "(Schreiboperation, evtl. auf einem Produktionssystem via SSH) und wurde "
            "BLOCKIERT. Wichtig: eine Zustimmung im Chat hebt diese Sperre NICHT auf "
            "— der Hook sieht die Konversation nicht, und du kannst ihn nicht "
            "umgehen. Beschreibe dem Nutzer die geplante Änderung (genauer Befehl + "
            "Zielsystem + Wirkung) und bitte ihn, in der CentralStation-Konsole "
            "\"Schreibzugriff freigeben\" zu klicken; danach ist der Befehl für die "
            "Dauer des Zeitfensters erlaubt und du kannst ihn erneut ausführen. "
            "Reine Lese-Diagnose und lokale Workspace-Dateien sind immer erlaubt."
        )
        # Block via BOTH mechanisms for cross-version compatibility:
        #  - JSON permissionDecision "deny" (newer Claude Code), and
        #  - exit code 2 with the reason on stderr (classic PreToolUse block).
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }))
        sys.stderr.write(reason + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
