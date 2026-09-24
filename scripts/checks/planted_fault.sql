-- The planted overload fault, visible by hand (Phase 2 "done when").
-- Current draw is compared with the baseline for the SAME drive mode;
-- V-001 is an ordinary vehicle, included for contrast.
-- Run: Get-Content scripts/checks/planted_fault.sql | docker compose exec -T postgres psql -U opscopilot -d opscopilot

SELECT t.vehicle_id,
       v.model_code,
       round(avg(t.metric_value) FILTER (WHERE t.metric_name = 'current_draw')::numeric, 1) AS current_a,
       round(avg(b.nominal_value) FILTER (WHERE t.metric_name = 'current_draw')::numeric, 1) AS normal_a,
       round((100 * (avg(t.metric_value) FILTER (WHERE t.metric_name = 'current_draw')
                   / avg(b.nominal_value) FILTER (WHERE t.metric_name = 'current_draw') - 1))::numeric, 0) AS pct_above,
       count(*) FILTER (WHERE t.metric_name = 'payload' AND t.metric_value > b.rated_payload_kg) AS overloaded_trips,
       round(max(t.metric_value) FILTER (WHERE t.metric_name = 'cell_health')::numeric, 1) AS battery_health
FROM vehicle_telemetry t
JOIN vehicles v USING (vehicle_id)
LEFT JOIN vehicle_baseline_specs b
       ON b.model_code = v.model_code
      AND b.drive_mode = t.drive_mode
      AND b.metric_name = t.metric_name
WHERE t.vehicle_id IN ('V-042', 'V-007', 'V-029', 'V-001')
  AND t.recorded_at >= now() - interval '9 days'
GROUP BY t.vehicle_id, v.model_code
ORDER BY pct_above DESC;
