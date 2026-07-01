#!/usr/bin/env python3
"""URL 목록의 Sentinel-1 SLC 를 ASF 에서 다운로드 (WSL2 내부 실행용).

대시보드가 선택한 장면 URL 들을 파일로 넘기면, ~/.netrc 의 Earthdata 자격으로
인증해 지정 폴더에 받는다. 받은 SLC 는 같은 WSL 의 ISCE2/SARvey 처리 입력이 된다.

사용: python3 dl_urls.py --urls urls.txt --out ~/insar_dl/SLC
"""
from __future__ import annotations

import argparse
import netrc
import os


def main() -> None:
    p = argparse.ArgumentParser(description="ASF SLC 다운로드(URL 목록, netrc 인증)")
    p.add_argument("--urls", required=True, help="한 줄에 하나씩 SLC URL")
    p.add_argument("--out", required=True, help="다운로드 폴더")
    a = p.parse_args()
    import asf_search as asf

    urls = [ln.strip() for ln in open(a.urls, encoding="utf-8") if ln.strip()]
    os.makedirs(a.out, exist_ok=True)

    session = asf.ASFSession()
    try:
        auth = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
        if auth:
            session = session.auth_with_creds(auth[0], auth[2])
            print(f"netrc 인증: {auth[0]}")
    except Exception as exc:  # noqa: BLE001
        print(f"netrc 인증 경고({exc}) — 익명 시도")

    print(f"다운로드 {len(urls)}장 → {a.out}")
    asf.download_urls(urls, path=a.out, session=session, processes=2)
    zips = [f for f in os.listdir(a.out) if f.lower().endswith(".zip")]
    print(f"DONE: zip {len(zips)}개 in {a.out}")


if __name__ == "__main__":
    main()
