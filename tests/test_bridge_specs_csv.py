"""교량 제원 CSV — 파일에 있는 실측을 추정보다 먼저 쓴다.

전국교량표준데이터에는 **경간수·최대경간이 없어** 최대경간을 연장×비율로 추정했고,
광안대교에서 5,565m(실측 500m)가 나왔다. 그 두 값을 담은 CSV 가 있으면 추정할 이유가 없다.
"""

from __future__ import annotations

from inframon.bridge_specs_csv import find_spec_csvs, load_specs, lookup, nearest_spec


def _write(path, rows, cols):
    import csv
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def test_joins_coordinate_file_and_spec_file_by_key(tmp_path):
    """좌표 파일과 제원 파일이 키로 나뉘어 있어도 합쳐 읽는다(흔한 배포 형태)."""
    load = _write(tmp_path / "load.csv",
                  [{"seq_no": "18720", "name": "정자교", "lon": "127.1089", "lat": "37.3685",
                    "structure": "girder", "material": "steel"}],
                  ["seq_no", "name", "lon", "lat", "structure", "material"])
    specs = _write(tmp_path / "specs.csv",
                   [{"seq_no": "18720", "n_spans": "5", "length_m": "108",
                     "max_span_m": "27", "lane_count": "6",
                     "kotsa_format": "PC슬래브교(PCS)"}],
                   ["seq_no", "n_spans", "length_m", "max_span_m", "lane_count",
                    "kotsa_format"])
    got = nearest_spec(load_specs(load, specs), 37.3685, 127.1089, name="정자교")
    assert got is not None
    assert got.n_spans == 5 and got.max_span_m == 27.0 and got.length_m == 108.0
    assert got.lanes == 6
    # 형식은 더 구체적인 후보(kotsa_format)가 이긴다 — 'girder' 로 뭉뚱그리지 않는다
    assert got.structure_raw == "PC슬래브교(PCS)"
    assert "load.csv" in got.source_file and "specs.csv" in got.source_file


def test_measured_returns_only_present_values(tmp_path):
    """없는 값은 채우지 않는다 — 지어낸 값보다 '없음'이 낫다."""
    p = _write(tmp_path / "x.csv",
               [{"name": "가", "lat": "37.0", "lon": "127.0", "length_m": "50"}],
               ["name", "lat", "lon", "length_m"])
    s = nearest_spec(load_specs(p), 37.0, 127.0)
    assert s.measured() == {"length_m": 50.0}
    assert s.n_spans is None and s.max_span_m is None


def test_name_match_wins_over_distance(tmp_path):
    """등록 좌표가 멀어도 이름이 맞으면 그 기록이다(표준데이터 좌표는 교량시작점)."""
    p = _write(tmp_path / "x.csv",
               [{"name": "청양교", "lat": "36.4506", "lon": "126.8068", "n_spans": "2"},
                {"name": "다른교", "lat": "36.4547", "lon": "126.8013", "n_spans": "9"}],
               ["name", "lat", "lon", "n_spans"])
    s = nearest_spec(load_specs(p), 36.4547, 126.8013, name="청양교")
    assert s.name == "청양교" and s.n_spans == 2


def test_finds_spec_csv_by_columns_not_filename(tmp_path):
    """파일 이름 규칙이 아니라 **컬럼**으로 제원 CSV 를 알아본다."""
    _write(tmp_path / "anything.csv",
           [{"name": "가", "lat": "37.0", "lon": "127.0", "max_span_m": "30"}],
           ["name", "lat", "lon", "max_span_m"])
    _write(tmp_path / "unrelated.csv", [{"a": "1"}], ["a"])
    found = [p.name for p in find_spec_csvs(tmp_path)]
    assert found == ["anything.csv"]


def test_lookup_returns_none_when_no_csv(tmp_path):
    assert lookup(37.0, 127.0, root=tmp_path) is None
