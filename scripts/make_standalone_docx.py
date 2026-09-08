#!/usr/bin/env python3
"""'Claude 없이 혼자 돌리기' 안내서(.docx) — 타 컴퓨터에서 받기부터 결과까지, 단계마다 확인 기준.

    python scripts/make_standalone_docx.py    → docs/inframon_혼자_돌리기.docx
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/inframon_혼자_돌리기.docx"
IMG = ROOT / "docs/img"
NAVY = (0x1F, 0x3A, 0x5F)


def _font(run, size=10.5, bold=False, color=None, mono=False):
    run.font.size = Pt(size)
    run.font.bold = bold
    name = "Consolas" if mono else "맑은 고딕"
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if color:
        run.font.color.rgb = RGBColor(*color)


def H(doc, text, level=1):
    p = doc.add_heading(level=level)
    _font(p.add_run(text), {1: 16, 2: 13, 3: 11.5}[level], bold=True, color=NAVY)


def P(doc, text="", size=10.5, color=None, after=6, align=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    if align:
        p.alignment = align
    for i, chunk in enumerate(text.split("**")):
        if chunk:
            _font(p.add_run(chunk), size, bold=(i % 2 == 1), color=color)
    return p


def CODE(doc, lines):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.space_after = Pt(8)
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), "EEF2F7")
    pPr.append(shd)
    lines = lines if isinstance(lines, list) else [lines]
    for i, ln in enumerate(lines):
        _font(p.add_run(ln + ("\n" if i < len(lines) - 1 else "")), 10, mono=True, color=(0x1B, 0x26, 0x31))


def CHECK(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.space_after = Pt(10)
    _font(p.add_run("확인 ☐  "), 10.5, bold=True, color=(0x1E, 0x7E, 0x34))
    for i, chunk in enumerate(text.split("**")):
        if chunk:
            _font(p.add_run(chunk), 10.5, bold=(i % 2 == 1))


def BUL(doc, text):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    for i, chunk in enumerate(text.split("**")):
        if chunk:
            _font(p.add_run(chunk), 10.5, bold=(i % 2 == 1))


def TABLE(doc, rows, widths=None):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            c = t.cell(i, j)
            c.text = ""
            p = c.paragraphs[0]
            for k, chunk in enumerate(str(cell).split("**")):
                if chunk:
                    _font(p.add_run(chunk), 9.5, bold=(i == 0) or k % 2 == 1)
            if i == 0:
                tcPr = c._tc.get_or_add_tcPr()
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), "DCE6F1")
                tcPr.append(shd)
    if widths:
        for row in t.rows:
            for j, w in enumerate(widths):
                row.cells[j].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def FIG(doc, path: Path, caption: str, width_cm=16.0):
    if not path.exists():
        return
    doc.add_picture(str(path), width=Cm(width_cm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    P(doc, caption, size=9, color=(0x55, 0x5F, 0x6B), after=10, align=WD_ALIGN_PARAGRAPH.CENTER)


def build() -> Path:
    doc = Document()
    for s in doc.sections:
        s.left_margin = s.right_margin = Cm(2.2)
        s.top_margin = s.bottom_margin = Cm(2.0)
    st = doc.styles["Normal"]
    st.font.name = "맑은 고딕"
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")
    st.font.size = Pt(10.5)

    # 표지
    p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(110)
    _font(p.add_run("inframon"), 30, bold=True, color=NAVY)
    p = doc.add_paragraph()
    _font(p.add_run("혼자 돌리기 — 다른 컴퓨터에서 받기부터 결과까지"), 20, bold=True)
    p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(14)
    _font(p.add_run("이 프로그램은 Python 으로 혼자 돕니다. Claude 나 다른 AI 는 필요 없습니다.\n"
                    "아래 순서대로 한 줄씩 치고, 각 단계의 '확인 ☐' 이 맞으면 다음으로 갑니다."),
          12, color=(0x33, 0x44, 0x55))
    p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(40)
    _font(p.add_run(f"{date.today().isoformat()} · https://github.com/tjddnr8334-sudo/inframon"),
          10, color=(0x77, 0x77, 0x77))
    doc.add_page_break()

    # 0
    H(doc, "0. 준비물 — 두 개만 설치 (한 번)", 1)
    TABLE(doc, [
        ["", "어디서", "주의"],
        ["Python 3.11 이상", "https://www.python.org/downloads/", "설치 첫 화면 맨 아래 **'Add python.exe to PATH' 체크**"],
        ["Git", "https://git-scm.com/download/win", "전부 기본값으로 '다음'"],
    ], widths=[3.2, 5.8, 7.0])
    P(doc, "PowerShell 을 엽니다: 키보드 **Win + X** → **터미널**. 그리고:")
    CODE(doc, ["python --version", "git --version"])
    CHECK(doc, "`Python 3.11.x` 이상, `git version 2.x` 가 찍힌다. 'python 은 인식되지 않는 명령' 이면 Python 재설치(PATH 체크).")

    # 1
    H(doc, "1. GitHub 에서 받기", 1)
    CODE(doc, ["cd $HOME",
               "git clone https://github.com/tjddnr8334-sudo/inframon",
               "cd inframon"])
    CHECK(doc, "`Receiving objects: 100%` 가 찍히고, `cd inframon` 뒤 프롬프트가 `...\\inframon>` 로 바뀐다.")

    # 2
    H(doc, "2. 전부 설치 (한 줄, 5~10분)", 1)
    CODE(doc, ["python start.py --full"])
    P(doc, "이 한 줄이: 파이썬 확인 → 가상환경(.venv) 생성 → inframon 과 필요한 패키지 전부(torch·scipy·pyproj·"
           "rasterio·asf_search·streamlit 등 20개) 설치 → 데모 실행 → 진단 리포트. 한 번만 하면 됩니다.")
    CHECK(doc, "마지막 부분에 **`판정: ✅ 코어 동작 가능`**. 그 위 [의존성] 목록이 전부 ✅.")
    P(doc, "남는 ⚠ 는 하나뿐이어야 합니다 — **Earthdata 토큰 없음**. 이것은 위성 원본(SLC)을 내려받을 때만 "
           "필요하고, 5번에서 넣습니다. 이미 만들어진 교량 결과를 보는 데는 필요 없습니다.")

    # 3
    H(doc, "3. 대시보드 띄우기", 1)
    CODE(doc, ["python start.py --dashboard"])
    P(doc, "브라우저가 자동으로 열립니다. 안 열리면 주소창에 **http://localhost:8501** 을 직접 칩니다. "
           "끄려면 그 터미널에서 **Ctrl + C**. 다음부터 대시보드만 바로 띄우려면:")
    CODE(doc, [".venv\\Scripts\\streamlit run src\\inframon\\dashboard\\app.py"])
    FIG(doc, IMG / "dashboard_start.jpg", "그림 1. 대시보드 — 왼쪽에서 교량을 고르고, 본문 ⓪ 시작에서 전 과정을 돌린다.")
    CHECK(doc, "그림 1 과 같은 화면. **① 이 컴퓨터 준비 상태** 에서 점검 항목이 대부분 ✅ 이고, ⚠ 는 Earthdata 하나.")

    # 4
    H(doc, "4. 교량 골라서 끝까지 (대시보드)", 1)
    for t in [
        "왼쪽 **🔎 교량명 검색 (CSV+OSM)** 을 펼치고 교량 이름 입력 → 지도에 마커가 찍힘",
        "마커 클릭 (또는 지도에서 교량 위치 클릭)",
        "**🔎 이 위치에서 교량 확인 (OSM)** 버튼 → 확인된 교량 목록에서 선택",
        "**💾 타깃으로 저장** 버튼",
        "**🚀 끝까지 돌리기** 버튼 — 단계 ①~⑩ 로그가 화면에 흐르고, 끝나면 결과가 그 자리에 뜸",
    ]:
        BUL(doc, t)
    P(doc, "이미 위성 트랙이 있는 교량(정자교·청양교·내곡교 등)은 **약 1분**. 없는 교량은 5번(토큰)이 먼저 필요하고 "
           "SLC 다운로드·처리까지 **1~3시간** 걸립니다.")
    CHECK(doc, "로그 마지막에 `⑩ 결과 문서` 와 `요약` 표. 결과 폴더 `docs\\bridges\\<교량>\\` 가 생겼다.")

    H(doc, "4-1. 같은 것을 명령 한 줄로 (대시보드 없이)", 2)
    CODE(doc, ["python scripts\\demo_4pm.py                     # 정자교 44초, 결과 창 4개 자동 열림",
               "python scripts\\bridge_run.py --name 청양교 --lat 36.450655 --lon 126.80732",
               "python scripts\\bridge_run.py --batch docs\\bridges\\batch.json   # 여러 교량 한 번에"])

    # 5
    H(doc, "5. 새 교량을 위성 원본(SLC)부터 — 토큰 한 번", 1)
    P(doc, "위성 원본은 NASA Earthdata 계정이 있어야 받습니다. 프로그램이 대신 가입할 수 없으니 이것만 직접:")
    for t in [
        "https://urs.earthdata.nasa.gov 에서 가입(무료) → 로그인",
        "오른쪽 위 프로필 → **Generate Token** → 긴 문자열 복사",
        "PowerShell 에서:",
    ]:
        BUL(doc, t)
    CODE(doc, ["python -m inframon --earthdata-save <붙여넣은 토큰>"])
    CHECK(doc, "`Earthdata 토큰 저장: ...earthdata_token` 이 찍힌다. `python -m inframon --doctor` 에서 Earthdata ✅.")
    P(doc, "InSAR 처리에는 **SNAP**(ESA, https://step.esa.int/main/download/snap-download/)과 **snaphu**(WSL: "
           "`wsl --install` 후 `sudo apt install snaphu`)가 더 필요합니다. 없으면 `--doctor` 가 어느 것이 없는지와 "
           "설치법을 그 자리에 적어 줍니다.")
    P(doc, "그다음 새 교량:")
    CODE(doc, ["python -m inframon --pipeline 37.5337,126.9366 --pipeline-mode plan --out docs\\bridges\\마포대교\\plan   # 계획만(1분)",
               "python scripts\\bridge_run.py --name 마포대교 --lat 37.5337 --lon 126.9366                           # 끝까지(1~3시간)"])
    CHECK(doc, "계획: `②④ SLC·트랙·프레임  ASC path127 · 41장` 처럼 장면 수가 나온다. 끝까지: `⓪ SLC → InSAR` 부터 `⑩` 까지 찍힌다.")

    # 6
    H(doc, "6. 결과 보기", 1)
    TABLE(doc, [
        ["파일 (docs\\bridges\\<교량>\\)", "무엇", "여는 법"],
        ["twin.viewer.html", "3D 디지털 트윈 — 변위 속도", "더블클릭 (인터넷 불필요)"],
        ["twin_cri.viewer.html", "3D 디지털 트윈 — CRI 위험도", "더블클릭"],
        ["brief.png", "건기연 형식 4단 그림 (a)(b)(c)(d)", "더블클릭"],
        ["결과.md", "수치 표 · 무엇을 어디서 가져왔나 · 감사 판정 · 못 한 것", "메모장·VS Code"],
        ["<교량>_proxy.ifc", "IFC4 교량 모델", "Revit · BlenderBIM"],
    ], widths=[4.6, 7.0, 4.4])
    FIG(doc, IMG / "twin_3d_jeongjagyo.png", "그림 2. twin.viewer.html — IFC 부재(A1·P1~P4·S1) 위 InSAR 점. 드래그 회전, 점 클릭 = 값·부재.")
    FIG(doc, ROOT / "docs/bridges/정자교/brief.png", "그림 3. brief.png — (a) PS 배치 (b) 잔차고도 (c) 속도 ± 95% CI (d) 시계열.")

    H(doc, "6-1. 결과.md 에서 반드시 볼 세 줄", 2)
    TABLE(doc, [
        ["줄", "뜻"],
        ["**감사 | 보고 가능 / 조건부 / 보고 불가**", "이 결과를 보고에 써도 되는가를 파일이 스스로 판정. 사유가 뒤에 붙음"],
        ["**ⓘ EI·고유진동수는 설계 제원 기반**", "위성은 상대 변위만 보므로 강성은 관측이 아님 — 감점이 아니라 표기"],
        ["**⚠ 알려진 사고 교량**", "정자교처럼 사고 이력이 있으면 맨 앞에 적힘. '정상' 을 보고 근거로 쓰지 말 것"],
    ], widths=[6.0, 10.0])

    # 7
    H(doc, "7. 막히면", 1)
    TABLE(doc, [
        ["증상", "조치"],
        ["`python` 은 인식되지 않는 명령", "Python 재설치, 'Add python.exe to PATH' 체크. PowerShell 새로 열기"],
        ["`git` 은 인식되지 않는 명령", "Git 설치 후 PowerShell 새로 열기"],
        ["설치가 중간에 멈춤 / 빨간 글씨", "인터넷·방화벽 확인 후 같은 명령 다시 (이미 깔린 것은 건너뜀)"],
        ["8501 이 안 열림", "터미널 마지막 줄 주소를 브라우저에 직접. 이미 쓰는 포트면 `--server.port 8502`"],
        ["끝까지 돌리기에서 '토큰 없음'", "5번"],
        ["'SNAP gpt 없음' / 'snaphu 없음'", "`python -m inframon --doctor` 출력의 설치법"],
        ["OSM 조회 실패(504)", "자동 재시도 3회. 잠시 후 다시. 캐시가 있으면 캐시 사용"],
        ["실행이 죽음", "같은 명령 다시 (받은 위성 자료는 재사용). `결과.md` 의 '예외:' 줄이 사유"],
        ["'교면 위 점 0'", "교량이 작거나 궤도 방향이 불리. 계획(5번)에서 장면 수·궤도 먼저"],
    ], widths=[5.5, 10.5])

    # 8
    H(doc, "8. 전체 그림 — 무엇이 어디서 오나", 1)
    CODE(doc, [
        "유저: 교량 좌표 하나",
        "  │",
        "  ├─ ① 제원      파트너 실측 CSV → OSM → 전국교량표준데이터",
        "  ├─ ⓪ SLC       ASF (Earthdata 토큰)          ┐ 트랙이 없을 때만",
        "  ├─ ⓪ InSAR     SNAP → snaphu 언래핑          ┘ (1~3시간)",
        "  ├─ ④ 점 선택   쉬프트 보정(기하·처리 오프셋) → 교량 위 PS/DS",
        "  ├─ ⑤ 잔차고도  점이 지면이 아니라 교면 위임을 고도로 확인",
        "  ├─ ⑥ IFC 트윈  실측 제원 → IFC4 → 점을 부재에 결합 → 3D",
        "  ├─ ⑦ PINN·CRI  가상센싱(전체 변위장) → 위험도·경보",
        "  ├─ ⑧ 브리프    건기연 형식 4단 그림",
        "  ├─ ⑨ 감사      보고 가능 / 조건부 / 불가 — 파일이 스스로",
        "  └─ ⑩ 결과.md   전부 기록. 못 한 것은 못 했다고",
    ])
    P(doc, "없는 것은 없다고 적고 넘어갑니다 — 멈추지 않습니다. 어느 교량을 보고에 쓸지는 유저가 정합니다.",
      color=(0x33, 0x44, 0x55))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    return OUT


if __name__ == "__main__":
    out = build()
    print(f"저장: {out}  ({out.stat().st_size:,} B)")
