"""
Заглушка stop для JupyterLab (ключ jupyter в multi-terminal).

Процесс JupyterLab завершается вместе с терминалом; отдельная остановка не нужна.
"""

from __future__ import annotations


def main() -> int:
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
