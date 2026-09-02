from __future__ import annotations

import os
import queue
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DateRange, DownloadError

APP_TITLE = "TikTok Save"
APP_VERSION = "0.3.6"


class UserCancelled(Exception):
    """Raised when the user stops an active download."""


def normalize_profile_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Paste a TikTok profile link, @username, or username.")
    if value.startswith("@"):
        value = f"https://www.tiktok.com/{value}"
    elif re.fullmatch(r"[A-Za-z0-9._-]+", value):
        value = f"https://www.tiktok.com/@{value}"
    elif not value.startswith(("http://", "https://")):
        value = "https://" + value
    if "tiktok.com/@" not in value.lower():
        raise ValueError("Use a TikTok profile link such as https://www.tiktok.com/@username")
    return value.split("?")[0].rstrip("/")


def profile_username(url: str) -> str:
    match = re.search(r"tiktok\.com/@([^/?#]+)", url, flags=re.I)
    return match.group(1) if match else "TikTok"


def video_date_from_id(video_id: str) -> tuple[str, int | None]:
    """Derive the publication timestamp encoded in a TikTok post ID."""
    try:
        timestamp = int(video_id) >> 32
        if timestamp < 1_500_000_000 or timestamp > int(time.time()) + 86400 * 7:
            return "", None
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y%m%d"), timestamp
    except (TypeError, ValueError, OSError, OverflowError):
        return "", None


