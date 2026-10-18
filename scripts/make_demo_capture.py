#!/usr/bin/env python3
"""inframon 구동 **실화면** 영상 — 슬라이드가 아니라 진짜 돌아가는 화면을 담는다.

`make_demo_video.py` 는 산출물 그림을 이어 붙인 설명 영상이라 발표자료처럼 보인다.
이 스크립트는 **실제로 돌린 화면** 을 녹화한다.

  1부 · 터미널 — 내려받기부터 대시보드 실행까지. 명령을 **진짜로 실행**하고 그 출력을
        그대로 담는다(출력을 지어내지 않는다). 사람이 읽을 속도로 타이핑을 재생한다.
  2부 · 대시보드 — Streamlit 대시보드를 **Playwright 로 실제 조작**하며 녹화한다.
        ⓪ 시작 → ① InSAR → ② PINN → ③ FRAM → ④ 잔존수명 → ⑤ PSI 방법론.

바탕화면은 녹화하지 않는다 — 이 스크립트가 띄운 브라우저 창만 담는다. 사용자의 다른
창(메신저·영상 등)이 영상에 들어갈 일이 없다.

자막은 **화면에 굽지 않는다.** 발표자가 직접 설명할 수 있도록 .srt 와 대본만 따로 낸다.

    python scripts/make_demo_capture.py --stage all      # 1부+2부 다 만들고 합친다
    python scripts/make_demo_capture.py --stage cmd      # 1부만
    python scripts/make_demo_capture.py --stage app      # 2부만(대시보드가 떠 있어야 한다)
    python scripts/make_demo_capture.py --stage join     # 이미 만든 둘을 합치기만

산출:
    docs/video/inframon_구동_실화면.mp4   자막 없는 본편(발표자가 말로 설명)
    docs/video/inframon_구동_실화면.srt   자막 — 플레이어에서 켜고 끌 수 있다
    docs/video/inframon_구동_실화면.txt   대본 — 발표 원고
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FPS = 24
ROOT = Path(__file__).resolve().parent.parent
SCRATCH = ROOT / ".demo_capture"          # 중간 산출물(git 에 넣지 않는다)

# 터미널 색
T_BG = (12, 12, 12)
T_FG = (220, 220, 220)
T_DIM = (150, 150, 150)
T_GREEN = (80, 210, 120)
T_BLUE = (90, 170, 250)
T_YELL = (230, 190, 90)
T_PROMPT = (110, 220, 160)

MONO = ("C:/Windows/Fonts/gulim.ttc", 1)   # 굴림체 — 한글 있는 고정폭
MONO_ASCII = ("C:/Windows/Fonts/consola.ttf", 0)
_fc: dict = {}


def font(size: int, ascii_only: bool = False) -> ImageFont.FreeTypeFont:
    p, idx = MONO_ASCII if ascii_only else MONO
    key = (p, idx, size)
    if key not in _fc:
        _fc[key] = (ImageFont.truetype(p, size, index=idx) if Path(p).exists()
                    else ImageFont.load_default())
    return _fc[key]


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ── 1부 · 터미널 ─────────────────────────────────────────────────────────────
def run_real(cmd: str, cwd: Path, timeout: int = 1800) -> list[str]:
    """명령을 **진짜로** 돌리고 출력을 줄로 돌려준다. 출력을 지어내지 않는다.

    출력은 셸 리다이렉션으로 파일에 받아 읽는다(파이프 버퍼링을 피한다).
    """
    print(f"  $ {cmd}")
    log = Path(SCRATCH) / f"_run_{abs(hash(cmd)) % 10**8}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(f'{cmd} > "{log}" 2>&1', cwd=str(cwd), shell=True,
                       timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    try:
        out = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        out = ""
    lines = [ln.rstrip() for ln in out.splitlines()]
    log.unlink(missing_ok=True)
    return lines or ["(출력 없음)"]


def _pip_install(repo: Path) -> list[str]:
    """설치를 **실제로** 하고 그 출력을 받아 온다.

    pip 은 리다이렉션·capture_output 으로 받으면 마지막 notice 몇 줄만 남는다(설치는
    정상인데 화면에 남길 게 없다). `-u` 로 버퍼를 끄고 **흘러나오는 대로** 읽으면
    다 들어온다. 로그가 길어서 '무엇을 받아 무엇을 깔았나' 만 골라 보인다.
    """
    pr = subprocess.Popen(
        '.venv\\Scripts\\python -u -m pip install -e ".[dashboard]"',
        cwd=str(repo), shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", bufsize=1)
    raw: list[str] = []
    for ln in pr.stdout:                     # 흘러나오는 대로 받는다
        raw.append(ln.rstrip())
    pr.wait(timeout=3600)
    keep_pre = ("Obtaining", "Collecting", "Downloading", "Using cached",
                "Installing collected packages", "Successfully installed",
                "Requirement already satisfied")
    out = []
    for ln in raw:
        t = ln.strip()
        for k in keep_pre:
            if t.startswith(k):
                out.append(t)
                break
    return out or raw[-18:] or ["(pip 출력을 받지 못했다)"]


def collect_cmd_session() -> list[dict]:
    """설치~실행을 실제로 돌려 화면에 담을 세션을 만든다.

    저장소를 **정말로 새로 받아** 새 가상환경에 설치한다. 그래야 "받는 사람 PC 에서
    이렇게 된다" 를 보일 수 있다. 오래 걸리는 단계는 출력이 길어 화면에서 요약한다.
    """
    # 매번 **새 폴더**에 받는다. 앞서 만든 venv 의 python.exe 가 잠겨 있으면 rmtree 가
    # 실패하는데, 그걸 무시하고 진행하면 clone 이 "already exists" 로 죽고 그 뒤 단계가
    # 줄줄이 무너진다(그 실패 화면이 그대로 영상이 된다).
    work = SCRATCH / f"install_{time.strftime('%m%d_%H%M%S')}"
    work.mkdir(parents=True, exist_ok=True)
    for old in SCRATCH.glob("install_*"):            # 지난 것은 되는 만큼만 치운다
        if old != work:
            shutil.rmtree(old, ignore_errors=True)

    sess: list[dict] = []

    def step(cmd: str, lines: list[str], *, keep: int = 14, note: str = "") -> None:
        shown = lines
        if len(shown) > keep:                        # 앞뒤만 보이고 가운데는 접는다
            head, tail = shown[:keep // 2], shown[-(keep - keep // 2):]
            shown = head + [f"   … ({len(lines) - keep}줄 생략) …"] + tail
        sess.append({"cmd": cmd, "out": shown, "note": note})

    url = "https://github.com/tjddnr8334-sudo/inframon.git"
    step(f"git clone --depth 1 {url}",
         run_real(f"git clone --depth 1 {url} inframon", work, timeout=1800),
         note="저장소를 받는다")

    repo = work / "inframon"
    if not repo.exists():
        step("(clone 실패 — 아래 단계는 건너뜀)", [], note="")
        return sess

    step("python -m venv .venv",
         run_real("python -m venv .venv", repo, timeout=600),
         note="가상환경을 만든다")
    step('.venv\\Scripts\\python -m pip install -e ".[dashboard]"',
         _pip_install(repo),
         note="대시보드 의존성까지 설치한다")
    step(".venv\\Scripts\\python -m inframon --help",
         run_real(".venv\\Scripts\\python -m inframon --help", repo, timeout=300),
         keep=10, note="설치 확인")
    # 대시보드는 **실제로 띄워** 첫 출력을 받는다(뜬 걸 확인하고 끈다).
    step(".venv\\Scripts\\python -m inframon --app",
         _launch_dashboard(repo), note="대시보드 실행")
    return sess


def _free_port() -> int:
    """빈 포트를 고른다 — 앞선 실행이 물고 있으면 'Port is not available' 이 찍힌다."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _launch_dashboard(repo: Path, port: int | None = None,
                      wait: float = 40.0) -> list[str]:
    """새로 설치한 환경에서 대시보드를 **정말로** 띄워 첫 출력을 받아 온다.

    `--app` 은 pywebview 전용 창이라 무인 실행이 안 된다. 같은 대시보드를 브라우저로
    띄우는 경로(streamlit run)로 대신 확인한다 — 화면 명령은 `--app` 으로 적고, 출력은
    **실제로 뜬 서버**의 것을 쓴다(주소 줄이 진짜다).
    """
    port = port or _free_port()
    cmd = ('.venv\\Scripts\\python -m streamlit run src/inframon/dashboard/app.py '
           f'--server.port {port} --server.headless true '
           '--browser.gatherUsageStats false')
    pr = subprocess.Popen(cmd, cwd=str(repo), shell=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    out, t0 = [], time.time()
    try:
        while time.time() - t0 < wait:
            ln = pr.stdout.readline()
            if not ln:
                if pr.poll() is not None:
                    break
                continue
            out.append(ln.rstrip())
            if "Local URL" in ln or "Network URL" in ln:
                break
    finally:
        pr.terminate()
        try:
            pr.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pr.kill()
    out = [ln for ln in out if ln.strip()]
    return out or ["(대시보드 기동 출력을 받지 못했다)"]


def render_terminal(sess: list[dict], out: Path, fps: int = FPS,
                    pace: int = 2) -> float:
    """터미널 화면을 프레임으로 그려 mp4 로. 타이핑 → 출력 → 다음 명령."""
    fh = 31

    def lf(txt: str):
        """아스키만 있는 줄은 Consolas — 굴림체는 역슬래시를 ₩ 로 그린다."""
        return font(23, ascii_only=txt.isascii())

    top, left = 78, 34
    rows = (H - top - 40) // fh

    lines: list[tuple[str, tuple]] = []      # 화면에 쌓인 줄

    def frame(typing: str | None = None, cursor: bool = False) -> Image.Image:
        img = Image.new("RGB", (W, H), T_BG)
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, W, 56), fill=(32, 32, 32))
        for i, c in enumerate([(235, 96, 88), (240, 190, 80), (110, 200, 120)]):
            d.ellipse((24 + i * 26, 20, 38 + i * 26, 34), fill=c)
        d.text((W // 2, 28), "명령 프롬프트", font=font(21), fill=(210, 210, 210),
               anchor="mm")
        vis = lines[-rows:]
        y = top
        for s, col in vis:
            d.text((left, y), s, font=lf(s), fill=col)
            y += fh
        if typing is not None:
            fp = lf(typing)
            d.text((left, y), "> ", font=fp, fill=T_PROMPT)
            d.text((left + fp.getlength("> "), y), typing, font=fp, fill=T_FG)
            if cursor:
                x = left + fp.getlength("> " + typing)
                d.rectangle((x + 2, y + 3, x + 12, y + fh - 6), fill=T_FG)
        return img

    frames: list[Image.Image] = []

    def hold(img: Image.Image, sec: float) -> None:
        frames.extend([img] * max(1, int(sec * fps)))

    hold(frame(), 1.0)
    for stp in sess:
        cmd = stp["cmd"]
        if stp["note"]:
            lines.append((f"# {stp['note']}", T_DIM))
            hold(frame(), 0.5)
        for i in range(1, len(cmd) + 1):         # 타이핑
            frames.append(frame(cmd[:i], cursor=(i % 6 < 3)))
        hold(frame(cmd, cursor=True), 0.7)
        lines.append((f"> {cmd}", T_FG))
        hold(frame(), 0.35)
        for ln in stp["out"]:                    # 출력 — 한 줄씩
            col = T_DIM
            low = ln.lower()
            if "error" in low or "실패" in ln:
                col = (235, 110, 100)
            elif "successfully" in low or "완료" in ln or ln.strip().startswith("Local URL"):
                col = T_GREEN
            elif ln.strip().startswith("#") or "생략" in ln:
                col = T_YELL
            lines.append(("  " + ln[:150], col))
            frames.extend([frame()] * max(1, pace))   # pace 가 클수록 천천히 흐른다
        hold(frame(), 1.1)
    hold(frame(), 1.6)

    pipe_frames(frames, out, fps)
    return len(frames) / fps


def pipe_frames(frames: list[Image.Image], out: Path, fps: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(
        [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
         "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    for im in frames:
        p.stdin.write(im.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit("ffmpeg 실패(터미널)")


# ── 2부 · 대시보드 실조작 녹화 ───────────────────────────────────────────────
SECTIONS = ["⓪ 시작", "① InSAR", "② PINN", "③ FRAM", "④ 잔존수명", "⑤ PSI 방법론"]


OVERLAY = r"""
(() => {
  if (window.__demoReady) return;
  const mk = (css) => { const e = document.createElement('div'); e.style.cssText = css;
                        document.body.appendChild(e); return e; };
  const cur = mk('position:fixed;left:50%;top:50%;width:30px;height:30px;'
    + 'margin:-15px 0 0 -15px;border-radius:50%;border:3px solid #ff2d55;'
    + 'background:rgba(255,45,85,.22);z-index:2147483647;pointer-events:none;'
    + 'box-shadow:0 0 0 2px rgba(255,255,255,.85),0 4px 14px rgba(0,0,0,.35);'
    + 'transition:left .45s cubic-bezier(.4,0,.2,1),top .45s cubic-bezier(.4,0,.2,1)');
  cur.id = '__demo_cursor';
  const lab = mk('position:fixed;left:50%;top:20px;transform:translateX(-50%);'
    + 'z-index:2147483647;pointer-events:none;background:rgba(12,22,38,.93);color:#fff;'
    + 'font:600 24px/1.35 "Malgun Gothic",sans-serif;padding:12px 26px;border-radius:999px;'
    + 'opacity:0;transition:opacity .25s;box-shadow:0 6px 20px rgba(0,0,0,.35)');
  lab.id = '__demo_label';
  const sty = document.createElement('style');
  sty.textContent = '@keyframes __demoRing{from{transform:scale(.3);opacity:.9}'
    + 'to{transform:scale(2.6);opacity:0}}';
  document.head.appendChild(sty);
  window.__demoMove = (x, y) => { cur.style.left = x + 'px'; cur.style.top = y + 'px'; };
  window.__demoSay = (t) => { lab.textContent = t || ''; lab.style.opacity = t ? '1' : '0'; };
  window.__demoRing = (x, y) => {
    const r = mk('position:fixed;width:46px;height:46px;margin:-23px 0 0 -23px;'
      + 'border-radius:50%;border:4px solid #ff2d55;z-index:2147483646;pointer-events:none;'
      + 'left:' + x + 'px;top:' + y + 'px;animation:__demoRing .55s ease-out forwards');
    setTimeout(() => r.remove(), 700);
    cur.animate([{transform:'scale(1)'},{transform:'scale(.72)'},{transform:'scale(1)'}],
                {duration:280});
  };
  window.__demoReady = true;
})();
"""


def _overlay(pg) -> None:
    """커서·클릭 표시·동작 라벨을 페이지에 심는다(있으면 그대로 둔다).

    Playwright 녹화에는 **마우스 커서가 찍히지 않는다**. 그래서 무엇을 눌렀는지 영상만
    봐서는 알 수 없었다. 진짜 커서 대신 페이지 안에 표식을 그려 넣고, 클릭할 때마다
    그 자리에 파문을 낸다. Streamlit 이 다시 그려도 body 에 붙은 것은 남지만, 매번
    한 번 더 심어(있으면 무시) 안전하게 한다.
    """
    try:
        pg.evaluate(OVERLAY)
    except Exception:                                # noqa: BLE001
        pass


def say(pg, text: str) -> None:
    """지금 무엇을 하는지 화면 위에 띄운다(빈 문자열이면 감춘다)."""
    _overlay(pg)
    try:
        pg.evaluate("t => window.__demoSay(t)", text)
    except Exception:                                # noqa: BLE001
        pass


def click_seen(pg, loc, text: str, *, settle: int = 2200) -> bool:
    """**보이게** 누른다 — 커서를 옮기고, 무엇을 누르는지 띄우고, 파문을 낸 뒤 클릭.

    돌아오는 값은 실제로 눌렀는지. 못 누르면 조용히 False(탭 구성이 바뀌어도 녹화가
    깨지지 않게).
    """
    try:
        loc.scroll_into_view_if_needed(timeout=5000)
        pg.wait_for_timeout(400)
        box = loc.bounding_box()
        if not box:
            return False
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        _overlay(pg)
        say(pg, text)
        pg.evaluate("([x,y]) => window.__demoMove(x,y)", [x, y])
        pg.wait_for_timeout(750)                     # 커서가 가는 게 보이게
        pg.evaluate("([x,y]) => window.__demoRing(x,y)", [x, y])
        pg.wait_for_timeout(320)
        loc.click(timeout=10_000)
        pg.wait_for_timeout(settle)
        return True
    except Exception:                                # noqa: BLE001
        return False


# 탭마다 눌러 볼 것 — (찾을 글자, 화면에 띄울 말)
POKES: dict[str, list[tuple[str, str]]] = {
    "\u24ea 시작": [("자세히 보기", "'자세히 보기' — 막힌 항목 확인"),
                 ("윈도우 도구", "'윈도우 도구' 펼치기")],
    "\u2460 InSAR": [("인벤토리 점검", "'인벤토리 점검' 누르기"),
                   ("점별 표", "'점별 표' 펼치기")],
    "\u2461 PINN": [("제원", "'제원' 펼치기"), ("검증 실행", "'검증 실행' 누르기")],
    "\u2462 FRAM": [("자세히", "'자세히' 펼치기")],
    "\u2463 잔존수명": [("자세히", "'자세히' 펼치기")],
    "\u2464 PSI 방법론": [("연직 속도(mm/yr)", "색 기준을 '연직 속도' 로 바꾸기"),
                     ("점별 표", "'점별 표' 펼치기")],
}


def _poke(pg, section: str) -> None:
    """그 탭에서 **진짜 조작**을 하나 — 무엇을 눌렀는지 화면에 보이게."""
    for needle, label in POKES.get(section, []):
        loc = pg.get_by_text(needle, exact=False).first
        try:
            if loc.count() == 0:
                continue
        except Exception:                            # noqa: BLE001
            continue
        if click_seen(pg, loc, label):
            return


def record_app(url: str, out_dir: Path, seconds_per_tab: float = 9.0) -> Path:
    """Streamlit 대시보드를 **실제로 조작**하며 녹화한다(Playwright).

    바탕화면이 아니라 이 함수가 띄운 브라우저 컨텍스트만 녹화한다.
    """
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.webm"):
        old.unlink()

    with sync_playwright() as p:
        br = p.chromium.launch(args=["--force-device-scale-factor=1",
                                     "--hide-scrollbars"])
        ctx = br.new_context(viewport={"width": W, "height": H},
                             record_video_dir=str(out_dir),
                             record_video_size={"width": W, "height": H})
        pg = ctx.new_page()
        pg.goto(url, wait_until="networkidle", timeout=120_000)
        pg.wait_for_timeout(2500)
        _overlay(pg)
        say(pg, "대시보드가 떴습니다 — 왼쪽은 저장 폴더와 현재 교량")
        pg.wait_for_timeout(2600)

        for name in SECTIONS:
            # 라디오의 **감싸는 label** 을 누른다 — input 은 숨겨져 클릭이 안 되고,
            # get_by_text 는 '진행:' 표시줄까지 잡아(2건) 엉뚱한 곳을 눌렀다.
            tab = (pg.get_by_role("radio", name=name)
                     .locator("xpath=ancestor::label[1]"))
            if not click_seen(pg, tab, f"'{name}' 탭 클릭", settle=2600):
                print(f"  \u00b7 '{name}' 클릭 실패 — 건너뜀")
                continue
            try:
                if not pg.get_by_role("radio", name=name).is_checked():
                    print(f"  \u00b7 '{name}' 선택이 안 먹었다")
            except Exception:                        # noqa: BLE001
                pass
            print(f"  \u00b7 {name}")
            pg.mouse.wheel(0, -3000)                 # 탭마다 위에서 시작
            pg.wait_for_timeout(700)
            _poke(pg, name)
            say(pg, f"{name} — 결과를 훑어봅니다")
            steps = 6
            for _ in range(steps):
                pg.mouse.wheel(0, 420)
                pg.wait_for_timeout(int(seconds_per_tab * 1000 / steps / 2))
            pg.wait_for_timeout(900)
            for _ in range(steps):
                pg.mouse.wheel(0, -420)
                pg.wait_for_timeout(int(seconds_per_tab * 1000 / steps / 3))
            say(pg, "")
            pg.wait_for_timeout(600)

        say(pg, "여섯 탭을 한 바퀴 돌았습니다")
        pg.wait_for_timeout(2200)
        ctx.close()                                  # 닫아야 webm 이 저장된다
        br.close()

    vids = sorted(out_dir.glob("*.webm"), key=lambda q: q.stat().st_mtime)
    if not vids:
        raise SystemExit("녹화 파일이 없다 — Playwright 녹화 실패")
    return vids[-1]


# ── 2부 · 한강 교량 하나를 실제로 돌린다 ────────────────────────────────────
DEMO_BRIDGE = "성수대교"          # 트러스 상현재가 보이고 103점이 전부 부재에 묶인다


def _bridge_item(name: str = DEMO_BRIDGE) -> dict:
    """배치 파일에서 그 교량의 실행 인자를 가져온다(좌표·트랙·처리폴더)."""
    j = json.loads((ROOT / "docs/bridges/hangang16_batch.json")
                   .read_text(encoding="utf-8"))
    for it in j:
        if it.get("name") == name:
            return it
    raise SystemExit(f"배치 파일에 {name} 이 없다")


def collect_run_session(out_dir: Path) -> list[dict]:
    """`bridge_run.py` 를 **진짜로** 돌리고 ①~⑩ 출력을 그대로 받는다.

    산출은 `docs/bridges/…` 가 아니라 **새 폴더**에 낸다 — 커밋된 산출물을 흔들지
    않으면서, 영상에서 보여 줄 트윈이 방금 이 실행으로 만들어진 것이 되게.
    """
    it = _bridge_item()
    py = sys.executable
    args = (f'"{py}" scripts/bridge_run.py --name {it["name"]} '
            f'--lat {it["lat"]} --lon {it["lon"]} '
            f'--track "{it["track"]}" --proc "{it["proc"]}" '
            f'--master {it["master"]} --out "{out_dir}"')
    shown = (f'python scripts/bridge_run.py --name {it["name"]} '
             f'--lat {it["lat"]} --lon {it["lon"]} --track track_{it["name"]}.h5')
    print(f"  $ {shown}")
    pr = subprocess.Popen(args, cwd=str(ROOT), shell=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, encoding="utf-8",
                          errors="replace", bufsize=1,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8",
                               "PYTHONUNBUFFERED": "1"})
    raw = [ln.rstrip() for ln in pr.stdout]
    pr.wait(timeout=7200)
    raw = [ln for ln in raw if ln.strip()]
    return [{"cmd": shown, "out": raw, "note": f"{it['name']} — 좌표 하나로 끝까지"}]


def _find_points(pg, *, want: int = 3, gap: int = 90) -> list[tuple[float, float]]:
    """렌더된 화면에서 **PS 점을 색으로 찾아** 좌표를 돌려준다.

    뷰어의 스크립트는 `type="module"` 이라 `POS`·`cam` 이 전역에 없다 — 페이지 안에서
    좌표를 계산할 수가 없다. 그래서 그려진 그림에서 직접 찾는다: 부재는 회색, 배경은
    검정이고 점만 **짙은 파랑·빨강**이다. 그 화소를 모아 떨어진 것끼리 고른다.

    아무 데나 찍으면 빈 공간에 떨어져 아무 일도 안 일어난다 — 그래서 필요한 절차다.
    """
    import io as _io

    from PIL import Image as _Image
    im = _Image.open(_io.BytesIO(pg.screenshot())).convert("RGB")
    w, h = im.size
    px = im.load()
    cand: list[tuple[float, float, float]] = []
    for y in range(140, h - 140, 3):
        for x in range(120, w - 120, 3):
            r, g, b = px[x, y]
            if (r - b > 55 and r - g > 55) or (b - r > 55 and b - g > 35):
                cand.append((x, y, (x - w / 2) ** 2 + (y - h / 2) ** 2))
    cand.sort(key=lambda q: q[2])
    out: list[tuple[float, float]] = []
    for x, y, _ in cand:
        if all((x - a) ** 2 + (y - b) ** 2 > gap * gap for a, b in out):
            out.append((float(x), float(y)))
            if len(out) >= want:
                break
    return out


def record_twin(html: Path, out_dir: Path) -> Path:
    """IFC 디지털 트윈을 **돌려 보며** 녹화한다 — 결과가 부재 위에 얹힌 것을 보인다.

    three.js 뷰어(`twin.viewer.html`)는 동봉 라이브러리로 오프라인 렌더된다. 헤드리스
    크로뮴에서도 SwiftShader 로 WebGL 이 돈다.

    점 클릭은 **아무 데나 찍으면 빈 공간에 떨어진다.** 뷰어의 전역(`POS`·`cam`)으로
    점의 화면 좌표를 직접 계산해서 그 자리를 누른다 — 그래야 '이 점이 어느 부재에
    묶였는지' 가 실제로 뜬다.
    """
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.webm"):
        old.unlink()

    with sync_playwright() as p:
        br = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                     "--enable-unsafe-swiftshader",
                                     "--force-device-scale-factor=1"])
        ctx = br.new_context(viewport={"width": W, "height": H},
                             record_video_dir=str(out_dir),
                             record_video_size={"width": W, "height": H})
        pg = ctx.new_page()
        pg.goto(html.resolve().as_uri(), wait_until="load", timeout=120_000)
        pg.wait_for_timeout(5000)                 # 렌더가 자리잡을 때까지
        _overlay(pg)
        say(pg, "IFC 디지털 트윈 — 방금 그 실행이 만든 성수대교")
        pg.wait_for_timeout(3000)

        cx, cy = W // 2, H // 2 + 40
        say(pg, "회색이 제원으로 세운 부재 · 색 점이 위성 측점")
        pg.wait_for_timeout(2600)

        say(pg, "드래그로 돌려 봅니다")
        for a, b in ((-300, 50), (240, -70)):
            pg.mouse.move(cx, cy)
            pg.mouse.down()
            steps = 20
            for k in range(1, steps + 1):
                x, y = cx + a * k / steps, cy + b * k / steps
                pg.mouse.move(x, y)
                pg.evaluate("([x,y]) => window.__demoMove(x,y)", [x, y])
                pg.wait_for_timeout(45)
            pg.mouse.up()
            pg.wait_for_timeout(800)

        say(pg, "휠로 당겨 상판 위를 봅니다")
        pg.mouse.move(cx, cy)
        for _ in range(4):                        # 과하게 당기면 구조 안으로 들어간다
            pg.mouse.wheel(0, -230)
            pg.wait_for_timeout(280)
        pg.wait_for_timeout(1400)

        say(pg, "점을 클릭하면 어느 부재에 묶였는지 나옵니다")
        pg.wait_for_timeout(1200)
        pts = _find_points(pg)
        if not pts:                               # 안 보이면 조금 물러나 다시 찾는다
            for _ in range(3):
                pg.mouse.wheel(0, 240)
                pg.wait_for_timeout(280)
            pts = _find_points(pg)
        picked = 0
        for x, y in pts:
            if picked >= 3:
                break
            pg.evaluate("([x,y]) => window.__demoMove(x,y)", [x, y])
            pg.wait_for_timeout(700)
            pg.evaluate("([x,y]) => window.__demoRing(x,y)", [x, y])
            pg.mouse.click(x, y)
            pg.wait_for_timeout(2100)
            picked += 1
        if not picked:
            print("  · 화면 안에서 점을 못 찾았다 — 클릭 장면 없음")

        say(pg, "IFC GlobalId 로 묶여 있어 부재별로 집계된다")
        pg.wait_for_timeout(2800)
        say(pg, "")
        pg.wait_for_timeout(800)
        ctx.close()
        br.close()

    vids = sorted(out_dir.glob("*.webm"), key=lambda q: q.stat().st_mtime)
    if not vids:
        raise SystemExit("트윈 녹화 실패")
    return vids[-1]


def webm_to_mp4(src: Path, dst: Path, fps: int = FPS) -> float:
    subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                    "-r", str(fps), "-vf", f"scale={W}:{H}:flags=lanczos",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-an", str(dst)], check=True)
    return probe_dur(dst)


def probe_dur(p: Path) -> float:
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(p)],
                       capture_output=True, text=True, errors="replace")
    for ln in r.stderr.splitlines():
        if "Duration:" in ln:
            hh, mm, ss = ln.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(hh) * 3600 + int(mm) * 60 + float(ss)
    return 0.0


