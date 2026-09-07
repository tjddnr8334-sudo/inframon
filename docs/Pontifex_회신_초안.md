# Pontifex 전달본 회신 — 초안

> **보내기 전에 확인해 주세요.** 아래 본문을 그대로 메일에 붙이면 됩니다.
> 첨부 권장: 없음(재현 명령이 본문에 있습니다). 상세본이 필요하면
> `docs/Pontifex_연동_검토.md` 를 별도 첨부하시면 됩니다.
>
> 작성 근거: `pontifex-1.0.tar.gz` 전수 검토(533파일) · inframon 현행 `main` 기준.

---

**제목:** Re: 시스템 초안 전달 — 검토 의견 (연동 계약 OK · 수정 요청 3건)

김태헌 님, 안녕하세요.

보내주신 `pontifex-1.0` 잘 받았습니다. 전달본을 코드 수준까지 살펴봤고, 확인된 것과
수정이 필요한 것을 정리해 드립니다.

먼저 말씀드릴 것은 **연동 계약(4-A JSON API)이 명확하다**는 점입니다. 필드·필수 여부·
등급 매핑·인증까지 문서에 다 있어서 저희 쪽 붙이는 데 막힘이 없었습니다. 실제 저희
산출물(`project.h5`)로 레코드 생성까지 확인했습니다.

다만 **웹 UI 는 아직 못 봤습니다.** 저희 개발 PC 에 Docker 가 없어(Windows·WSL 모두)
`./install.sh` 를 돌리지 못했습니다. 설치해서 화면을 확인한 뒤 별도로 회신드리겠습니다.
아래는 코드·데이터를 읽어 확인한 내용입니다.

---

## 1. `scripts/ingest_inframon.py:76` — 실데이터에서 항상 죽습니다 (급함)

```python
labels = [str(x).decode() if isinstance(x, bytes) else str(x)
          for x in f["/insar/date_labels"][:]]
```

`str(x)` 는 이미 `str` 이라 `.decode()` 가 항상 `AttributeError` 입니다. h5py 는 고정폭
문자열을 `bytes`(`|S8`)로 돌려주므로 **`date_labels` 가 있는 모든 실데이터**가 이 분기를
탑니다. 즉 "절대일자(basis=absolute)" 경로 — 실 InSAR 산출 경로 — 가 통째로 실행되지
않습니다. 합성 데모는 `date_labels` 가 없어 이 분기를 안 타기 때문에 데모 검증에서는
드러나지 않습니다.

재현:

```
$ python scripts/ingest_inframon.py <실 project.h5> --bridge-id 40001
AttributeError: 'str' object has no attribute 'decode'. Did you mean: 'encode'?
```

수정(한 줄):

```python
labels = [x.decode() if isinstance(x, bytes) else str(x)
          for x in f["/insar/date_labels"][:]]
```

## 2. `scripts/generate_proxy_ifc.py:137` — IFC 12종 전부 형상이 안 나옵니다

`data/ifc/` 의 프록시 IFC 12개를 저희 파이프라인에 물려 보니 **314부재 전부** 형상
생성이 실패했습니다. 원인은 한 곳입니다.

```
#9=IFCPROJECT('6U3FLOQ6LFFCHBKYWHGOLT',#5,'P1',Pontifex_girder_proxy,$,'Pontifex',$,(#8),#7);
                                             └─ STEP 문자열인데 작은따옴표가 없습니다
```

STEP 파서는 따옴표 없는 이 인자를 **통째로 버립니다.** 그러면 뒤 5개 속성이 한 칸씩
밀립니다:

| 속성 | 있어야 할 값 | 실제로 읽히는 값 |
|---|---|---|
| Description | `'Pontifex_girder_proxy'` | (없음) |
| ObjectType | `$` | `'Pontifex'` |
| Phase | `$` | `#8` 표현컨텍스트 |
| RepresentationContexts | `(#8)` | `#7` 단위할당 |
| **UnitsInContext** | `#7` | **없음 → 오류** |

단위를 못 읽으니 형상 처리가 전부 죽습니다. 재현(ifcopenshell 0.8.5):

```python
import ifcopenshell
from ifcopenshell import geom
f = ifcopenshell.open("data/ifc/girder_proxy.ifc")
s = geom.settings(); s.set(s.USE_WORLD_COORDS, True)
geom.create_shape(s, f.by_type("IfcElement")[0])
# RuntimeError: Index 8 is out of range for variant of size 8
```

수정(한 줄) — `generate_proxy_ifc.py:137` 에서 `project_name` 만 따옴표가 빠져 있습니다:

