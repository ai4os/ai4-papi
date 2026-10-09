# Energy stats API (dashboard integration)

Reference for the energy consumption and footprint (carbon and water, scope 2) that PAPI exposes. Architecture and internals are in [energy-accounting.md](./energy-accounting.md).

All of this is additive and no new endpoints were created. The data rides on endpoints that already exist, at three levels. The accumulated numbers (and the live Mimir top-up) are **always** present at every level; the time series is only included when `full_info=true`, because it is large (hundreds to a thousand points per entity).

| Level | Where | Accumulated (+ top-up) | Time series (`full_info=true`) |
|---|---|---|---|
| Datacenter / site | `GET /v1/deployments/stats/cluster` -> `datacenters[<site>].energy` | always | per site, downsampled to `energy.cluster_series_points` |
| User | `GET /v1/deployments/stats/user?vo=<vo>` -> `energy` | always | merged across the user's deployments |
| Deployment | `energy` key in the deployment info (list and individual endpoints, all four types) | always | per deployment. On the individual endpoint `full_info` defaults to `true`, so it comes back as `EnergyTimeSeries`; the list form is `EnergyStats` |

There is **no platform-wide / VO total** in the response: `/stats/cluster` returns one `energy` block per datacenter and the client sums the datacenters it cares about.

`full_info` defaults to `false` on the list-shaped endpoints (`/stats/cluster`, `/stats/user`, the deployment list) and to `true` on the single-deployment endpoint. Pass it explicitly to override.

Every endpoint that attaches energy also takes `energy` (default `true`). Pass `energy=false` to drop the `energy` block entirely, for a leaner and slightly faster response when the caller does not need it. PAPI itself passes `energy=false` on its internal quota and resource checks.

Notes:

