"""
Tests for the energy-accounting feature.

Pure-function checks run everywhere. The infra checks (Mimir / a real
deployment) are skipped unless MIMIR_USER/MIMIR_PASSWORD and PAPI_TESTS_ENERGY_VO
are set, so the suite stays runnable without the monitoring stack.
"""

import datetime

from ai4papi import mimir
from ai4papi.accounting import compute


def _series(values, scale_str=str):
    """Build a Mimir matrix result with one series from [(ts, val)]."""
    return [{"metric": {}, "values": [[t, scale_str(v)] for t, v in values]}]


# --- compute.integrate --------------------------------------------------------

# 1 W constant for 1h at 30s step -> 1 Wh at TUE 1.0, 2.03 Wh at TUE 2.03
step = 30
n = 3600 // step + 1
cpu = _series([(1_000_000 + i * step, 1_000_000) for i in range(n)])  # 1e6 uW = 1 W
buckets = compute.integrate(cpu, [], step_s=step, bucket_s=3600, tue_factor=1.0)
total = sum(b.energy_wh for b in buckets)
assert abs(total - 1.0) < 1e-3, total

buckets_tue = compute.integrate(cpu, [], step_s=step, bucket_s=3600, tue_factor=2.03)
assert abs(sum(b.energy_wh for b in buckets_tue) - 2.03) < 1e-2

# gap longer than gap_factor*step is not integrated across
gapped = _series([(0, 1_000_000), (30, 1_000_000), (600, 1_000_000), (630, 1_000_000)])
gb = compute.integrate(gapped, [], step_s=step, bucket_s=3600, tue_factor=1.0)
# only the two 30s intervals contribute: 2 * (1W * 30s) = 60 Ws = 60/3600 Wh
assert abs(sum(b.energy_wh for b in gb) - 60 / 3600) < 1e-4

# a single sample -> no energy
assert compute.integrate(_series([(0, 1_000_000)]), [], 30, 3600) == []

# --- compute.sanitize (data quality) ---------------------------------------

dq = {"enabled": True, "min_power_w": 0, "max_power_w": 1000, "spike_factor": 5}
pw = [(0, 100.0), (30, -5.0), (60, 100.0), (90, 99999.0), (120, 100.0)]
clean = compute.sanitize(pw, dq)
assert (30, -5.0) not in clean  # negative dropped
assert (90, 99999.0) not in clean  # over ceiling dropped
assert len(clean) == 3

# spike detection against the rolling median
spiky = [(i * 30, 100.0) for i in range(20)]
spiky[10] = (300, 5000.0)
clean = compute.sanitize(spiky, {"spike_factor": 10, "spike_window": 7})
assert (300, 5000.0) not in clean and len(clean) == 19

# disabled -> passthrough
assert compute.sanitize(pw, {"enabled": False}) == pw
assert compute.sanitize(pw, None) == pw

# integrate drops the anomaly so it never inflates the accumulated energy
anom = _series(
    [(t, 1_000_000) for t in range(0, 300, 30)]
    + [(300, 900_000_000)]
    + [(t, 1_000_000) for t in range(330, 600, 30)]
)
raw = sum(
    b.energy_wh
    for b in compute.integrate(anom, [], step_s=30, bucket_s=3600, tue_factor=1.0)
)
clean = sum(
    b.energy_wh
    for b in compute.integrate(
        anom,
        [],
        step_s=30,
        bucket_s=3600,
        tue_factor=1.0,
        data_quality={"max_power_w": 100},
    )
)
assert raw > 5  # the 900 W spike dominates the ~1 W baseline
assert clean < 0.3  # spike dropped -> only the ~1 W baseline remains

# CPU + GPU add up
cpu2 = _series([(0, 2_000_000), (30, 2_000_000)])  # 2 W
gpu2 = _series([(0, 8), (30, 8)])  # 8 W
mb = compute.integrate(cpu2, gpu2, step_s=30, bucket_s=3600, tue_factor=1.0)
assert abs(sum(b.energy_wh for b in mb) - 10 * 30 / 3600) < 1e-4

