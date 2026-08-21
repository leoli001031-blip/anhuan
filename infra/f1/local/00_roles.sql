\set ON_ERROR_STOP on
\getenv local_database POSTGRES_DB

BEGIN;

-- Local engineering bootstrap.  Passwords are read by PostgreSQL directly
-- from mounted 0600 files; they never appear in Compose environment values,
-- argv, SQL files, or command output.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"local_database" FROM PUBLIC;

DO $bootstrap$
DECLARE
  migration_password text := btrim(pg_read_file('/run/secrets/f1/f0d_migration_password'));
  runtime_password text := btrim(pg_read_file('/run/secrets/f1/f0d_runtime_password'));
  worker_password text := btrim(pg_read_file('/run/secrets/f1/f0d_worker_password'));
BEGIN
  IF migration_password = '' OR runtime_password = '' OR worker_password = '' THEN
    RAISE EXCEPTION 'LOCAL_ROLE_SECRET_EMPTY';
  END IF;
  EXECUTE format(
    'CREATE ROLE f0d_migration LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4 PASSWORD %L',
    migration_password
  );
  EXECUTE format(
    'CREATE ROLE f0d_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20 PASSWORD %L',
    runtime_password
  );
  EXECUTE format(
    'CREATE ROLE f0d_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10 PASSWORD %L',
    worker_password
  );
END
$bootstrap$;

GRANT CONNECT, CREATE ON DATABASE :"local_database" TO f0d_migration;
GRANT CONNECT ON DATABASE :"local_database" TO f0d_runtime, f0d_worker;
CREATE SCHEMA f0d AUTHORIZATION f0d_migration;
REVOKE ALL ON SCHEMA f0d FROM PUBLIC;

ALTER ROLE f0d_migration SET search_path = f0d, pg_catalog;
ALTER ROLE f0d_runtime SET search_path = f0d, pg_catalog;
ALTER ROLE f0d_worker SET search_path = f0d, pg_catalog;
ALTER ROLE f0d_migration SET statement_timeout = '60s';
ALTER ROLE f0d_runtime SET statement_timeout = '15s';
ALTER ROLE f0d_worker SET statement_timeout = '60s';
ALTER ROLE f0d_migration SET lock_timeout = '10s';
ALTER ROLE f0d_runtime SET lock_timeout = '3s';
ALTER ROLE f0d_worker SET lock_timeout = '5s';
ALTER ROLE f0d_migration SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE f0d_runtime SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE f0d_worker SET idle_in_transaction_session_timeout = '30s';

COMMIT;
