"""Feedback and update checks.

Nothing here talks to the network on its own:
- feedback only builds a pre-filled GitHub issue link that opens in the user's
  browser, where they can read and edit it before sending;
- the update check runs only when the user clicks "Check for updates" (or
  turned on "check at start" in Settings) and reads one public JSON file.
"""

from __future__ import annotations

import json
import os
import platform
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from opensweep import REPO_URL, __version__

MAX_BODY = 3500


def anonymize(text: str) -> str:
    """Replace the user's own folder and name in paths, so reports don't reveal who they are."""
    home = os.path.expanduser("~")
    user = os.path.basename(home)
    if home and home != "~":
        text = re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.IGNORECASE)
    if user and len(user) > 2:
        text = re.sub(rf"(?<![\w]){re.escape(user)}(?![\w])", "<user>", text, flags=re.IGNORECASE)
    return text


def issue_url(kind: str, title: str, details: dict[str, str]) -> str:
    """Link to a new GitHub issue pre-filled with what the user was looking at.

    kind: "misjudgment" (OpenSweep said something wrong about an item) or "bug".
    """
    lines = [f"**{k}:** {anonymize(str(v))}" for k, v in details.items() if v]
    lines += ["", f"OpenSweep {__version__} · {platform.system()} {platform.release()} ({platform.version()})",
              "", "<!-- What is wrong, and what should OpenSweep say or do instead? -->", ""]
    body = "\n".join(lines)[:MAX_BODY]
    query = urllib.parse.urlencode({"template": f"{kind}.yml" if kind else "", "title": anonymize(title)[:120],
                                    "body": body, "labels": kind})
    return f"{REPO_URL}/issues/new?{query}"


# ------------------------------------------------------------------ updates

@dataclass
class Release:
    version: str
    url: str
    notes: str


def _parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:4]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return _parse_version(candidate) > _parse_version(current)


def latest_release(timeout: float = 8.0) -> Release | None:
    """Ask GitHub for the newest release. None if offline or the repository has no releases yet."""
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)", REPO_URL)
    if not match:
        return None
    api = f"https://api.github.com/repos/{match.group(1)}/{match.group(2)}/releases/latest"
    request = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": f"OpenSweep/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    tag = data.get("tag_name") or ""
    if not tag:
        return None
    return Release(tag.lstrip("vV"), data.get("html_url") or f"{REPO_URL}/releases", (data.get("body") or "")[:2000])
