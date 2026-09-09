#!/usr/bin/env python3
"""유저용 워크플로우 안내서(.docx) — 무엇을 하는 프로그램이고, 어떻게 돌리며, 결과를 어떻게 읽나.

    python scripts/make_workflow_docx.py            → docs/inframon_워크플로우_안내서.docx
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/inframon_워크플로우_안내서.docx"
IMG = ROOT / "docs/img"

# ── 스타일 도우미 ─────────────────────────────────────────────────────────
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
    r = p.add_run(text)
    _font(r, {0: 22, 1: 16, 2: 13, 3: 11.5}[level], bold=True, color=(0x1F, 0x3A, 0x5F))
    return p


def P(doc, text="", *, bold=False, size=10.5, color=None, after=6):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    for i, chunk in enumerate(text.split("**")):
        if chunk:
            _font(p.add_run(chunk), size, bold=(bold or i % 2 == 1), color=color)
    return p


def CODE(doc, lines):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.space_after = Pt(8)
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), "F2F4F7")
    pPr.append(shd)
    for i, ln in enumerate(lines if isinstance(lines, list) else [lines]):
        r = p.add_run(ln + ("\n" if i < len(lines) - 1 else ""))
        _font(r, 9.5, mono=True, color=(0x1B, 0x26, 0x31))
    return p


def CHECK(doc, text):
    """'확인 ☐' 줄 — 이 단계가 성공했는지 사용자가 눈으로 대조하는 기준."""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.space_after = Pt(10)
    _font(p.add_run("확인 ☐  "), 10.5, bold=True, color=(0x1E, 0x7E, 0x34))
    for i, chunk in enumerate(text.split("**")):
        if chunk:
            _font(p.add_run(chunk), 10.5, bold=(i % 2 == 1))
    return p


def BUL(doc, text, level=0):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.left_indent = Cm(0.8 + 0.6 * level)
    p.paragraph_format.space_after = Pt(3)
    for i, chunk in enumerate(text.split("**")):
        if chunk:
            _font(p.add_run(chunk), 10.5, bold=(i % 2 == 1))
    return p


def TABLE(doc, rows, widths=None, header=True):
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
                    _font(p.add_run(chunk), 9.5, bold=(header and i == 0) or k % 2 == 1)
            if header and i == 0:
                tcPr = c._tc.get_or_add_tcPr()
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), "DCE6F1")
                tcPr.append(shd)
    if widths:
        for row in t.rows:
            for j, w in enumerate(widths):
                row.cells[j].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return t


def FIG(doc, path: Path, caption: str, width_cm=16.0):
    if not path.exists():
        P(doc, f"(그림 없음: {path.name})", color=(0x99, 0x99, 0x99))
        return
    doc.add_picture(str(path), width=Cm(width_cm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = P(doc, caption, size=9, color=(0x55, 0x5F, 0x6B), after=10)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER


# ── 문서 ─────────────────────────────────────────────────────────────────
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
    p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(120)
    _font(p.add_run("inframon"), 30, bold=True, color=(0x1F, 0x3A, 0x5F))
    p = doc.add_paragraph()
    _font(p.add_run("교량 InSAR → PINN 가상센싱 → 위험도 → IFC 디지털 트윈"), 15, color=(0x33, 0x44, 0x55))
    p = doc.add_paragraph()
    _font(p.add_run("사용자 워크플로우 안내서"), 20, bold=True)
    p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(40)
    _font(p.add_run(f"{date.today().isoformat()} · https://github.com/tjddnr8334-sudo/inframon"),
          10, color=(0x77, 0x77, 0x77))
    doc.add_page_break()

    # 1. 이게 뭔가
    H(doc, "1. 이 프로그램이 하는 일", 1)
    P(doc, "위성 레이더(Sentinel-1 InSAR)로 **교량의 미세 변위**를 재고, 그 관측을 물리 모델(PINN)에 "
           "넣어 **관측점이 없는 곳까지 변위장**을 채우고, 그것으로 **위험도(CRI)**를 내고, "
           "전부를 **3D 디지털 트윈(IFC)** 위에 올립니다. 유저가 하는 것은 **교량을 고르는 것**뿐입니다.")
    TABLE(doc, [
        ["단계", "무엇을", "어디서"],
        ["① 교량 선정", "좌표 하나 → 연장·경간·폭·형식·형하고", "파트너 실측 CSV → OSM → 표준데이터"],
        ["⓪ SLC 확보", "Sentinel-1 SLC 검색·다운로드", "ASF (Earthdata 토큰 필요)"],
        ["⓪ InSAR", "간섭도 → 위상 언래핑 → 변위 시계열", "SNAP + snaphu"],
        ["④ PS/DS", "교량 위 산란체 선별, 쉬프트 보정", "inframon"],
        ["⑤ 잔차고도", "점이 지면이 아니라 **교면 위**임을 고도로 증명", "inframon (수직기선 B⊥)"],
        ["⑥ IFC 트윈", "실측 제원 → IFC4 → 점을 부재에 결합 → 3D", "inframon"],
        ["⑦ PINN·CRI", "가상센싱(전체 변위장) → 위험도·경보", "inframon"],
        ["⑧ 브리프", "건기연 형식 4단 그림 (a)(b)(c)(d)", "inframon"],
        ["⑨ 감사", "이 결과를 보고에 써도 되는가 — 파일이 스스로 판정", "inframon"],
        ["⑭ BMAP", "파트너 플랫폼(Pontifex) 전송", "HTTP API"],
    ], widths=[2.6, 8.0, 5.4])

    # 2. 흐름 그림
    H(doc, "2. 전체 흐름", 1)
    CODE(doc, [
        "  유저: 교량 좌표 하나  (예: 마포대교 37.5337, 126.9366)",
        "         │",
        "         ▼",
        "  ① 제원 ──► ⓪ SLC 다운로드 ──► ⓪ InSAR(SNAP·snaphu) ──► 트랙 h5",
        "         │                                                  │",
        "         │        (트랙이 이미 있으면 여기부터)              ▼",
        "         └──► ② 데크선 ─► ③ 지면 ─► ④ 점 선택(쉬프트 보정) ─► ⑤ 잔차고도",
        "                                                            │",
        "                       ┌────────────────────────────────────┘",
        "                       ▼",
        "  ⑥ IFC 트윈(3D) ◄── ⑦ PINN 가상센싱 → CRI 위험도 ──► ⑧ 브리프 그림",
        "                       │",
        "                       ▼",
        "  ⑨ 감사(보고 가능 / 조건부 / 불가) ──► ⑩ 결과.md ──► ⑭ BMAP 전송(선택)",
    ])
    P(doc, "각 단계는 **없는 것은 없다고 적고 넘어갑니다** — 멈추지 않습니다. 어느 교량을 보고에 쓸지는 "
           "감사 판정과 결과.md 를 보고 **유저가 정합니다**.")

    # 3. 설치 — 2026-09-09 사용자 PC(E:\inframon)에서 이 순서 그대로 검증
    H(doc, "3. 다른 컴퓨터에서 처음부터 — PowerShell 단계별", 1)
    P(doc, "아래 순서 그대로 하면 됩니다. 각 단계에 **확인 ☐** 이 있으니 그것이 나오면 다음으로, 안 나오면 6장(막히면)으로. "
           "처음 한 번은 10~20분, 그다음부터는 대시보드만 띄우면 됩니다.")
    TABLE(doc, [
        ["단계", "하는 것", "시간", "확인"],
        ["3.1", "프로그램을 둘 폴더로 이동 → **한 줄** 붙여넣기 (받기·파이썬 패키지·SNAP·snaphu 전부)", "10~20분", "`결과: snap ✅, snaphu ✅, earthdata ✅, slc_dir ✅`"],
        ["3.2", "  ↳ 그 안에서 **Earthdata 토큰** 붙여넣기 (브라우저가 열림)", "1분", "`✅ Earthdata 토큰 확인(CMR 200)`"],
        ["3.3", "  ↳ 그 안에서 **SLC 보관 폴더** 고르기", "10초", "`✅ SLC 보관 폴더 — E:\\SLC`"],
        ["3.4", "이 PC 에 뭐가 있나 (진단)", "10초", "[외부 도구·자격] 네 줄 전부 ✅"],
        ["3.5", "**대시보드 띄우기** — 화면으로 보고 누르기", "20초", "브라우저에 http://localhost:8501"],
        ["3.6", "먼저 되는 것으로 한 번 (정자교 시연)", "44초", "`⑩ 결과 문서` · `rc=0` · 창 4개"],
        ["3.7", "새 교량 — 계획만", "1분", "`②④ SLC·트랙·프레임 … N장`"],
        ["3.8", "새 교량 — 끝까지 (SLC 다운로드 → InSAR → 트윈)", "1~3시간", "`docs\\bridges\\<교량>\\결과.md`"],
        ["3.9", "결과 보기", "—", "twin.viewer.html 더블클릭"],
    ], widths=[1.2, 8.6, 1.8, 4.4])

    H(doc, "3.0 미리 있어야 하는 것", 2)
    TABLE(doc, [
        ["", "무엇", "없으면"],
        ["Python 3.11+", "python.org — 설치 화면에서 'Add python.exe to PATH' 체크", "3.1 의 한 줄이 winget 으로 깔아 줌"],
        ["Git", "git-scm.com", "3.1 의 한 줄이 winget 으로 깔아 줌 (그것도 안 되면 zip 으로 받음)"],
        ["**Earthdata 계정**", "https://urs.earthdata.nasa.gov/users/new — 무료, 3분, 메일 인증", "**사람이 직접** 가입해야 함 — 프로그램이 대신 못 하는 유일한 것"],
        ["디스크", "SLC 1장 4~8 GB × 교량당 30~50장 → 200~400 GB", "큰 드라이브를 3.3 에서 고른다"],
    ], widths=[3.0, 7.0, 6.0])
    P(doc, "SNAP·snaphu·토큰 저장·SLC 폴더는 3.1 한 줄이 받아서 준비합니다. 트랙이 이미 있는 교량(정자교·청양교·내곡교 등)만 "
           "볼 거면 Python·Git 만 있으면 됩니다.")

    H(doc, "3.1 폴더로 이동 → 한 줄 (10~20분)", 2)
    P(doc, "PowerShell 을 엽니다(Win + X → 터미널). **먼저 프로그램을 둘 폴더로 이동**합니다 — 그 아래에 `inframon` 폴더가 생깁니다. "
           "그다음 한 줄을 붙여넣고 Enter.")
    CODE(doc, ["cd E:\\                                                                              # ① 프로그램을 둘 곳 (원하는 드라이브·폴더)",
               "irm https://raw.githubusercontent.com/tjddnr8334-sudo/inframon/main/install.ps1 | iex   # ② 전부"])
    P(doc, "이 한 줄이 순서대로:")
    for t in [
        "Python·Git 확인 — 없으면 winget 으로 설치",
        "GitHub 에서 받기 → `E:\\inframon` (이미 있으면 git pull 로 갱신)",
        "가상환경 `.venv` 만들고 파이썬 패키지 전부(torch·scipy·pyproj·rasterio·asf_search·streamlit 등) 설치 — 5~10분",
        "**SNAP** 1.1 GB 내려받아 무인 설치 (이미 있으면 건너뜀) · **snaphu** WSL 안에 설치 (WSL 없으면 설치 걸고 재부팅 안내)",
        "**Earthdata 토큰** — 브라우저를 열고 붙여넣기 기다림 → 3.2",
        "**SLC 보관 폴더** — 드라이브를 묻는다 → 3.3",
        "데모 → 진단 → 브라우저에 대시보드 http://localhost:8501 (끄려면 Ctrl + C)",
    ]:
        BUL(doc, t)
    P(doc, "손으로 하려면 (같은 폴더에서, 한 줄과 같은 일):")
    CODE(doc, ["cd E:\\",
               "git clone https://github.com/tjddnr8334-sudo/inframon",
               "cd inframon",
               "python start.py --full --tools"])
    CHECK(doc, "`결과: snap ✅, snaphu ✅, earthdata ✅, slc_dir ✅` 와 그 아래 진단의 `판정: ✅ 코어 동작 가능`.")
    P(doc, "**이후 명령은 `python` 이 아니라 `.venv\\Scripts\\python`** 으로 부릅니다 — start.py 가 시스템 파이썬이 아니라 "
           "`.venv` 안에 설치하기 때문입니다. 그냥 `python -m inframon` 은 새 컴퓨터에서 'No module named inframon' 이 납니다. "
           "(`python start.py …` 만 예외 — 이 파일은 표준 라이브러리만 씁니다.)", color=(0x8A, 0x2B, 0x2B))

    H(doc, "3.2 Earthdata 토큰 — 프롬프트가 뜨면 (1분)", 2)
    P(doc, "터미널에 `3) 여기 붙여넣고 Enter` 가 뜨고 브라우저에 Earthdata 페이지가 열립니다.")
    for t in [
        "계정이 없으면 가입(무료): https://urs.earthdata.nasa.gov/users/new → 메일 인증 → 로그인",
        "프로필 화면(이름·Username·Email 이 보임) **위쪽 작은 메뉴 Generate Token** → 아래 **GENERATE TOKEN** 버튼",
        "가려진 토큰 옆 **Show Token** 또는 복사 아이콘 → `eyJ0eXAiOi…` 로 시작하는 긴 문자열 **전체** 복사",
        "PowerShell 로 돌아와 붙여넣고 Enter — 프로그램이 NASA 서버(CMR)에 한 번 조회해 **유효할 때만** 저장",
        "토큰은 비밀번호와 같습니다 — **터미널에만** 붙여넣고 채팅·메일·문서에는 넣지 않습니다. 노출됐으면 같은 페이지에서 새로 발급(최대 2개)하고 옛것은 Revoke",
        "60일짜리 — 만료 7일 전부터 프로그램이 자동 갱신하므로 다시 붙여넣을 일은 거의 없습니다",
    ]:
        BUL(doc, t)
    CHECK(doc, "`✅ Earthdata 토큰 확인(CMR 200) → C:\\Users\\<이름>\\.inframon\\earthdata_token`. 그냥 Enter 로 건너뛰었으면 나중에 `python start.py --tools --no-demo`.")

    H(doc, "3.3 SLC 보관 폴더 — 물으면 (10초)", 2)
    P(doc, "위성 원본은 장당 4~8 GB, 교량 하나에 200~400 GB 까지 갑니다. C: 에 두면 곧 차니 **큰 드라이브**를 고릅니다. "
           "드라이브별 여유 공간이 표시됩니다.")
    CODE(doc, ["    드라이브 여유 공간:",
               "      C:\\  여유    120.3 GB / 476 GB",
               "      E:\\  여유   1827.0 GB / 1863 GB",
               "    SLC 보관 폴더 (Enter = E:\\SLC, 건너뛰려면 '-'):  E:\\SLC"])
    P(doc, "원하는 경로를 치거나 Enter(여유가 가장 큰 드라이브의 `\\SLC`). 이미 정해져 있으면 현재 위치를 보여 주고 **Enter = 유지, 새 경로 = 변경**. "
           "이후 다운로드는 `E:\\SLC\\<궤도_프레임>\\` 에 떨어지고, 같은 프레임을 쓰는 다음 교량은 다운로드를 건너뜁니다. "
           "나중에 바꾸려면 `.venv\\Scripts\\python -m inframon --slc-dir D:\\SLC` (없으면 만듦).")
    CHECK(doc, "`✅ SLC 보관 폴더 — E:\\SLC (새로 만듦)` 또는 `유지: …`.")

    H(doc, "3.4 이 PC 에 뭐가 있나 (10초)", 2)
    CODE(doc, [".venv\\Scripts\\python -m inframon --doctor"])
    CODE(doc, ["  [외부 도구·자격 — `python start.py --tools` 가 준비하는 것]",
               "    ✅ Earthdata 토큰   C:\\Users\\<이름>\\.inframon\\earthdata_token (만료 D-59)",
               "    ✅ SNAP gpt       C:\\Program Files\\esa-snap\\bin\\gpt.exe",
               "    ✅ snaphu         wsl:/usr/bin/snaphu",
               "    ✅ SLC 보관 폴더      E:\\SLC (0장)",
               "  [가능한 기능]  … ✅ slc_download  ✅ insar_snap  ✅ unwrap_snaphu  ✅ full_pipeline",
               "  판정: ✅ 코어 동작 가능"])
    CHECK(doc, "[외부 도구·자격] 네 줄 전부 ✅, [가능한 기능] 에 `full_pipeline ✅`. ❌ 가 있으면 그 줄에 적힌 대로 하거나 `python start.py --tools --no-demo` (빠진 것만 다시).")

    H(doc, "3.5 대시보드 띄우기 (20초)", 2)
    P(doc, "명령 대신 화면으로 하려면 대시보드를 띄웁니다. 3.1 의 한 줄 설치기는 끝에 자동으로 띄우지만, "
           "`--tools` 나 `--full` 만 돌렸으면 뜨지 않으니 따로 띄웁니다:")
    CODE(doc, ["python start.py --dashboard                                  # 브라우저 자동 열림 (설치 상태도 같이 확인)",
               ".venv\\Scripts\\streamlit run src\\inframon\\dashboard\\app.py     # 다음부터 대시보드만 바로"])
    P(doc, "브라우저가 자동으로 열립니다. 안 열리면 주소창에 **http://localhost:8501** 을 직접 칩니다. 끄려면 그 터미널에서 **Ctrl + C**.")
    FIG(doc, IMG / "dashboard_start.jpg", "그림 1. 대시보드 — 왼쪽에서 교량을 고르고, 본문 ①→②→③ 순으로 누른다.")
    TABLE(doc, [
        ["순서", "화면", "누를 것"],
        ["①", "이 컴퓨터 준비 상태", "보기만. '모든 항목 준비 완료' 면 아래로. 막힌 항목이 있으면 🔧 외부 도구 준비 펼침에서 버튼/붙여넣기"],
        ["②", "교량 선택", "**교량명** 입력 → **🔎 찾기** (또는 **위도·경도** 칸에 직접 입력)"],
        ["③", "전 과정 실행", "먼저 **📋 계획 보기** (10~30초, SLC 몇 장인지 확인) → 그다음 **▶ 전체 실행** (1~3시간)"],
    ], widths=[1.2, 3.8, 11.0])
    P(doc, "3.6~3.8 의 명령줄과 같은 일을 이 세 단계가 합니다. 진행 상황에 `②④ SLC·트랙·프레임 — ASC path127 · 41장` 처럼 ✅ 가 찍히면 "
           "그 교량은 돌릴 수 있는 것입니다. 왼쪽 사이드바 **🔎 교량명 검색** → 지도 마커 클릭 → **💾 타깃 저장** → **🚀 끝까지 돌리기** 로 해도 같습니다.")
    CHECK(doc, "브라우저에 그림 1 화면. **① 이 컴퓨터 준비 상태** 가 '모든 항목 준비 완료'.")

    H(doc, "3.6 먼저 되는 것으로 한 번 — 정자교 시연 (44초)", 2)
    CODE(doc, [".venv\\Scripts\\python scripts\\demo_4pm.py"])
    P(doc, "이미 처리된 트랙으로 정자교를 끝까지 돌리고 결과 창 4개(3D 속도 · 3D 위험도 · 브리프 그림 · 결과.md)를 엽니다. "
           "이것이 되면 ⑥~⑩(트윈·PINN·브리프·감사·결과)이 이 PC 에서 도는 것입니다.")
    CHECK(doc, "터미널에 **⑩ 결과 문서** 까지 찍히고 `rc=0`. 3D 창에서 드래그 회전, 점 클릭 = 값·부재.")
    FIG(doc, IMG / "twin_3d_jeongjagyo.png", "그림 2. 3D 디지털 트윈 — IFC 부재(A1·P1~P4·S1) 위에 InSAR 점. 점을 클릭하면 값·부재·GlobalId.")

    H(doc, "3.7 새 교량 — 계획만 먼저 (1분)", 2)
    CODE(doc, [".venv\\Scripts\\python -m inframon --pipeline 37.5337,126.9366 --pipeline-mode plan --out docs\\bridges\\마포대교\\plan"])
    P(doc, "좌표만 주면 제원(파트너 CSV → OSM → 표준데이터)과, 어느 궤도에 SLC 가 몇 장 있는지를 검색합니다. 다운로드는 하지 않습니다. "
           "장면 수가 10장 미만이면 그 교량은 이 궤도로 어렵습니다 — 다른 교량이나 궤도를 봅니다.")
    CHECK(doc, "**②④ SLC·트랙·프레임  ASC path127 frame120 · 41장** 처럼 궤도·프레임·장면 수. ①의 ❌ 는 OSM 서버 일시 오류(504) — 다시 돌리면 됩니다.")

    H(doc, "3.8 새 교량 — 끝까지 (1~3시간)", 2)
    CODE(doc, [".venv\\Scripts\\python scripts\\bridge_run.py --name 마포대교 --lat 37.5337 --lon 126.9366"])
    P(doc, "트랙이 없으므로 **⓪ SLC 다운로드(3.3 폴더로) → SNAP → 언래핑**부터 갑니다. 단계마다 찍힙니다:")
    CODE(doc, ["⓪ SLC → InSAR      ← 대부분의 시간 (다운로드 GB 단위 · SNAP 수십 분)",
               "① 제원  ② 데크선  ③ 지면  ④ 점 선택  ⑤ 잔차고도",
               "⑥ IFC 트윈  ⑦ PINN·CRI  ⑧ 브리프  ⑨ 감사  ⑩ 결과 문서"])
    P(doc, "중간에 죽으면 같은 명령을 다시 — 받아 둔 SLC 는 재사용합니다. 여러 교량은 `--batch docs\\bridges\\batch.json`.")
    CHECK(doc, "`docs\\bridges\\마포대교\\결과.md` — 각 단계가 무엇을 어디서 가져왔는지, 못 한 것은 왜인지. 4장에서 읽는 법.")

    H(doc, "3.9 결과 보기", 2)
    TABLE(doc, [
        ["파일 (docs\\bridges\\<교량>\\)", "무엇", "여는 법"],
        ["twin.viewer.html", "3D 트윈(변위 속도 채널)", "더블클릭 — 인터넷 불필요"],
        ["twin_cri.viewer.html", "3D 트윈(CRI 위험도 채널)", "더블클릭"],
        ["brief.png", "건기연 형식 4단 그림 (a)(b)(c)(d)", "더블클릭"],
        ["결과.md", "수치 표 · 출처 · 감사 판정 · 적어 둘 것 · 원리상 못 하는 것", "메모장 · VS Code"],
        ["<교량>_proxy.ifc", "IFC4 프록시 교량", "Revit · BlenderBIM"],
    ], widths=[4.8, 7.2, 4.0])
    P(doc, "화면으로 보려면 3.5 의 대시보드 — 왼쪽 포트폴리오에서 교량을 고르면 위 탭 ① InSAR · ② PINN · ③ FRAM 에 같은 결과가 나옵니다.")

    # 4. 결과 읽기
    doc.add_page_break()
    H(doc, "4. 결과를 어떻게 읽나", 1)
    H(doc, "4.1 브리프 그림 (a)(b)(c)(d)", 2)
    FIG(doc, ROOT / "docs/bridges/정자교/brief.png",
        "그림 3. 정자교 — (a) PS 교축 배치 (b) 잔차고도: 교면 위 점이 지면보다 +5.2 ± 1.2 m (c) 속도 ± 95% CI (d) 시계열·추세")
    TABLE(doc, [
        ["패널", "보는 것", "판단"],
        ["(a)", "PS 가 교면 안에 몇 개, 어디에", "교면 밖 점은 지반 — 교량 값에 섞이면 안 됨"],
        ["(b)", "DEM 대비 상대고도", "교면 위 점이 지면보다 형하고만큼 높으면 **교면 위 산란체 확인**"],
        ["(c)", "속도 ± 95% CI, ±0.5 mm/yr 참고범위", "오차막대가 0 을 포함 → 유의한 변형 없음"],
        ["(d)", "시계열 중앙값 · 추세", "계절 주기 정상 · 추세 mm/yr"],
    ], widths=[1.4, 6.0, 8.6])

    H(doc, "4.2 감사 판정", 2)
    TABLE(doc, [
        ["판정", "뜻", "예"],
        ["✅ 보고 가능", "언래핑·교량 포함·경간·PINN 물리값 전부 통과", "청양교 · 내곡교"],
        ["🟡 조건부", "쓸 수는 있으나 사유가 있음 — 사유를 함께 적을 것", "정자교(붕괴 교량, 관측으로 못 봄)"],
        ["❌ 보고 불가", "래핑 위상 · 교량 위 점 0 · 비물리 값", "동수원(OSM 연장 불일치)"],
    ], widths=[3.0, 8.0, 5.0])
    P(doc, "**ⓘ 표기**는 감점이 아니라 반드시 알아야 할 사실입니다 — 예: 'EI·고유진동수는 관측값이 아니라 설계 제원 기반'.")

    H(doc, "4.3 PINN 가상센싱", 2)
    FIG(doc, ROOT / "docs/twin/twin_pinn.png",
        "그림 4. 관측점 12개로 학습한 PINN 이 교축 200점 × 201시점의 전체 변위장을 채운다. 흰 선 = 관측점 위치. 관측점 밖은 외삽.")

    # 5. 못 하는 것
    H(doc, "5. 이 프로그램이 원리상 못 하는 것 — 결과를 읽기 전에", 1)
    TABLE(doc, [
        ["항목", "왜", "그러면"],
        ["EI(강성) 관측 식별", "InSAR 는 상대 변위 — 자중 처짐(청양교 이론 345 mm)은 위성이 보기 전에 이미 들어가 있음", "EI·고유진동수는 설계 제원 기반 (ⓘ 표기). 절대 강성은 레벨링·GNSS 필요"],
        ["데크 위 PS 밀도", "Sentinel-1 화소 ~11 m. 81 m 교량은 교축 구간당 화소 1.3개", "고해상도 SAR·코너리플렉터. 긴 교량(마포대교 1,390 m)은 유리"],
        ["쉬프트 방향", "궤도 heading 이 정함 — 조정 대상 아님", "크기 δh 는 잔차고도로 관측값 대체"],
        ["국부 붕괴 탐지", "정자교 2023-04-05 보도부 붕괴(수 m)는 화소보다 작음. 열·추세 제거 후 계단 z=−0.6", "알려진 사고 교량은 자동 표기하고 '정상'을 보고 근거로 쓰지 않음"],
        ["시점 수", "속도 95% CI 는 장 수가 정함. 25장 → 2.5 mm/yr, 201장 → 0.2 mm/yr", "브리프 기준 ≥100장. 부족하면 판정 보류"],
    ], widths=[3.2, 7.2, 5.6])

    # 6. 막히면
    H(doc, "6. 막히면", 1)
    for t in [
        "**⓪ '토큰 없음'** → 3.2 로. 발급은 urs.earthdata.nasa.gov (1분).",
        "**'SNAP gpt 없음'** → SNAP 설치 후 PATH 또는 환경변수 SNAP_HOME.",
        "**'snaphu 없음'** → WSL 에 snaphu. 언래핑이 실패하면 파라미터 사다리 4단계가 자동으로 돌고, 그래도 안 되면 unwrap_retry.json 에 사유.",
        "**OSM 504** → 자동 재시도 3회. 캐시(osm_roads_500m.json)가 있으면 캐시.",
        "**중간에 죽음** → 같은 명령 다시. 받은 SLC 는 재사용. 결과.md 에 '예외:' 줄이 남음.",
        "**'교면 위 점 0'** → 교량이 작거나 궤도 방향이 불리함. 계획(3.7)에서 장면 수·궤도를 먼저 볼 것.",
    ]:
        BUL(doc, t)

    # 7. 예시 교량
    H(doc, "7. 지금까지 돌린 교량 (docs/bridges/)", 1)
    TABLE(doc, [
        ["교량", "점", "CRI", "잔차고도(교면−지면)", "감사", "비고"],
        ["청양교", "44", "0.784", "+12.5 ± 6.4 m", "보고 가능", "25시점 — 속도 CI 넓음"],
        ["정자교", "12", "0.569", "+5.2 ± 1.2 m (z 4.3)", "조건부", "2023-04-05 붕괴 교량 — 관측으로 못 봄"],
        ["내곡교", "34", "0.821 경고", "—", "보고 가능", "건기연 브리프 같은 교량 (PS 35 vs 32)"],
        ["칠백로", "22", "0.808 경고", "—", "조건부", ""],
        ["상규", "3", "0.879 위험", "—", "보고 가능", "점 3개 — 판정 신뢰도 낮음"],
        ["동수원", "77", "0.729", "—", "보고 불가", "CSV 890 m vs OSM 1227 m"],
    ], widths=[2.0, 1.2, 2.2, 3.6, 2.2, 4.8])
    P(doc, "여러 교량을 한 번에: `.venv\\Scripts\\python scripts\\bridge_run.py --batch docs\\bridges\\batch.json`")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    return OUT


if __name__ == "__main__":
    out = build()
    print(f"저장: {out}  ({out.stat().st_size:,} B)")
    sys.exit(0)
