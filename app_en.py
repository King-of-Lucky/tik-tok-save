from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import main

APP_TITLE = "TikTok Save"
APP_VERSION = "0.3.6"
AUTHOR = "King of Lucky"


def normalize_single_video_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Paste a TikTok video link.")
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    if "tiktok.com" not in value.lower():
        raise ValueError("This does not look like a TikTok link.")
    return value


def canonical_video_parts(url: str) -> tuple[str, str, str]:
    match = re.search(r"https?://(?:www\.)?tiktok\.com/@([^/?#]+)/video/(\d+)", url, re.I)
    if not match:
        raise ValueError(
            "A direct TikTok video link is required, for example:\n"
            "https://www.tiktok.com/@username/video/1234567890"
        )
    username, video_id = match.group(1), match.group(2)
    canonical = f"https://www.tiktok.com/@{username}/video/{video_id}"
    profile = f"https://www.tiktok.com/@{username}"
    return canonical, profile, video_id


def translate_backend_text(text: str) -> str:
    return text or ""


class SingleVideoWorker(threading.Thread):
    """Single-video worker whose download routine is installed at startup."""

    def __init__(
        self,
        video_url: str,
        output_dir: Path,
        events: queue.Queue,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self.video_url = video_url
        self.output_dir = output_dir
        self.events = events
        self.stop_event = stop_event

    def emit(self, kind: str, **payload) -> None:
        self.events.put((kind, payload))

    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise main.UserCancelled()

    def _resolve_short_link(self, url: str) -> str:
        if re.search(r"/@[^/]+/video/\d+", url):
            return url
        self.emit("status", text="Resolving TikTok share link…", detail=url)
        try:
            import requests

            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
                timeout=20,
                stream=True,
            )
            resolved = response.url
            response.close()
            if "/video/" in resolved:
                return resolved
        except Exception:
            pass
        return url

    def run(self) -> None:
        self.emit(
            "error",
            text="The single-video download engine was not initialized correctly.",
        )


