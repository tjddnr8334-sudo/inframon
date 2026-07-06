# inframon InSAR F코어 처리도구 컨테이너 — ISCE2 + MiaplPy/MintPy + SARvey.
#
# "처리도구를 프로그램 내 저장" 의 재현가능 실현: 도구 바이너리(각 수 GB·컴파일)를 파이썬
# 패키지에 담을 수는 없지만, **이 정의는 리포에 버전 저장**되고 한 번 빌드하면 자체완결·
# 이식가능한 이미지가 된다. inframon 의 `--insar-tools` 가 이 레시피를 안내/구동한다.
#
# 빌드:  docker build -t inframon-insar -f scripts/wsl_sarvey/Dockerfile .
# 실행:  docker run --rm -it -v "$PWD:/work" -w /work inframon-insar bash
#          conda activate isce2  && bash scripts/wsl_sarvey/20_stack_isce.sh <recipe> <work>
#          conda activate miaplpy&& bash scripts/wsl_sarvey/30_miaplpy.sh    <recipe> <work>
#          conda activate sarvey && bash scripts/wsl_sarvey/40_sarvey.sh     <recipe> <work>
# (Apptainer/Singularity 가 필요하면: apptainer build inframon-insar.sif docker-daemon://inframon-insar:latest)

FROM condaforge/miniforge3:24.3.0-0

SHELL ["/bin/bash", "-lc"]
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        git wget unzip build-essential && rm -rf /var/lib/apt/lists/*

# (1) isce2 — 검색·다운로드·topsStack 코레지스트레이션
RUN mamba create -y -n isce2 -c conda-forge python=3.10 isce2 asf_search sentineleof sardem \
    && mamba clean -afy

# (2) miaplpy/mintpy — 위상 연결
RUN mamba create -y -n miaplpy -c conda-forge python=3.10 mintpy \
    && ( conda run -n miaplpy pip install "git+https://github.com/insarlab/MiaplPy.git" || true ) \
    && mamba clean -afy

# (3) sarvey — MTI 시계열 (+ inframon 코어 인제스트용)
RUN mamba create -y -n sarvey -c conda-forge python=3.10 numpy h5py \
    && conda run -n sarvey pip install sarvey \
    && mamba clean -afy

# ISCE2 topsStack 경로(스크립트가 참조하는 ISCE_STACK)
ENV ISCE_STACK=/opt/conda/envs/isce2/share/isce2/topsStack

# inframon 코어(50 변환/인제스트) — 소스는 실행 시 -v 로 마운트하거나 여기서 설치
RUN conda run -n sarvey pip install --no-deps inframon 2>/dev/null || \
    echo "inframon 은 실행 시 -v 마운트한 소스에서 'pip install -e /work' 로 설치하세요."

CMD ["bash", "-lc", "echo 'inframon-insar: conda activate {isce2|miaplpy|sarvey}'; bash"]
