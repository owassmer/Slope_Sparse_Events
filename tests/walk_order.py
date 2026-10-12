"""Compatibility entry point for the independent production walk checks."""
import sys

from app.analysis.walk_checks import Checker, FutureChecker, State, check, main  # noqa: F401

if __name__ == '__main__':
    args = sys.argv[2:]
    main(sys.argv[1], saved=next((a.split('=', 1)[1] for a in args if a.startswith('--saved=')), None),
         limit=next((int(a.split('=')[1]) for a in args if a.startswith('--limit=')), None))
