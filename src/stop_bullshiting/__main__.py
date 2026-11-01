import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "hook" and sys.argv[2] in ("claude", "codex"):
        # Runs on every tool call: skip the CLI's imports, and never exit non-zero.
        from .hooks import hook_main
        raise SystemExit(hook_main(sys.argv[2]))
    from .cli import main
    raise SystemExit(main())
