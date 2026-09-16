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


def render_terminal(sess: list[dict], out: Path, fps: int = FPS) -> float:
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
            frames.append(frame())
            frames.append(frame())
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


def _poke(pg, section: str) -> None:
    """탭마다 **진짜 조작**을 하나씩 — 스크롤만 하면 구동이 아니라 구경이 된다.

    누를 게 없으면 조용히 넘어간다(탭 구성이 바뀌어도 녹화가 깨지지 않게).
    """
    targets = {
        "⓪ 시작": ["자세히 보기", "윈도우 도구"],
        "① InSAR": ["인벤토리 점검", "점별 표"],
        "② PINN": ["검증 실행", "제원"],
        "③ FRAM": ["자세히", "경보"],
        "④ 잔존수명": ["자세히", "가정"],
        "⑤ PSI 방법론": ["연직 속도(mm/yr)", "점별 표"],
    }
    for t in targets.get(section, []):
        try:
            el = pg.get_by_text(t, exact=False).first
            if el.count() == 0:
                continue
            el.scroll_into_view_if_needed(timeout=4000)
            pg.wait_for_timeout(500)
            el.click(timeout=4000)
            pg.wait_for_timeout(1800)
            return
        except Exception:                        # noqa: BLE001 — 없으면 그만
            continue


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

        for name in SECTIONS:
            try:
                pg.get_by_text(name, exact=True).first.click(timeout=15_000)
            except Exception as e:                       # noqa: BLE001
                print(f"  · '{name}' 클릭 실패 — 건너뜀 ({type(e).__name__})")
                continue
            pg.wait_for_timeout(2200)
            print(f"  · {name}")
            _poke(pg, name)                     # 그 탭에서 실제로 뭔가 눌러 본다
            # 천천히 훑어 내린다 — 차트가 그려지는 걸 보이게
            steps = 6
            for _ in range(steps):
                pg.mouse.wheel(0, 420)
                pg.wait_for_timeout(int(seconds_per_tab * 1000 / steps / 2))
            pg.wait_for_timeout(900)
            for _ in range(steps):
                pg.mouse.wheel(0, -420)
                pg.wait_for_timeout(int(seconds_per_tab * 1000 / steps / 3))
            pg.wait_for_timeout(600)

        pg.wait_for_timeout(1200)
        ctx.close()                                  # 닫아야 webm 이 저장된다
        br.close()

    vids = sorted(out_dir.glob("*.webm"), key=lambda q: q.stat().st_mtime)
    if not vids:
        raise SystemExit("녹화 파일이 없다 — Playwright 녹화 실패")
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


def build_cues(t_cmd: float, t_app: float) -> list[tuple[float, float, str]]:
    """발표자가 읽을 대본. 화면에 굽지 않으므로 길이에 여유가 있다."""
    c: list[tuple[float, float, str]] = []

    def add(t0: float, dur: float, s: str) -> None:
        c.append((t0, t0 + dur, s))

    add(0.5, 5.0, "받는 사람 PC 에서 어떻게 되는지 그대로 보여 드립니다. "
                  "저장소를 새로 받는 것부터 시작합니다.")
    add(6.0, 5.0, "깃 클론 한 번이면 소스가 통째로 내려옵니다.")
    seg = max(t_cmd - 12.0, 6.0) / 3.0
    add(12.0, seg, "가상환경을 따로 만듭니다. 쓰던 파이썬 환경을 건드리지 않습니다.")
    add(12.0 + seg, seg, "대시보드 의존성까지 한 줄로 설치합니다. "
                         "스트림릿·플로틀리·폴리움이 여기서 같이 들어옵니다.")
    add(12.0 + 2 * seg, seg, "설치가 끝나면 명령 한 줄로 대시보드가 뜹니다. "
                             "여기부터는 터미널을 볼 일이 없습니다.")

    a = t_cmd
    add(a + 0.5, 6.0, "여기가 실제 대시보드입니다. 지금 보시는 건 캡처 화면이 아니라 "
                      "실제로 돌아가는 화면입니다.")
    add(a + 7.0, 6.0, "왼쪽에 저장 폴더와 현재 교량이 있습니다. "
                      "교량 이름으로 찾거나 지도에서 찍어 정합니다.")
    n = 6
    per = max((t_app - 14.0) / n, 4.0)
    texts = [
        "영 번, 시작 탭입니다. 이 컴퓨터가 돌릴 준비가 됐는지 먼저 점검합니다. "
        "필요한 도구가 없으면 여기서 바로 받습니다.",
        "일 번, 인사 탭입니다. 위성 영상에서 뽑은 변위 시계열이 여기 들어옵니다. "
        "지도에서 교량을 찍으면 그 교량의 점만 골라 봅니다.",
        "이 번, 핀 탭입니다. 물리식을 함께 푸는 신경망이 거동을 성분으로 나눕니다 — "
        "열, 하중, 침하, 그리고 설명되지 않는 이상 성분.",
        "삼 번, 프램 탭입니다. 공진 위험 지수를 계산해 네 단계 경보를 냅니다.",
        "사 번, 잔존수명 탭입니다. 사용성 한계까지 남은 시간을 봅니다.",
        "오 번, 피에스아이 방법론 탭입니다. 피에스와 에스바스, 큐피에스를 "
        "같은 자료에 대고 비교합니다.",
    ]
    for i, s in enumerate(texts):
        add(a + 14.0 + i * per, min(per - 0.5, 9.0), s)
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all", choices=["all", "cmd", "app", "join"])
    ap.add_argument("--reuse", action="store_true",
                    help="이미 받아 둔 설치 기록으로 1부만 다시 그린다(설치를 또 돌리지 않음)")
    ap.add_argument("--url", default="http://localhost:8599/")
    ap.add_argument("--out", default="docs/video/inframon_구동_실화면.mp4")
    a = ap.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    p_cmd, p_app = SCRATCH / "part1_cmd.mp4", SCRATCH / "part2_app.mp4"
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

    if a.stage in ("all", "app"):
        print("2부 · 대시보드 — 실제로 조작하며 녹화합니다")
        webm = record_app(a.url, SCRATCH / "webm")
        d = webm_to_mp4(webm, p_app)
        print(f"  → {p_app} ({d:.1f}초)")

    if a.stage in ("all", "join"):
        parts = [q for q in (p_cmd, p_app) if q.exists()]
        if not parts:
            print("합칠 조각이 없다", file=sys.stderr)
            return 2
        total = join(parts, out)
        t_cmd = probe_dur(p_cmd) if p_cmd.exists() else 0.0
        t_app = probe_dur(p_app) if p_app.exists() else 0.0
        n = write_subs(build_cues(t_cmd, t_app),
                       out.with_suffix(".srt"), out.with_suffix(".txt"))
        print(f"wrote {out}  ({total:.1f}초 = 터미널 {t_cmd:.0f}s + 대시보드 {t_app:.0f}s)")
        print(f"자막 {n}줄(화면에 굽지 않음) — {out.with_suffix('.srt')} · "
              f"{out.with_suffix('.txt')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
