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


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # unreadable input → do not interfere
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    if _is_write(cmd):
        reason = (
            "READ-ONLY-MODUS: Dieser Befehl verändert ein System "
            "(Schreiboperation, evtl. auf einem Produktionssystem via SSH). "
            "Führe ihn NICHT aus. Beschreibe dem Nutzer die geplante Änderung "
            "(genauer Befehl + Zielsystem + Wirkung) und frage EXPLIZIT um "
            "Erlaubnis. Erst nach ausdrücklicher Zustimmung des Nutzers in einer "
            "Folgenachricht darf die Operation ausgeführt werden. Reine "
            "Lese-Diagnose und lokale Workspace-Dateien sind erlaubt."
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
