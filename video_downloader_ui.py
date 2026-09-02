from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import app_en
import main

APP_VERSION = "0.3.6"
MAX_VIDEO_LINKS = 10


def video_link_key(url: str) -> str:
    """Normalize direct TikTok links enough to catch accidental duplicates."""
    value = url.strip()
    direct = re.search(
        r"https?://(?:www\.)?tiktok\.com/@([^/?#]+)/video/(\d+)",
        value,
        re.I,
    )
    if direct:
        return f"tiktok:{direct.group(1).lower()}:{direct.group(2)}"
    return value.rstrip("/").lower()


class _ChildEventProxy:
    def __init__(self, owner: "MultiVideoWorker", index: int, total: int) -> None:
        self.owner = owner
        self.index = index
        self.total = total
        self.result_kind: str | None = None
        self.result_payload: dict = {}

    def put(self, event) -> None:
        kind, payload = event
        payload = dict(payload or {})

        if kind in {"done", "error", "cancelled"}:
            self.result_kind = kind
            self.result_payload = payload
            return

        if kind == "status":
            text = payload.get("text", "")
            payload["text"] = f"Video {self.index}/{self.total}: {text}" if text else f"Video {self.index}/{self.total}"
        elif kind == "progress":
            payload["completed"] = self.owner.saved

        self.owner.events.put((kind, payload))


