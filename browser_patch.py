from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import main


WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(value: str, fallback: str, max_length: int = 140) -> str:
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", " ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = fallback
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    if len(value) > max_length:
        value = value[:max_length].rstrip(" .")
    return value or fallback


def _looks_like_caption(text: str) -> bool:
    text = (text or "").strip()
    if not text or len(text) < 2:
        return False
    if re.fullmatch(r"[\d\s.,KkMmBbTt]+", text):
        return False
    lowered = text.lower()
    rejected = ("play", "watch video", "views", "view video", "tiktok")
    return not any(lowered == item for item in rejected)


def extract_profile_items(page):
    raw_items = page.eval_on_selector_all(
        'a[href*="/video/"]',
        """
        els => els.map(a => {
            const img = a.querySelector('img');
            const candidates = [
                img?.getAttribute('alt') || '',
                a.getAttribute('aria-label') || '',
                a.getAttribute('title') || '',
                a.innerText || '',
                a.closest('[data-e2e]')?.innerText || '',
                a.parentElement?.innerText || ''
            ];
            return {href: a.href, candidates};
        }).filter(x => x.href)
        """,
    )

    result = []
    for item in raw_items:
        href = str(item.get("href") or "")
        match = re.search(r"/video/(\d+)", href)
        if not match:
            continue
        video_id = match.group(1)
        date_text, timestamp = main.video_date_from_id(video_id)

        candidates = []
        for candidate in item.get("candidates") or []:
            text = re.sub(r"\s+", " ", str(candidate or "")).strip()
            if _looks_like_caption(text) and text not in candidates:
                candidates.append(text)

        title = candidates[0] if candidates else ""
        result.append((video_id, href.split("?")[0], date_text, timestamp, title))
    return result


def patched_collect_browser_videos(self, page):
    seen: set[str] = set()
    videos: list[tuple[str, str, str, int | None, str]] = []
    stagnant = 0
    older_rounds = 0
    rounds = 0
    refresh_attempts = 0
    reload_attempts = 0

    deadline = time.time() + 120
    while time.time() < deadline:
        self.check_stop()
        links = extract_profile_items(page)
        if links:
            break

        page_text = ""
        try:
            page_text = page.locator("body").inner_text(timeout=1500)
        except Exception:
            pass

        if "Something went wrong" in page_text:
            if refresh_attempts < 4:
                refresh_attempts += 1
                self.emit(
                    "status",
                    text=f"TikTok did not return the feed. Refreshing ({refresh_attempts}/4)…",
                    detail="The profile was found. Requesting the video feed again through Edge.",
                )
                clicked = False
                for selector in ('button:has-text("Refresh")', 'text="Refresh"'):
                    try:
                        page.locator(selector).first.click(timeout=3000)
                        clicked = True
                        break
                    except Exception:
                        continue
                if not clicked:
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=45000)
                    except Exception:
                        pass
                time.sleep(3)
                continue

            if reload_attempts < 2:
                reload_attempts += 1
                self.emit(
                    "status",
                    text=f"Reloading the TikTok profile ({reload_attempts}/2)…",
                    detail="TikTok loaded the profile but has not returned the Videos tab yet.",
                )
                try:
                    page.reload(wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass
                time.sleep(4)
                continue

        self.emit(
            "status",
            text="Waiting for profile videos in Edge…",
            detail=(
                "If TikTok shows a verification, cookie prompt, or Refresh button, "
                "the app will try to handle it automatically."
            ),
        )
        time.sleep(2)
    else:
        raise RuntimeError(
            "TikTok opened the profile but did not return the Videos tab. "
            "This is a TikTok browser-session limitation, not an invalid profile link."
        )

    while True:
        self.check_stop()
        rounds += 1
        before = len(videos)
        new_dates: list[str] = []
        for item in extract_profile_items(page):
            if item[0] in seen:
                continue
            seen.add(item[0])
            videos.append(item)
            if item[2]:
                new_dates.append(item[2])

        self.emit(
            "status",
            text=f"Edge mode: found {len(videos)} videos…",
            detail="The video list was collected from the regular TikTok profile page in Edge.",
        )

        if self.mode == "limit" and self.limit and len(videos) >= self.limit + 6:
            break

        if self.mode == "dates" and self.date_from and rounds >= 3 and new_dates:
            if max(new_dates) < self.date_from:
                older_rounds += 1
            else:
                older_rounds = 0
            if older_rounds >= 2:
                break

        try:
            page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 2.5, 1800))")
        except Exception:
            pass
        time.sleep(1.4)

        if len(videos) == before:
            stagnant += 1
        else:
            stagnant = 0
        if stagnant >= 7:
            break

    videos.sort(key=lambda item: int(item[0]), reverse=True)

    if self.mode == "limit" and self.limit:
        videos = videos[: self.limit]
    elif self.mode == "dates":
        filtered = []
        for item in videos:
            date_text = item[2]
            if not date_text:
                continue
            if self.date_from and date_text < self.date_from:
                continue
            if self.date_to and date_text > self.date_to:
                continue
            filtered.append(item)
        videos = filtered

    return videos


