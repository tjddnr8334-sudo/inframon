"""실 Sentinel-1 SLC 자동 다운로드 — 자격증명만 있으면 레시피 장면을 프로그램이 받는다.

`slc_search` 가 고른 트랙(레시피 processing_manifest.json 의 정확한 granule 이름)을 ASF 에서
받는다. 처리(ISCE2/SARvey)는 WSL2 F코어지만 **다운로드는 순수 Python·Windows 가능**.

자격증명(우선순위): 명시 토큰 > 사용자/비번 > `~/.netrc`(urs.earthdata.nasa.gov). 어느
것도 없으면 `SlcAuthError` 로 명확히 안내(네트워크 인증 우회 불가). 네트워크 호출은
`_granule_search`/`_download_urls` 두 곳으로 격리(테스트에서 monkeypatch).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EARTHDATA_HOST = "urs.earthdata.nasa.gov"


class SlcAuthError(RuntimeError):
    """Earthdata 자격증명이 없어 다운로드할 수 없다(토큰/사용자·비번/.netrc 중 하나 필요)."""


class SlcRecipeError(RuntimeError):
    """레시피에서 다운로드할 장면 목록을 찾지 못했다."""


@dataclass
class SlcDownloadResult:
    requested: int          # 레시피 장면 수
    selected: int           # SLC·VV·중복제거 후
    downloaded: int         # 실제 받은 수(스킵 제외)
    skipped_existing: int   # 이미 있어서 건너뜀
    missing: list[str]      # granule 검색에서 못 찾은 이름
    gigabytes: float        # 선택 장면 총 용량(GB)
    out_dir: str
    auth: str               # 사용한 인증 방식(token|creds|netrc)

    def as_dict(self) -> dict:
        return {
            "requested": self.requested, "selected": self.selected,
            "downloaded": self.downloaded, "skipped_existing": self.skipped_existing,
            "missing": self.missing, "gigabytes": round(self.gigabytes, 1),
            "out_dir": self.out_dir, "auth": self.auth,
        }


def scene_names_from_recipe(recipe_dir: str | Path) -> list[str]:
    """레시피(processing_manifest.json) 의 stack.scene_names → granule 이름 목록."""
    recipe_dir = Path(recipe_dir)
    mf = recipe_dir / "processing_manifest.json"
    if not mf.exists():
        raise SlcRecipeError(f"processing_manifest.json 이 없습니다: {mf} "
                             "(먼저 --make-sarvey-config 로 레시피를 만드세요)")
    stack = json.loads(mf.read_text(encoding="utf-8")).get("stack", {})
    names = list(stack.get("scene_names") or [])
    if not names:
        raise SlcRecipeError(f"{mf} 에 stack.scene_names 가 비어 있습니다.")
    return names


def build_session(*, username: str | None = None, password: str | None = None,
                  token: str | None = None):
    """Earthdata 인증 ASF 세션 + 사용한 방식 문자열. 자격 없으면 SlcAuthError.

    우선순위: token > (username,password) > ~/.netrc. asf_search 는 지연 import.
    """
    import asf_search as asf

    if token:
        return asf.ASFSession().auth_with_token(token), "token"
    if username and password:
        return asf.ASFSession().auth_with_creds(username, password), "creds"
    import netrc
    try:
        auth = netrc.netrc().authenticators(EARTHDATA_HOST)
    except (FileNotFoundError, netrc.NetrcParseError):
        auth = None
    if auth and auth[0] and auth[2]:
        return asf.ASFSession().auth_with_creds(auth[0], auth[2]), "netrc"
    raise SlcAuthError(
        "Earthdata 자격증명이 없습니다. 다음 중 하나를 주세요:\n"
        "  --earthdata-token <토큰>  (urs.earthdata.nasa.gov 에서 발급)\n"
        "  --earthdata-user <ID> --earthdata-pass <PW>\n"
        f"  또는 ~/.netrc 에 'machine {EARTHDATA_HOST} login <ID> password <PW>'")


def _granule_search(names: list[str]) -> list[dict]:
    """ASF granule_search → properties dict 리스트(네트워크 격리 지점)."""
    import asf_search as asf

    return [dict(r.properties) for r in asf.granule_search(names)]


def _download_urls(urls: list[str], out_dir: str, session) -> None:
    """ASF download_urls(네트워크 격리 지점)."""
    import asf_search as asf

    asf.download_urls(urls=urls, path=out_dir, session=session)


def _select_slc_vv(props: list[dict], names: list[str]) -> tuple[list[dict], list[str]]:
    """granule 결과에서 **요청 장면**의 SLC·VV·중복제거만 남기고, 누락 이름을 함께 반환."""
    requested = set(names)
    seen: set[str] = set()
    sel: list[dict] = []
    for p in props:
        if p.get("processingLevel") != "SLC":
            continue
        if "VV" not in (p.get("polarization") or "").upper():
            continue
        name = p.get("sceneName") or p.get("fileID") or ""
        if not name or name in seen or name not in requested:
            continue
        seen.add(name)
        sel.append(p)
    missing = [n for n in names if n not in seen]
    return sel, missing


def download_recipe_slc(
    recipe_dir: str | Path,
    out_dir: str | Path | None = None,
    *,
    username: str | None = None,
    password: str | None = None,
    token: str | None = None,
    limit: int = 0,
    skip_existing: bool = True,
) -> SlcDownloadResult:
    """레시피의 선별 장면을 Earthdata 자격으로 자동 다운로드.

    out_dir 기본은 `<recipe_dir>/SLC`. limit=0 이면 전체, N>0 이면 처음 N장(테스트).
    skip_existing 이면 이미 받은 `.zip` 은 건너뛴다(증분 재개).
    """
    recipe_dir = Path(recipe_dir)
    out = Path(out_dir) if out_dir else recipe_dir / "SLC"
    out.mkdir(parents=True, exist_ok=True)

    names = scene_names_from_recipe(recipe_dir)
    session, auth = build_session(username=username, password=password, token=token)

    props = _granule_search(names)
    sel, missing = _select_slc_vv(props, names)
    if limit and limit > 0:
        sel = sel[:limit]
    gigabytes = sum((p.get("bytes") or 0) for p in sel) / 1e9

    to_get, skipped = [], 0
    for p in sel:
        name = p.get("sceneName") or p.get("fileID") or ""
        zpath = out / f"{name}.zip"
        if skip_existing and zpath.exists() and zpath.stat().st_size > 0:
            skipped += 1
            continue
        url = p.get("url")
        if url:
            to_get.append(url)

    if to_get:
        _download_urls(to_get, str(out), session)

    return SlcDownloadResult(
        requested=len(names), selected=len(sel), downloaded=len(to_get),
        skipped_existing=skipped, missing=missing, gigabytes=gigabytes,
        out_dir=str(out), auth=auth,
    )
