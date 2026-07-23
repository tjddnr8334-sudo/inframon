# configs/ — 공유·수정 가능한 교량 입력

이 디렉터리는 **협업자가 편집하는 곳**이다. 교량 하나를 정의하는 작은 텍스트 입력만
들어가고, 무거운/민감한 데이터는 들어오지 않는다.

## 왜 `data/` 가 아니라 여기인가

`data/` 는 통째로 `.gitignore` 되어 있다. 거기엔 SLC·`project.h5`(수십 MB)·중간 산출물이
쌓이고, 그건 git 에 올릴 것이 아니다. 그런데 그 규칙이 **레시피·부재 테이블 같은 몇 KB짜리
텍스트 입력까지 같이 막아** 협업자가 교량 설정을 공유하거나 고칠 수 없었다.

그래서 둘을 갈랐다.

| | 위치 | git | 예 |
|---|---|---|---|
| **공유 입력** | `configs/<교량>/` | ✅ 추적 | 레시피 4종, 부재 테이블, 좌표 정합, 한계값 |
| **작업 데이터** | `data/` | ❌ 무시 | SLC, `*.h5`, 중간 산출물, 스크린샷 원본 |

## 한 교량 디렉터리에 들어가는 것

| 파일 | 무엇 | 만드는 법 |
|---|---|---|
| `bridge_target.json` | 교량 타깃(OSM way·AOI bbox·연장) | 대시보드 ① 탭 지도 또는 `osm_bridge` |
| `selection_criteria.json` | SLC 선별 기준(편파·baseline) | 대시보드 ① 탭 |
| `track_selection.json` | 선택된 트랙·장면 목록 | `slc_search` (ASF) |
| `master_selection_era5.json` | ERA5 master 선정(선택) | `--make-sarvey-config` 전에 생성 |
| `bim_elements.json` / `.csv` | BIM 부재 테이블(GUID·타입·경계상자) | IFC 에서 추출 또는 도면에서 작성 |
| `bim_map_conversion.json` | IFC 로컬↔지도 좌표 변환 | IFC `IfcMapConversion` 또는 수기 |
| `bim_control_points.json` | 측량 기준점 쌍(IfcMapConversion 없을 때) | 측량 성과 |
| `life_limits.json` | 잔존수명 한계값(침하·처짐·각변위) | 설계도서·지반조사 |

**전부 선택**이다. 있는 것만 두면 되고, 없으면 해당 기능이 기본값으로 동작하거나
사유와 함께 비활성된다.

## 쓰는 법

```bash
# SLC 처리 번들 생성 (+ GNSS 지상 근거)
python -m inframon --make-sarvey-config configs/jeongjagyo --gnss-anchor-km 60

# BIM 정합 — 부재 테이블·좌표변환을 여기서 읽는다
python -m inframon --bim-align data/project.h5,configs/jeongjagyo/bim_elements.json,out/bridge \
  --bim-map-conversion configs/jeongjagyo/bim_map_conversion.json --bim-source-crs EPSG:4326
```

산출물(`processing_manifest.json`·`sarvey_config.json`)도 여기 생기지만 **git 은 무시한다**
(`.gitignore`). 입력에서 언제든 재생성되고, 커밋해 두면 입력과 어긋난 채 남아 오해를 부른다.

## 새 교량 추가하기

```bash
cp -r configs/jeongjagyo configs/<새교량>
# bridge_target.json 의 좌표·OSM id·bbox 를 바꾸고 나머지를 채운다
```

## 여기 올리면 안 되는 것

- **SLC·`*.h5`·GeoTIFF** — 용량. `data/` 에 두고 경로로 참조한다.
- **API 키·serviceKey·`.netrc`** — data.go.kr·EX 교통 API 키는 환경변수나 로컬 파일로.
  이 디렉터리는 공개 리포에 그대로 올라간다.
- **비공개 도면·좌표** — 발주처가 공개를 허락하지 않은 측량 성과·IFC 는 올리지 말 것.
  대신 `configs/<교량>/README.md` 에 "어디서 받는지"만 적는다.

커밋 전에 `git diff --cached` 로 한 번 훑는 습관을 권한다.