# per-source data quality: a spike in one series does not discard the others
two_gpu = [
    {"metric": {"UUID": "g0"}, "values": [[0, "10"], [30, "10"], [60, "10"]]},
    {"metric": {"UUID": "g1"}, "values": [[0, "10"], [30, "9000"], [60, "10"]]},
]
mb = compute.integrate(
    [],
    two_gpu,
    step_s=30,
    bucket_s=3600,
    tue_factor=1.0,
    data_quality={"max_power_w": 100},
)
got = sum(b.energy_wh for b in mb)
# g0: 3 samples of 10 W over 60 s = 10*60/3600. g1: middle sample dropped, gap
# 0->60 s at 10 W = 10*60/3600. Total ~ 2 * 10 * 60/3600.
assert abs(got - 2 * 10 * 60 / 3600) < 1e-4, got

# --- compute.apply_footprint -------------------------------------------------

b = compute.Bucket("2026-01-01T00:00:00Z", 2000.0, 40.0, 0.0, 0.0)  # 2 kWh
out = compute.apply_footprint([b])  # no series -> DEFAULTS
assert abs(out[0].carbon_g - 2.0 * compute.DEFAULT_CARBON) < 1e-6
assert abs(out[0].water_l - 2.0 * compute.DEFAULT_WATER) < 1e-6

series = [["2026-01-01T00:00:00Z", 100.0], ["2026-01-01T00:15:00Z", 200.0]]
out = compute.apply_footprint([b], carbon_series=series, water_series=series)
assert abs(out[0].carbon_g - 2.0 * 100.0) < 1e-6

# bucket before the series -> DEFAULTS
early = compute.Bucket("2025-12-31T00:00:00Z", 1000.0, 20.0, 0.0, 0.0)
out = compute.apply_footprint([early], carbon_series=series)
assert abs(out[0].carbon_g - 1.0 * compute.DEFAULT_CARBON) < 1e-6

# --- compute.intensity_at ---------------------------------------------------

ts_arr = [10.0, 20.0, 30.0]
val_arr = [1.0, 2.0, 3.0]
assert compute.intensity_at(ts_arr, val_arr, 5.0, -1) == -1  # before first
assert compute.intensity_at(ts_arr, val_arr, 20.0, -1) == 2.0  # exact
assert compute.intensity_at(ts_arr, val_arr, 25.0, -1) == 2.0  # between
assert compute.intensity_at(ts_arr, val_arr, 999.0, -1) == 3.0  # after last
assert compute.intensity_at([], [], 1.0, 7) == 7  # empty

# --- compute.downsample ---------------------------------------------------

many = [compute.Bucket(compute._iso(i * 300), 1.0, 10.0, 0.1, 0.01) for i in range(100)]
ds = compute.downsample(many, 10)
assert len(ds) == 10
assert abs(sum(b.energy_wh for b in ds) - sum(b.energy_wh for b in many)) < 1e-6
assert compute.downsample(many, 500) is many  # nothing to do

# --- compute.merge_buckets ------------------------------------------------

b1 = compute.Bucket("t0", 2.0, 20.0, 0.5, 0.02)
b2 = compute.Bucket("t0", 3.0, 30.0, 0.7, 0.03)
merged = compute.merge_buckets([[b1], [b2]])
assert len(merged) == 1
assert merged[0].energy_wh == 5.0 and merged[0].power_w_avg == 50.0

# --- compute.running_seconds --------------------------------------------

W = compute.AllocWindow
assert compute.running_seconds([W("a", 0, 100)], 0, 200) == 100
assert compute.running_seconds([W("a", 0, 100), W("b", 50, 150)], 0, 200) == 150
assert compute.running_seconds([W("a", 100, None)], 0, 200) == 100  # still running


print("🟢 energy: pure-function checks passed!")


# --- live Mimir check (optional, before any monkeypatching) ------------------

import os  # noqa: E402

