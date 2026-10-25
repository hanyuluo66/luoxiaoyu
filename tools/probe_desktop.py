"""Debug: what can this process see on the desktop?"""
from pywinauto import Desktop

for backend in ("uia", "win32"):
    try:
        wins = Desktop(backend=backend).windows()
        print(f"--- backend={backend}: {len(wins)} windows")
        for w in wins[:40]:
            try:
                print(
                    f"    pid={w.process_id()} class={w.class_name()!r} "
                    f"title={w.window_text()!r}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"    <info failed: {exc}>")
    except Exception as exc:  # noqa: BLE001
        print(f"--- backend={backend} FAILED: {type(exc).__name__}: {exc}")
