#!/usr/bin/env python3
"""ASF 버스트 SLC 제품(.tiff + 메타데이터 .xml) 을 버스트 ID 별로 내려받는다. Windows·WSL 어디서든 돈다.

    python scripts/wsl_gwdata/download_bursts.py --out E:/InSAR_DATA/gangwon/bursts \
        --bursts 054_114060_IW2 054_114061_IW2 054_114062_IW2 --start 2025-01-01 --pol VV

자격증명: ~/.netrc 의 urs.earthdata.nasa.gov (없으면 EARTHDATA_TOKEN 환경변수).
ASF 버스트 추출기는 처음 요청하면 202 {"message":"Accepted"} 를 주고 비동기로 제품을 만든다.
준비될 때까지(보통 1~3 분) 20 초마다 다시 요청한다. 이미 받은 유효 파일은 건너뛴다.
결과는 out/<burst>/<granule>.tiff, .xml 과 out/<burst>/manifest.json.
"""
from __future__ import annotations

import argparse
import json
import netrc
import os
import sys
import time
from pathlib import Path

import asf_search as asf

URS = "urs.earthdata.nasa.gov"
TIFF_MAGIC = b"II*" + bytes([0])


def session() -> asf.ASFSession:
    tok = os.environ.get("EARTHDATA_TOKEN")
    if tok:
        return asf.ASFSession().auth_with_token(tok)
    auth = netrc.netrc().authenticators(URS)
    if not auth:
        sys.exit(f"~/.netrc 에 {URS} 항목이 없다")
    return asf.ASFSession().auth_with_creds(auth[0], auth[2])


def valid(dest: Path) -> bool:
    """받은 파일이 진짜 제품인지: tiff 는 'II*' 헤더, xml 은 '<' 로 시작하고 1 KB 이상."""
    if not dest.exists() or dest.stat().st_size < 1024:
        return False
    with dest.open("rb") as f:
        head = f.read(4)
    return head.startswith(TIFF_MAGIC) if dest.suffix == ".tiff" else head.startswith(b"<")


def fetch(url: str, dest: Path, s: asf.ASFSession, wait_max_s: int = 1800) -> str:
    if valid(dest):
        return "skip"
    tmp = dest.with_suffix(dest.suffix + ".part")
    t0 = time.time()
    err: object = None
    while time.time() - t0 < wait_max_s:
        try:
            with s.get(url, stream=True, timeout=300, allow_redirects=True) as r:
                if r.status_code == 202:
                    time.sleep(20)
                    continue
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            tmp.replace(dest)
            if valid(dest):
                return f"ok({int(time.time() - t0)}s)"
            dest.unlink(missing_ok=True)
            err = "invalid content"
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(10)
    return f"fail: {err or 'timeout waiting for 202'}"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bursts", nargs="+", required=True, help="fullBurstID 예: 054_114061_IW2")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--pol", default="VV")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    s = None if a.dry_run else session()
    n_ok = n_skip = n_fail = 0
    for b in a.bursts:
        res = asf.search(dataset=asf.DATASET.SLC_BURST, fullBurstID=b, polarization=a.pol,
                         start=a.start, end=a.end)
        res = sorted(res, key=lambda r: r.properties["startTime"])
        outdir = Path(a.out) / b
        outdir.mkdir(parents=True, exist_ok=True)
        span = f"{res[0].properties['startTime'][:10]} .. {res[-1].properties['startTime'][:10]}" if res else "-"
        print(f"[{b}] {len(res)} products {span}", flush=True)
        manifest = []
        for r in res:
            p = r.properties
            name = p["sceneName"]
            tiff = outdir / f"{name}.tiff"
            xml = outdir / f"{name}.xml"
            entry = dict(granule=name, date=p["startTime"][:10], startTime=p["startTime"],
                         bytes=p.get("bytes"), url=p["url"], xml_url=(p.get("additionalUrls") or [None])[0],
                         platform=p.get("platform"), orbit=p.get("orbit"), tiff=str(tiff), xml=str(xml))
            if not a.dry_run:
                st = fetch(p["url"], tiff, s)
                sx = fetch(entry["xml_url"], xml, s) if entry["xml_url"] else "no-xml-url"
                entry["status"] = f"{st}/{sx}"
                n_ok += st.startswith("ok")
                n_skip += st == "skip"
                n_fail += st.startswith("fail")
                print(f"  {p['startTime'][:10]} {name} {st} xml:{sx}", flush=True)
            manifest.append(entry)
            # 제품마다 갱신 — 중간에 끊겨도 받은 만큼은 build_safes.py 가 쓸 수 있게
            (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"done ok={n_ok} skip={n_skip} fail={n_fail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
