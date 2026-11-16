"""mail2cal command line."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from . import __version__
from .core import DEFAULT_CONFIG, Store, extract, get_tz, home_dir, load_config, run


def _services():
    from .google_api import Calendar, Gmail, get_credentials
    creds = get_credentials(home_dir())
    return Gmail(creds), Calendar(creds)


def cmd_init(args):
    home = home_dir()
    cfg = home / "config.yaml"
    if cfg.exists():
        print(f"Config already exists: {cfg}")
    else:
        cfg.write_text(DEFAULT_CONFIG, encoding="utf-8")
        print(f"Wrote {cfg}")
    if not (home / "credentials.json").exists():
        print(f"Next: save your Google OAuth client file as {home / 'credentials.json'} "
              "(see README, 'Google setup'), then run: mail2cal auth")


def cmd_auth(args):
    gmail, _ = _services()
    print(f"Signed in. Found {len(gmail.labels())} Gmail labels. Try: mail2cal run --dry-run")


def cmd_labels(args):
    gmail, _ = _services()
    for name in sorted(gmail.labels().values(), key=str.lower):
        print(name)


def cmd_run(args):
    cfg = load_config()
    if args.days:
        cfg["scan"]["query"] = f"newer_than:{args.days}d -in:spam -in:trash -in:sent -in:drafts"
    gmail, cal = _services()
    n = run(cfg, gmail, cal, Store(home_dir() / "state.db"), dry_run=args.dry_run)
    print(f"{n} deadline(s) {'found' if args.dry_run else 'added to your calendar'}.")


def cmd_watch(args):
    print(f"Checking every {args.interval} min. Ctrl+C to stop.")
    while True:
        try:
            cfg = load_config()                       # picks up config edits
            gmail, cal = _services()
            n = run(cfg, gmail, cal, Store(home_dir() / "state.db"))
            if n:
                print(f"{datetime.now():%H:%M} added {n} deadline(s)")
        except KeyboardInterrupt:
            raise
        except Exception as e:                        # network blips shouldn't kill the loop
            print(f"{datetime.now():%H:%M} error: {e}", file=sys.stderr)
        time.sleep(args.interval * 60)


def cmd_test(args):
    """Try the detector on some text without touching Gmail."""
    from types import SimpleNamespace
    cfg = load_config()
    text = " ".join(args.text) if args.text else sys.stdin.read()
    mail = SimpleNamespace(subject="", body=text, received=datetime.now(get_tz(cfg)))
    found = extract(mail, cfg)
    if not found:
        print("No deadline found.")
    for d in found:
        print(f"{d.due:%a %d %b %Y %H:%M}  {'all-day' if d.all_day else 'timed  '}  "
              f"confidence {d.confidence:.2f}  <- {d.evidence[:80]}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="mail2cal",
                                description="Turn deadlines in your Gmail into Google Calendar events.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the config file").set_defaults(fn=cmd_init)
    sub.add_parser("auth", help="sign in to Google").set_defaults(fn=cmd_auth)
    sub.add_parser("labels", help="list your Gmail labels").set_defaults(fn=cmd_labels)
    r = sub.add_parser("run", help="check mail once")
    r.add_argument("--dry-run", action="store_true", help="show what would be added, change nothing")
    r.add_argument("--days", type=int, help="look back this many days instead of the configured query")
    r.set_defaults(fn=cmd_run)
    w = sub.add_parser("watch", help="keep running, checking every N minutes")
    w.add_argument("--interval", type=int, default=15, metavar="MIN")
    w.set_defaults(fn=cmd_watch)
    t = sub.add_parser("test", help="try the deadline detector on text")
    t.add_argument("text", nargs="*")
    t.set_defaults(fn=cmd_test)
    args = p.parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