if os.environ.get("PAPI_TESTS_MIMIR") and mimir.MIMIR_USER:
    _e = datetime.datetime.now(datetime.timezone.utc)
    _s = _e - datetime.timedelta(hours=1)
    assert isinstance(mimir.query_range("up", _s, _e, step_s=60), list)
    print("🟢 energy: live Mimir query_range reachable!")
else:
    print("🟡 energy: skipping live Mimir check (set PAPI_TESTS_MIMIR + MIMIR_* creds)")


# --- sweep pipeline (fake Mimir + Nomad, no infra) ---------------------------

import json  # noqa: E402
import tempfile  # noqa: E402

os.environ["ACCOUNTING_PTH"] = tempfile.mkdtemp()
# make the feature "enabled" for the read API even without real Mimir creds
mimir.MIMIR_USER = mimir.MIMIR_USER or "test"
mimir.MIMIR_PASSWORD = mimir.MIMIR_PASSWORD or "test"

from ai4papi import conf as papiconf  # noqa: E402
from ai4papi.accounting import store, sweep  # noqa: E402
import ai4papi.accounting as accounting  # noqa: E402

_NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
_NS, _UUID, _DC = "ai4eosc", "sweep-test-uuid", "ifca-ai4eosc"
# fixed, test-owned TUE: independent of whatever var/datacenters.csv says for
# `_DC` in production, so editing that file never breaks this test.
_TUE = 2.5
papiconf.datacenters[_DC] = {**papiconf.datacenters[_DC], "TUE": _TUE}


def _fake_query_range(promql, start, end, step_s=30):
    # 100 W constant (100e6 uW), one sample per step
    t, out = start.timestamp(), []
    while t <= end.timestamp():
        out.append([t, "100000000" if "scaph" in promql else "0"])
        t += step_s
    return [{"metric": {}, "values": out}]


mimir.query_range = _fake_query_range
mimir.query_range_chunked = _fake_query_range
compute.resolve_alloc_ids = lambda job_id, ns: (
    {_DC: ["alloc-1"]},
    [compute.AllocWindow("alloc-1", _NOW.timestamp() - 3 * 3600, None)],
    [{"alloc_id": "alloc-1", "datacenter": _DC}],
    False,
)
sweep.green_director.metrics = {_DC: {"carbon": [], "water": []}}

_info = {
    "uuid": _UUID,
    "job_name": f"module-{_UUID}",
    "namespace": _NS,
    "owner": "user@egi.eu",
    "submit_time": _NOW.timestamp() - 3 * 3600,
    "status": "running",
}

sweep.process_single(_info, _NOW)
doc = store.read_accum(_NS, _UUID)
acc = doc["accumulated"]
assert doc["metrics_available"] is True
assert abs(doc["tue_factor"] - _TUE) < 1e-9
# ~3h of 100 W -> ~300 Wh raw, x _TUE (11 or 12 complete 15-min buckets
# depending on where "now" falls in the current bucket)
assert 270 * _TUE < acc["energy_wh"] < 305 * _TUE, acc
assert abs(acc["carbon_g"] - acc["energy_wh"] / 1000 * compute.DEFAULT_CARBON) < 1.0

# idempotent: same "now" -> byte-identical document
before = json.dumps(doc, sort_keys=True)
accounting._read_doc.cache_clear()
sweep.process_single(_info, _NOW)
assert json.dumps(store.read_accum(_NS, _UUID), sort_keys=True) == before

# monotonic on a later sweep
accounting._read_doc.cache_clear()
sweep.process_single(_info, _NOW + datetime.timedelta(hours=1))
assert store.read_accum(_NS, _UUID)["accumulated"]["energy_wh"] >= acc["energy_wh"]

# buckets age out of the 24h tail into the persisted series
accounting._read_doc.cache_clear()
sweep.process_single(_info, _NOW + datetime.timedelta(hours=30))
assert len(store.read_series(_NS, _UUID, None, None)) > 0

# read API + pydantic validation
from ai4papi import schemas  # noqa: E402