def find_media_url(value) -> str | None:
    """Recursively find a TikTok play/download URL in JSON-like data."""
    if isinstance(value, dict):
        for key in ("playAddr", "downloadAddr", "play_addr", "download_addr"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith("http"):
                return candidate
            if isinstance(candidate, dict):
                url_list = candidate.get("url_list") or candidate.get("UrlList")
                if isinstance(url_list, list):
                    for url in url_list:
                        if isinstance(url, str) and url.startswith("http"):
                            return url
        for child in value.values():
            found = find_media_url(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_media_url(child)
            if found:
                return found
    return None


def clean_message(message: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(message)).strip()
    return re.sub(r"^(ERROR|WARNING):\s*", "", text, flags=re.I)


class YDLLogger:
    def __init__(self, worker: "DownloadWorker") -> None:
        self.worker = worker

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self.worker.last_warning = clean_message(msg)

    def error(self, msg: str) -> None:
        self.worker.last_error = clean_message(msg)


class DownloadWorker(threading.Thread):
    """Backend worker used by the Profile Downloader tab."""

    def __init__(
        self,
        *,
        profile_url: str,
        output_dir: Path,
        mode: str,
        limit: int | None,
        date_from: str | None,
        date_to: str | None,
        preserve_date: bool,
        skip_existing: bool,
        event_queue: queue.Queue,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self.profile_url = profile_url
        self.output_dir = output_dir
        self.mode = mode
        self.limit = limit
        self.date_from = date_from
        self.date_to = date_to
        self.preserve_date = preserve_date
        self.skip_existing = skip_existing
        self.events = event_queue
        self.stop_event = stop_event
        self.completed = 0
        self.last_error = ""
        self.last_warning = ""

    def emit(self, kind: str, **payload) -> None:
        self.events.put((kind, payload))

    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise UserCancelled()

    def progress_hook(self, data: dict) -> None:
        self.check_stop()
        info = data.get("info_dict") or {}
        if data.get("status") == "downloading":
            done = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            self.emit(
                "progress",
                percent=(done / total * 100) if total else None,
                speed=data.get("speed"),
                eta=data.get("eta"),
                filename=data.get("filename") or info.get("_filename") or "",
                completed=self.completed,
            )
        elif data.get("status") == "finished":
            self.completed += 1
            filename = data.get("filename") or info.get("_filename") or ""
            if self.preserve_date and filename:
                timestamp = info.get("timestamp") or info.get("release_timestamp")
                if timestamp:
                    try:
                        os.utime(filename, (timestamp, timestamp))
                    except OSError:
                        pass
            self.emit("item_finished", completed=self.completed)

    def match_filter(self, info: dict, *, incomplete: bool = False) -> str | None:
        self.check_stop()
        return None

    @staticmethod
    def should_use_browser_fallback(text: str) -> bool:
        low = text.lower()
        markers = (
            "unexpected response from webpage request",
            "failed to parse json",
            "jsondecodeerror",
            "unable to extract secondary user id",
            "403",
            "forbidden",
        )
        return any(marker in low for marker in markers)

    @staticmethod
    def friendly_error(text: str) -> str:
        low = text.lower()
        if "429" in low or "too many requests" in low:
            return "TikTok temporarily rate-limited the requests (429). Please try again later."
        return text

    def _run_ytdlp(self) -> tuple[int | None, str]:
        self.last_error = ""
        self.last_warning = ""
        options = {
            "outtmpl": str(
                self.output_dir
                / "%(uploader_id,uploader)s"
                / "%(upload_date)s_%(id)s.%(ext)s"
            ),
            "format": "best",
            "progress_hooks": [self.progress_hook],
            "match_filter": self.match_filter,
            "logger": YDLLogger(self),
            "windowsfilenames": True,
            "noprogress": True,
            "quiet": True,
            "no_warnings": False,
            "ignoreerrors": True,
            "continuedl": True,
            "overwrites": False,
        }
        if self.skip_existing:
            options["download_archive"] = str(self.output_dir / ".tiktok-save-archive.txt")
        if self.mode == "limit" and self.limit:
            options["playlistend"] = self.limit
        if self.mode == "dates":
            options["daterange"] = DateRange(self.date_from, self.date_to)

        self.emit(
            "status",
            text="Checking TikTok with the direct method…",
            detail=self.profile_url,
        )
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                result = ydl.download([self.profile_url])
                self.check_stop()
            return result, self.last_error
        except DownloadError as exc:
            return 1, clean_message(exc)

    def _archive_ids(self) -> set[str]:
        archive = self.output_dir / ".tiktok-save-archive.txt"
        if not archive.exists():
            return set()
        result: set[str] = set()
        try:
            for line in archive.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.strip().split()
                if parts:
                    result.add(parts[-1])
        except OSError:
            pass
        return result

    def _append_archive(self, video_id: str) -> None:
        if not self.skip_existing:
            return
        archive = self.output_dir / ".tiktok-save-archive.txt"
        try:
            with archive.open("a", encoding="utf-8") as file:
                file.write(f"TikTok {video_id}\n")
        except OSError:
            pass

    # These methods are installed by the current runtime patches in launcher.py.
    # Keeping explicit placeholders makes the backend API clear and prevents hidden
    # dependencies on old versioned launcher files.
    def _collect_browser_videos(self, page):
        raise RuntimeError("Browser profile collector is not installed.")

    def _media_url_from_page(self, context, video_url: str):
        raise RuntimeError("Browser media resolver is not installed.")

    def _download_direct(
        self,
        media_url: str,
        video_url: str,
        video_id: str,
        target: Path,
        user_agent: str,
        cookies: list[dict],
    ) -> None:
        raise RuntimeError("Direct media downloader is not installed.")

    def _run_browser_fallback(self) -> None:
        raise RuntimeError("Browser fallback is not installed.")

    def run(self) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            result, error_text = self._run_ytdlp()

            if error_text and self.should_use_browser_fallback(error_text):
                self._run_browser_fallback()
                return

            if self.completed == 0 and error_text:
                self.emit("error", text=self.friendly_error(error_text))
                return
            if result not in (None, 0) or (error_text and self.completed):
                self.emit(
                    "warning",
                    text="Some posts could not be downloaded. The remaining videos were saved.",
                )
            self.emit("done", completed=self.completed)
        except UserCancelled:
            self.emit("cancelled", completed=self.completed)
        except Exception as exc:
            self.emit("error", text=self.friendly_error(clean_message(exc)))
