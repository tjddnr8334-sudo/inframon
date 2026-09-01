# Pontifex 연동 검토 — 스마트인사이드에이아이 전달본 `pontifex-1.0`

> 받은 것: 전국 교량 33,120개 모니터링 웹 플랫폼(GeoDjango + PostGIS + Docker). inframon
> 산출을 JSON API 로 받는다. **우리 목표 체인의 마지막 고리(⑭ BMAP 등록)가 실제로 닿는
> 첫 상대 시스템**이다.
>
> 검토일 2026-09-01 · inframon `main` 기준 · 검토 범위: README·install.sh·Dockerfile·
> compose·`scripts/ingest_inframon.py`·번들된 `refs/inframon` 스냅샷.

## 요약

| 항목 | 판단 |
|---|---|
| 연동 계약(4-A JSON API) | **명확하다.** 필드·필수 여부·등급 매핑·인증까지 문서에 다 있다 |
| 우리 산출물과의 호환 | **맞다** — 실 `project.h5`(청양 20000점×29시점)로 29 레코드 생성 확인 |
| 4-B 스크립트 경로 | **실데이터에서 항상 깨진다** (아래 A-1). 한 줄 수정 필요 |
| 번들된 inframon | **6주 전 스냅샷**(2026-07-21). 그 사이 우리 쪽 중대 수정이 다수 |
| 지금 올려도 되는 값 | **없다** — 우리 산출물 14건 전부 '보고 가능' 아님(`docs/산출물_감사표.md`) |

## A. 상대 코드에서 확인한 결함

### A-1. `scripts/ingest_inframon.py:76` — 실데이터 경로가 항상 죽는다 (치명)

```python
labels = [str(x).decode() if isinstance(x, bytes) else str(x)   # ← str 에는 decode 가 없다
          for x in f["/insar/date_labels"][:]]
```

`str(x)` 는 이미 `str` 이라 `.decode()` 가 항상 `AttributeError` 다. h5py 는 고정폭 문자열을
`bytes`(`|S8`)로 돌려주므로 **`date_labels` 가 있는 모든 실데이터에서** 이 분기를 탄다.
즉 "절대일자(basis=absolute)" 경로 — 실 InSAR 산출 경로 — 가 통째로 실행되지 않는다.
합성 데모(`date_labels` 없음)만 통과하므로 데모 검증에서는 드러나지 않았다.

재현:
```
$ PYTHONPATH=src python scripts/ingest_inframon.py data/chyg/project.h5 --bridge-id 40001
AttributeError: 'str' object has no attribute 'decode'. Did you mean: 'encode'?
```

수정(한 줄):
```python
labels = [x.decode() if isinstance(x, bytes) else str(x) for x in f["/insar/date_labels"][:]]
```

이 한 줄만 고치면 나머지는 그대로 동작한다 — 우리 실 산출물로 확인했다:

```
경보 등급        : 정상 (최종 관측)
CRI global max  : 0.7400
관측            : 20000점 × 29시점 (2018-01-02 ~ 2023-12-26, basis=absolute)
날짜별 level 분포: 정상 13, 주의 12, 경고 4
산출물: sensing_summary_records.json (29 레코드)
```

### A-2. 번들된 `refs/inframon` 이 6주 전 스냅샷이다 (높음)

`refs/inframon/src/inframon/pipeline_bridge.py` 가 우리 `f1f7233`(2026-07-21)과 일치한다.
그 이후 들어간 것 중 연동에 직접 영향을 주는 것:

- **⑧ 처리 엔진 선택**(SNAP·HyP3·SARvey·MintPy·MiaplPy·StaMPS) — 산출 경로 자체가 늘었다
- **⑨ 데크 마스킹** — 광역 PSI 필드를 교량 범위로 자른다(honam 118,043점 → 39점)
- **위상 언래핑(snaphu)** — 아래 B의 핵심
- **preflight 게이트·출처 각인·산출물 감사** — 무효 산출물을 하류로 보내지 않는다

`refs/` 는 참조용이므로 급하지 않지만, 재현 안내(4-B)가 이 스냅샷을 `pip install -e` 하도록
쓰여 있어 **사용자가 6주 전 코드로 산출물을 만들게 된다**. 최신 태그를 받아가시길 권한다.

### A-3. 사소한 것

- `install.sh` 는 Docker·Compose·포트·패키지 무결성을 먼저 검사하고 멱등이다. 검토 결과
  외부 다운로드나 호스트 권한 상승이 없다 — **설치 스크립트 자체는 안전하다**고 본다.
