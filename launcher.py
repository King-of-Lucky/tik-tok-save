from __future__ import annotations

import json
import re
import time

import app_en
import browser_patch
import download_patch
import embed_player_patch
import main
import ui_help_patch
import video_downloader_ui

APP_VERSION = "0.3.6"


def find_media_for_video(value, video_id: str) -> str | None:
    """Find a media URL in TikTok JSON, preferring an object with the requested post ID."""
    if isinstance(value, dict):
        identifiers = []
        for key in ("id", "aweme_id", "awemeId", "itemId", "item_id"):
            if key in value:
                identifiers.append(str(value.get(key)))
        if video_id in identifiers:
            found = main.find_media_url(value)
            if found:
                return found

        for child in value.values():
            found = find_media_for_video(child, video_id)
            if found:
                return found

    elif isinstance(value, list):
        for child in value:
            found = find_media_for_video(child, video_id)
            if found:
                return found

    return None


def media_from_scripts(page, video_id: str) -> str | None:
    """Read common TikTok hydration scripts and return media for one exact post ID."""
    selectors = (
        "script#__UNIVERSAL_DATA_FOR_REHYDRATION__",
        "script#SIGI_STATE",
        "script#__NEXT_DATA__",
    )
    for selector in selectors:
        try:
            text = page.locator(selector).first.text_content(timeout=1200)
            if not text:
                continue
            data = json.loads(text)
            found = find_media_for_video(data, video_id)
            if found:
                return found
        except Exception:
            continue
    return None


def patched_media_url_from_page(self, context, video_url: str):
    """Resolve a profile video by clicking its exact card instead of direct navigation."""
    match = re.search(r"/video/(\d+)", video_url)
    if not match:
        raise RuntimeError("Could not determine the TikTok video ID.")
    video_id = match.group(1)

    pages = context.pages
    if not pages:
        raise RuntimeError("The TikTok browser page was closed unexpectedly.")
    profile_page = pages[0]
    original_profile_url = self.profile_url

    try:
        user_agent = profile_page.evaluate("navigator.userAgent")
    except Exception:
        user_agent = "Mozilla/5.0"

    media_url = media_from_scripts(profile_page, video_id)
    if media_url:
        return media_url, user_agent, context.cookies()

    candidates: list[str] = []
    watched_pages = []

    def remember_response(response) -> None:
        try:
            url = response.url
            content_type = (response.headers.get("content-type") or "").lower()
            low_url = url.lower()
            if (
                "video/" in content_type
                or "/video/tos/" in low_url
                or "mime_type=video" in low_url
                or "video_mp4" in low_url
            ):
                if url.startswith("http") and url not in candidates:
                    candidates.append(url)
        except Exception:
            pass

    def watch_page(page) -> None:
        try:
            page.on("response", remember_response)
            watched_pages.append(page)
        except Exception:
            pass

    def on_new_page(page) -> None:
        watch_page(page)

    for page in list(context.pages):
        watch_page(page)
    try:
        context.on("page", on_new_page)
    except Exception:
        pass

    anchor_selector = f'a[href*="/video/{video_id}"]'
    anchor = profile_page.locator(anchor_selector).first
    try:
        count = anchor.count()
    except Exception:
        count = 0

    if not count:
        try:
            profile_page.goto(original_profile_url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass

        for _ in range(18):
            self.check_stop()
            anchor = profile_page.locator(anchor_selector).first
            try:
                if anchor.count():
                    break
            except Exception:
                pass
            try:
                profile_page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 2, 1500))")
            except Exception:
                pass
            time.sleep(0.8)

    try:
        if not anchor.count():
            raise RuntimeError(
                f"TikTok listed video {video_id}, but its card is no longer available on the profile page."
            )
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError("Could not find the requested video card in the TikTok profile.") from exc

    self.emit(
        "status",
        text="Opening the video inside the TikTok profile…",
        detail=f"Video ID: {video_id}",
    )

    try:
        anchor.scroll_into_view_if_needed(timeout=10000)
    except Exception:
        pass

    try:
        anchor.evaluate("(el) => el.click()")
    except Exception:
        try:
            anchor.click(timeout=10000, no_wait_after=True)
        except Exception as exc:
            raise RuntimeError(f"Could not open TikTok video {video_id} from the profile page.") from exc

    media_url = ""
    active_page = profile_page
    deadline = time.time() + 25
    while time.time() < deadline:
        self.check_stop()

        current_pages = context.pages
        if len(current_pages) > 1:
            active_page = current_pages[-1]

        found = media_from_scripts(active_page, video_id)
        if found:
            media_url = found
            break

        try:
            src = active_page.locator("video").first.evaluate(
                "(v) => v.currentSrc || v.src || ''"
            )
            if isinstance(src, str) and src.startswith("http"):
                media_url = src
                break
        except Exception:
            pass

        if candidates:
            media_url = candidates[-1]
            break

        time.sleep(0.35)

    try:
        if active_page is not profile_page:
            active_page.close()
    except Exception:
        pass

    try:
        profile_page.keyboard.press("Escape")
        time.sleep(0.3)
    except Exception:
        pass

    try:
        if "/video/" in (profile_page.url or ""):
            profile_page.goto(original_profile_url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass

    try:
        context.remove_listener("page", on_new_page)
    except Exception:
        pass
    for watched in watched_pages:
        try:
            watched.remove_listener("response", remember_response)
        except Exception:
            pass

    if not media_url:
        raise RuntimeError(
            f"TikTok opened video {video_id}, but no media URL was returned."
        )

    return media_url, user_agent, context.cookies()


def run() -> None:
    main.APP_VERSION = APP_VERSION
    app_en.APP_VERSION = APP_VERSION
    video_downloader_ui.APP_VERSION = APP_VERSION

    download_patch.install()
    browser_patch.install()

    # Profile downloads keep the proven profile-card resolver.
    main.DownloadWorker._media_url_from_page = patched_media_url_from_page

    # Single and batch video downloads use TikTok's exact-ID embed player.
    embed_player_patch.install()

    # Explain the temporary Edge window inside both tabs.
    ui_help_patch.install()

    video_downloader_ui.run()


if __name__ == "__main__":
    run()
