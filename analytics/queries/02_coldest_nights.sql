-- name: Coldest nights vs stress-free temperature
-- description: Lowest rail temperature per division per night, and how far below its stress-free temperature it fell. The top rows are the nights to schedule extra patrols for.
WITH temps AS (
    SELECT
        division,
        segment_id,
        -- Shift by 12 h so one night (evening → morning) falls on a single date.
        date(from_unixtime(ts / 1000) AT TIME ZONE 'Asia/Kolkata' - INTERVAL '12' HOUR) AS night_of,
        metrics.rail_temp_c AS rail_c
    FROM telemetry_raw
    WHERE sensor_type = 'TMP'
      AND dt >= date_format(current_date - INTERVAL '90' DAY, '%Y-%m-%d')
      AND metrics.rail_temp_c IS NOT NULL
)
SELECT
    division,
    night_of,
    round(min(rail_c), 1) AS coldest_rail_c,
    -- Mirrors stress_free_temp_c in the Assets table (simulator/railpulse_sim/fleet.py).
    round(min(rail_c) - CASE division WHEN 'DLI' THEN 35 WHEN 'BB' THEN 38 WHEN 'BCT' THEN 38 WHEN 'MAS' THEN 40
                     WHEN 'HWH' THEN 37 WHEN 'SC' THEN 38 WHEN 'SBC' THEN 35 WHEN 'LKO' THEN 35 END, 1)
        AS below_stress_free_c,
    min_by(segment_id, rail_c) AS coldest_segment
FROM temps
GROUP BY division, night_of
ORDER BY below_stress_free_c ASC
LIMIT 50
