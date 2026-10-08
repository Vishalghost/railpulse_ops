-- name: Fracture risk by hour of day (IST)
-- description: Ultrasonic back-wall echo loss vs each sensor's own 30-day median, by IST hour. Losses cluster in cold pre-dawn hours, when contracting rail opens cracks.
WITH uts AS (
    SELECT
        division,
        sensor_id,
        from_unixtime(ts / 1000) AT TIME ZONE 'Asia/Kolkata' AS t_ist,
        metrics.backwall_echo_db AS echo_db
    FROM telemetry_raw
    WHERE sensor_type = 'UTS'
      AND dt >= date_format(current_date - INTERVAL '30' DAY, '%Y-%m-%d')
      AND metrics.backwall_echo_db IS NOT NULL
),
baseline AS (
    SELECT sensor_id, approx_percentile(echo_db, 0.5) AS median_db
    FROM uts
    GROUP BY sensor_id
)
SELECT
    u.division,
    hour(u.t_ist) AS hour_ist,
    count(*) AS readings,
    round(avg(b.median_db - u.echo_db), 2) AS avg_loss_db,
    count_if(b.median_db - u.echo_db >= 6) AS readings_past_crack_threshold
FROM uts u
JOIN baseline b ON u.sensor_id = b.sensor_id
GROUP BY u.division, hour(u.t_ist)
ORDER BY u.division, hour_ist