- `docker-compose.yml` 이 프로젝트 전체(`../:/pontifex`)를 컨테이너에 마운트한다. 개발
  편의로는 맞지만 `--prod` 에서는 코드만 마운트하는 편이 낫다(`.env` 가 함께 올라간다).
- dev 기본이 인증 비활성(`PONTIFEX_INGEST_TOKEN` 미설정)이다. 문서에 명시돼 있어 의도된
  선택으로 보이나, 컨테이너가 `0.0.0.0:38000` 으로 열리므로 같은 네트워크의 다른 기기가
  인제스트 API 에 접근할 수 있다. 기본을 `127.0.0.1:38000` 바인딩으로 두는 편이 안전하다.

## B. 지금 올려도 되는 값이 없다 — 우리 쪽 사정 (가장 중요)

플랫폼은 잘 만들어졌지만, **우리가 지금 넘길 수 있는 유효한 CRI 가 0건이다.**

우리 SNAP 레인은 최근까지 **위상 언래핑을 하지 않았다**(`snaphu` 호출 0건). 래핑 위상에
−λ/4π 를 곱해 그대로 LOS[mm] 로 썼으므로, 변위가 λ/4(13.87mm)를 넘는 순간 값이 접힌다.
그렇게 나온 CRI 는 물리적 의미가 없다. 산출물 전수 감사 결과(`docs/산출물_감사표.md`):

- 14건 중 **보고 가능 0 · 조건부 7 · 보고 불가 7**
- 청양교 CRI 0.740 — 래핑 위상 + 교량 30m 내 7/20000점(0.03%)
- 호남 CRI 0.985 — 언래핑은 됐으나 **PINN 경간 16,968m vs 실연장 50m(×339)**

플랫폼 `data/ingest_out/` 에 들어 있는 예시 센싱데이터도 그 시절 산출이므로, 화면에 보이는
CRI 를 **성능 근거로 인용하지 말아 주시길** 부탁드린다.

우리 쪽 조치는 끝났다 — 언래핑 3단계(SnaphuExport → snaphu → SnaphuImport)를 넣고 실
1쌍으로 성립을 확인했다(|LOS|max 13.87mm → 149.2mm, λ/4 초과 68.3%). 재처리 후 유효한
값으로 다시 연락드리겠다.

## C. 우리가 붙인 연동 (inframon 쪽)

`--pontifex-push` 로 ⑭를 플랫폼에 직접 보낸다. **감사에서 '보고 불가'면 기본적으로 막는다**
— 무의미한 수치가 남의 플랫폼에 '측정값'으로 남으면 되돌리기 어렵기 때문이다.

```bash
# 무엇을 올릴지 먼저 본다(전송 없음)
python -m inframon --pontifex-push data/chyg/project.h5 --pontifex-bridge-id 40001 --pontifex-dry-run

# 교량 등록 + 센싱 적재를 한 번에
python -m inframon --pontifex-register "청양교,36.4547,126.8013" \
    --pontifex-push data/chyg/project.h5 --pontifex-token "$TOKEN"
```

- 관측일이 없는 합성 산출물은 **거부**한다 — 오늘 날짜로 앵커링해 올리면 플랫폼에 가짜
  관측일이 박힌다(4-B 스크립트의 `synthetic_anchored_today` 는 데모 표시용으로만 쓰시길).
- `warning_level` 은 FRAM 등급 0~3 을 그대로 쓴다(플랫폼 문서와 동일).
- 플랫폼이 안 떠 있으면 `docker compose ps` 를 확인하라고 안내한다.

## D. 아직 확인하지 못한 것

**웹 UI 는 아직 못 봤다.** 이 개발 PC 에 Docker 가 설치돼 있지 않아(Windows·WSL 모두)
`./install.sh` 를 돌리지 못했다. 설치 후 다음을 확인할 예정이다:

- `/bridge/<id>/` BIM(IFC) 뷰어의 부재 색상 코딩이 `express_ids` 와 맞는가
- CRI 시계열 차트가 29시점을 그리는가(우리 실 산출물 기준)
- 시군구 자동 매칭이 우리 대상 교량(청양교·정자교)에서 맞는가

## E. 요청드리는 것

1. `ingest_inframon.py:76` 한 줄 수정 (A-1)
2. `refs/inframon` 을 최신으로 교체 (A-2)
3. 화면의 CRI 를 성능 근거로 인용하지 않기 — 유효한 값은 재처리 후 전달 (B)
4. `member_records.express_ids` 를 우리가 채우려면, IFC 프록시의 부재별 express id 목록을
   구조형식(girder/box_girder/…)마다 알려주시면 좋겠다. 지금은 `cri_value` 만 채운다.
