# KAIA 과제 — InSAR·PINN → VLM 안전성 평가 파이프라인

KAIA 과제의 전체 흐름과 **inframon(우리 파트)의 책임 경계**, VLM 팀으로의 **데이터 핸드오프
규약**을 정리한다. 1차년도 목표는 **InSAR + PINN 을 Bmaps 에 통합**(InSAR 필수, PINN 가능한 만큼).

## 전체 흐름

```
[우리 파트 — inframon]                          [타 팀 — VLM]
InSAR(수직변위)  ─┐                      ┌─ 정밀안전보고서
  asc+desc 융합   ├─ 통일 데이터 패키지 ─┼─ CAD 도면          ┐
PINN(가상센싱)  ─┘   (CSV·JSON·figures)  └─ InSAR·PINN(우리)  ┴→ VLM 입력
  전체 변위·EI·성분                                              │
                                              대한민국 시방서·법률 기반 안전성 평가
                                                       │
                                              텍스트 → 지식그래프
                                                       │
                                              기준 초과 시 보수·보강 추천
```

- **우리(inframon)**: InSAR 변위 추출 → PINN 구조해석 → **VLM 이 삼킬 수 있는 단위통일 데이터** 산출.
- **VLM 팀**: 우리 데이터 + CAD + 기존 보고서를 받아 시방서·법규로 안전성 평가 → 지식그래프 → 보수보강.
- 경계: **안전성의 최종 판정은 VLM+시방서**가 한다. 우리의 CRI·경보는 *참고용 내부 물리지표*로만 넘긴다.

## 우리 파트 단계별 현황

| 단계 | 산출 | 상태 | 진입점 |
|---|---|---|---|
| InSAR 수직변위 | LOS·종축·**연직(asc+desc 융합)** 변위장 | ✅ | `--engine insar=real --insar-source [--insar-source-desc]` |
| PINN 가상센싱 | 전체 변위장·4성분 분해·절대 EI·고유진동수 | ✅ | `--engine pinn=real` |
| CRI(참고) | 공명위험지수·4단계 경보 (시방서 판정 아님) | ✅ | `--engine fram=real` |
| **CSV 출력** | 점×시점 롱포맷 변위 테이블 | ✅ | `--export-csv <csv>` |
| **VLM 입력 패키지** | manifest·csv·summary·narrative·**knowledge_graph**·figures 번들 | ✅ | `--export-vlm <dir> [--zip] [--no-figures]` |
| **지식그래프(KG) 확장점** | 타입 있는 속성 그래프 + triples + JSON-LD | ✅ | `--export-kg <json>` |
| **VLM 백엔드 확장점** | 평가 백엔드 플러그인(Protocol+레지스트리) | ✅ 소켓 | `--vlm-eval <dir> [--vlm-backend NAME]` |
| Bmaps 연동 | 8 분석 엔드포인트 + **CSV/VLM 다운로드** | ✅ | `--serve-api` |
| 실데이터 관통 | 실 Sentinel-1 SLC → CRI | 🟡 데이터 대기 | WSL2(F코어), [실데이터 런북](실데이터_런북.md) |

## VLM 입력 패키지 규약 (핸드오프 계약)

`<bridge_id>_vlm/` 폴더 번들(또는 `.zip`):

| 파일 | 용도 |
|---|---|
| `manifest.json` | 자기기술 — 단위·enum·CSV 컬럼·스키마·provenance·**위험 면책**. VLM 팀이 추측 불필요 |
| `displacement.csv` | 점×시점 롱포맷(`export.py`) |
| `summary.json` | 구조화 다이제스트 — 변위통계·핫스팟·부재롤업·PINN(EI/저강성/고유진동수)·**가상센싱(상부거더 1D+상판 2D)**·위험참고 |
| `narrative.md` | 템플릿 자연어(LLM 아님) — VLM grounded 컨텍스트 |
| `knowledge_graph.json` | **지식그래프** — 타입 있는 속성 그래프(Bridge/Member/Point/StructuralParameter/VirtualSensingField/RiskSignal + 엣지). 온톨로지 자기기술 |
| `figures/*.png` | Vision 입력 — 변위맵·CRI히트맵·핫스팟시계열·PINN성분 |

## 향후 확장점 — 지식그래프 · VLM (소켓)

KAIA 후반(시방서 RAG·지식그래프·보수보강)은 VLM 팀 영역이지만, 그 모델·저장소가 **끼워질
소켓**을 inframon 이 규약으로 제공한다(구현은 타 팀).

- **지식그래프(`kg.py`)**: `build_graph(summary)` 가 VLM 다이제스트를 재사용해 타입 있는 속성
  그래프로 변환. `export_kg()`/`--export-kg` 는 그래프 JSON + `triples`(RDF) + `jsonld` 사이드카
  산출. 온톨로지(노드/엣지 타입·단위)가 자기기술 → 다른 KG 스키마는 어댑터로 변환해 붙인다.
- **VLM 백엔드(`vlm/` 서브패키지)**: `VLMBackend` Protocol + `register_backend`/`resolve_backend`
  (엔진 핫스왑과 동일 패턴). 기본 `template` 백엔드는 LLM 아님(grounded 스텁, 코드판정 아님).
  실 모델은 `register_backend("claude", factory)` 로 등록 → `--vlm-eval --vlm-backend claude`.
  평가 결과는 `assessment.json`(자기기술·면책).

**단위 통일**(전 산출물 공통): 변위 `mm` · 거리/고도 `m` · 날짜 `ISO YYYY-MM-DD` ·
좌표 `WGS84(lat,lon)` + 원좌표 `EPSG:5179` 병기 · 부재 enum `deck/pier/abutment/bearing` ·
CRI `[0,1]`. 모든 규약은 `manifest.json` 이 선언한다.

> 위험 면책(manifest `risk_disclaimer`, summary `risk_reference.note`): CRI·경보는 inframon
> 내부 물리지표이며 **시방서 기반 코드 판정이 아니다**. 최종 안전성 평가는 VLM+시방서가 한다.

### Bmaps/REST 경유 다운로드

```
GET /api/v1/bridges/{id}/insar/export.csv                  → text/csv
GET /api/v1/bridges/{id}/insar/vlm-package.zip?figures=    → application/zip
```
Bmaps 탭에 "변위 CSV / VLM 패키지(.zip)" 버튼으로 노출(`examples/bmaps_tab/`).

## 무엇이 부족한가 — 데이터 vs 구축

- **데이터 부족(코드는 됨)**: 실 Sentinel-1 SLC(InSAR 입구), 실 교량 제원(PINN EI 검증 G4),
  CAD·기존 안전보고서(VLM 입력 — 타 팀), 국내 교량 시방서·법규 코퍼스(VLM 평가 — 타 팀).
- **구축 미완(우리 범위 밖)**: 시방서 RAG 평가·지식그래프 *추론*·보수보강 추천은 **VLM 팀 영역**.
  단, inframon 은 이들이 끼워질 **확장점(KG 그래프 산출 + VLM 백엔드 소켓)**을 제공한다(위 참고).

## 1차년도 판정

InSAR + PINN + Bmaps 통합은 **소프트웨어 레벨에서 완성**(합성·데모 검증). VLM 으로의 데이터
핸드오프(CSV·패키지·다운로드 엔드포인트)까지 끝. 남은 우리 과제는 **실 SLC 로 전 파이프라인
한 번 관통**(WSL2)뿐이며, 그 외 KAIA 후반(시방서·KG·보수보강)은 VLM 팀이 우리 패키지를 입력으로
이어받는다.
