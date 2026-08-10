\set ON_ERROR_STOP on
\getenv db_name POSTGRES_DB
\getenv migration_password F0D_MIGRATION_PASSWORD
\getenv runtime_password F0D_RUNTIME_PASSWORD
\getenv worker_password F0D_WORKER_PASSWORD

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;

CREATE ROLE f0d_migration
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 4 PASSWORD :'migration_password';
CREATE ROLE f0d_runtime
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 20 PASSWORD :'runtime_password';
CREATE ROLE f0d_worker
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 10 PASSWORD :'worker_password';

GRANT CONNECT, CREATE ON DATABASE :"db_name" TO f0d_migration;
GRANT CONNECT ON DATABASE :"db_name" TO f0d_runtime, f0d_worker;
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
