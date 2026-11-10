"""Run management commands with the SAME explicit env file as Gunicorn, without echoing secrets."""
import argparse
import os
import sys
from pathlib import Path
from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    env_file = Path(args.env_file).resolve()
    if not env_file.is_file() or not args.command:
        parser.error('An existing environment file and a management command are required.')
    for key, value in dotenv_values(env_file).items():
        if key.lower() not in {'username', 'password'} and value is not None:
            os.environ[key] = value
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    from django.core.management import execute_from_command_line
    execute_from_command_line(['manage.py', *args.command])


if __name__ == '__main__':
    main()
