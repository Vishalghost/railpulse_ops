-- name: How often incidents get worse
-- description: Incidents opened vs. escalated to a higher severity, per division and type, over the last 30 days. A high ratio means defects are being found late.
SELECT
    detail.division AS division,
    detail.type AS incident_type,
    count(DISTINCT detail.incident_id) FILTER (WHERE detail_type = 'AnomalyDetected') AS opened,
    count(DISTINCT detail.incident_id) FILTER (WHERE detail_type = 'IncidentEscalated') AS escalated
FROM incident_events
WHERE dt >= date_format(current_date - INTERVAL '30' DAY, '%Y-%m-%d')
GROUP BY 1, 2
ORDER BY escalated DESC, opened DESC