- A datacenter carries an `energy` block only if it is in `energy.metrics_datacenters` (has a power exporter). `/stats/cluster?vo=<vo>` still returns every instrumented datacenter's `energy` even when that VO has no nodes there (site infrastructure energy is a property of the site); the VO filter only prunes the node lists.
- Datacenter energy measures **infrastructure** power (host + GPU of the whole site, times TUE). User and deployment energy is **workload** attribution (power of that deployment's allocations). They are not expected to add up.
- There is no VO-level energy block. The `full-agg` / `timeseries` keys already in `GET /stats/user` are the older CPU/GPU usage stats, unrelated to energy.

If the feature is disabled or has no data, every one of these is `null` (or an absent key) and the API behaves exactly as before.

## Units and conventions

- Energy: `energy_wh` in watt-hours. Divide by 1000 for kWh.
- Carbon: `carbon_g` in grams of CO2 equivalent.
- Water: `water_l` in liters.
- Power: `power_w` in watts.
- Timestamps: ISO-8601 UTC, always with the `Z` suffix (`2026-09-07T07:50:00Z`).
- Every numeric field is a JSON number (float), returned at full precision (not rounded): the accumulated total always equals the sum of `series`, and idle CPU workloads can legitimately sit at 1e-5 Wh. Round for display on the client.
- Every energy, power, carbon and water value is normalized by the datacenter TUE (grid to compute ratio). The raw pre-TUE figure is not sent, but `tue_factor` is: divide by it to get the raw value. On a single-datacenter deployment `tue_factor` is that datacenter's configured TUE; on the per-user aggregate and on a deployment that moved between sites it is the blended `energy_wh / raw_energy_wh`.
- `power_w` in the block is the **current draw**: the last sample read from Mimir (~30 to 60 s old) at every level. `series[].power_w` in the time series is different: the **mean** over that 15-min bucket (so `energy_wh ~= power_w * bucket_hours`). Either way it is a rate, not a total, and is never summed.

## The `EnergyStats` object

One object, the same shape at every level, always with the live top-up applied: `energy` per deployment (list endpoints), `accumulated` inside the per-deployment time series, `energy` per datacenter in `GET /stats/cluster`, and `energy` per user in `GET /stats/user`. `coverage_ratio` and `datacenters` are `null` on the datacenter block; `deployments` is set only on the per-user aggregate.

```json
{
  "energy_wh": 3088.98,
  "tue_factor": 2.03,
  "carbon_g": 929.78,
  "water_l": 37.07,
  "power_w": 220.0,
  "since": "2026-09-01T10:00:00Z",
  "as_of": "2026-09-07T07:50:00Z",
  "live_as_of": "2026-09-07T07:54:58Z",
  "complete": false,
  "coverage_ratio": 1.0,
  "degraded": false,
  "datacenters": ["ifca-imagine"]
}
```

| Field | Type | Meaning |
|---|---|---|
| `energy_wh` | number | Accumulated energy, normalized by the datacenter TUE. This is the headline number. Divide by `tue_factor` for the raw figure at the meter. |
| `tue_factor` | number or null | The normalization applied: `energy_wh / raw_energy_wh`. On a single-datacenter deployment it is that datacenter's configured TUE. `null` only before there is any energy. |
| `carbon_g` | number | Accumulated carbon footprint (gCO2eq). |
| `water_l` | number | Accumulated water footprint (L). |
| `power_w` | number or null | Current draw: the last power sample read from Mimir (~30 to 60 s old), normalized by TUE. `0` for a running deployment drawing nothing measurable; `null` if it is not running. |
| `since` | string or null | Start of the accumulation window: the point `energy_wh` and the footprint are counted from. Pinned when accounting first picks up the deployment (its submit time, or as far back as `initial_lookback_hours` / Mimir retention allow) and stable afterwards, so it is not the same as `as_of`. Pair it with `complete: false` for a "since \<date\>" note. |
| `as_of` | string or null | Timestamp of the last persisted point. |
| `live_as_of` | string or null | Instant the numbers actually reflect, once the live top-up is applied. Use this for "updated N ago". Null when the values come straight from the store with no live top-up. |
| `complete` | boolean | `false` if accounting started after the deployment was created, or the window was clamped by Mimir retention. When `false`, treat `energy_wh` and the footprint as a lower bound. |
| `degraded` | boolean | `true` if Mimir or Wattnet was unreachable for the live part. The stored value is still served, it may just be slightly stale. |
| `coverage_ratio` | number or null | Deployment and per-user only: fraction of the run time that has power data (`1.0` = fully covered). `null` on the datacenter block. |
| `datacenters` | string[] or null | Deployment and per-user only: datacenters the deployment(s) ran in. `null` on the datacenter block. |
| `deployments` | number or null | Per-user aggregate only: how many deployments went into the sum. |
| `series` | array or null | Per-user aggregate only, and only with `full_info=true`: the merged 15-min time series. `null` otherwise. |

The values already include the live top-up: `energy_wh` is the persisted accumulator plus the slice from the last persisted point up to `live_as_of`, computed live against Mimir on each request (cached ~25 s). That is why the counter moves between refreshes without a background job.

## 1. `energy` in the deployment info

Added to the existing deployment object. Its shape follows the endpoint's existing `full_info` query param:

- `full_info=false` -> `energy` is an `EnergyStats` (or `null`).
- `full_info=true` -> `energy` is an `EnergyTimeSeries` (or `null`): the accumulated stats plus the full time series for the detail-view graphs.

The defaults line up with how the dashboard uses each endpoint:

| Endpoint | `full_info` default | `energy` shape |
|---|---|---|
| `GET /v1/deployments/modules?vos=<vo>` (and `tools` / `batch` / `try_me/nomad` list variants) | `false` | `EnergyStats` per deployment |
| `GET /v1/deployments/modules/{uuid}?vo=<vo>` (and `tools` / `batch` variants) | `true` | `EnergyTimeSeries` |
| `GET /v1/deployments/try_me/nomad/{uuid}` (no `vo`, fixed VO) | `true` | `EnergyTimeSeries` |

You can override in either direction: `...?vos=<vo>&full_info=true` gives the full series per deployment in the list (heavier, one Mimir top-up per deployment), and `.../{uuid}?vo=<vo>&full_info=false` gives just the accumulated `EnergyStats`.

- `energy` is `null` when the deployment has no data (site or node without monitoring, still queued, or the feature is off). Show "not available" or hide the widget, never a zero.
- Query param `energy` (default `true`). Pass `?energy=false` to skip the block entirely, for example when you only need the deployment list fast.

### List view (`full_info=false`)

```json
{
  "job_ID": "aaaaaaaa-...",
  "name": "module-aaaaaaaa-...",
  "status": "running",
  "energy": {
    "energy_wh": 3088.98,
    "carbon_g": 929.78,
    "water_l": 37.07,
    "power_w": 220.0,
    "live_as_of": "2026-09-07T07:54:58Z",
    "complete": false, "coverage_ratio": 1.0, "degraded": false,
    "datacenters": ["ifca-imagine"],
    "...": "see EnergyStats above"
  }
}
```

The list endpoint resolves all of the user's deployments with one or two Mimir queries total, regardless of how many deployments there are. Poll it every 30 to 60 s for the list view.

### Detail view (`full_info=true`)

```json
{
  "job_ID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  "name": "module-aaaaaaaa-...",
  "status": "running",
  "energy": {
    "deployment_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "start": "2026-09-07T00:55:00Z",
    "end": "2026-09-07T07:54:58Z",
    "source": "mixed",
    "series": [
      {
        "ts": "2026-09-07T00:55:00Z",
        "power_w": 220.0,
        "energy_wh": 37.22,
        "carbon_g": 11.20,
        "water_l": 0.45,
        "live": false
      },
      { "ts": "2026-09-07T01:00:00Z", "power_w": 220.0, "energy_wh": 37.22, "...": "..." }
    ],
    "accumulated": { "...": "EnergyStats, exactly the list-view object" }
  }
}
```

| Field | Type | Meaning |
|---|---|---|
| `deployment_uuid` | string | Echo of the deployment. |
| `start` / `end` | string | Time span covered by `series`. `start` equals `accumulated.since`, `end` the live edge (the last point). Convenience only, both are derivable from the series. |
| `series` | array | Ordered by `ts` ascending. Whole deployment life, downsampled to `energy.deployment_series_points` (default 1000). |
| `series[].ts` | string | Bucket start. |
| `series[].power_w` | number or null | Mean power over the part of the bucket that had data, normalized by TUE. `energy_wh` is about `power_w` times the bucket duration. |
| `series[].energy_wh` | number or null | Energy in that bucket (per-bucket increment). A running total is `cumsum(series[].energy_wh)` and matches `accumulated.energy_wh`. |
| `series[].carbon_g` | number or null | Carbon in that bucket. Same, cumsum matches `accumulated.carbon_g`. |
| `series[].water_l` | number or null | Water in that bucket. Same, cumsum matches `accumulated.water_l`. |
| `series[].live` | boolean | `true` for the tail points served live from Mimir (not yet persisted). They can change on the next refresh. |
| `source` | string | `"store"` (only the persisted archive) or `"mixed"` (archive plus a live tail). Equivalent to `series[-1].live`. |
| `accumulated` | EnergyStats | The current totals, same object as the list view. Use it for the headline numbers next to the chart (and its `degraded` flag). |

- The persisted archive is at 15-minute resolution (`energy.series_bucket_seconds`); the response is downsampled to `energy.deployment_series_points` (default 1000) across the whole life.
- The deployment is running, so `end` reaches "now" and the last points carry `live: true`. Calling the endpoint again a minute later returns a slightly longer series with updated `live` points. That is what makes the chart grow.
- Poll the detail view every 30 to 60 s.

## 2. `energy` in the per-user stats

```
GET /v1/deployments/stats/user?vo=<vo>
GET /v1/deployments/stats/user?vo=<vo>&full_info=true
```

The existing response gains an `energy` key: all of the user's deployments in that VO, each with its live top-up, summed into one `EnergyStats` object (plus a `deployments` count):

```json
{
  "full-agg": { "...": "existing compute-usage stats" },
  "timeseries": { "...": "existing" },
  "users-agg": { "...": "existing" },
  "energy": {
    "energy_wh": 3088.98,
    "tue_factor": 2.03,
    "carbon_g": 929.78,
    "water_l": 37.07,
    "power_w": 220.0,
    "since": "2026-09-01T10:00:00Z",
    "as_of": "2026-09-09T07:00:00Z",
    "live_as_of": "2026-09-09T07:04:58Z",
    "complete": false,
    "coverage_ratio": 0.98,
    "degraded": false,
    "datacenters": ["ifca-imagine"],
    "deployments": 3,
    "series": null
  }
}
```

- Same fields as the per-deployment `EnergyStats` (see above), aggregated: `since` is the earliest across the user's deployments, `as_of` / `live_as_of` the latest, `complete` is the AND, `degraded` the OR, `power_w` the sum over the running ones, `coverage_ratio` the mean, `datacenters` the union, `tue_factor` the blended `energy_wh / raw_energy_wh`.
- `deployments`: how many accounted deployments went into the sum.
- `series`: `null` unless `full_info=true`, then a 15-min time series of the user's deployments summed by timestamp and downsampled once to `energy.user_series_points` (default 1000; `EnergyTimeSeriesPoint` shape, uniform step across the whole series), for a "my usage over time" chart.
- `null` when the user has no accounted deployments.
- Independent of the pre-existing CSV usage stats: if those are missing the endpoint still returns the `energy` block.

## 3. `energy` in the cluster stats

```
GET /v1/deployments/stats/cluster
GET /v1/deployments/stats/cluster?vo=<vo>
GET /v1/deployments/stats/cluster?full_info=true
```

Each `datacenters[<dc>]` object gains an `energy` block: the same `EnergyStats` object as everywhere else (`coverage_ratio`, `datacenters`, `deployments` are `null` here). There is **no** response-root `energy`: to get a platform-wide or per-VO figure the client sums the datacenter blocks it wants.

- **`power_w`**: the current draw from the last Mimir scrape, refreshed every ~30 s, normalized by TUE. It is `(sum of scaphandre host power + sum of GPU power) * TUE`.
- **accumulated** energy and footprint since accounting started (`energy_wh`, `carbon_g`, `water_l`, all TUE-normalized, divide by `tue_factor` for raw), kept indefinitely and topped up live on every read.
- **`series`**: `null` by default; with `full_info=true`, a 15-min series downsampled to `energy.cluster_series_points` (default 500).

`?vo=<vo>` filters the node lists but not the `energy` blocks: an instrumented datacenter keeps its `energy` even when the VO has no nodes there (its `nodes` map is then empty).

```json
{
  "datacenters": {
    "ifca-imagine": {
      "lat": 43.47, "lon": -3.8, "PUE": 1.31,
      "nodes": { "...": "..." },
      "footprints": { "...": "existing" },
      "energy": {
        "power_w": 1421.0,
        "energy_wh": 184203.5,
        "carbon_g": 55450.2,
        "water_l": 2210.9,
        "tue_factor": 2.03,
        "complete": false,
        "since": "2026-06-10T00:00:00Z",
        "as_of": "2026-09-08T09:10:00Z",
        "live_as_of": "2026-09-08T09:12:35Z",
        "degraded": false,
        "coverage_ratio": null, "datacenters": null, "deployments": null,
        "series": null
      }
    }
  },
  "cluster": { "...": "existing ResourceStats" },
  "updated_at": "2026-09-08T09:12:05Z"
}
```

Field meanings: see the `EnergyStats` table above.

- `energy` is `null` for datacenters outside `energy.metrics_datacenters` and whenever Mimir is unreachable; never shown as zero.
- A `vo` filter does not touch the `energy` blocks; it only prunes each datacenter's `nodes`. An instrumented datacenter with no nodes for that VO is still returned, with its `energy` and an empty `nodes`.
- Refreshed by the same 30 s background task as the rest of the cluster stats. The persisted accumulator advances every sweep (~15 min); the live top-up keeps `energy_wh` moving between sweeps. The endpoint itself is cached ~30 s.

## UX notes

- Values come at full float precision; round on the client. Show `energy_wh` in Wh, or kWh with enough decimals to move, or animate the counter. At 50 W the increment per 30 s poll is about 0.4 Wh, invisible if you round to whole kWh.
- The displayed value trails real time by about 25 to 30 s (Mimir ingestion margin). Use `live_as_of` for the "updated N seconds ago" label.
- Poll cadence: list view every 30 to 60 s against the deployment list endpoint (`full_info=false`); detail view every 30 to 60 s against the individual deployment endpoint (`full_info=true`).
- Flags to surface in the UI: `complete: false` (show the value as a lower bound, for example a "since <date>" note), `coverage_ratio < 1.0` (for example "78% of run time with data"), `degraded: true` (a small "data may be delayed" hint).
- `vo` values: `vo.ai4eosc.eu`, `vo.imagine-ai.eu`, `vo.ai4life.eu`, `kmd4eosc`, `tutorials`. The token must have a role for that VO.

## Note on the test suite

`tests/test_routes.py` requires `fastapi < 0.116` in the environment: from 0.116 `include_router` is lazy and the route assertions do not see the routes until the app starts.