accounting._read_doc.cache_clear()
stats = accounting.get_accumulated(_NS, _UUID, live=False)
schemas.EnergyStats(**stats)
series = accounting.get_series(_NS, _UUID)
schemas.EnergyTimeSeries(**series)
assert series["series"] and all(p["energy_wh"] is not None for p in series["series"])
# per-bucket increments only; running total is cumsum(series[].energy_wh)
_pt = set(series["series"][-1])
assert _pt == {
    "ts",
    "power_w",
    "energy_wh",
    "carbon_g",
    "water_l",
    "live",
}, _pt
_ps = accounting.get_accumulated(_NS, _UUID, live=True)
assert _ps["power_w"] and abs(_ps["tue_factor"] - _TUE) < 1e-9

# per-user aggregate: sums the owner's deployments, EnergyStats-shaped
accounting._read_doc.cache_clear()
_ue = accounting.get_user_energy(_NS, "user@egi.eu", series=False)
schemas.EnergyStats(**_ue)
assert _ue["deployments"] >= 1 and _ue["series"] is None
assert _ue["energy_wh"] >= acc["energy_wh"] and _ue["since"]
_ues = accounting.get_user_energy(_NS, "user@egi.eu", series=True)
schemas.EnergyStats(**_ues)
assert _ues["series"] and all(p["energy_wh"] is not None for p in _ues["series"])
# the aggregate total must reconcile with the sum of the series (no rounding)
assert abs(_ues["energy_wh"] - sum(p["energy_wh"] for p in _ues["series"])) < 1e-9, _ues
assert abs(_ues["carbon_g"] - sum(p["carbon_g"] for p in _ues["series"])) < 1e-9
# merge-then-downsample: uniform step across the whole persisted series
_uts = [
    datetime.datetime.fromisoformat(p["ts"].replace("Z", "+00:00"))
    for p in _ues["series"]
    if not p["live"]
]
_steps = {round((_uts[i + 1] - _uts[i]).total_seconds()) for i in range(len(_uts) - 1)}
assert len(_steps) == 1, _steps
# hard cap on the persisted point count, same as the other levels
_capped = accounting.get_user_energy(_NS, "user@egi.eu", series=True, target_points=5)
assert len([p for p in _capped["series"] if not p["live"]]) <= 5
assert accounting.get_user_energy(_NS, "nobody@egi.eu") is None

# live tail: a running deployment's graph keeps growing past the stored series
_live_doc = {
    "namespace": _NS,
    "deployment_uuid": _UUID,
    "metrics_available": True,
    "pointer_ts": compute._iso(
        datetime.datetime.now(datetime.timezone.utc).timestamp() - 600
    ),
    "active_allocs": [{"alloc_id": "alloc-1", "datacenter": _DC}],
}
_lp = sweep.live_series(_live_doc, bucket_s=60)
assert _lp and all(p["live"] for p in _lp), _lp
assert all("energy_wh" in p and "carbon_g" in p for p in _lp)

# the live top-up also carries the current power (100 W raw, x TUE)
_tu = sweep.topup(_live_doc)
_tue = sweep._tue(_DC)
assert _tu["power_w"] is not None
assert abs(_tu["power_w"] - 100.0 * _tue) < 5.0 * _tue, _tu

# a running deployment's accumulated stats expose a power figure
accounting._read_doc.cache_clear()
_st = accounting.get_accumulated(_NS, _UUID, live=True)
assert _st["power_w"] is not None

# the accumulated stats always carry a float coverage_ratio (1.0 = full)
assert isinstance(_st["coverage_ratio"], float)

