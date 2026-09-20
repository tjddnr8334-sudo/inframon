#!/usr/bin/env python3
"""제안서를 **파일 하나로** 뽑는다 — Claude 로그인 없이 누구나 열 수 있게.

artifact 링크는 claude.ai 에 있어서 계정 없이 열리는지 장담할 수 없다. 그래서 같은
내용을 **자기완결 HTML 한 장**으로 만든다. 그림을 data URI 로 안에 박아 넣으므로
파일 하나만 있으면 되고, 메일에 붙이거나 USB 에 담아 가도 그대로 열린다.

글꼴은 인터넷이 되면 Google Fonts 에서 받고, 안 되면 맑은 고딕으로 떨어진다 —
어느 쪽이든 글은 다 읽힌다.

    python scripts/export_proposal.py --src <artifact.html> --out docs/제안서_Inframon.html

산출: 자기완결 HTML (그림 포함)
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEAD = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>html{color-scheme:light dark}body{margin:0}img{max-width:100%}</style>
"""


def inline(html: str, assets: Path) -> tuple[str, list]:
    """<img src="a.png"> 를 data URI 로 바꾼다."""
    done = []

    def sub(m):
        name = m.group(1)
        p = assets / name
        if not p.exists():
            done.append((name, "못 찾음"))
            return m.group(0)
        mime = mimetypes.guess_type(name)[0] or "image/png"
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        done.append((name, f"{p.stat().st_size / 1000:.0f} KB"))
        return f'src="data:{mime};base64,{b64}"'

    return re.sub(r'src="([^":]+\.(?:png|jpg|jpeg|svg))"', sub, html), done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="artifact 로 올린 HTML")
    ap.add_argument("--assets", default=str(ROOT / "docs" / "img" / "proposal"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "제안서_Inframon.html"))
    a = ap.parse_args()

    body = Path(a.src).read_text(encoding="utf-8")
    body, done = inline(body, Path(a.assets))
    out = Path(a.out)
    out.write_text(HEAD + body + "\n</body>\n</html>\n", encoding="utf-8")
    for n, s in done:
        print(f"   그림 {n:<16} {s}")
    print(f"만들었다: {out}  ({out.stat().st_size / 1e6:.2f} MB · 파일 하나로 끝)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
