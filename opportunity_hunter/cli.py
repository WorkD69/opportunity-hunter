"""Local inspection/build commands. No deployment, secrets or Telegram sending."""
import argparse
import json
from pathlib import Path

from .core import Store
from .packaging import build_profile
from .runtime import format_card


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'init', 'profile', 'health', 'list', 'preview'])
    parser.add_argument('--home', type=Path, default=Path('runtime/opportunity-hunter'))
    parser.add_argument('--output', type=Path, default=Path('build/opportunity-hunter'))
    parser.add_argument('--category', choices=list('ABCD'))
    parser.add_argument('--saved', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'build':
        print(str(build_profile(args.output)))
        return
    if args.home.resolve().name != 'opportunity-hunter':
        parser.error('Home must be the isolated opportunity-hunter directory')
    store = Store(args.home / 'opportunities.db')
    if args.command in ('init', 'health'):
        result = store.stats()
    elif args.command == 'profile':
        result = store.profile()
    else:
        result = store.list(category=args.category, bookmarked=args.saved)
        if args.command == 'preview':
            print('\n\n'.join(format_card(item) for item in result) or 'No opportunities stored.')
            return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