```python
proj = w.emit("IFCPROJECT", [f"'{guid()}'", f"#{oh}", "'P1'", f"'{project_name}'",
                             "$", f"'{project_name.split('_')[0]}'", "$",
                             f"(#{ctx})", f"#{ua}"])
```

**영향 범위:** 형상이 필요한 모든 소비자입니다. Revit·BlenderBIM 등 뷰어에서도 부재가
안 보일 것이고, 플랫폼의 `/bridge/<id>/` IFC 뷰어가 이 파일을 쓴다면 같은 증상일
가능성이 있습니다. 확인해 보시면 좋겠습니다.

## 3. 같은 파일 — `IfcAxis2Placement3D` 규칙 위반 (경미)

314부재 전부에서 `Axis` 는 주고 `RefDirection` 을 비웠습니다. IFC 규칙(WR2)은 둘 다
있거나 둘 다 없기를 요구합니다. `Axis=(0,0,1)` 이라 실질 영향은 없지만, 엄격한 검사기
(IDS·bSDD 검증기 등)는 오류로 잡습니다. 2번을 고치실 때 같이 보시면 좋겠습니다.

## 4. 보안 — 기본 바인딩을 `127.0.0.1` 로

`install.sh` 자체는 검토 결과 외부 다운로드나 호스트 권한 상승이 없어 **안전하다고
봅니다.** 다만 컨테이너가 `0.0.0.0:38000` 으로 열려 같은 네트워크의 다른 기기가 인제스트
API 에 접근할 수 있습니다. 기본을 `127.0.0.1:38000` 으로 두고 필요할 때 여는 편이
안전하겠습니다.

## 5. 번들된 `refs/inframon` 이 6주 전 스냅샷입니다

`refs/inframon` 은 2026-07-21 판입니다. 그 사이 저희 쪽에 중대 수정이 여러 건 있어서,
재현 안내(4-B)가 이 스냅샷을 `pip install -e` 하도록 되어 있으면 현재와 다른 결과가
납니다. 최신으로 교체 부탁드립니다.

---

## 6. 저희 쪽 사정 — 지금은 올릴 수 있는 값이 없습니다

정직하게 말씀드립니다. 저희 기존 산출물은 **위상 언래핑이 빠진 상태**로 계산된 것이라
CRI 값이 물리적 의미가 없습니다. 전수 감사 결과 14건 중 **보고 가능 0 · 조건부 7 ·
보고 불가 7** 입니다.

- 청양교 CRI 0.740 — 래핑 위상 + 교량 30 m 내 7/20,000점(0.03%)
- 호남 CRI 0.985 — 언래핑은 됐으나 PINN 경간 16,968 m vs 실연장 50 m (×339)

플랫폼 `data/ingest_out/` 의 예시 센싱데이터도 그 시절 산출입니다. **화면에 보이는 CRI 를
성능 근거로 인용하지 말아 주시길** 부탁드립니다.

저희 쪽 조치는 끝났습니다 — 언래핑 3단계(SnaphuExport → snaphu → SnaphuImport)를 넣고
실 데이터로 성립을 확인했습니다(|LOS|max 13.87 mm → 149.2 mm, λ/4 초과 68.3%). 청양교
24쌍·25시점, 정자교 5쌍 재처리를 마쳤고, 유효한 값이 나오는 대로 다시 연락드리겠습니다.

참고로 저희 쪽 전송 경로(`--pontifex-push`)는 **감사에서 '보고 불가'면 기본적으로
막도록** 해 두었습니다. 무의미한 수치가 귀사 플랫폼에 '측정값'으로 남으면 되돌리기
어렵기 때문입니다.

## 7. 요청드리는 것

| | 항목 | 규모 |
|---|---|---|
| 1 | `ingest_inframon.py:76` 수정 | 한 줄 |
| 2 | `generate_proxy_ifc.py:137` 따옴표 추가 + IFC 12종 재생성 | 한 줄 |
| 3 | `IfcAxis2Placement3D` 의 `RefDirection` 채우기 | 경미 |
| 4 | 기본 바인딩 `127.0.0.1:38000` | 설정 |
| 5 | `refs/inframon` 최신 교체 | 교체 |
| 6 | `member_records.express_ids` 를 저희가 채우려면 — IFC 프록시의 **부재별 express id 목록**을 어떤 형식으로 주시면 되는지 알려주시면 맞추겠습니다 | 회신 |

수정하신 IFC 를 다시 주시면 저희 쪽 파이프라인에 물려 부재 결합까지 확인해 드리겠습니다.
Docker 설치 후 웹 UI 확인 결과는 별도로 회신드리겠습니다.

감사합니다.
