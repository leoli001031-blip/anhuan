#!/bin/sh
set -eu
umask 077
# Named volumes belong to the isolated candidate project, never the default
# stack. Preflight every input before altering any runtime credential file.
for role in api ingestion worker; do
  for kind in user password; do test -s "/source/minio_${role}_${kind}"; done
done
test -s /source/f1_source_reader_password
test -s /source/f1_report_worker_password
test -s /source/f1_ingestion_worker_password
mkdir -p /source-gateway /migrator
cp /source/f1_source_reader_password /source-gateway/f1_source_reader_password
cp /source/f1_source_reader_password /migrator/f1_source_reader_password
chmod 0600 /source-gateway/f1_source_reader_password /migrator/f1_source_reader_password
mkdir -p /storage-provisioner
chmod 0700 /storage-provisioner
for role in api ingestion worker; do
  for kind in user password; do
    cp "/source/minio_${role}_${kind}" "/storage-provisioner/minio_${role}_${kind}"
    chmod 0600 "/storage-provisioner/minio_${role}_${kind}"
    if [ "$role" != worker ]; then
      destination=/api
      if [ "$role" = ingestion ]; then destination=/source-gateway; fi
      cp "/source/minio_${role}_${kind}" "$destination/minio_service_$kind"
      chmod 0600 "$destination/minio_service_$kind"
    fi
  done
done
for destination in /api /source-gateway /ingestion-worker /worker; do
  rm -f "$destination/minio_root_user" "$destination/minio_root_password"
done
for destination in /ingestion-worker /worker; do
  rm -f "$destination/minio_service_user" "$destination/minio_service_password"
done

mkdir -p /report-worker
cp /source/f1_report_worker_password /report-worker/f1_report_worker_password
cp /source/f1_report_worker_password /migrator/f1_report_worker_password
chmod 0600 /report-worker/f1_report_worker_password /migrator/f1_report_worker_password
rm -f /report-worker/f1_api_password /report-worker/f1_worker_password

cp /source/f1_ingestion_worker_password /ingestion-worker/f1_ingestion_worker_password
cp /source/f1_ingestion_worker_password /migrator/f1_ingestion_worker_password
chmod 0600 /ingestion-worker/f1_ingestion_worker_password /migrator/f1_ingestion_worker_password
rm -f /ingestion-worker/f1_api_password /ingestion-worker/f1_worker_password
