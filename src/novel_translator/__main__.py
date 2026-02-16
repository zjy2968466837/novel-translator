"""允许通过 python -m novel_translator 启动"""

import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "gui":
        from novel_translator.gui import run_gui
        run_gui()
    else:
        from novel_translator.cli import main as cli_main
        cli_main()


if __name__ == "__main__":
    main()
