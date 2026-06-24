"""다중 교량 레지스트리 — bridge_id ↔ project.h5 매핑.

inframon 은 본래 "교량 1개 = project.h5 1개"라 교량 식별자 개념이 없다. Bmaps 는
교량관리번호로 다수 교량을 식별하므로, 그 사이를 잇는 레지스트리가 첫 연결 고리다
(설계 §2).

`bridge_registry.json` 형식::

    {
      "bridges": [
        {
          "bridge_id": "KICT-2024-00137",   # Bmaps 교량관리번호 (PK)
          "name": "정자교",
          "project_h5": "jeongjagyo/project.h5",  # 레지스트리 파일 기준 상대/절대
          "wgs84_center": [37.3219, 127.1083],    # (lat, lon) 지도 초기 뷰 (선택)
          "track_source": "track_2024H1.h5",      # (선택) 출처 메모
          "last_run_utc": "2026-06-01T03:00:00Z"  # (선택) 갱신 시각/캐시 키
        }
      ]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class RegistryError(Exception):
    """레지스트리 파일/항목 문제."""


@dataclass(frozen=True)
class BridgeEntry:
    bridge_id: str
    name: str
    project_h5: Path
    wgs84_center: tuple[float, float] | None = None
    track_source: str | None = None
    last_run_utc: str | None = None

    def exists(self) -> bool:
        """연결된 project.h5 가 실재하는지(InSAR 산출 여부는 transform 단계에서 판단)."""
        return self.project_h5.exists()


class BridgeRegistry:
    """`bridge_registry.json` 을 읽어 bridge_id 로 교량을 조회한다."""

    def __init__(self, entries: list[BridgeEntry]):
        self._by_id: dict[str, BridgeEntry] = {}
        for e in entries:
            if e.bridge_id in self._by_id:
                raise RegistryError(f"중복 bridge_id: {e.bridge_id!r}")
            self._by_id[e.bridge_id] = e

    # ── 로딩 ──
    @classmethod
    def from_file(cls, path: str | Path) -> "BridgeRegistry":
        p = Path(path)
        if not p.exists():
            raise RegistryError(f"레지스트리 파일이 없습니다: {p}")
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RegistryError(f"레지스트리 JSON 파싱 실패: {p} — {exc}") from exc

        base = p.parent
        bridges = raw.get("bridges")
        if not isinstance(bridges, list):
            raise RegistryError("레지스트리 최상위에 'bridges' 배열이 필요합니다.")

        entries: list[BridgeEntry] = []
        for i, b in enumerate(bridges):
            try:
                bid = str(b["bridge_id"])
                name = str(b.get("name", bid))
                h5_raw = Path(str(b["project_h5"]))
            except (KeyError, TypeError) as exc:
                raise RegistryError(
                    f"bridges[{i}] 에 bridge_id·project_h5 가 필요합니다."
                ) from exc
            # 상대경로는 레지스트리 파일 위치 기준으로 해석한다.
            h5 = h5_raw if h5_raw.is_absolute() else (base / h5_raw)
            center = b.get("wgs84_center")
            center_t = (
                (float(center[0]), float(center[1]))
                if isinstance(center, (list, tuple)) and len(center) == 2
                else None
            )
            entries.append(
                BridgeEntry(
                    bridge_id=bid,
                    name=name,
                    project_h5=h5.resolve(),
                    wgs84_center=center_t,
                    track_source=(str(b["track_source"]) if b.get("track_source") else None),
                    last_run_utc=(str(b["last_run_utc"]) if b.get("last_run_utc") else None),
                )
            )
        return cls(entries)

    @classmethod
    def single(cls, bridge_id: str, name: str, project_h5: str | Path) -> "BridgeRegistry":
        """레지스트리 파일 없이 단일 교량으로 구성(기존 --serve 호환·테스트용)."""
        return cls([BridgeEntry(bridge_id=bridge_id, name=name,
                                project_h5=Path(project_h5).resolve())])

    # ── 조회 ──
    def list(self) -> list[BridgeEntry]:
        return list(self._by_id.values())

    def get(self, bridge_id: str) -> BridgeEntry | None:
        return self._by_id.get(bridge_id)

    def __len__(self) -> int:
        return len(self._by_id)