def unique_target_for_title(target_dir: Path, title: str, video_id: str, date_text: str) -> Path:
    fallback = f"{date_text or 'unknown'}_{video_id}"
    base = sanitize_filename(title, fallback)
    target = target_dir / f"{base}.mp4"
    if not target.exists():
        return target
    short_id = video_id[-8:]
    return target_dir / f"{sanitize_filename(base, fallback, 125)}_{short_id}.mp4"


def load_file_map(target_dir: Path) -> dict[str, str]:
    path = target_dir / ".tiktok-save-files.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def save_file_map(target_dir: Path, mapping: dict[str, str]) -> None:
    path = target_dir / ".tiktok-save-files.json"
    try:
        path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def is_good_video_file(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size >= 256 * 1024
    except OSError:
        return False


def patched_run_browser_fallback(self) -> None:
    self.emit(
        "status",
        text="TikTok blocked the direct method. Opening Edge…",
        detail="Starting a clean browser session for the profile fallback.",
    )

    browser_profile = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "TikTokSave" / "BrowserProfileV2"
    browser_profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = None
        launch_errors: list[str] = []

        for channel in ("msedge", "chrome"):
            try:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(browser_profile),
                    channel=channel,
                    headless=False,
                    viewport={"width": 1200, "height": 850},
                    chromium_sandbox=True,
                    ignore_default_args=["--enable-automation"],
                    args=["--disable-blink-features=AutomationControlled"],
                )
                break
            except PlaywrightError as exc:
                launch_errors.append(str(exc))

        if context is None:
            raise RuntimeError(
                "Could not open Edge or Chrome for the browser fallback. "
                + (launch_errors[-1] if launch_errors else "")
            )

        try:
            context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                """
            )

            pages = context.pages
            page = pages[0] if pages else context.new_page()
            page.goto(self.profile_url, wait_until="domcontentloaded", timeout=45000)
            videos = self._collect_browser_videos(page)
            self.check_stop()

            archived = self._archive_ids() if self.skip_existing else set()
            username = main.profile_username(self.profile_url)
            target_dir = self.output_dir / username
            target_dir.mkdir(parents=True, exist_ok=True)
            file_map = load_file_map(target_dir)

            if not videos:
                self.emit("done", completed=self.completed)
                return

            self.emit(
                "status",
                text=f"Videos selected: {len(videos)}",
                detail="Starting downloads through the Edge fallback.",
            )

            failures: list[str] = []
            for index, (video_id, video_url, date_text, timestamp, title) in enumerate(videos, start=1):
                self.check_stop()

                mapped_path = target_dir / file_map[video_id] if video_id in file_map else None
                old_id_matches = list(target_dir.glob(f"*{video_id}*.mp4"))
                old_good = next((p for p in old_id_matches if is_good_video_file(p)), None)

                if self.skip_existing and video_id in archived:
                    if mapped_path and is_good_video_file(mapped_path):
                        self.emit(
                            "status",
                            text=f"Skipping an already downloaded video: {index}/{len(videos)}",
                            detail=mapped_path.name,
                        )
                        continue

                    if old_good:
                        new_target = unique_target_for_title(target_dir, title, video_id, date_text)
                        try:
                            if new_target != old_good:
                                old_good.replace(new_target)
                            file_map[video_id] = new_target.name
                            save_file_map(target_dir, file_map)
                            self.emit(
                                "status",
                                text=f"Updated an existing filename: {index}/{len(videos)}",
                                detail=new_target.name,
                            )
                            continue
                        except OSError:
                            pass

                for existing in old_id_matches:
                    try:
                        if existing.stat().st_size < 256 * 1024:
                            existing.unlink(missing_ok=True)
                    except OSError:
                        pass
                if mapped_path and mapped_path.exists() and not is_good_video_file(mapped_path):
                    try:
                        mapped_path.unlink(missing_ok=True)
                    except OSError:
                        pass

                target = unique_target_for_title(target_dir, title, video_id, date_text)

                self.emit(
                    "status",
                    text=f"Getting video {index}/{len(videos)} through Edge…",
                    detail=title or video_url,
                )

                try:
                    media_url, user_agent, cookies = self._media_url_from_page(context, video_url)
                    self._download_direct(media_url, video_url, video_id, target, user_agent, cookies)
                    if self.preserve_date and timestamp:
                        try:
                            os.utime(target, (timestamp, timestamp))
                        except OSError:
                            pass
                    self._append_archive(video_id)
                    archived.add(video_id)
                    file_map[video_id] = target.name
                    save_file_map(target_dir, file_map)
                    self.completed += 1
                    self.emit("item_finished", completed=self.completed)
                except main.UserCancelled:
                    raise
                except Exception as exc:
                    failures.append(f"{video_id}: {exc}")

            if failures and self.completed == 0:
                raise RuntimeError(failures[0])
            if failures:
                self.emit(
                    "warning",
                    text=f"Saved {self.completed}. Failed to download: {len(failures)}.",
                )
            self.emit("done", completed=self.completed)
        finally:
            context.close()


def install() -> None:
    main.DownloadWorker._collect_browser_videos = patched_collect_browser_videos
    main.DownloadWorker._run_browser_fallback = patched_run_browser_fallback
