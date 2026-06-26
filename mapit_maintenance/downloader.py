from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from .config import DOWNLOAD_DIR


def find_mapit_report_links(text: str) -> list[str]:
    text = unescape(text or "")
    candidates = re.findall(r'https?://[^\s"\'<>]+', text)
    links: list[str] = []
    for raw in candidates:
        url = raw.rstrip(").,;]")
        decoded = unquote(url)
        if "route-report" in decoded or "InformeMapit" in decoded or "pdfUrl=" in decoded:
            links.append(url)
    seen = set()
    out = []
    for link in links:
        if link not in seen:
            seen.add(link)
            out.append(link)
    return out


def filename_from_url(url: str, fallback: str = "InformeMapit.pdf") -> str:
    decoded = unquote(url)
    m = re.search(r"(InformeMapit_[^/?#]+\.pdf)", decoded)
    if m:
        return re.sub(r"[^A-Za-z0-9_.() -]+", "_", m.group(1))
    name = Path(urlparse(decoded).path).name
    if name.lower().endswith(".pdf"):
        return re.sub(r"[^A-Za-z0-9_.() -]+", "_", name)
    return fallback


def download_pdf_from_link(link: str, dest_dir: Path = DOWNLOAD_DIR) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename_from_url(link)
    headers = {"User-Agent": "Mozilla/5.0 mapit-maintenance/4.0"}
    r = requests.get(link, headers=headers, allow_redirects=True, timeout=60)
    r.raise_for_status()
    content_type = r.headers.get("content-type", "").lower()
    if "pdf" not in content_type and not r.content.startswith(b"%PDF"):
        links = find_mapit_report_links(r.text)
        if links and links[0] != link:
            return download_pdf_from_link(links[0], dest_dir)
        raise RuntimeError(f"El enlace no devolvió un PDF. Content-Type={content_type}")
    dest.write_bytes(r.content)
    return dest
