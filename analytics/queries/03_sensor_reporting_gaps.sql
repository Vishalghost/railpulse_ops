-- name: Sensors that under-reported yesterday
-- description: Messages per sensor yesterday against the 17,280 expected at one reading every 5 s. Long-term view of what the 5-minute heartbeat checker catches live.
SELECT
    division,
    sensor_id,
    count(*) AS messages,
    round(100.0 * count(*) / 17280, 1) AS pct_of_expected,
    round((max(ts) - min(ts)) / 3600000.0, 1) AS hours_covered
FROM telemetry_raw
WHERE dt = date_format(current_date - INTERVAL '1' DAY, '%Y-%m-%d')
GROUP BY division, sensor_id
HAVING count(*) < 0.9 * 17280
ORDER BY pct_of_expected ASC
