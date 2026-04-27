#!/usr/bin/env python
"""Utilitário de linha de comando do Django para tarefas administrativas."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app_relatorios.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django não encontrado. Certifique-se de que está no ambiente virtual correto."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()