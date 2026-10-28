-- `attempts` now counts executions caused by failures, not resumptions.
-- A run that pauses for approval twice is still on attempt 1; only a crash
-- (stale heartbeat) or an unexpected error moves it to the next attempt.
ALTER TABLE runs ALTER COLUMN attempts SET DEFAULT 1;
UPDATE runs SET attempts = 1 WHERE attempts = 0;
