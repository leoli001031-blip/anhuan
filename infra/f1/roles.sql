\set ON_ERROR_STOP on
\getenv db_name F1_DB_NAME
\getenv api_password F1_API_PASSWORD
\getenv worker_password F1_WORKER_PASSWORD

-- F1.1 low-privilege runtime roles.  API/Worker never use the f0d_migration
-- role or any BYPASSRLS role.  Each SECURITY DEFINER domain has a distinct,
-- membership-free owner so one function family cannot borrow another
-- family's table privileges.
DO $$
DECLARE
  definer_role text;
BEGIN
  FOREACH definer_role IN ARRAY ARRAY[
    'f1_auth_definer',
    'f1_identity_read_definer',
    'f1_enterprise_create_definer',
    'f1_invite_create_definer',
    'f1_invite_consume_definer',
    'f1_upload_definer',
    'f1_outbox_definer',
    'f1_qa_definer'
  ] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = definer_role) THEN
      EXECUTE format(
        'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
        'NOINHERIT NOREPLICATION NOBYPASSRLS', definer_role
      );
    END IF;
  END LOOP;
END
$$;

-- psql does not interpolate variables inside a dollar-quoted DO body.
-- Generate exactly one safely quoted CREATE statement only when needed.
SELECT format(
  'CREATE ROLE f1_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
  'NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20 PASSWORD %L',
  :'api_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'f1_api')
\gexec
SELECT format(
  'CREATE ROLE f1_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
  'NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10 PASSWORD %L',
  :'worker_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'f1_worker')
\gexec

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
      FROM pg_roles AS r
     WHERE r.rolname = ANY (ARRAY[
       'f1_auth_definer', 'f1_identity_read_definer',
       'f1_enterprise_create_definer', 'f1_invite_create_definer',
       'f1_invite_consume_definer', 'f1_upload_definer',
       'f1_outbox_definer', 'f1_qa_definer'
     ])
       AND (r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
            OR r.rolinherit OR r.rolreplication OR r.rolbypassrls)
  ) THEN
    RAISE EXCEPTION 'F1_DEFINER_ROLE_UNSAFE';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM pg_auth_members AS m
      JOIN pg_roles AS granted ON granted.oid = m.roleid
      JOIN pg_roles AS member ON member.oid = m.member
     WHERE granted.rolname = ANY (ARRAY[
       'f1_auth_definer', 'f1_identity_read_definer',
       'f1_enterprise_create_definer', 'f1_invite_create_definer',
       'f1_invite_consume_definer', 'f1_upload_definer',
       'f1_outbox_definer', 'f1_qa_definer'
     ])
        OR member.rolname = ANY (ARRAY[
       'f1_auth_definer', 'f1_identity_read_definer',
       'f1_enterprise_create_definer', 'f1_invite_create_definer',
       'f1_invite_consume_definer', 'f1_upload_definer',
       'f1_outbox_definer', 'f1_qa_definer'
     ])
  ) THEN
    RAISE EXCEPTION 'F1_DEFINER_ROLE_MEMBERSHIP_FORBIDDEN';
  END IF;
END
$$;
GRANT CONNECT ON DATABASE :"db_name" TO f1_api, f1_worker;
