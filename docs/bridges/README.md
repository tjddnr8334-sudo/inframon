# 교량별 실행 결과 — `bridge_run` 배치

유저가 교량을 고르면 **좌표 하나**로 SLC 트랙 → InSAR PS/DS → PINN → CRI → IFC 트윈까지
간다. 여기는 그 실행 결과다. 어느 교량을 보고에 쓸지는 감사 판정과 `결과.md` 의
"적어 둘 것"을 보고 **유저가 정한다** — 파이프라인은 사유를 남기고 멈추지 않는다.

    python scripts/bridge_run.py --batch docs/bridges/batch.json
    python scripts/bridge_run.py --name 청양교 --lat 36.450655 --lon 126.80732 --track <track.h5>

| 교량 | 점 | CRI | 잔차고도(교면 위 − 밖) | 감사 | 폴더 |
|---|---|---|---|---|---|
| 청양교 | 44 | 0.784 | +12.5 ± 6.4 m (z 1.96) | 보고 가능 | [청양교/](청양교/) |
| 정자교 | 12 | 0.546 | **+5.2 ± 1.2 m (z 4.31)** | 조건부 | [정자교/](정자교/) — 201시점 SARvey |
| 내곡교 | 34 | 0.821 경고 | — (B⊥ 없음) | 보고 가능 | [내곡교/](내곡교/) — 건기연 브리프 같은 교량 |
| 칠백로 | 22 | 0.808 경고 | — | 조건부 | [칠백로/](칠백로/) |
| 상규 | 3 | 0.879 위험 | — | 보고 가능 | [상규/](상규/) — 점 3개, 판정 신뢰도 낮음 |
| 동수원 | 77 | 0.729 | — | 보고 불가 | [동수원/](동수원/) — CSV 890 m vs OSM 1227 m 불일치 |

트랙이 없으면 `--track` 을 빼고 좌표만 준다 — ⓪ SLC 검색·다운로드 → SNAP → 언래핑(실패 시
자동 재시도)부터 만든다. 필요한 외부 자격·도구(Earthdata 토큰·SNAP·snaphu)는
`python -m inframon --doctor` 가 알려주고, 토큰은 `--earthdata-save <토큰>` 한 번이면 된다.
⑭ BMAP 전송은 `--pontifex-mock` 모의 서버로 Docker 없이 HTTP 왕복을 확인했다.

각 폴더: `결과.md` · `brief.png`(건기연 형식 4단) · `twin.viewer.html`(더블클릭 3D) ·
`twin_cri.viewer.html` · `*_proxy.ifc` · `project.h5`

입력은 **SLC** 뿐이다(ASF `processingLevel=SLC`). RAW(Level-0)는 파이프라인에 들어가지 않는다.
