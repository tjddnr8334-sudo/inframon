# B-Maps 탑재 — 산출물을 플랫폼에 올리고 **계속 갱신하기**

> 대상 플랫폼: Pontifex(스마트인사이드에이아이) — 전국 교량 33,120개 GeoDjango+PostGIS.
> 연동 계약은 `docs/Pontifex_연동_검토.md`, 산출물 품질 기준은 `docs/산출물_감사표.md`.

## 한 줄

```bash
python -m inframon --bmap-sync --bmap-register --pontifex-token "$TOKEN"
```

`data/` 아래 산출물을 모아 **감사한 뒤 통과한 것만** 올린다. 바뀐 것만 보내고, 무엇을 왜
올렸는지/걸렀는지 `data/sync_state.json` 에 남긴다.

## 왜 게이트가 앞에 있나

남의 플랫폼에 올라간 수치는 되돌리기 어렵다. 지금까지 실제로 있었던 일:

- 위상 언래핑을 안 해 LOS 가 ±13.87mm 에 갇힌 산출물 — CRI 0.740 이 물리적 의미 없음
- 6×6km 광역 필드에서 교량 30m 안에 점이 6개(0.03%) — 교량이 아니라 지반을 잰 값
- PINN 경간이 실연장의 339배 — 다른 교량 제원으로 구조해석
- 1차 고유진동수 232Hz — 90m 교량에서 불가능(EI 식별 실패를 값으로 오해)

`--bmap-sync` 는 이 여섯 가지를 매번 검사한다(`--audit-artifacts` 와 같은 기준).
**❌ 보고 불가는 어떤 경우에도 올라가지 않는다**(`--pontifex-force` 를 명시하지 않는 한).

| 옵션 | 뜻 |
|---|---|
| `--bmap-dry-run` | 무엇을 올릴지만 보여준다(전송·기록 없음) |
| `--bmap-strict` | ✅ 보고 가능만 올린다(🟡 조건부도 제외) |
| `--bmap-register` | 플랫폼 교량 id 가 없으면 등록하고 받은 id 를 기록 |
| `--bmap-root DIR` | 대상 폴더(기본 `data`) |
| `--pontifex-base URL` | 플랫폼 주소(기본 `http://localhost:38000`) |

## 처음 한 번 — 교량 id 잇기

플랫폼의 `bridge.id` 는 **사람이 정하는 값**이라 지어내지 않는다. 두 가지 방법:

1. `--bmap-register` — 이름·좌표로 플랫폼에 등록하고 받은 id 를 산출물 곁
   `bridge_target.json` 의 `pontifex_id` 에 적는다. 다음 실행부터 그 id 를 재사용한다.
   (적어두지 않으면 실행할 때마다 새 교량이 만들어진다.)
2. 이미 플랫폼에 있는 교량이면 `bridge_target.json` 에 `"pontifex_id": 40001` 을 직접 적는다.

## 계속 갱신하기

새 SLC 가 쌓이면:

```bash
python scripts/rerun_unwrap_batch.py --stack all        # ① 언래핑 재처리(쌍은 재사용)
python -m inframon --audit-artifacts                    # ② 무엇이 통과했나
python -m inframon --bmap-sync --pontifex-token "$TOKEN"  # ③ 통과분만 반영
```

②는 생략해도 된다 — ③이 같은 감사를 다시 돌린다. 산출물 내용이 그대로면 ③은
`변경없음` 으로 건너뛰므로 매일 돌려도 안전하다(cron·작업 스케줄러에 그대로 넣을 수 있다).

## 지금 상태 (2026-09-02)

| 교량 | 감사 | 올릴 수 있나 |
|---|---|---|
| 청양교 (25시점·데크 48점) | 🟡 조건부 | ○ — 사유(EI 설계값 기준)와 함께 |
| 정자교 (6시점·데크 47점) | 🟡 조건부 | ○ — 사유(EI·실 제원 확인)와 함께 |
| 그 외 옛 산출물 14건 | ❌ 보고 불가 | ✗ — 재처리 필요(런북 참조) |

**아직 ✅ 가 아닌 공통 사유**: 강성(EI) 절대식별이 수렴하지 않아 고유진동수를 설계 제원
기준으로 낸다. 단일 궤도 LOS 만으로는 처짐형상이 휨 정보를 충분히 담지 못한다 —
asc+desc 융합으로 **연직 변위**를 얻으면 형상 기반 식별(`_ei_from_shape`)이 열린다.
그때까지 EI·고유진동수는 "설계 제원 기준"으로 표시되며, **변위·CRI 는 관측 기반**이다.

## 플랫폼이 안 떠 있으면

```
⛔ http://localhost:38000/api/ingest/sensing/ 에 연결하지 못했습니다 —
   Pontifex 스택이 떠 있는지 확인하세요(cd backend && docker compose ps).
```

Docker 가 없는 PC 에서는 `--bmap-dry-run` 으로 무엇이 올라갈지까지 확인할 수 있다.