# GPU/CPU energy is clipped to the job's life: a finished allocation whose power
# metrics linger for a few minutes must not keep accumulating energy.
_ENDED_UUID = "sweep-ended-uuid"
_ended_end = _NOW.timestamp() - 2 * 3600  # allocation stopped 2h ago
compute.resolve_alloc_ids = lambda job_id, ns: (
    {_DC: ["alloc-e"]},
    [compute.AllocWindow("alloc-e", _NOW.timestamp() - 5 * 3600, _ended_end)],
    [],  # nothing running
    False,
)
_ended_info = {
    "uuid": _ENDED_UUID,
    "job_name": f"module-{_ENDED_UUID}",
    "namespace": _NS,
    "owner": "user@egi.eu",
    "submit_time": _NOW.timestamp() - 5 * 3600,
    "status": "running",
}
accounting._read_doc.cache_clear()
sweep.process_single(_ended_info, _NOW)
_edoc = store.read_accum(_NS, _ENDED_UUID)
_last_ts = max(
    (b["ts"] for b in _edoc.get("tail", [])),
    default=_edoc.get("settled_ts", ""),
)
# fake Mimir returns 100 W for the whole [settled, now] window, but no bucket
# should land after the allocation ended (+ one bucket of slack).
assert compute._parse_iso(_last_ts) <= _ended_end + 300, _last_ts

# a GPU-only monitored site (no scaphandre series at all) still accounts energy
# from the GPU power alone: a missing `scaph_*` metric comes back as an empty
# result, not an error, so the integrator just sums the GPU series.
_GPU_UUID = "sweep-gpu-only-uuid"


def _fake_qr_gpu_only(promql, start, end, step_s=30):
    if "scaph" in promql:
        return []
    t, out = start.timestamp(), []
    while t <= end.timestamp():
        out.append([t, "150"])  # 150 W per GPU
        t += step_s
    return [{"metric": {}, "values": out}]


mimir.query_range = _fake_qr_gpu_only
mimir.query_range_chunked = _fake_qr_gpu_only
compute.resolve_alloc_ids = lambda job_id, ns: (
    {_DC: ["alloc-g"]},
    [compute.AllocWindow("alloc-g", _NOW.timestamp() - 3 * 3600, None)],
    [{"alloc_id": "alloc-g", "datacenter": _DC}],
    False,
)
_gpu_info = {
    "uuid": _GPU_UUID,
    "job_name": f"module-{_GPU_UUID}",
    "namespace": _NS,
    "owner": "gpu@egi.eu",
    "submit_time": _NOW.timestamp() - 3 * 3600,
    "status": "running",
}
accounting._read_doc.cache_clear()
sweep.process_single(_gpu_info, _NOW)
_gdoc = store.read_accum(_NS, _GPU_UUID)
assert _gdoc["metrics_available"] is True, _gdoc
assert _gdoc["accumulated"]["energy_wh"] > 0, _gdoc

print("🟢 energy: sweep pipeline checks passed!")


# --- cluster energy: instantaneous power + accumulator + series --------------

_CNS = sweep.CLUSTER_NS
_ISO0 = "2020-01-01T00:00:00Z"
sweep.green_director.metrics = {
    _DC: {"carbon": [[_ISO0, 300.0]], "water": [[_ISO0, 10.0]]}
}
_tue_dc = sweep._tue(_DC)


def _fake_query(promql, at=None, timeout=30):
    if "scaph_host_power" in promql:
        # 500 W reported in microwatts
        return [
            {"metric": {"datacenter": _DC}, "value": [_NOW.timestamp(), "500000000"]}
        ]
    if "DCGM" in promql:
        return [
            {"metric": {"datacenter": _DC}, "value": [_NOW.timestamp(), "200"]}
        ]  # W
    return []


mimir.query = _fake_query

# 1. instantaneous power: power_w = (host + gpu) * TUE, normalized only
accounting.get_datacenter_power.cache_clear()
_cp = accounting.get_datacenter_power()
assert _DC in _cp, _cp
assert abs(_cp[_DC]["power_w"] - (500.0 + 200.0) * _tue_dc) < 1.0, _cp
assert set(_cp[_DC]) == {"power_w", "tue_factor", "as_of"}, _cp[_DC]


