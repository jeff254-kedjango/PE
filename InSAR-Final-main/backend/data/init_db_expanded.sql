-- infra-proptech: DuckDB schema for the multi-AOI MVP
-- Run with: duckdb backend/data/demo.duckdb < backend/scripts/init_db.sql
--
-- Design notes:
--   - Hot tables (buildings, subsidence_time_series, environmental_index) are
--     backed by *Hive-partitioned* Parquet on disk: data/parquet/<table>/aoi=*/.
--     DuckDB reads these via VIEWs with hive_partitioning=true, so a query
--     filtered by aoi_code prunes to a single partition automatically.
--   - aoi_registry is a small lookup table; one Parquet file is plenty.
--   - We keep aoi_code as an explicit column (not just a partition key) so
--     joins are uniform whether or not partitioning is in play.

INSTALL spatial;
LOAD spatial;

-- ---------------------------------------------------------------------------
-- aoi_registry: human-facing metadata + per-AOI narrative copy for the UI.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW aoi_registry AS
SELECT * FROM read_parquet('/home/jeff/infra-proptech/backend/data/parquet/aoi_registry.parquet');

-- ---------------------------------------------------------------------------
-- buildings: one row per footprint per AOI. WKB stored in `geom_wkb`; we
-- expose a derived GEOMETRY column via the view for spatial predicates.
-- Sources:  Huruma  → Google Open Buildings
--           Mombasa → OpenStreetMap
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW buildings AS
SELECT
    building_id,
    aoi_code,
    footprint_source,            -- 'open_buildings' | 'osm' | 'synthetic'
    osm_id,                      -- nullable (only for source='osm')
    open_buildings_id,           -- nullable (only for source='open_buildings')
    ST_GeomFromWKB(geom_wkb) AS geom,
    centroid_lon,
    centroid_lat,
    height_m,                    -- floor-count estimate (n_floors × ~3 m)
    insar_height_m,              -- InSAR phase-fringe-inversion estimate
    insar_height_sigma_m,        -- per-building σ on the InSAR estimate
    fused_height_m,              -- inverse-variance blend; used for 3D extrusion
    n_floors,
    soil_class,
    riparian_dist_m,
    shoreline_dist_m,            -- NULL for inland AOIs
    reclaimed_land,              -- BOOLEAN, NULL when not applicable
    built_year,
    classification,              -- u8: 0=INDETERMINATE 1=CONFIRMED 2=NOISE 3=STABLE
    velocity_accel_mm_yr2,       -- annualized 6-mo acceleration; - = accelerating subsidence
    trend_slope_mm_yr,           -- STL trend slope (mm/yr); decoupled from seasonal soil swelling
    seasonal_amplitude_mm,       -- STL seasonal amplitude (peak-to-peak, mm)
    trend_r2,                    -- 1 - var(resid)/var(disp); fit quality of trend+seasonal
    failure_mode,                -- u8: 0=ELASTIC 1=PLASTIC (plastic = trend < -5 mm/yr & R² > .85)
    velocity_sigma_mm_yr,        -- σ on velocity_mm_yr; ≈ k*(1-γ) calibrated to InSAR noise floor
    velocity_ew_sigma_mm_yr,     -- σ on horizontal drift; same coherence-driven model
    cohort_composite_pct,        -- u8 0-100: pct rank of composite_risk in (height-band × soil) peers
    cohort_shear_pct,            -- u8 0-100: pct rank of |v_ew| in peer cohort
    cohort_size                  -- u16: # buildings in peer cohort
FROM read_parquet(
    '/home/jeff/infra-proptech/backend/data/parquet/buildings/aoi=*/*.parquet',
    hive_partitioning = true
);

-- ---------------------------------------------------------------------------
-- subsidence_time_series: monthly LOS-vertical displacement per building.
-- displacement_mm: cumulative from t0, negative = subsiding.
-- velocity_mm_yr: trailing 12-mo linear-fit slope (annualized).
-- coherence: mean InSAR coherence over the footprint pixels.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW subsidence_time_series AS
SELECT
    building_id,
    aoi_code,
    observation_date,
    displacement_mm,
    trend_displacement_mm,          -- STL trend component (mm); rain-decoupled signal
    velocity_mm_yr,
    velocity_horizontal_ew_mm_yr,   -- + east, - west; from asc/desc decomposition
    coherence
FROM read_parquet(
    '/home/jeff/infra-proptech/backend/data/parquet/subsidence/aoi=*/*.parquet',
    hive_partitioning = true
);

-- ---------------------------------------------------------------------------
-- environmental_index: quarterly context (groundwater anomaly, rainfall,
-- vegetation proxy) + the MVP composite_risk score.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW environmental_index AS
SELECT
    building_id,
    aoi_code,
    period_start,
    groundwater_anom,
    rainfall_anom_mm,
    ndvi_proxy,
    composite_risk
FROM read_parquet(
    '/home/jeff/infra-proptech/backend/data/parquet/env_index/aoi=*/*.parquet',
    hive_partitioning = true
);

-- ---------------------------------------------------------------------------
-- Latest-per-building view. The window function pushes down to one partition
-- when filtered by aoi_code.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_building_latest AS
WITH s_latest AS (
    SELECT building_id, aoi_code, observation_date, displacement_mm, velocity_mm_yr, coherence,
           ROW_NUMBER() OVER (PARTITION BY aoi_code, building_id ORDER BY observation_date DESC) AS rn
    FROM subsidence_time_series
),
e_latest AS (
    SELECT building_id, aoi_code, composite_risk,
           ROW_NUMBER() OVER (PARTITION BY aoi_code, building_id ORDER BY period_start DESC) AS rn
    FROM environmental_index
)
SELECT
    b.building_id,
    b.aoi_code,
    b.geom,
    b.height_m,
    b.soil_class,
    b.riparian_dist_m,
    b.shoreline_dist_m,
    b.reclaimed_land,
    s.observation_date     AS latest_date,
    s.displacement_mm      AS latest_displacement_mm,
    s.velocity_mm_yr       AS latest_velocity_mm_yr,
    s.coherence            AS latest_coherence,
    e.composite_risk       AS latest_composite_risk
FROM buildings b
LEFT JOIN s_latest s
  ON s.building_id = b.building_id AND s.aoi_code = b.aoi_code AND s.rn = 1
LEFT JOIN e_latest e
  ON e.building_id = b.building_id AND e.aoi_code = b.aoi_code AND e.rn = 1;
