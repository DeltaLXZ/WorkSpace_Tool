"""Tkinter front end. Wraps the same pipeline the CLI uses.

Dropping a folder onto the built .exe in Explorer passes it as an argument, which is
why no third-party drag-and-drop library is required. If ``tkinterdnd2`` happens to be
installed, in-window dropping is enabled as a bonus.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .config import load_settings
from .constants import VERDICT_FAIL, VERDICT_READY, VERDICT_WARN
from .models import verdict_code
from .pipeline import run_audit
from .report_json import write_summary
from .report_xlsx import default_workbook_name, write_workbook
from .version import __product__, __version__

_BANNER_COLORS = {
    VERDICT_READY: ("#E2EFDA", "#375623"),
    VERDICT_WARN: ("#FFF2CC", "#833C00"),
    VERDICT_FAIL: ("#FBE4E4", "#C0392B"),
}

AUTO_PRODUCT = "(automatic - newest installed)"


def describe_roles(files: list[str]) -> str:
    """Report which export roles a set of files provides, so the picker is self-checking."""
    from .constants import ALL_ROLES
    from .detect import classify

    found = {role for role, _ in (classify(f) for f in files) if role}
    if not found:
        return "none recognised - expected FD/FS/ET .xml or a Level .csv"
    missing = [r for r in ALL_ROLES if r not in found]
    text = ", ".join(r for r in ALL_ROLES if r in found)
    return text + (f"   (missing: {', '.join(missing)})" if missing else "   (complete set)")


class CheckerWindow:
    def __init__(self, initial: list[str] | None = None):
        self.root = tk.Tk()
        self.root.title(f"{__product__} {__version__}")
        self.root.geometry("880x600")
        self.root.minsize(720, 480)

        self.inputs: list[str] = list(initial or [])
        self.out_dir = tk.StringVar(value=str(Path.cwd() / "output"))
        self.extract = tk.BooleanVar(value=True)
        self.product = tk.StringVar(value=AUTO_PRODUCT)
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.written: list[Path] = []
        self.exit_code = 0

        self._build()
        self._enable_dnd()
        self._refresh_inputs()
        self.root.after(100, self._drain)

    # -- layout ------------------------------------------------------------- #
    def _build(self) -> None:
        header = tk.Frame(self.root, bg="#1F3864", height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header,
            text="Workspace Standards Health Check",
            bg="#1F3864", fg="white",
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left", padx=16)

        body = tk.Frame(self.root, padx=14, pady=12)
        body.pack(fill="both", expand=True)

        self.drop = tk.Label(
            body,
            text="Drop a workspace folder onto the application icon,\nor use the buttons below.",
            relief="ridge", bd=2, height=4, fg="#44546A",
            font=("Segoe UI", 10),
        )
        self.drop.pack(fill="x")

        buttons = tk.Frame(body, pady=8)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Add workspace folder...", command=self._pick_folder).pack(side="left")
        ttk.Button(
            buttons, text="Add exported FD / FS / ET / Level files...", command=self._pick_files
        ).pack(side="left", padx=6)
        ttk.Button(buttons, text="Clear", command=self._clear).pack(side="left")
        ttk.Checkbutton(
            buttons, text="Extract from DGNLIBs", variable=self.extract
        ).pack(side="right")

        tk.Label(
            body,
            justify="left",
            fg="#595959",
            font=("Segoe UI", 8),
            text=(
                "Workspace folder: scans the whole tree - configuration, DGNLIBs, cells, seeds "
                "- and picks up any exports already inside it.\n"
                "Exported files: add FD / FS / ET .xml and the Level .csv you exported from "
                "OpenRoads or OpenBridge when they live outside the workspace. Roles are "
                "detected from the file, not the name."
            ),
        ).pack(fill="x", pady=(0, 6))

        out_row = tk.Frame(body)
        out_row.pack(fill="x", pady=(0, 8))
        tk.Label(out_row, text="Output folder:").pack(side="left")
        ttk.Entry(out_row, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(out_row, text="...", width=3, command=self._pick_out).pack(side="left")

        product_row = tk.Frame(body)
        product_row.pack(fill="x", pady=(0, 8))
        tk.Label(product_row, text="Bentley product:").pack(side="left")
        self.product_box = ttk.Combobox(
            product_row, textvariable=self.product, state="readonly",
            values=self._product_choices(),
        )
        self.product_box.pack(side="left", fill="x", expand=True, padx=6)

        self.banner = tk.Label(body, text="Ready", height=2, font=("Segoe UI", 12, "bold"),
                               bg="#F2F2F2", fg="#808080")
        self.banner.pack(fill="x", pady=(0, 8))

        self.log = tk.Text(body, height=14, wrap="none", font=("Consolas", 9),
                           bg="#FAFAFA", relief="solid", bd=1)
        self.log.pack(fill="both", expand=True)

        footer = tk.Frame(self.root, padx=14, pady=10)
        footer.pack(fill="x")
        self.run_button = ttk.Button(footer, text="Run check", command=self._run)
        self.run_button.pack(side="left")
        self.open_button = ttk.Button(footer, text="Open report", command=self._open_report,
                                      state="disabled")
        self.open_button.pack(side="left", padx=6)
        ttk.Button(footer, text="Close", command=self.root.destroy).pack(side="right")

    def _product_choices(self) -> list[str]:
        try:
            from .extract.locator import find_products

            return [AUTO_PRODUCT] + [p.label for p in find_products()]
        except Exception:  # noqa: BLE001 - discovery must never block the window
            return [AUTO_PRODUCT]

    def _enable_dnd(self) -> None:
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
        except ImportError:
            return
        try:
            self.root.tk.call("package", "require", "tkdnd")
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self._on_drop)
            _ = TkinterDnD
        except tk.TclError:
            pass

    def _on_drop(self, event) -> None:
        for token in self.root.tk.splitlist(event.data):
            if os.path.exists(token):
                self.inputs.append(token)
        self._refresh_inputs()

    # -- actions -------------------------------------------------------------- #
    def _pick_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Select a workspace folder")
        if chosen:
            self.inputs.append(chosen)
            self._refresh_inputs()

    def _pick_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            title="Select exported FD / FS / ET .xml and Level .csv files",
            filetypes=[
                ("Standards exports", "*.xml *.csv"),
                ("Feature / symbology / template XML", "*.xml"),
                ("Level CSV", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        self.inputs.extend(chosen)
        self._refresh_inputs()

    def _pick_out(self) -> None:
        chosen = filedialog.askdirectory(title="Select an output folder")
        if chosen:
            self.out_dir.set(chosen)

    def _clear(self) -> None:
        self.inputs.clear()
        self.written.clear()
        self.open_button.config(state="disabled")
        self.log.delete("1.0", "end")
        self._set_banner("Ready", "#F2F2F2", "#808080")
        self._refresh_inputs()

    def _refresh_inputs(self) -> None:
        if not self.inputs:
            self.drop.config(
                text="Drop a workspace folder onto the application icon,\n"
                     "or use the buttons below."
            )
            return

        folders = [p for p in self.inputs if Path(p).is_dir()]
        files = [p for p in self.inputs if Path(p).is_file()]
        lines = [f"{len(folders)} folder(s), {len(files)} file(s) selected"]
        lines += [f"  {Path(p).name}" for p in folders[:2]]

        if files:
            roles = describe_roles(files)
            lines.append(f"  exports detected: {roles}")
        self.drop.config(text="\n".join(lines))

    def _set_banner(self, text: str, bg: str, fg: str) -> None:
        self.banner.config(text=text, bg=bg, fg=fg)

    def _write(self, message: str) -> None:
        self.log.insert("end", message + "\n")
        self.log.see("end")

    def _run(self) -> None:
        if not self.inputs:
            self._write("Select a workspace folder or export files first.")
            return
        self.run_button.config(state="disabled")
        self.open_button.config(state="disabled")
        self.log.delete("1.0", "end")
        self._set_banner("Working...", "#D9E1F2", "#1F3864")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            settings = load_settings()
            out_dir = Path(self.out_dir.get())
            chosen = self.product.get()
            result = run_audit(
                inputs=list(self.inputs),
                settings=settings,
                extract=self.extract.get(),
                product=None if chosen == AUTO_PRODUCT else chosen,
                out_dir=out_dir,
                progress=lambda m: self.messages.put(("log", m)),
            )
            written = [write_summary(result, out_dir / f"{result.tag}_health.json")]
            written.append(
                write_workbook(result, out_dir / default_workbook_name(result.tag), settings)
            )
            self.messages.put(("done", (result, written)))
        except Exception as exc:  # noqa: BLE001 - surfaced in the window
            self.messages.put(("error", str(exc)))

    def _drain(self) -> None:
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._write(str(payload))
            elif kind == "error":
                self._write(f"ERROR: {payload}")
                self._set_banner("Failed", "#FBE4E4", "#C0392B")
                self.run_button.config(state="normal")
                self.exit_code = 4
            elif kind == "done":
                result, written = payload
                self.written = written
                counts = result.issue_counts()
                self._write("")
                for path in written:
                    self._write(f"wrote {path}")
                bg, fg = _BANNER_COLORS.get(result.verdict, ("#F2F2F2", "#808080"))
                self._set_banner(
                    f"{result.tag}: {result.verdict}  -  "
                    f"{counts['fail']} fail, {counts['warn']} warn",
                    bg, fg,
                )
                self.run_button.config(state="normal")
                self.open_button.config(state="normal")
                self.exit_code = verdict_code(result.verdict)
        self.root.after(150, self._drain)

    def _open_report(self) -> None:
        for path in self.written:
            if path.suffix == ".xlsx":
                try:
                    os.startfile(str(path))  # noqa: S606 - opening the report we just wrote
                except (AttributeError, OSError):
                    subprocess.run(["cmd", "/c", "start", "", str(path)], check=False)
                return

    def run(self) -> int:
        self.root.mainloop()
        return self.exit_code


def _hide_console() -> None:
    """Hide the console the frozen build owns, so the window looks native."""
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetConsoleWindow()
        if handle:
            ctypes.windll.user32.ShowWindow(handle, 0)
    except (AttributeError, OSError):
        pass


def launch(initial: list[str] | None = None) -> int:
    _hide_console()
    try:
        return CheckerWindow(initial).run()
    except tk.TclError as exc:
        print(f"Could not open a window ({exc}). Run with --help for command line use.",
              file=sys.stderr)
        return 4
