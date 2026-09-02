from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

import main


MIN_VALID_FILE_SIZE = 64 * 1024
RANGE_CHUNK_SIZE = 4 * 1024 * 1024


def _is_valid_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= MIN_VALID_FILE_SIZE
    except OSError:
        return False


def _existing_valid_file(output_dir: Path, video_id: str) -> bool:
    # Legacy filenames contained the post ID directly.
    for path in output_dir.rglob(f"*_{video_id}.mp4"):
        if _is_valid_file(path):
            return True

    # Current caption-based filenames are tracked by a per-account file map.
    for map_path in output_dir.rglob(".tiktok-save-files.json"):
        try:
            data = json.loads(map_path.read_text(encoding="utf-8"))
            filename = data.get(video_id) if isinstance(data, dict) else None
            if filename and _is_valid_file(map_path.parent / str(filename)):
                return True
        except Exception:
            pass

    return False


def patched_archive_ids(self) -> set[str]:
    archive = self.output_dir / ".tiktok-save-archive.txt"
    if not archive.exists():
        return set()

    result: set[str] = set()
    try:
        for line in archive.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            video_id = parts[-1]
            if _existing_valid_file(self.output_dir, video_id):
                result.add(video_id)
    except OSError:
        pass
    return result


def cleanup_incomplete_files(output_dir: Path) -> None:
    for path in output_dir.rglob("*.mp4"):
        try:
            if path.is_file() and path.stat().st_size < MIN_VALID_FILE_SIZE:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _build_session(cookies: list[dict]) -> requests.Session:
    session = requests.Session()
    for cookie in cookies:
        try:
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path") or "/",
            )
        except Exception:
            pass
    return session


def _parse_content_range(value: str) -> tuple[int, int, int | None] | None:
    match = re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", (value or "").strip(), flags=re.I)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    total = None if match.group(3) == "*" else int(match.group(3))
    return start, end, total


def patched_download_direct(
    self,
    media_url: str,
    video_url: str,
    video_id: str,
    target: Path,
    user_agent: str,
    cookies: list[dict],
) -> None:
    """Download the complete TikTok media file, following HTTP 206 byte ranges."""
    self.check_stop()
    session = _build_session(cookies)

    base_headers = {
        "User-Agent": user_agent,
        "Referer": video_url,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    try:
        part.unlink(missing_ok=True)
    except OSError:
        pass

    expected_total: int | None = None
    downloaded = 0
    started = time.time()

    try:
        for _request_no in range(10000):
            self.check_stop()

            range_end = downloaded + RANGE_CHUNK_SIZE - 1
            headers = dict(base_headers)
            headers["Range"] = f"bytes={downloaded}-{range_end}"

            with session.get(
                media_url,
                headers=headers,
                stream=True,
                timeout=(20, 90),
                allow_redirects=True,
            ) as response:
                content_type = (response.headers.get("content-type") or "").lower()
                if response.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {response.status_code}")
                if "text/html" in content_type or "application/json" in content_type:
                    raise RuntimeError(
                        f"TikTok returned {content_type or 'a non-video response'} instead of video data."
                    )

                content_range = _parse_content_range(response.headers.get("content-range") or "")

                if response.status_code == 206:
                    if not content_range:
                        raise RuntimeError("TikTok returned a partial file without Content-Range.")
                    range_start, range_finish, range_total = content_range
                    if range_start != downloaded:
                        raise RuntimeError(
                            f"TikTok returned the wrong byte range: expected {downloaded}, got {range_start}."
                        )
                    if range_total is not None:
                        expected_total = range_total
                else:
                    if downloaded:
                        downloaded = 0
                        try:
                            part.unlink(missing_ok=True)
                        except OSError:
                            pass
                    length = int(response.headers.get("content-length") or 0)
                    if length:
                        expected_total = length

                mode = "ab" if downloaded else "wb"
                bytes_this_response = 0
                with part.open(mode) as file:
                    for chunk in response.iter_content(chunk_size=512 * 1024):
                        self.check_stop()
                        if not chunk:
                            continue
                        file.write(chunk)
                        size = len(chunk)
                        downloaded += size
                        bytes_this_response += size

                        elapsed = max(time.time() - started, 0.001)
                        speed = downloaded / elapsed
                        eta = (
                            int((expected_total - downloaded) / speed)
                            if expected_total and expected_total > downloaded and speed
                            else 0 if expected_total and downloaded >= expected_total
                            else None
                        )
                        self.emit(
                            "progress",
                            percent=(downloaded / expected_total * 100) if expected_total else None,
                            speed=speed,
                            eta=eta,
                            filename=str(target),
                            completed=self.completed,
                        )

                if bytes_this_response <= 0:
                    raise RuntimeError("TikTok returned an empty video fragment.")

                if response.status_code == 200:
                    break

                if expected_total is not None and downloaded >= expected_total:
                    break

                if content_range:
                    _range_start, range_finish, _range_total = content_range
                    if downloaded != range_finish + 1:
                        raise RuntimeError(
                            "The received TikTok fragment size does not match Content-Range."
                        )
        else:
            raise RuntimeError("Too many byte-range requests were required to download the video.")

        actual_size = part.stat().st_size if part.exists() else 0
        if expected_total is not None and actual_size < expected_total:
            raise RuntimeError(
                f"The video download is incomplete: {actual_size} of {expected_total} bytes."
            )
        if actual_size < MIN_VALID_FILE_SIZE:
            raise RuntimeError(
                f"TikTok returned only {actual_size} bytes. This is not a complete video file."
            )

        try:
            with part.open("rb") as file:
                header = file.read(64)
            if b"ftyp" not in header:
                raise RuntimeError("The downloaded file does not look like a valid MP4 file.")
        except OSError as exc:
            raise RuntimeError(f"Could not validate the downloaded file: {exc}") from exc

        part.replace(target)

    except Exception as exc:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"Could not download video {video_id}: {exc}") from exc


def install() -> None:
    main.DownloadWorker._archive_ids = patched_archive_ids
    main.DownloadWorker._download_direct = patched_download_direct
