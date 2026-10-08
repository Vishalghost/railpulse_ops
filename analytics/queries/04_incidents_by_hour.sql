-- name: Incidents by type and hour of day (IST)
-- description: When each kind of incident starts, per division, over the last 30 days. Distinct incident IDs, because event delivery is at-least-once.
SELECT
    detail.division AS division,
    detail.type AS incident_type,
    hour(from_unixtime(detail.first_seen / 1000) AT TIME ZONE 'Asia/Kolkata') AS hour_ist,
    count(DISTINCT detail.incident_id) AS incidents
FROM incident_events
WHERE detail_type = 'AnomalyDetected'
  AND dt >= date_format(current_date - INTERVAL '30' DAY, '%Y-%m-%d')
GROUP BY 1, 2, 3
ORDER BY division, incident_type, hour_ist
