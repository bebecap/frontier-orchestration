-- frontier-orchestration telemetry: aggregate counters only. No event rows, no ids, no IP, no
-- headers. Events cannot be linked to each other: there is no install id.
CREATE TABLE IF NOT EXISTS fo_budget (
  hour   text PRIMARY KEY,           -- 'YYYY-MM-DDTHH' (UTC): global count of admitted events
  n      integer NOT NULL            -- kept about 2 days, then deleted by api/prune.js
);
CREATE TABLE IF NOT EXISTS fo_counts (
  day    date    NOT NULL,
  field  text    NOT NULL,           -- 'event' or an allowlisted property name
  value  text    NOT NULL,           -- an allowlisted value
  n      integer NOT NULL,
  PRIMARY KEY (day, field, value)    -- kept 365 days, then deleted by api/prune.js
);
DROP TABLE IF EXISTS fo_installs;