class MultiVideoWorker(threading.Thread):
    def __init__(
        self,
        video_urls: list[str],
        output_dir: Path,
        events: queue.Queue,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self.video_urls = video_urls
        self.output_dir = output_dir
        self.events = events
        self.stop_event = stop_event
        self.saved = 0
        self.skipped = 0
        self.failed = 0
        self.failures: list[str] = []

    def emit(self, kind: str, **payload) -> None:
        self.events.put((kind, payload))

    def run(self) -> None:
        total = len(self.video_urls)
        try:
            for index, url in enumerate(self.video_urls, start=1):
                if self.stop_event.is_set():
                    self.emit("cancelled", completed=self.saved)
                    return

                self.emit("status", text=f"Preparing video {index}/{total}…", detail=url)

                proxy = _ChildEventProxy(self, index, total)
                worker = app_en.SingleVideoWorker(url, self.output_dir, proxy, self.stop_event)
                worker.run()

                if proxy.result_kind == "cancelled" or self.stop_event.is_set():
                    self.emit("cancelled", completed=self.saved)
                    return

                if proxy.result_kind == "done":
                    completed = int(proxy.result_payload.get("completed") or 0)
                    detail = str(proxy.result_payload.get("detail") or "")
                    if completed > 0:
                        self.saved += completed
                    elif detail.lower().startswith("already downloaded"):
                        self.skipped += 1
                    else:
                        self.skipped += 1
                else:
                    self.failed += 1
                    error_text = str(proxy.result_payload.get("text") or "Unknown error")
                    self.failures.append(f"{index}. {error_text}")
                    self.emit(
                        "warning",
                        text=f"Video {index}/{total} failed. Continuing with the next link…",
                    )

            summary = f"Downloaded: {self.saved} | Already downloaded/skipped: {self.skipped} | Failed: {self.failed}"
            self.emit("done", completed=self.saved, detail=summary)
        except main.UserCancelled:
            self.emit("cancelled", completed=self.saved)
        except Exception as exc:
            self.emit("error", text=str(exc))


class TikTokSaveApp(app_en.TikTokSaveEnglish):
    def __init__(self) -> None:
        self.video_url_vars: list[tk.StringVar] = []
        self.video_rows: list[ttk.Frame] = []
        self.video_rows_frame: ttk.Frame | None = None
        self.add_video_btn: ttk.Button | None = None
        self.link_counter_var: tk.StringVar | None = None
        super().__init__()
        self.geometry("860x820")
        self.minsize(780, 720)

    def _build_ui(self) -> None:
        self.link_counter_var = tk.StringVar(master=self, value="1 / 10 links")
        super()._build_ui()
        self.notebook.tab(self.single_tab, text="Video Downloader")

    def _build_profile_tab(self) -> None:
        super()._build_profile_tab()
        ttk.Label(
            self.profile_tab,
            text="Only public TikTok profiles are supported. Private profiles cannot be downloaded.",
            foreground="#666666",
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _build_single_tab(self) -> None:
        tab = self.single_tab
        tab.columnconfigure(0, weight=1)

        links = ttk.LabelFrame(tab, text="TikTok video links", padding=10)
        links.grid(row=0, column=0, sticky="ew")
        links.columnconfigure(0, weight=1)

        self.video_rows_frame = ttk.Frame(links)
        self.video_rows_frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.video_rows_frame.columnconfigure(0, weight=1)

        controls = ttk.Frame(links)
        controls.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        controls.columnconfigure(1, weight=1)
        self.add_video_btn = ttk.Button(controls, text="+ Add another video", command=self._add_video_row)
        self.add_video_btn.grid(row=0, column=0, sticky="w")
        ttk.Label(controls, textvariable=self.link_counter_var, foreground="#666666").grid(row=0, column=2, sticky="e")

        ttk.Label(
            links,
            text="Add up to 10 TikTok video links. Links can be from different public accounts.",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(
            links,
            text="Only public TikTok videos are supported. Content from private accounts cannot be downloaded.",
            foreground="#666666",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self._add_video_row()

    def _add_video_row(self) -> None:
        if len(self.video_url_vars) >= MAX_VIDEO_LINKS or self.video_rows_frame is None:
            return

        variable = tk.StringVar(master=self)
        row = ttk.Frame(self.video_rows_frame)
        row.grid(row=len(self.video_rows), column=0, sticky="ew", pady=(0 if not self.video_rows else 6, 0))
        row.columnconfigure(1, weight=1)

        number_label = ttk.Label(row, text=f"Video {len(self.video_rows) + 1}", width=8)
        number_label.grid(row=0, column=0, sticky="w", padx=(0, 6))

        entry = ttk.Entry(row, textvariable=variable)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self._bind_paste(entry, variable)

        ttk.Button(row, text="Paste", command=lambda e=entry, v=variable: self._paste(e, v)).grid(row=0, column=2, padx=(0, 6))
        remove_btn = ttk.Button(row, text="×", width=3, command=lambda r=row: self._remove_video_row(r))
        remove_btn.grid(row=0, column=3)

        row._number_label = number_label  # type: ignore[attr-defined]
        row._remove_btn = remove_btn  # type: ignore[attr-defined]
        row._variable = variable  # type: ignore[attr-defined]

        self.video_rows.append(row)
        self.video_url_vars.append(variable)
        self._refresh_video_rows()
        entry.focus_set()

    def _remove_video_row(self, row: ttk.Frame) -> None:
        if len(self.video_rows) <= 1:
            return
        try:
            index = self.video_rows.index(row)
        except ValueError:
            return
        self.video_rows.pop(index)
        self.video_url_vars.pop(index)
        row.destroy()
        self._refresh_video_rows()

    def _refresh_video_rows(self) -> None:
        total = len(self.video_rows)
        for index, row in enumerate(self.video_rows, start=1):
            row.grid_configure(row=index - 1)
            row._number_label.configure(text=f"Video {index}")  # type: ignore[attr-defined]
            row._remove_btn.configure(state="normal" if total > 1 else "disabled")  # type: ignore[attr-defined]

        if self.link_counter_var is not None:
            self.link_counter_var.set(f"{total} / {MAX_VIDEO_LINKS} links")
        if self.add_video_btn is not None:
            self.add_video_btn.configure(state="disabled" if total >= MAX_VIDEO_LINKS else "normal")

    def _get_video_links(self) -> list[str]:
        raw = [variable.get().strip() for variable in self.video_url_vars]
        raw = [value for value in raw if value]
        if not raw:
            raise ValueError("Paste at least one TikTok video link.")

        result: list[str] = []
        seen: set[str] = set()
        for value in raw:
            normalized = app_en.normalize_single_video_url(value)
            key = video_link_key(normalized)
            if key in seen:
                continue
            seen.add(key)
            result.append(normalized)

        if not result:
            raise ValueError("Paste at least one TikTok video link.")
        return result

    def _start(self) -> None:
        if self.notebook.index(self.notebook.select()) == 0:
            super()._start()
            return

        if self.worker and self.worker.is_alive():
            return

        folder_text = self.folder_var.get().strip()
        if not folder_text:
            messagebox.showerror(app_en.APP_TITLE, "Choose a folder for downloaded videos.")
            return

        try:
            urls = self._get_video_links()
        except ValueError as exc:
            messagebox.showerror(app_en.APP_TITLE, str(exc))
            return

        self.stop_event.clear()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set("Starting video queue…")
        self.detail_var.set(f"Unique links: {len(urls)}")
        self.download_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        self.worker = MultiVideoWorker(urls, Path(folder_text), self.events, self.stop_event)
        self.worker.start()


def run() -> None:
    app_en.APP_VERSION = APP_VERSION
    main.APP_VERSION = APP_VERSION
    TikTokSaveApp().mainloop()


if __name__ == "__main__":
    run()
