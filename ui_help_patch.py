from __future__ import annotations

from tkinter import ttk

import video_downloader_ui


HELP_TEXT = (
    "TikTok Save may open Microsoft Edge automatically to access public TikTok pages "
    "and retrieve the requested videos. While a download is running, do not close or "
    "use the Edge window. The app controls the browser automatically and closes it when "
    "the current download task is finished."
)


def install() -> None:
    cls = video_downloader_ui.TikTokSaveApp
    original_profile = cls._build_profile_tab
    original_video = cls._build_single_tab

    def build_profile_with_help(self) -> None:
        original_profile(self)
        info = ttk.LabelFrame(self.profile_tab, text="How it works", padding=10)
        info.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        info.columnconfigure(0, weight=1)
        ttk.Label(
            info,
            text=HELP_TEXT,
            wraplength=760,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

    def build_video_with_help(self) -> None:
        original_video(self)
        info = ttk.LabelFrame(self.single_tab, text="How it works", padding=10)
        info.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        info.columnconfigure(0, weight=1)
        ttk.Label(
            info,
            text=HELP_TEXT,
            wraplength=760,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

    cls._build_profile_tab = build_profile_with_help
    cls._build_single_tab = build_video_with_help
