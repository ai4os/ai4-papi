# Per-deployment energy consumption and footprint

This feature exposes, for every PAPI deployment, its accumulated energy consumption and footprint (carbon and water, scope 2), plus time series for the dashboard graphs.

- Accumulated (indefinite retention): energy (Wh), carbon (gCO2eq), water (L).
- Time series of the same three variables, for the deployment detail view.

It is fully additive: nothing in the existing API changes behaviour and no new endpoints are added. Deployments get a new `energy` key, `/v1/deployments/stats/user` gains an `energy` block (the user's deployments summed), `/v1/deployments/stats/cluster` gains an `energy` block per datacenter (no platform-wide total, clients sum), and there is one extra background thread. If the feature is disabled or its inputs are missing, `energy` is `null` (or absent) and the rest of the API is identical.

## Inputs

| Input | Native resolution | Availability | Notes |
|---|---|---|---|
| CPU power: `scaph_process_power_consumption_microwatts` (uW) | 30 s | Mimir retention (tunable, will drop to ~3 months) | label `container_label_com_hashicorp_nomad_alloc_id`; aggregates every container of the alloc (task `main` + sidecars). The Nomad nodes are VMs, so scaphandre's per-process figures are small: real CPU energy only shows up under real load. |
| GPU power: `DCGM_FI_DEV_POWER_USAGE` (W) | 30 s | Mimir retention | board-level power, labels `UUID`, `instance` |
| GPU to alloc mapping: `nomad_gpu_allocation_info` | 30 s | only while a GPU deployment runs | info gauge, label `alloc_id`; kept as the mapping mechanism on purpose (other accounting software relies on it to avoid depending on the Nomad API) |
| Carbon and water intensity (Wattnet) | 15 min | full history on demand; `green_director` keeps a rolling 7-day window | already integrated in `ai4papi/wattnet.py`. Recent intervals are provisional and get revised every ~15 min until settled. `DEFAULTS` (301 gCO2/kWh, 12 L/kWh) outside coverage. |
| Allocations of a deployment | event | while the Nomad job is not garbage-collected (hours) | `Nomad.job.get_allocations`; several allocations over a job's life due to reallocations |

Power metrics currently exist only in the `ifca-*` datacenters, and not on every node. Availability is effectively per allocation: it is decided by whether Mimir has data for that `alloc_id`, not by the datacenter alone. The CPU (scaphandre) and GPU (DCGM) sources are independent: whichever one has data contributes, so a site that only monitors its GPUs still accounts the GPU energy (a missing metric comes back as an empty Mimir result, not an error). The feature is best-effort: if there is data it is reported; if there is none, `energy` is `null` (never zeros); if there is data for only part of the deployment's life, that part is accounted and `coverage_ratio` drops below `1.0`.

Mimir endpoint: `https://mimir.k8s.cloud.ai4eosc.eu/prometheus/api/v1/query_range`, HTTP Basic auth, no `X-Scope-OrgID` header.

## Normalization

`energy_wh = integrated_Wh * TUE[datacenter]`, where `integrated_Wh` is the raw trapezoidal integral of `CPU + GPU` power.

The TUE (Total Usage Effectiveness, grid to compute ratio) is per datacenter and already covers CPU plus GPU, so it is applied once, before the footprint (which is scope 2, so it must reflect the whole facility draw). It is a single empirical factor per datacenter (default 2.73), consistent with the platform's existing energy accounting. Every energy, power, carbon and water value in an API response is already TUE-normalized; the applied factor is reported as `tue_factor` (per datacenter, or blended `energy_wh / raw` on multi-datacenter and per-user aggregates) so a client that wants the raw meter figure divides by it. The accumulator stores `tue_factor` per document.

The TUE is read from the `TUE` column of `var/datacenters.csv`, only populated for the datacenters that have a value (currently `ifca-ai4eosc` and `ifca-imagine`). Blank cells load as `None` and `energy.default_tue_factor` is used as fallback.

## Architecture

Four layers: a durable background sweep that owns the persisted record, a live read-time top-up on top of it, an on-demand version of the sweep for a deployment the background thread has not reached yet, and a pure-Mimir fallback for one still too young to have anything settled at all.

```
   background thread in ai4papi.main: @repeat_every(energy.sweep_seconds)
     accounting.sweep.run_sweep()
       per deployment:   Nomad (jobs + allocations + node -> datacenter)
                         Mimir query_range (per-alloc power, un-consolidated window)
       per datacenter:   Mimir query_range (per-host + per-GPU power)
       green_director.metrics  (Wattnet 15-min intensity)
       integrate 30 s samples -> 15-min buckets, x TUE, x intensity
                    |
                    v  atomic write, one JSON per deployment / per datacenter
   $ACCOUNTING_PTH/energy/<namespace>/<uuid>.accum.json    meta + accumulator + 24h tail
   $ACCOUNTING_PTH/energy/<namespace>/<uuid>.series.jsonl   consolidated 15-min buckets, append-only
   $ACCOUNTING_PTH/energy/_cluster/<datacenter>.accum.json  per-datacenter accumulator + 24h tail
   $ACCOUNTING_PTH/energy/_cluster/<datacenter>.series.jsonl
                    |
                    v  cached read
   PAPI API (stateless)
     energy block = stored accumulator + live top-up (Mimir, short window, cache 25 s)
     GET /v1/deployments/{modules,tools,batch,try_me/nomad}         -> + energy (EnergyStats, list view)
     GET /v1/deployments/{modules,tools,batch,try_me/nomad}/{uuid}  -> + energy (EnergyTimeSeries, detail view)
     GET /v1/deployments/stats/user                                 -> + energy (per-user total)
     GET /v1/deployments/stats/cluster                              -> + energy (per-datacenter: power + accumulated + series)
```

The shape of the deployment `energy` block follows the existing `full_info` query param: `full_info=false` (the list default) attaches the accumulated `EnergyStats`; `full_info=true` (the individual-endpoint default) attaches the extended `EnergyTimeSeries`.

The diagram above is the steady state, once a deployment has a persisted doc. Before that, the read path itself does the work the diagram shows the background thread doing (Layer 3), and short of that, falls back to querying Mimir directly with nothing persisted at all (Layer 4) -- see both below.

### Layer 1: durable sweep

`accounting.sweep.run_sweep()` runs on a `@repeat_every(energy.sweep_seconds)` thread started in `ai4papi.main`'s `lifespan`, next to `get_cluster_stats_thread`. Same posture: not async, one `try/except` per deployment, no-op if the feature is disabled.

Per namespace it lists the active Nomad jobs (`Status != "dead"`, names matching `^(module|tool|batch|try)`) and also every existing `.accum.json` whose job has vanished, to close it out. Per deployment it calls `process_single(info, now)`:

1. Load `.accum.json`. The integration pointer `settled_ts` comes from the doc; on first encounter it is clamped to `now - initial_lookback_hours` (pre-existing deployments are not fully backfilled) and to `now - mimir_retention_days` (older than that, `complete` is `false`). `window_start` (exposed as `since`) is pinned to that first-encounter value and never moves afterwards unless it falls behind `now - mimir_retention_days`; unlike `settled_ts` it is not a moving pointer, so it always marks the true start of the span the totals cover. A doc written before this field existed has it backfilled once, from the oldest bucket on record (`.series.jsonl`, else the oldest still in `tail`).
2. Resolve allocations via `compute.resolve_alloc_ids` into `{datacenter: [alloc_id]}` (whole lifetime), the currently-running allocs (for the top-up), the alloc time windows (to estimate `run_seconds`, which spans the whole life including time on datacenters without metrics), and `partial_dc` (an alloc ran in a datacenter without metrics).
3. Per datacenter, query Mimir for `[settled_ts, now - query_lag_seconds]` (chunked only if the span is huge), `sanitize` the samples, `integrate` trapezoidally into 15-min buckets applying that datacenter's TUE, then `apply_footprint` with the Wattnet intensity of that datacenter. Merge the per-datacenter buckets by timestamp.
4. Best-effort coverage: if the deployment was clearly running (`run_seconds` significant) but Mimir returned nothing, mark `metrics_available: false` and retry next sweep. Otherwise `metrics_available: true` and `coverage_ratio = covered_time / run_seconds` (`1.0` = full; lower means part of the life ran unmonitored).
5. Merge the new buckets over the existing 24h tail. Buckets older than `now - footprint_revision_hours` are consolidated: their totals are added to the running `settled` accumulator, `settled_ts` advances past them, and they are appended to `.series.jsonl`. The rest stay in `tail` and are recomputed in full every sweep, so the footprint self-heals as Wattnet settles. `settled_ts` therefore trails `now` by `footprint_revision_hours` in steady state and must not be read as the window start; that is `window_start` / `since`.
6. `accumulated = settled + sum(tail)`. `pointer_ts` is the end of the last tail bucket. Write the doc atomically.

The sweep is idempotent: re-running with the same `now` produces the same document (buckets keyed by timestamp, `settled` only advances over buckets that leave the 24h window).

The consolidation step (fold new buckets over the tail, age the old ones into `settled` + `.series.jsonl`, recompute `accumulated` / `pointer_ts`) is `sweep._fold_and_accumulate`, shared with the datacenter sweep below.

### Layer 1b: per-datacenter sweep

After the deployment loop, `run_sweep()` calls `_sweep_cluster(now)`, which runs `process_datacenter(dc, now)` for every `energy.metrics_datacenters`. Documents live under the pseudo-namespace `_cluster` (`$ACCOUNTING_PTH/energy/_cluster/<dc>.accum.json` + `.series.jsonl`), reusing `accounting.store` unchanged.

`process_datacenter` is `process_single` without the Nomad parts: no allocation resolution, no `coverage_ratio`, no owner/close handling.

1. Load the doc; `settled_ts` from it, else clamped to `initial_lookback_hours` / `mimir_retention_days` (retention clamp sets `complete: false`). `window_start` / `since` is pinned on the first sweep, same as the per-deployment path.
2. Query Mimir over `[settled_ts, now - query_lag_seconds]` with `_cluster_promql(dc)`: `sum by (node) (scaph_host_power_microwatts{datacenter="<dc>"})` (one series per host) and `sum by (UUID) (DCGM_FI_DEV_POWER_USAGE{datacenter="<dc>"})` (one series per GPU). Grouped, not summed, so `compute.integrate` runs the data-quality filter on each raw host / GPU series before summing, exactly as the per-deployment path does per allocation.
3. Data quality is `_cluster_dq()`: `energy.data_quality` with `max_power_w` forced to `0` (the per-alloc / per-GPU ceiling of ~1000 W is meaningless for a whole host). Negative-drop and the scale-free rolling-median spike check still apply.
4. `integrate` with `TUE[dc]`, `apply_footprint` with the datacenter's Wattnet intensity, then `_fold_and_accumulate`. If the datacenter has no host-power series at all: `metrics_available: false`, retry next sweep, no zeros.

Note on double counting: scaphandre host power is RAPL (CPU package + DRAM), it does not include the GPU board, so `host + GPU` is correct.

### Layer 2: read-time top-up

The stored accumulator only reaches `pointer_ts` (up to one sweep interval behind). On each API read, `accounting.sweep.topup(doc)` queries Mimir live for `[pointer_ts, now]` (a single `query_range`, no chunking, no ingestion-lag cushion -- Mimir data is immutable once ingested, so querying right up to "now" cannot return a wrong value, only occasionally miss a sample scraped a moment ago that is not visible *yet*, which self-corrects on the very next call), applies the TUE and the current Wattnet intensity, and returns the energy deltas plus `live_as_of` and `power_w`. `power_w` is the **current draw**: the TUE-normalized sum of the actual last sample of every series in that same query, however old that sample is, not a window mean. The result is cached for `topup_cache_seconds` (25 s, below the dashboard's 30 s poll, so the number visibly moves). Any failure returns `{"degraded": true}` and the API serves the stored value.

`topup_cluster(dc, doc)` / `live_series_cluster(dc, doc, bucket_s)` are the datacenter equivalents (same window logic, `_cluster_promql` instead of the per-alloc query). They are computed inside `get_cluster_stats_bg()` (which runs every 30 s anyway), so the datacenter counter moves at the same cadence as the per-deployment one.

`accumulated (stored, frozen for up to a sweep) + top-up (growing)` stays continuous: when the next sweep advances `pointer_ts` and folds that slice into `accumulated`, the top-up window shrinks by the same amount.

### Layer 3: on-demand sweep

A deployment the background thread has not reached yet has no `.accum.json`, so the top-up (which reads `pointer_ts` off the stored doc) has nothing to attach to. Rather than making a fresh deployment wait up to `sweep_seconds` for its first numbers, every read path runs one `process_single` pass synchronously (the same function and same write path the background thread uses) for whatever it is about to serve and has no doc yet:

- Single-deployment reads (`get_accumulated`, `get_series`) call `accounting.sweep.ensure_swept(namespace, uuid)`: fetch that one Nomad job, sweep it, return the resulting doc.
- Bulk/per-user reads (`get_accumulated_bulk`, `get_user_energy`) call `accounting.sweep.ensure_swept_bulk(namespace, owner)` first: list the owner's active jobs (one cheap Nomad call, no per-job fetch) and sweep only the ones still missing a doc, before falling back to the normal `store.iter_owner` scan.

Once a doc exists, both are a single cheap `store.read_accum` check (or, for the bulk path, one job-list call) and defer entirely to the background sweep; neither re-queries Mimir beyond that first pass. A job Nomad no longer knows about (or a listing failure) degrades to `energy: null` / that deployment simply absent from the aggregate. `/stats/cluster` (`get_cluster_energy`, per-datacenter) is not covered by this: its instantaneous power is already live regardless (`get_datacenter_power`), and its accumulator is seeded by the small, fixed `metrics_datacenters` list, not by individual deployments.

`process_single` also persists a bare stub (no `accumulated` key, no `metrics_available` verdict either way) the first time it runs for a job with no allocation resolved at all yet (eg. queued, or Nomad has not placed the allocation on a node). That stub is not a negative result, just "too early to tell": the read path treats "doc is `None`" and "doc exists but has no `accumulated` and `metrics_available` is not explicitly `False`" the same way, both falling through to Layer 4. Only an explicit `metrics_available: False` (a confirmed gap, set after the deployment has run long enough that the absence of data is meaningful) stops the fallback and returns `null` outright, deferring to the next real sweep.

### Layer 4: pure-Mimir fallback

`process_single` leaves a deployment with **no** doc when it is younger than one `series_bucket_seconds`: there is nothing settled to fold into an accumulator yet, so Layer 3 alone still reports `energy: null` for the first few minutes of a deployment's life. `sweep.live_only_doc(ns, uuid)` closes that gap: a transient, **never persisted** stand-in doc (`accumulated` all zero, `pointer_ts = submit_time`, the currently-running allocations) shaped so the normal `topup` / `live_series` machinery treats it exactly like a real doc -- the whole reported number then comes straight from Mimir, through the same lag-free `_live_window` as Layer 2. `accounting._live_only_stats()` wraps it and only returns something when the top-up actually found data (`power_w` set, or a non-zero energy/carbon/water delta) -- an all-zero top-up is indistinguishable from "no measurement yet", so it is treated as not populated (`null`) rather than shown as a fabricated `0` reading. Used by all four read functions whenever they still have no usable doc after Layer 3.

## Persistence

One small JSON per deployment under `$ACCOUNTING_PTH/energy/<namespace>/`, atomic writes (temp file + `os.replace`). `process_single` / `process_datacenter` are the only writers, run either by the periodic sweep thread or, for a single deployment's very first doc, synchronously from the read path (see [Layer 3](#layer-3-on-demand-sweep)). Same convention as `ai4papi.utils.retrieve_from_snapshots`. No database. The `accounting.store` interface is deliberately small so it can move to PostgreSQL later without touching the routers.

`<uuid>.accum.json` (rewritten every sweep, ~25 KB with a full 24h tail):

```json
{
  "deployment_uuid": "...", "job_name": "module-...", "namespace": "vo-...",
  "owner": "...@egi.eu", "submit_time": "2026-01-05T10:00:00Z",
  "submit_time_epoch": 1736071200.0,
  "status": "running", "closed_at": null,
  "metrics_available": true, "complete": true, "coverage_ratio": 1.0,
  "datacenters": ["ifca-imagine"],
  "active_allocs": [{"alloc_id": "...", "datacenter": "ifca-imagine"}],
  "window_start": "2026-09-02T12:00:00Z",
  "settled_ts": "2026-09-02T12:00:00Z", "pointer_ts": "2026-09-03T12:45:00Z",
  "updated_at": "2026-09-03T12:50:00Z", "tue_factor": 2.73,
  "settled":     {"energy_wh": 41200.0, "carbon_g": 12100.0, "water_l": 480.0},
  "tail":        [{"ts": "2026-09-02T12:00:00Z", "energy_wh": 4.1, "power_w_avg": 24.3, "carbon_g": 1.2, "water_l": 0.05}],
  "accumulated": {"energy_wh": 42873.4, "carbon_g": 12550.2, "water_l": 498.1}
}
```

`<uuid>.series.jsonl`: one line per consolidated bucket (`energy.series_bucket_seconds`, 15 min), append-only (a bucket is written once, when it leaves the 24h tail, never rewritten). About 35k lines (~3 MB) per year for an always-on deployment, a few KB appended per sweep.

There is no summary file. Per-user and per-datacenter aggregates are computed on read from the `.accum.json` documents (`get_user_energy`, `get_cluster_energy`), because they need the live top-up anyway. If the per-user read (a namespace scan filtered by owner) ever gets heavy, cache a namespace snapshot rather than reviving a stale summary.

## Resolution strategy

| Layer | Resolution | Why |
|---|---|---|
| Integration for the accumulator | 30 s (native) | the accumulator is as precise as the source, independent of bucket size |
| Persisted archive series | 15 min (`energy.series_bucket_seconds`) | matches Wattnet's 15-min cadence, ~3 MB/year for a long deployment, append-only file |
| Recent-window graph | 30 s from Mimir, downsampled to ~1000 points | full resolution and always current while the data is in Mimir |
| Old-window graph (past Mimir retention) | 15 min from the store | the only source left, enough to see the shape |
| Live counter tail (`pointer_ts -> now`) | 30 s | the number moves at scrape cadence |

The store is the permanent low-resolution archive; Mimir is the high-resolution source for anything recent.

What the `series` in an API response actually is: the persisted 15-min archive (plus the still-mutable tail) `compute.downsample`d to a `target_points` cap, then the live 15-min tail appended un-downsampled. Caps: deployment detail `energy.deployment_series_points` (1000), datacenter `energy.cluster_series_points` (500), per-user `energy.user_series_points` (1000, merged across the user's deployments first, then one downsample). Each function falls back to its hardcoded default when the key is absent from the config. Below the cap you get the raw 15-min buckets; above it, `downsample` groups `ceil(n / cap)` consecutive buckets, so the effective step is `ceil(n / cap) * 15 min`, uniform across the series. `get_series(namespace, uuid)` takes no time-range or resolution arguments (the routers always call it with the two positional args): the response is always the whole life.

## Computation details

Module `ai4papi/accounting/compute.py`, all pure and unit-testable except the cached Nomad lookup:

- `resolve_alloc_ids(job_id, namespace)`: `Nomad.job.get_allocations` plus `_node_datacenter` (TTLCache), filtered to `energy.metrics_datacenters`.
- `power_promql(alloc_ids)`: the CPU and GPU queries for one datacenter's alloc subset. Grouped, not fully summed, so the data-quality filter runs on the original per-source series.
  - CPU: `sum by (container_label_com_hashicorp_nomad_alloc_id) (scaph_process_power_consumption_microwatts{container_label_com_hashicorp_nomad_alloc_id=~"id1|id2"})` (one series per allocation)
  - GPU: `DCGM_FI_DEV_POWER_USAGE * on(UUID, instance) group_left(alloc_id) nomad_gpu_allocation_info{alloc_id=~"id1|id2"}` (one series per GPU)
- `sanitize(samples, dq)`: data-quality filter on one power series (see below).
- `integrate(cpu_result, gpu_result, step_s, bucket_s, tue_factor, gap_factor, data_quality)`: CPU uW/1e6 plus GPU W, `sanitize` per source series, trapezoidal integral of each into `bucket_s` energy buckets, summed, then `energy_wh = raw_integral * tue_factor`. Gaps longer than `gap_factor * step_s` are not integrated across (deployment stopped, scrape hole, dropped anomaly).
- `apply_footprint(buckets, carbon_series, water_series)`: `carbon_g = (energy_wh / 1000) * intensity_at(ts)`, same for water. `intensity_at` is a bisect for the last Wattnet sample at or before the bucket, `DEFAULTS` otherwise.
- `merge_buckets`, `downsample`, `running_seconds`.

`Bucket` is a namedtuple `(ts, energy_wh, power_w_avg, carbon_g, water_l)` with `ts` an ISO-8601 UTC string and every value already TUE-normalized.

### Footprint consolidation

The sweep uses `green_director.metrics[dc]` (Wattnet, already refreshed every 30 s for the cluster stats) with `DEFAULTS` as fallback. For a backfill older than the 7-day window it calls `green_director.footprint_series(dc, start, end)`, which reuses the Wattnet token and hits `/v1/footprints` for the requested range.

Every sweep recomputes carbon and water for all buckets newer than `now - footprint_revision_hours` (24 h) with the current intensity, so a bucket's footprint firms up as Wattnet settles it. Older buckets are frozen. The accumulated footprint is always `sum` over buckets, so it self-corrects.

The live top-up multiplies its `delta_wh` by the current provisional intensity; the next sweep reconciles it (typical correction under 1-2%).

### Data quality

`compute.sanitize` runs on each original power series (per allocation for CPU, per GPU for GPU power), before integration and before the per-series energies are summed. Anomalous samples are dropped, never clamped, so a bad reading never inflates the accumulated energy (the integrator sees a gap in that one source) and the other sources at the same instant are untouched. Config `energy.data_quality`, every check individually skippable:

- `min_power_w`: drop samples below this (kills negatives).
- `max_power_w`: drop samples above this hard ceiling (`0` disables). Applies per source, so it is a physically bounded signal (one allocation, one GPU), not the deployment total.
- `spike_factor` plus `spike_window`: drop a sample above `spike_factor` times the rolling median of its `spike_window` neighbours (`0` disables).

## API surface

The consumer-facing reference (response formats field by field, examples, polling and UX notes) is in [energy-api.md](./energy-api.md). This section is the maintainer view.

### `energy` embedded in the deployment info

The four deployment routers (`modules`, `tools`, `batch`, `try_me/nomad`), in both `get_deployment` and `get_deployments`, attach an `energy` object built from the store plus the live top-up. Its shape follows the existing `full_info` param:

- `full_info=false` (the `get_deployments` default): `energy` = `accounting.get_accumulated(...)` for the individual endpoint, or one batch `accounting.get_accumulated_bulk(namespace, owner)` mapped over the list. `EnergyStats`. One or two Mimir queries total for a whole list.
- `full_info=true` (the `get_deployment` default): `energy` = `accounting.get_series(namespace, uuid)` (whole life). `EnergyTimeSeries` with `accumulated` nested. On a list this is one `get_series` per deployment, matching the "may increase latency" note that `full_info` already carries.

A `energy: bool = True` query param lets a client opt out; the internal quota-check calls pass `False`. `nomad_utils` is not touched.

`energy` is `null` when there is no rollup or `metrics_available` is `false` (deployment in a site or node without monitoring). When `coverage_ratio < 1.0` the available data is still returned, flagged.

`schemas.EnergyStats`:

```
energy_wh            accumulated energy, normalized (x TUE); Wh so the dashboard counter visibly moves
tue_factor           the normalization applied (energy_wh / raw); divide by it for the raw figure
carbon_g             gCO2eq, scope 2
water_l              L, scope 2
power_w              current draw: last Mimir sample (~30-60s old); 0 if running but idle, null if not running
since                start of the accumulation window (`window_start`): pinned on the first sweep, only advances if it falls behind Mimir retention
as_of                store pointer timestamp
live_as_of           instant of the live top-up
complete             False if it started late or was clamped by Mimir retention
coverage_ratio       fraction of run time that has power data (float, 1.0 = full); null on the per-datacenter block
degraded             Mimir or Wattnet unavailable for the top-up
datacenters          list of datacenters the deployment or user has run in; null on the per-datacenter block
deployments          set only on the per-user aggregate: number of deployments summed
series               15-min EnergyTimeSeriesPoint list; null unless full_info=true (per-user aggregate only; the per-deployment full_info uses EnergyTimeSeries instead)
```

`power_w` is a rate, not an accumulator, so it is never summed. The `power_w` in the block (deployment, user, datacenter) is the **current draw**: the last power sample read from Mimir, however old it is (`compute.instant_power` does not drop it for being stale -- Mimir data is immutable once ingested, so the actual last sample is always the right one to show; it does still drop spiky samples via `sanitize`'s data-quality filter). Deployment and user take it from the last point of the top-up range query; the datacenter takes it from a dedicated instant query (`get_datacenter_power`). `series[].power_w` in the time series is different: a **mean** over its 15-min bucket (so `energy_wh ~= power_w * bucket_hours`).

### Time series, for the detail view

The detail view calls the deployment's own individual endpoint (`GET /v1/deployments/{type}/{uuid}`, which defaults to `full_info=true`) and reads `energy` from the response. That `energy` is `schemas.EnergyTimeSeries` from `accounting.get_series(namespace, uuid)`: `{deployment_uuid, start, end, source, series, accumulated}`, where `series` is a list of points (`energy` / `carbon` / `water` per-bucket increment, `power_w` per point; a running total is `cumsum` client-side and matches `accumulated`) and `accumulated` is the `EnergyStats`. It serves the persisted 15-min archive downsampled to ~1000 points plus the live tail (`pointer_ts -> now` from Mimir, `live: true`) appended un-downsampled, so the graph keeps growing on every refresh; `source` is then `"mixed"`. Ownership and 404 handling are the individual endpoint's usual behaviour; `energy` is simply `null` when there is no rollup.

### Per-datacenter energy, in the cluster stats

`get_cluster_stats_bg()` (the 30 s background thread, which already refreshes `green_director`) calls `accounting.get_cluster_energy(series=True)`, which merges three things per datacenter into `schemas.EnergyStats`:

1. **Instantaneous power** from `accounting.get_datacenter_power()`: two instant Mimir queries, `power_w = (sum by (datacenter) scaph_host_power_microwatts + sum by (datacenter) DCGM_FI_DEV_POWER_USAGE) * TUE[dc]`. The host / GPU split is not exposed (on the ifca VMs `scaph_host_power` reads near zero, so it would be misleading; in practice `power_w` is GPU-dominated). A datacenter listed in `energy.metrics_datacenters` is reported with whatever it has (host-only, GPU-only or both); without that allowlist a host-power series is required so a stray GPU metric for an unmonitored site does not fabricate a total.
2. **Accumulated energy** from the `_cluster/<dc>.accum.json` document (persisted by the datacenter sweep) plus a `topup_cluster` live slice: `energy_wh`, `carbon_g`, `water_l`, `since`, `complete`, `live_as_of`.
3. **Time series**: the consolidated `.series.jsonl` (`_cluster_archive_series`, cached ~5 min so the 30 s thread does not re-parse it) plus the 24h tail, downsampled to `energy.cluster_series_points`, with the live tail (`live_series_cluster`) appended. Points carry per-bucket increments only.

Attached as `stats.datacenters[<dc>].energy`. There is **no** response-root total: `schemas.ClusterStats` has no `energy` field, and clients sum the datacenter blocks they want. `get_cluster_stats()` (the VO-filtered endpoint) prunes each datacenter's node list but keeps the `energy` block, so an instrumented datacenter with no nodes for that VO is still returned (empty `nodes`). The background thread always computes the series; `get_cluster_stats(full_info=false)` (the default) strips it from every datacenter block before returning, so the series only ships when the caller asks for it. Any failure in this block is caught and leaves the datacenters without `energy`. A datacenter that is not in `energy.metrics_datacenters` and has no accumulator doc simply has no `energy` block; a monitored GPU-only site does get one (accumulator plus series, and an instantaneous `power_w` from its GPU draw).

### Per-user aggregate

`stats.deployments.get_user_stats` gains an `energy` key from `accounting.get_user_energy(namespace, owner, series=full_info)`: it iterates the owner's `.accum.json` documents, applies each one's live top-up, and sums them into one `EnergyStats`-shaped block (`since` = earliest, `as_of` / `live_as_of` = latest, `complete` = AND, `degraded` = OR, `power_w` = sum of the running ones, `coverage_ratio` = mean, `tue_factor` = blended `energy_wh / raw`, `datacenters` = union, plus a `deployments` count). With `full_info=true` it also returns a merged time series (`_user_series`): the raw 15-min buckets of every deployment (archive + tail) are summed by timestamp first, then `compute.downsample`d once to `target_points` (1000). Merging before downsampling keeps the resolution uniform across the whole series and gives the same hard cap as the other levels, regardless of how long each deployment individually has been running; the live tails are merged and appended un-downsampled, as elsewhere. The energy block is independent of the pre-existing CSV usage stats: if those are missing, the endpoint still returns the `energy` block instead of failing.

## Configuration

`etc/main.yaml`, `energy` section:

```yaml
energy:
  enabled: true
  sweep_seconds: 900             # background thread cadence
  series_bucket_seconds: 900     # persisted time-series bucket size (matches Wattnet's 15-min cadence)
  query_step_seconds: 30         # Mimir scrape interval
  query_lag_seconds: 120         # ignore the most recent data (ingestion lag)
  initial_lookback_hours: 24     # window covered on a deployment's first sweep
  mimir_retention_days: 90       # tune to Mimir's actual retention
  footprint_revision_hours: 24   # re-compute footprint for buckets newer than this
  topup_cache_seconds: 25        # live top-up cache (< dashboard poll)
  deployment_series_points: 1000 # downsample target for the per-deployment series in /deployments/*
  user_series_points: 1000       # downsample target for the merged per-user series in /stats/user
  cluster_series_points: 500     # downsample target for the per-datacenter series in /stats/cluster
  metrics_datacenters: [ifca-ai4eosc, ifca-imagine]
  default_tue_factor: 2.73       # fallback when a datacenter has no TUE in datacenters.csv
  data_quality: {enabled: true, min_power_w: 0, max_power_w: 1000, spike_factor: 12, spike_window: 11}
```

`max_power_w` is a per-source ceiling (one allocation's CPU, or one GPU), not the deployment total, so `1000` W is safe for any single GPU and any CPU allocation on a VM. A dropped sample becomes a gap in that source only. The datacenter sweep reuses this `data_quality` block but forces `max_power_w` to `0` (a whole host draws far more than any single alloc), keeping only the negative-drop and the rolling-median spike check.

- `ai4papi/mimir.py`: `MIMIR_URL` is a module constant. `MIMIR_USER` and `MIMIR_PASSWORD` come from the environment (`os.environ.get`, like `wattnet.py`, not `load_env`, so a missing value disables the feature instead of breaking PAPI startup). Add both to `template.env` and to the PAPI deployment.
- `var/datacenters.csv`: `TUE` column, populated only for the datacenters that have a value. `ai4papi/conf.py` tolerates a blank numeric cell (loads it as `None`).
- The Prometheus `datacenter` label values must match the `name` column of `datacenters.csv`.

## Cadences

| What | Value | Reason |
|---|---|---|
| Mimir scrape | 30 s | given |
| Sweep thread | `sweep_seconds` (900) | keeps the accumulator current, consolidates the footprint at Wattnet's revision cadence, short enough to catch short jobs before Nomad GC |
| Top-up cache | `topup_cache_seconds` (25) | below the dashboard's 30 s poll, so the counter moves |
| Cluster archive-series cache | 300 s | the `.series.jsonl` only changes at sweep cadence; keeps the 30 s thread from re-parsing it |

For the user to perceive the increment every 30 s the frontend must show Wh (or kWh with 3 decimals) or animate the counter: at 50 W, 30 s is ~0.4 Wh.

## Degradation

Same posture as `get_cluster_stats`.

- Mimir down: the sweep logs and skips (store frozen); the top-up returns `degraded: true`; the API serves the stored accumulator with its `as_of`.
- Wattnet down: energy is still computed; the footprint falls back to `DEFAULTS`; `degraded: true`.
- Unreadable file: `get_accumulated` returns `None`, so `energy` is `null`.
- `energy.enabled: false` or no Mimir credentials: `get_accumulated` / `get_series` / `get_datacenter_power` return `None` / `{}`, so `energy` is `null` everywhere and the rest of the API is unchanged.
- Deployment on a site or node without power monitoring: Mimir returns nothing, `metrics_available` is `false`, `energy` is `null` (best-effort, no misleading zeros); retried every sweep in case monitoring appears or the alloc moves.
- Partial coverage: the available data is returned with `coverage_ratio < 1.0`.
- Cluster stats: a Mimir failure leaves `DatacenterStats.energy` `null` without affecting the rest of the cluster stats. The datacenter sweep logs and skips per datacenter; the persisted accumulator is served with its `as_of`.
- Datacenter with no host-power series: `metrics_available: false`, retried every sweep.
- Job garbage-collected before the first sweep: `complete: false`; mitigated by the short sweep interval. A deleted running deployment is deregistered without purge, so it stays queryable for ~4 h and the next sweep's close pass captures the final window.

## Files

New:

- `ai4papi/mimir.py`
- `ai4papi/accounting/__init__.py` (read API: `get_accumulated*`, `get_series`, `get_user_energy`, `get_datacenter_power`, `get_cluster_energy`)
- `ai4papi/accounting/store.py` (file store)
- `ai4papi/accounting/compute.py` (pure functions)
- `ai4papi/accounting/sweep.py` (deployment + datacenter sweep, top-up)
- `tests/test_energy.py`

Modified (all additive):

- `ai4papi/main.py`: the `energy_accounting_thread` `@repeat_every` and its call in `lifespan`
- `ai4papi/routers/v1/deployments/{modules,tools}.py`, `ai4papi/routers/v1/batch.py`, `ai4papi/routers/v1/try_me/nomad.py`: the `energy` key and param (`EnergyStats` or `EnergyTimeSeries` per `full_info`)
- `ai4papi/routers/v1/stats/deployments.py`: the per-user `energy` aggregate in `get_user_stats` (`full_info` for the series) and the per-datacenter `energy` block in `get_cluster_stats` (`full_info` for the series); no response-root total
- `ai4papi/schemas.py`: `EnergyStats` (per deployment and per user), `EnergyTimeSeriesPoint`, `EnergyTimeSeries` (its point list is `series`); `energy` on `DatacenterStats` (also `EnergyStats`)
- `ai4papi/wattnet.py`: `GreenDirector.footprint_series`
- `ai4papi/conf.py`: blank numeric CSV cell tolerated
- `var/datacenters.csv`: `TUE` column
- `etc/main.yaml` (`energy` section incl. `deployment_series_points` / `user_series_points` / `cluster_series_points`), `template.env`, `tests/test_routes.py`

## Testing

`tests/test_energy.py` (plain-script style, picked up by `tests/main.py`):

- Pure-function checks, no infrastructure: `integrate` (trapezoidal, TUE, gaps, single sample), `sanitize` (negatives, ceiling, spike detection, disabled passthrough), `apply_footprint` (Wattnet series and `DEFAULTS` fallback), `intensity_at`, `downsample`, `merge_buckets`, `running_seconds`.
- Sweep pipeline, Mimir and Nomad monkeypatched: `process_single` writes a correct `.accum.json`, idempotency (same `now` gives a byte-identical document), monotonicity across sweeps, buckets aging into `.series.jsonl`, and `get_accumulated` / `get_series` validate against the pydantic models.
- Cluster energy, `mimir.query` / `mimir.query_range` monkeypatched (several raw host series): `get_datacenter_power()` gives `power_w = (host + gpu) * TUE`; `process_datacenter` writes a correct `_cluster/<dc>.accum.json` with per-series sanitize then sum, monotonic across sweeps, buckets aging into `.series.jsonl`; `get_cluster_energy(series=True)` merges power + accumulator + a series that validates as `EnergyTimeSeriesPoint` and whose `cumsum` reconciles with the accumulated total.
- Per-user aggregate: `get_user_energy` sums the owner's deployments (`EnergyStats`-shaped), with and without the merged series, and its total reconciles with the merged series.
- An optional live Mimir check, gated on `PAPI_TESTS_MIMIR` plus real credentials.

Run with the repo venv:

```bash
IS_PROD=False env/bin/python tests/test_energy.py
cd tests && IS_PROD=False ../env/bin/python test_routes.py && cd ..
```

Note: install `fastapi < 0.116`. From 0.116 `include_router` is lazy and `test_routes.py` does not see the routes until the app starts.
