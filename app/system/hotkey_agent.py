"""Standalone global-hotkey listener process.

pynput's X11 record-extension thread is known to segfault occasionally;
running it in this separate process keeps the main app alive. Prints a
line to stdout whenever the hotkey fires; the app restarts the agent if
it dies.

Usage: hotkey_agent.py [<pynput-hotkey>]   e.g. '<ctrl>+<shift>+v'
"""
import sys

DEFAULT_HOTKEY = '<ctrl>+<shift>+v'


def main():
    from pynput import keyboard

    hotkey = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOTKEY

    def on_hotkey():
        print("HOTKEY", flush=True)

    try:
        hotkeys = keyboard.GlobalHotKeys({hotkey: on_hotkey})
    except ValueError:
        # invalid combo from settings — stay alive on the default instead
        # of dying and being restarted in a loop
        print(f"Invalid hotkey {hotkey!r}, using {DEFAULT_HOTKEY}",
              file=sys.stderr, flush=True)
        hotkeys = keyboard.GlobalHotKeys({DEFAULT_HOTKEY: on_hotkey})

    with hotkeys as listener:
        listener.join()


if __name__ == "__main__":
    sys.exit(main())