# 2. per-datacenter sweep: several raw host series -> sanitize each -> sum
def _fake_cluster_qr(promql, start, end, step_s=30):
    def _rows(pairs):
        out = []
        for label, val in pairs:
            vals, t = [], start.timestamp()
            while t <= end.timestamp():
                vals.append([t, val])
                t += step_s
            out.append({"metric": {"node": label, "UUID": label}, "values": vals})
        return out

    if "scaph_host_power" in promql:  # two hosts: 400 W + 600 W (in microwatts)
        return _rows([("nodeA", "400000000"), ("nodeB", "600000000")])
    if "DCGM" in promql:  # one GPU: 250 W
        return _rows([("gpu0", "250")])
    return []


mimir.query_range = _fake_cluster_qr
mimir.query_range_chunked = _fake_cluster_qr
sweep._live_cache.clear()

sweep.process_datacenter(_DC, _NOW)
cdoc = store.read_accum(_CNS, _DC)
assert cdoc["metrics_available"] is True
cacc = cdoc["accumulated"]
assert cdoc["tue_factor"] == _tue_dc
# window ~= initial_lookback_hours of (400 + 600 + 250) = 1250 W raw, x TUE
_expected = sweep._cfg()["initial_lookback_hours"] * 1250.0 * _tue_dc
assert 0.75 * _expected < cacc["energy_wh"] <= 1.05 * _expected, cacc
assert abs(cacc["carbon_g"] - cacc["energy_wh"] / 1000 * 300.0) < 1.0

# monotonic on a later sweep
sweep.process_datacenter(_DC, _NOW + datetime.timedelta(hours=1))
assert store.read_accum(_CNS, _DC)["accumulated"]["energy_wh"] >= cacc["energy_wh"]

# buckets age out of the 24h tail into the persisted series
sweep.process_datacenter(_DC, _NOW + datetime.timedelta(hours=30))
assert len(store.read_series(_CNS, _DC, None, None)) > 0

# 3. read API: get_cluster_energy merges power + accumulator + series
accounting.get_datacenter_power.cache_clear()
accounting._cluster_archive_series.cache_clear()
sweep._live_cache.clear()
_ce = accounting.get_cluster_energy(series=True)
assert _DC in _ce, _ce
_row = _ce[_DC]
assert _row["power_w"] is not None and _row["energy_wh"] is not None
assert _row["since"] and _row["as_of"]
assert _row["series"] and all(p["energy_wh"] is not None for p in _row["series"])
for _p in _row["series"]:
    schemas.EnergyTimeSeriesPoint(**_p)
schemas.EnergyStats(**_row)
assert _row["tue_factor"] == _tue_dc  # only conversion factor exposed
# the accumulated total reconciles with the sum of the series (no rounding)
assert abs(_row["energy_wh"] - sum(p["energy_wh"] for p in _row["series"])) < 1e-9, _row

# 4. datacenter live top-up carries the (TUE-normalized) energy deltas
sweep._live_cache.clear()
_ct = sweep.topup_cluster(_DC, cdoc)
assert _ct and _ct["energy_wh"] > 0 and _ct["live_as_of"]
assert abs(_ct["carbon_g"] - _ct["energy_wh"] / 1000 * 300.0) < 1.0, _ct


# 5. a GPU-only monitored datacenter (in metrics_datacenters, no host power) is
# still reported: for a declared site, GPU power alone is a valid total.
def _fake_query_gpu_only(promql, at=None, timeout=30):
    if "DCGM" in promql:
        return [{"metric": {"datacenter": _DC}, "value": [_NOW.timestamp(), "200"]}]
    return []


mimir.query = _fake_query_gpu_only
accounting.get_datacenter_power.cache_clear()
_gp = accounting.get_datacenter_power()
assert _DC in _gp, _gp
assert abs(_gp[_DC]["power_w"] - 200.0 * _tue_dc) < 1.0, _gp

# 6. a datacenter with no power series at all is absent from get_datacenter_power
mimir.query = lambda promql, at=None, timeout=30: []
mimir.query_range = lambda *a, **k: []
mimir.query_range_chunked = lambda *a, **k: []
accounting.get_datacenter_power.cache_clear()
assert accounting.get_datacenter_power() == {}

print("🟢 energy: cluster energy checks passed!")
