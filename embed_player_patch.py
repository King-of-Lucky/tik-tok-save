from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import app_en
import browser_patch
import main

PLAYER_URL = (
    "https://www.tiktok.com/player/v1/{video_id}"
    "?autoplay=1&controls=1&description=1&rel=0&native_context_menu=1"
)
VERIFY_FILE = ".tiktok-save-verified.json"


def _load_verified(target_dir: Path) -> dict[str, str]:
    path = target_dir / VERIFY_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(key): str(value) for key, value in data.items()}
    except Exception:
        pass
    return {}


def _save_verified(target_dir: Path, mapping: dict[str, str]) -> None:
    try:
        (target_dir / VERIFY_FILE).write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _oembed_title(canonical_url: str) -> str:
    """Get the public post caption without depending on the profile grid."""
    try:
        response = requests.get(
            "https://www.tiktok.com/oembed",
            params={"url": canonical_url},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=(10, 20),
        )
        if response.ok:
            data = response.json()
            title = str(data.get("title") or "").strip()
            if title:
                return title
    except Exception:
        pass
    return ""


def _largest_visible_video_src(page) -> str:
    try:
        return page.evaluate(
            """
            () => {
                const items = [...document.querySelectorAll('video')]
                    .map(v => {
                        const r = v.getBoundingClientRect();
                        const s = getComputedStyle(v);
                        return {
                            src: v.currentSrc || v.src || '',
                            area: Math.max(0, r.width) * Math.max(0, r.height),
                            visible: r.width > 40 && r.height > 40 &&
                                     s.display !== 'none' &&
                                     s.visibility !== 'hidden' &&
                                     Number(s.opacity || 1) > 0
                        };
                    })
                    .filter(x => x.visible && x.src && x.src.startsWith('http'))
                    .sort((a, b) => b.area - a.area);
                return items.length ? items[0].src : '';
            }
            """
        ) or ""
    except Exception:
        return ""


def _media_from_embed(page, video_id: str) -> str:
    candidates: list[str] = []

    def remember_response(response) -> None:
        try:
            url = response.url
            content_type = (response.headers.get("content-type") or "").lower()
            low = url.lower()
            if (
                content_type.startswith("video/")
                or "/video/tos/" in low
                or "mime_type=video" in low
                or "video_mp4" in low
            ):
                if url.startswith("http") and url not in candidates:
                    candidates.append(url)
        except Exception:
            pass

    page.on("response", remember_response)
    player_url = PLAYER_URL.format(video_id=video_id)

    try:
        page.goto(player_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as exc:
        raise RuntimeError(f"TikTok Embed Player could not open video {video_id}: {exc}") from exc

    deadline = time.time() + 25
    played = False
    while time.time() < deadline:
        try:
            import launcher

            exact = launcher.media_from_scripts(page, video_id)
            if exact:
                return exact
        except Exception:
            pass

        src = _largest_visible_video_src(page)
        if src:
            return src

        if candidates:
            return candidates[0]

        if not played:
            played = True
            try:
                page.evaluate(
                    """
                    () => {
                        const v = document.querySelector('video');
                        if (v) {
                            v.muted = true;
                            const p = v.play();
                            if (p && p.catch) p.catch(() => {});
                        }
                    }
                    """
                )
            except Exception:
                pass

        page.wait_for_timeout(300)

    raise RuntimeError(
        f"TikTok Embed Player opened video {video_id}, but no media stream was returned."
    )


def patched_single_video_run(self) -> None:
    try:
        url = self._resolve_short_link(self.video_url)
        canonical, profile_url, video_id = app_en.canonical_video_parts(url)
        username = main.profile_username(profile_url)
        target_dir = self.output_dir / username
        target_dir.mkdir(parents=True, exist_ok=True)

        verified = _load_verified(target_dir)
        verified_name = verified.get(video_id)
        if verified_name:
            verified_path = target_dir / verified_name
            if browser_patch.is_good_video_file(verified_path):
                self.emit(
                    "done",
                    completed=0,
                    detail=f"Already downloaded and verified: {verified_path.name}",
                )
                return

        title = _oembed_title(canonical)
        self.emit(
            "status",
            text="Opening the exact TikTok video…",
            detail=title or f"Video ID: {video_id}",
        )

        helper = main.DownloadWorker(
            profile_url=profile_url,
            output_dir=self.output_dir,
            mode="all",
            limit=None,
            date_from=None,
            date_to=None,
            preserve_date=True,
            skip_existing=True,
            event_queue=self.events,
            stop_event=self.stop_event,
        )

        browser_profile = (
            Path(os.getenv("LOCALAPPDATA") or Path.home())
            / "TikTokSave"
            / "BrowserProfileV2"
        )
        browser_profile.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            context = None
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
                except PlaywrightError:
                    pass

            if context is None:
                raise RuntimeError("Could not open Microsoft Edge or Chrome.")

            try:
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                pages = context.pages
                page = pages[0] if pages else context.new_page()

                media_url = _media_from_embed(page, video_id)
                self.check_stop()

                try:
                    user_agent = page.evaluate("navigator.userAgent")
                except Exception:
                    user_agent = "Mozilla/5.0"

                date_text, timestamp = main.video_date_from_id(video_id)
                target = browser_patch.unique_target_for_title(
                    target_dir,
                    title,
                    video_id,
                    date_text,
                )

                self.emit(
                    "status",
                    text="Downloading the verified TikTok video…",
                    detail=target.name,
                )
                helper._download_direct(
                    media_url,
                    PLAYER_URL.format(video_id=video_id),
                    video_id,
                    target,
                    user_agent,
                    context.cookies(),
                )

                if timestamp:
                    try:
                        os.utime(target, (timestamp, timestamp))
                    except OSError:
                        pass

                helper._append_archive(video_id)
                file_map = browser_patch.load_file_map(target_dir)
                file_map[video_id] = target.name
                browser_patch.save_file_map(target_dir, file_map)
                verified[video_id] = target.name
                _save_verified(target_dir, verified)

                self.emit(
                    "done",
                    completed=1,
                    detail=f"Saved and verified: {target.name}",
                )
            finally:
                context.close()
    except main.UserCancelled:
        self.emit("cancelled", completed=0)
    except Exception as exc:
        self.emit("error", text=str(exc))


def install() -> None:
    app_en.SingleVideoWorker.run = patched_single_video_run
