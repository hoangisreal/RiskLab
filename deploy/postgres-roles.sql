-- Run once using migration/operator credentials in a dedicated RiskLab database.
-- Group roles have no login; deployment provisions separate secret-backed login roles.
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'risklab_runtime') THEN
        CREATE ROLE risklab_runtime NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'risklab_worker') THEN
        CREATE ROLE risklab_worker NOLOGIN;
    END IF;
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