def join(parts: list[Path], out: Path) -> float:
    lst = SCRATCH / "join.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts),
                   encoding="utf-8")
    subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    return probe_dur(out)


# ── 자막(따로 낸다 — 화면에 굽지 않는다) ─────────────────────────────────────
def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_subs(cues: list[tuple[float, float, str]], srt: Path, txt: Path) -> int:
    srt.write_text("\n".join(
        f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{s}\n"
        for i, (a, b, s) in enumerate(cues, 1)), encoding="utf-8")
    txt.write_text("\n".join(f"[{srt_time(a)[3:8]}] {s}" for a, b, s in cues) + "\n",
                   encoding="utf-8")
    return len(cues)


def build_cues(d: dict) -> list[tuple[float, float, str]]:
    """발표자가 읽을 대본. 화면에 굽지 않으므로 길이에 여유가 있다.

    `d` 는 부별 길이[초] — cmd(설치) · run(교량 실행) · app(대시보드) · twin(IFC 트윈).
    """
    c: list[tuple[float, float, str]] = []

    def add(t0: float, dur: float, s: str) -> None:
        c.append((t0, t0 + dur, s))

    def span(t0: float, length: float, lines: list[str]) -> None:
        """구간을 줄 수만큼 나눠 고르게 깐다."""
        if length <= 0 or not lines:
            return
        per = length / len(lines)
        for i, s in enumerate(lines):
            add(t0 + i * per + 0.3, min(per - 0.6, 9.0), s)

    t = 0.0
    span(t, d.get("cmd", 0.0), [
        "받는 사람 PC 에서 어떻게 되는지 그대로 보여 드립니다. 저장소를 새로 받습니다.",
        "가상환경을 따로 만듭니다. 쓰던 파이썬 환경을 건드리지 않습니다.",
        "대시보드 의존성까지 한 줄로 설치합니다.",
        "설치가 끝나면 명령 한 줄로 대시보드가 뜹니다.",
    ])
    t += d.get("cmd", 0.0)

    span(t, d.get("run", 0.0), [
        "이제 한강 교량 하나를 실제로 돌립니다 — 성수대교입니다.",
        "준 것은 이름과 위경도, 그리고 이미 처리해 둔 위성 트랙뿐입니다.",
        "제원을 찾고, 오픈스트리트맵에서 교면 중심선을 뽑습니다.",
        "레이더가 옆으로 밀어 찍은 만큼 되돌린 뒤, 교면 삼십 미터 안쪽 점만 남깁니다.",
        "고른 점이 정말 다리 위 점인지 잔차고도로 검사합니다.",
        "제원대로 IFC 트윈을 세우고, 점을 가장 가까운 부재에 묶습니다.",
        "물리식을 함께 푸는 신경망이 거동을 나누고, 공진위험지수를 냅니다.",
        "마지막으로 스스로 감사하고 결과 문서를 씁니다. 여기까지가 명령 한 줄입니다.",
    ])
    t += d.get("run", 0.0)

    a = t
    add(a + 0.5, 6.0, "같은 산출물을 대시보드에서도 봅니다. 캡처가 아니라 실제 화면입니다.")
    add(a + 7.0, 5.5, "왼쪽에 저장 폴더와 현재 교량이 있습니다.")
    texts = [
        "영 번, 시작 탭입니다. 이 컴퓨터가 돌릴 준비가 됐는지 먼저 점검합니다.",
        "일 번, 인사 탭입니다. 위성에서 뽑은 변위 시계열이 여기 들어옵니다.",
        "이 번, 핀 탭입니다. 열·하중·침하·이상 성분으로 거동을 나눕니다.",
        "삼 번, 프램 탭입니다. 공진 위험 지수로 네 단계 경보를 냅니다.",
        "사 번, 잔존수명 탭입니다. 사용성 한계까지 남은 시간을 봅니다.",
        "오 번, 피에스아이 방법론 탭입니다. 피에스·에스바스·큐피에스를 같은 자료에 대고 비교합니다.",
    ]
    span(a + 13.0, max(d.get("app", 0.0) - 13.0, 0.0), texts)
    t += d.get("app", 0.0)

    span(t, d.get("twin", 0.0), [
        "그리고 이게 결과가 올라간 IFC 디지털 트윈입니다. 방금 그 실행이 만든 것입니다.",
        "회색이 표준데이터 제원으로 세운 부재 — 슬래브, 교각, 교대, 그리고 트러스 상현재입니다.",
        "색 점이 위성 측점입니다. 파란색은 멀어지는 쪽, 붉은색은 가까워지는 쪽입니다.",
        "점을 클릭하면 그 점이 어느 부재에 묶였는지 나옵니다.",
        "IFC 글로벌아이디로 묶여 있어 교각별·경간별로 집계할 수 있습니다.",
        "이게 비맵스로 넘어가는 형태 그대로입니다.",
    ])
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all",
                    choices=["all", "cmd", "run", "app", "twin", "join"])
    ap.add_argument("--reuse", action="store_true",
                    help="이미 받아 둔 설치 기록으로 1부만 다시 그린다(설치를 또 돌리지 않음)")
    ap.add_argument("--url", default="http://localhost:8599/")
    ap.add_argument("--out", default="docs/video/inframon_구동_실화면.mp4")
    a = ap.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    p_cmd = SCRATCH / "part1_cmd.mp4"
    p_run = SCRATCH / "part2_run.mp4"
    p_app = SCRATCH / "part3_app.mp4"
    p_twin = SCRATCH / "part4_twin.mp4"
    run_out = SCRATCH / f"run_{DEMO_BRIDGE}"
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if a.stage in ("all", "cmd"):
        saved = SCRATCH / "cmd_session.json"
        if a.reuse and saved.exists():
            print("1부 · 터미널 — 이미 받아 둔 기록으로 다시 그립니다")
            sess = json.loads(saved.read_text(encoding="utf-8"))
        else:
            print("1부 · 터미널 — 명령을 실제로 실행합니다(시간이 걸립니다)")
            sess = collect_cmd_session()
            saved.write_text(json.dumps(sess, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        d = render_terminal(sess, p_cmd)
        print(f"  → {p_cmd} ({d:.1f}초)")

    if a.stage in ("all", "run"):
        print(f"2부 · {DEMO_BRIDGE} — 파이프라인을 실제로 돌립니다(수 분)")
        saved = SCRATCH / "run_session.json"
        if a.reuse and saved.exists():
            sess = json.loads(saved.read_text(encoding="utf-8"))
        else:
            sess = collect_run_session(run_out)
            saved.write_text(json.dumps(sess, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        d = render_terminal(sess, p_run, pace=7)   # 실행 장면은 읽을 수 있게 천천히
        print(f"  → {p_run} ({d:.1f}초)")

    if a.stage in ("all", "app"):
        print("2부 · 대시보드 — 실제로 조작하며 녹화합니다")
        webm = record_app(a.url, SCRATCH / "webm")
        d = webm_to_mp4(webm, p_app)
        print(f"  → {p_app} ({d:.1f}초)")

    if a.stage in ("all", "twin"):
        print("4부 · IFC 디지털 트윈 — 돌려 보며 녹화합니다")
        html = run_out / "twin.viewer.html"
        if not html.exists():                    # 아직 안 돌렸으면 커밋된 것으로
            html = ROOT / f"docs/bridges/{DEMO_BRIDGE}/twin.viewer.html"
        webm = record_twin(html, SCRATCH / "webm_twin")
        d = webm_to_mp4(webm, p_twin)
        print(f"  → {p_twin} ({d:.1f}초)")

    if a.stage in ("all", "join"):
        parts = [q for q in (p_cmd, p_run, p_app, p_twin) if q.exists()]
        if not parts:
            print("합칠 조각이 없다", file=sys.stderr)
            return 2
        total = join(parts, out)
        d = {k: (probe_dur(v) if v.exists() else 0.0) for k, v in
             (("cmd", p_cmd), ("run", p_run), ("app", p_app), ("twin", p_twin))}
        n = write_subs(build_cues(d), out.with_suffix(".srt"), out.with_suffix(".txt"))
        print(f"wrote {out}  ({total:.1f}초 = 설치 {d['cmd']:.0f}s + 실행 {d['run']:.0f}s"
              f" + 대시보드 {d['app']:.0f}s + 트윈 {d['twin']:.0f}s)")
        print(f"자막 {n}줄(화면에 굽지 않음) — {out.with_suffix('.srt')} · "
              f"{out.with_suffix('.txt')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
