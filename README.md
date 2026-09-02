# TikTok Save

**TikTok Save** is a Windows desktop application for downloading videos from **public TikTok profiles and public TikTok video links**.

The application is designed to keep the workflow simple: paste a public profile or one or more video links, choose a destination folder, and let TikTok Save handle the rest.

**Created by King of Lucky**

## Features

### Profile Downloader

Download videos from a public TikTok profile.

You can enter:

- a full profile URL such as `https://www.tiktok.com/@username`;
- `@username`;
- or just `username`.

Available filters:

- **All videos** - download all videos that the app can collect from the public profile;
- **Latest N videos** - download only the newest number of videos you choose;
- **Date range** - download videos published within a selected date range. One side of the range may be left empty.

### Video Downloader

Download specific TikTok posts without downloading an entire profile.

- Add from **1 to 10 video links** at the same time.
- Links may come from **different public TikTok accounts**.
- Use **+ Add another video** to add more link fields.
- Remove an unnecessary field with the **×** button.
- Exact duplicate links are automatically ignored.
- If one video fails, the remaining links continue processing.
- The final result shows how many videos were downloaded, skipped, or failed.

TikTok Save uses the exact TikTok post ID for direct video downloads, helping prevent a different post from the same account from being saved by mistake.

## File names

When TikTok provides a post caption, TikTok Save uses that caption as the downloaded `.mp4` file name.

For example:

```text
Best moment of the day 😂 #funny.mp4
```

Windows-invalid filename characters are removed automatically.

If the caption is missing, TikTok Save falls back to a name based on the publication date and TikTok post ID.

If two videos would receive the same file name, a short part of the TikTok post ID is added to keep both files.

## Download folders

TikTok Save creates a separate subfolder for each TikTok account inside the folder you selected.

Example:

```text
TikTok/
└── username/
    ├── First video caption.mp4
    ├── Another video caption.mp4
    └── ...
```

This also applies to the Video Downloader when links come from different accounts.

## Browser window / Microsoft Edge

TikTok regularly restricts automated requests. Because of this, TikTok Save may automatically open **Microsoft Edge** (or Chrome as a fallback) to access the public TikTok page or player in a normal browser session.

This is expected behavior.

While a download is running:

- **do not close the browser window**;
- **do not use that temporary browser window for other browsing**;
- TikTok Save controls it automatically;
- the application closes the browser when the current download task is finished.

If TikTok displays a verification or cookie prompt, the application may wait for TikTok to finish loading. In some cases TikTok may require user interaction before the public page becomes available.

## Public content only

TikTok Save supports **public profiles and public videos only**.

Private profiles, private posts, deleted videos, unavailable posts, region-restricted content, or content TikTok refuses to serve cannot be downloaded by the application.

Use TikTok Save only for content you have the right to save.

## Skip already downloaded videos

When **Skip already downloaded videos** is enabled, TikTok Save keeps local metadata so it can recognize previously downloaded posts.

Depending on the download mode, the selected output folder may contain small application metadata files such as:

```text
.tiktok-save-archive.txt
.tiktok-save-files.json
.tiktok-save-verified.json
```

These files are not videos. They are used to track downloaded post IDs, caption-based file names, and verified single-video downloads.

If you want TikTok Save to remember previous downloads, do not delete these metadata files.

## Preserve publication date

When **Set file date to TikTok publication date** is enabled, TikTok Save sets the downloaded file timestamp to the TikTok post publication time whenever that information can be determined.

## Progress and Stop button

During downloading, the app can display:

- current progress;
- downloaded file name;
- download speed;
- estimated remaining time when available;
- number of completed videos.

Use **Stop** to request cancellation of the active task.

## Windows installation

### Recommended method

1. Install **Python 3** if it is not already installed.
2. Make sure **Microsoft Edge** is installed. Chrome can also be used as a fallback.
3. Download or clone this repository.
4. Run:

```text
run_windows.bat
```

On the first run, the script automatically:

- creates a local `.venv` Python virtual environment;
- installs the required Python dependencies;
- launches TikTok Save.

Later runs reuse the same virtual environment and update the dependencies before launching the app.

## Manual launch

From the repository folder:

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python launcher.py
```

## Main dependencies

TikTok Save currently uses:

- Python / Tkinter for the desktop interface;
- `yt-dlp` as the first profile-download method;
- Playwright with Microsoft Edge or Chrome for TikTok browser fallback;
- `requests` for media and metadata requests.

TikTok changes its website frequently, so browser behavior or extraction methods may require future updates.

## Project structure

The current application starts from:

```text
launcher.py
```

Important modules include:

```text
app_en.py                English base UI and single-video worker interface
video_downloader_ui.py   Multi-link Video Downloader UI
main.py                  Profile Downloader backend
browser_patch.py         Browser-based profile fallback and caption filenames
download_patch.py        Complete HTTP range download handling
embed_player_patch.py    Exact-ID video downloader using TikTok's player
ui_help_patch.py         In-app explanation of the browser workflow
```

## Contributing

Contributions are welcome.

If you find a bug, have an idea for an improvement, or want to add support for new TikTok behaviors, feel free to open an Issue or submit a Pull Request.

Please keep changes focused, explain what was changed, and test the affected download flow before submitting.

## License

TikTok Save is released under the **MIT License**. See `LICENSE` for details.

## Disclaimer

TikTok Save is an independent utility and is not affiliated with, endorsed by, or sponsored by TikTok.

You are responsible for complying with copyright, privacy, platform rules, and applicable law when saving or using downloaded content.