class TikTokSaveEnglish(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} {APP_VERSION}")
        self.geometry("820x760")
        self.minsize(760, 700)

        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None

        self.profile_url_var = tk.StringVar()
        self.single_url_var = tk.StringVar()
        self.folder_var = tk.StringVar(value=str(Path.home() / "Videos" / "TikTok"))
        self.mode_var = tk.StringVar(value="all")
        self.limit_var = tk.StringVar(value="50")
        self.date_from_var = tk.StringVar()
        self.date_to_var = tk.StringVar()
        self.skip_var = tk.BooleanVar(value=True)
        self.date_file_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")
        self.detail_var = tk.StringVar()

        self._build_ui()
        self._sync_mode()
        self.after(100, self._poll)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(7, weight=1)

        ttk.Label(root, text="TikTok Save", font=("Segoe UI", 20, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(root, text="Download public TikTok videos locally to your PC").grid(
            row=1, column=0, sticky="w", pady=(0, 14)
        )

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=2, column=0, sticky="ew")
        self.profile_tab = ttk.Frame(self.notebook, padding=12)
        self.single_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.profile_tab, text="Profile Downloader")
        self.notebook.add(self.single_tab, text="Single Video")
        self._build_profile_tab()
        self._build_single_tab()

        folder = ttk.LabelFrame(root, text="Save to", padding=12)
        folder.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        folder.columnconfigure(0, weight=1)
        ttk.Entry(folder, textvariable=self.folder_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(folder, text="Choose folder", command=self._choose_folder).grid(
            row=0, column=1
        )

        options = ttk.Frame(root)
        options.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(
            options,
            text="Skip already downloaded videos",
            variable=self.skip_var,
        ).pack(anchor="w")
        ttk.Checkbutton(
            options,
            text="Set file date to TikTok publication date",
            variable=self.date_file_var,
        ).pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(root)
        actions.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        actions.columnconfigure(0, weight=1)
        self.download_btn = ttk.Button(actions, text="Download", command=self._start)
        self.download_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.stop_btn = ttk.Button(actions, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.grid(row=0, column=1)

        box = ttk.LabelFrame(root, text="Progress", padding=12)
        box.grid(row=7, column=0, sticky="nsew", pady=(12, 0))
        box.columnconfigure(0, weight=1)
        ttk.Label(box, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.progress = ttk.Progressbar(box, maximum=100)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        ttk.Label(box, textvariable=self.detail_var, wraplength=740).grid(
            row=2, column=0, sticky="nw"
        )

        footer = ttk.Frame(root)
        footer.grid(row=8, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            text="Use only for content you have the right to save.",
            foreground="#666666",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            footer,
            text=f"Created by {AUTHOR}",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=1, sticky="e")

    def _build_profile_tab(self) -> None:
        tab = self.profile_tab
        tab.columnconfigure(0, weight=1)

        link = ttk.LabelFrame(tab, text="TikTok profile", padding=10)
        link.grid(row=0, column=0, sticky="ew")
        link.columnconfigure(0, weight=1)
        entry = ttk.Entry(link, textvariable=self.profile_url_var)
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._bind_paste(entry, self.profile_url_var)
        ttk.Button(
            link,
            text="Paste",
            command=lambda: self._paste(entry, self.profile_url_var),
        ).grid(row=0, column=1)
        ttk.Label(link, text="Paste a profile link, @username, or username.").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(5, 0)
        )

        filters = ttk.LabelFrame(tab, text="What to download", padding=10)
        filters.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        filters.columnconfigure(1, weight=1)
        ttk.Radiobutton(
            filters,
            text="All videos",
            variable=self.mode_var,
            value="all",
            command=self._sync_mode,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(
            filters,
            text="Latest",
            variable=self.mode_var,
            value="limit",
            command=self._sync_mode,
        ).grid(row=1, column=0, sticky="w", pady=(7, 0))
        self.limit_entry = ttk.Entry(filters, textvariable=self.limit_var, width=8)
        self.limit_entry.grid(row=1, column=1, sticky="w", pady=(7, 0))
        ttk.Label(filters, text="videos").grid(row=1, column=2, sticky="w", pady=(7, 0))

        ttk.Radiobutton(
            filters,
            text="Date range",
            variable=self.mode_var,
            value="dates",
            command=self._sync_mode,
        ).grid(row=2, column=0, sticky="w", pady=(7, 0))
        date_row = ttk.Frame(filters)
        date_row.grid(row=2, column=1, columnspan=2, sticky="w", pady=(7, 0))
        ttk.Label(date_row, text="from").pack(side="left")
        self.from_entry = ttk.Entry(date_row, textvariable=self.date_from_var, width=12)
        self.from_entry.pack(side="left", padx=(5, 10))
        ttk.Label(date_row, text="to").pack(side="left")
        self.to_entry = ttk.Entry(date_row, textvariable=self.date_to_var, width=12)
        self.to_entry.pack(side="left", padx=(5, 8))
        ttk.Label(date_row, text="DD.MM.YYYY").pack(side="left")

    def _build_single_tab(self) -> None:
        tab = self.single_tab
        tab.columnconfigure(0, weight=1)
        link = ttk.LabelFrame(tab, text="TikTok video link", padding=10)
        link.grid(row=0, column=0, sticky="ew")
        link.columnconfigure(0, weight=1)
        entry = ttk.Entry(link, textvariable=self.single_url_var)
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._bind_paste(entry, self.single_url_var)
        ttk.Button(
            link,
            text="Paste",
            command=lambda: self._paste(entry, self.single_url_var),
        ).grid(row=0, column=1)
        ttk.Label(
            link,
            text=(
                "Paste a link to one TikTok video. The file will use the video's "
                "caption as its name when available."
            ),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))

    def _bind_paste(self, entry, variable) -> None:
        entry.bind("<Control-v>", lambda e: self._paste_event(entry, variable))
        entry.bind("<Control-V>", lambda e: self._paste_event(entry, variable))
        entry.bind("<Shift-Insert>", lambda e: self._paste_event(entry, variable))
        entry.bind("<Button-3>", lambda e: self._context_menu(e, entry, variable))

    def _paste(self, entry, variable) -> None:
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            messagebox.showinfo(APP_TITLE, "The clipboard does not contain text.")
            return
        variable.set(text)
        entry.icursor("end")
        entry.focus_set()

    def _paste_event(self, entry, variable) -> str:
        self._paste(entry, variable)
        return "break"

    def _context_menu(self, event, entry, variable) -> str:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Paste", command=lambda: self._paste(entry, variable))
        menu.add_command(label="Copy", command=lambda: entry.event_generate("<<Copy>>"))
        menu.add_command(label="Cut", command=lambda: entry.event_generate("<<Cut>>"))
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _choose_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if folder:
            self.folder_var.set(folder)

    def _sync_mode(self) -> None:
        self.limit_entry.configure(state="normal" if self.mode_var.get() == "limit" else "disabled")
        state = "normal" if self.mode_var.get() == "dates" else "disabled"
        self.from_entry.configure(state=state)
        self.to_entry.configure(state=state)

    def _parse_date(self, value: str, label: str) -> str | None:
        from datetime import datetime

        value = value.strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, "%d.%m.%Y").strftime("%Y%m%d")
        except ValueError as exc:
            raise ValueError(f"{label}: use DD.MM.YYYY") from exc

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        folder_text = self.folder_var.get().strip()
        if not folder_text:
            messagebox.showerror(APP_TITLE, "Choose a folder for downloaded videos.")
            return
        folder = Path(folder_text)

        self.stop_event.clear()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set("Starting…")
        self.detail_var.set("")
        self.download_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        try:
            if self.notebook.index(self.notebook.select()) == 0:
                profile_url = main.normalize_profile_url(self.profile_url_var.get())
                self.profile_url_var.set(profile_url)
                mode = self.mode_var.get()
                limit = None
                date_from = date_to = None

                if mode == "limit":
                    try:
                        limit = int(self.limit_var.get())
                    except ValueError as exc:
                        raise ValueError("The number of videos must be a whole number.") from exc
                    if limit <= 0:
                        raise ValueError("The number of videos must be greater than zero.")

                if mode == "dates":
                    date_from = self._parse_date(self.date_from_var.get(), "Start date")
                    date_to = self._parse_date(self.date_to_var.get(), "End date")
                    if not date_from and not date_to:
                        raise ValueError("Enter at least one date.")
                    if date_from and date_to and date_from > date_to:
                        raise ValueError("The start date cannot be later than the end date.")

                self.worker = main.DownloadWorker(
                    profile_url=profile_url,
                    output_dir=folder,
                    mode=mode,
                    limit=limit,
                    date_from=date_from,
                    date_to=date_to,
                    preserve_date=self.date_file_var.get(),
                    skip_existing=self.skip_var.get(),
                    event_queue=self.events,
                    stop_event=self.stop_event,
                )
            else:
                video_url = normalize_single_video_url(self.single_url_var.get())
                self.single_url_var.set(video_url)
                self.worker = SingleVideoWorker(
                    video_url,
                    folder,
                    self.events,
                    self.stop_event,
                )

            self.worker.start()
        except ValueError as exc:
            self.progress.stop()
            self.download_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            messagebox.showerror(APP_TITLE, str(exc))

    def _stop(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.status_var.set("Stopping…")
            self.stop_btn.configure(state="disabled")

    def _finish(self) -> None:
        self.download_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    @staticmethod
    def _speed(value) -> str:
        if not value:
            return ""
        value = float(value)
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        index = 0
        while value >= 1024 and index < 3:
            value /= 1024
            index += 1
        return f"{value:.1f} {units[index]}"

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status_var.set(translate_backend_text(payload.get("text", "")))
                    if payload.get("detail") is not None:
                        self.detail_var.set(translate_backend_text(payload.get("detail", "")))

                elif kind == "progress":
                    percent = payload.get("percent")
                    if percent is None:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(12)
                    else:
                        self.progress.stop()
                        self.progress.configure(mode="determinate")
                        self.progress["value"] = percent

                    parts = [f"Downloaded: {payload.get('completed', 0)}"]
                    if payload.get("filename"):
                        parts.append(Path(payload["filename"]).name)
                    speed = self._speed(payload.get("speed"))
                    if speed:
                        parts.append(speed)
                    if payload.get("eta") is not None:
                        parts.append(f"~{payload['eta']} sec left")
                    self.detail_var.set(" | ".join(parts))
                    self.status_var.set("Downloading…")

                elif kind == "item_finished":
                    self.status_var.set(f"Saved videos: {payload.get('completed', 0)}")

                elif kind == "warning":
                    self.detail_var.set(translate_backend_text(payload.get("text", "")))

                elif kind == "done":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress["value"] = 100
                    self.status_var.set("Done")
                    detail = payload.get("detail")
                    if detail:
                        self.detail_var.set(detail)
                    elif payload.get("completed"):
                        self.detail_var.set(f"New videos saved: {payload['completed']}")
                    else:
                        self.detail_var.set("No new videos were saved.")
                    self._finish()

                elif kind == "cancelled":
                    self.progress.stop()
                    self.status_var.set("Stopped")
                    self.detail_var.set("The download was stopped.")
                    self._finish()

                elif kind == "error":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress["value"] = 0
                    text = translate_backend_text(payload.get("text", "Unknown error"))
                    self.status_var.set("Error")
                    self.detail_var.set(text)
                    self._finish()
                    messagebox.showerror(APP_TITLE, text)

        except queue.Empty:
            pass
        self.after(100, self._poll)


def run() -> None:
    main.APP_VERSION = APP_VERSION
    TikTokSaveEnglish().mainloop()


if __name__ == "__main__":
    run()
