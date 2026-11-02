"""PreToolUse guard hook entry point."""

import json
import sys
from pathlib import Path

from ml_referee.config import ConfigError, load_config
from ml_referee.policy import Decision, build_policy, decide


def main() -> int:
    """Read one event from stdin and print the deny decision, if any."""
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict) or not isinstance(event.get("cwd"), str):
            decision = Decision(allow=False, reason="Malformed hook event")
        else:
            workspace = Path(event["cwd"]).resolve()
            config = workspace / ".ml-referee.toml"
            try:
                config.lstat()
            except FileNotFoundError:
                return 0
            decision = decide(event, build_policy(load_config(config), workspace))
    except (ValueError, OSError, RuntimeError, ConfigError) as exc:
        decision = Decision(allow=False, reason=f"Guard cannot validate event: {exc}")
    if not decision.allow:
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": decision.reason,
                    },
                },
            )
            + "\n",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
