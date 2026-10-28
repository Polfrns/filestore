"""Convert a Markdown file to a self-contained PDF, embedding local images.

Usage: python md_to_pdf.py input.md output.pdf

Renders md -> HTML (images inlined as base64 data URIs) -> PDF via headless
Edge/Chrome. Self-contained so relative image paths and file access don't matter.
"""

import base64
import mimetypes
import re
import subprocess
import sys
from pathlib import Path

import markdown

CSS = """
@page { margin: 1.6cm; }
body {
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.45;
    color: #1a1a1a;
    max-width: 100%;
}
h1 { font-size: 20pt; border-bottom: 2px solid #ccc; padding-bottom: 4px; }
h2 { font-size: 15pt; margin-top: 1.4em; border-bottom: 1px solid #e0e0e0; padding-bottom: 2px; }
h3 { font-size: 12pt; }
img { max-width: 100%; height: auto; display: block; margin: 0.8em auto; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #bbb; padding: 5px 9px; text-align: left; font-size: 10pt; }
th { background: #f2f2f2; }
code { background: #f4f4f4; padding: 1px 4px; border-radius: 3px; font-size: 9.5pt; }
"""


def embed_images(html: str, base_dir: Path) -> str:
    def repl(match):
        src = match.group(1)
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        img_path = (base_dir / src).resolve()
        if not img_path.exists():
            print(f"  WARNING: image not found: {img_path}")
            return match.group(0)
        mime = mimetypes.guess_type(str(img_path))[0] or "image/png"
        data = base64.b64encode(img_path.read_bytes()).decode("ascii")
        return f'src="data:{mime};base64,{data}"'

    return re.sub(r'src="([^"]+)"', repl, html)


def find_browser() -> str:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    raise RuntimeError("No Edge/Chrome found for PDF rendering")


def main():
    md_path = Path(sys.argv[1]).resolve()
    pdf_path = Path(sys.argv[2]).resolve()

    body = markdown.markdown(md_path.read_text(encoding="utf-8"), extensions=["tables"])
    body = embed_images(body, md_path.parent)
    html = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"

    html_path = pdf_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")

    browser = find_browser()
    subprocess.run(
        [
            browser,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ],
        check=True,
        timeout=120,
    )
    html_path.unlink()
    print(f"Wrote {pdf_path} ({pdf_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
