from __future__ import annotations

import base64

import requests

from .config import DB_PATH, GITHUB_BRANCH, GITHUB_DB_PATH, GITHUB_REPO, GITHUB_TOKEN


def enabled() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_REPO)


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def download_db() -> None:
    if not enabled():
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DB_PATH}"
    r = requests.get(url, headers=headers(), params={"ref": GITHUB_BRANCH}, timeout=30)
    if r.status_code == 404:
        return
    r.raise_for_status()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_bytes(base64.b64decode(r.json()["content"]))


def upload_db(commit_message: str = "Actualiza DB mantenimiento Mapit") -> None:
    if not enabled() or not DB_PATH.exists():
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DB_PATH}"
    get = requests.get(url, headers=headers(), params={"ref": GITHUB_BRANCH}, timeout=30)
    sha = get.json().get("sha") if get.status_code == 200 else None
    if get.status_code not in (200, 404):
        get.raise_for_status()
    payload = {
        "message": commit_message,
        "content": base64.b64encode(DB_PATH.read_bytes()).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=headers(), json=payload, timeout=30)
    r.raise_for_status()
