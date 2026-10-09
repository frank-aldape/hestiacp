#!/bin/bash
# Restore a database dump in a disposable, isolated Linux container.
# Usage: bash test/mail_backup_restore.sh postgresql|mariadb /path/to/dump.sql.gz
set -euo pipefail
umask 077

motor=${1:-}
respaldo=${2:-}
if [ "$#" -ne 2 ] || [[ ! "$motor" =~ ^(postgresql|mariadb)$ ]] || [ ! -s "$respaldo" ]; then
	echo "Usage: $0 postgresql|mariadb /path/to/dump.sql.gz" >&2
	exit 2
fi
gzip -t -- "$respaldo"
command -v docker >/dev/null
directorio=$(mktemp -d /root/hestia-restore-check-XXXXXXXX)
contenedor="hestia-restore-check-$motor-$$"
creado=false
limpiar() {
	resultado=$?
	if [ "$creado" = true ]; then
		docker logs "$contenedor" > "$directorio/container.log" 2>&1 || true
		docker rm -fv "$contenedor" >/dev/null || true
	fi
	echo "Result=$resultado; private logs: $directorio"
}
trap limpiar EXIT

if [ "$motor" = postgresql ]; then
	# Match the dump's grantor and locale; no changes to data or permissions.
	# CREATE ROLE rap_admin is omitted only because initdb already created it.
	if [ "$(gzip -dc -- "$respaldo" | awk '$0 == "CREATE ROLE rap_admin;" { n++ } END { print n+0 }')" != 1 ]; then
		echo "Unsupported bootstrap role; review the dump before restoring." >&2
		exit 2
	fi
	export POSTGRES_PASSWORD
	POSTGRES_PASSWORD=$(openssl rand -hex 24)
	docker create --name "$contenedor" --network none --memory 1g --cpus 0.5 \
		-e POSTGRES_PASSWORD -e POSTGRES_USER=rap_admin -e POSTGRES_DB=postgres \
		-e POSTGRES_INITDB_ARGS='--locale=en_US.utf8' \
		postgres:16.13-bookworm -c shared_buffers=64MB -c max_connections=10 \
		> "$directorio/container-id"
	creado=true
	docker start "$contenedor" >/dev/null
	listo=false
	for intento in {1..120}; do
		# Require the final server, after initdb's temporary server has stopped.
		if docker exec "$contenedor" sh -c 'test "$(cat /proc/1/comm)" = postgres' && \
			docker exec "$contenedor" pg_isready -U rap_admin -d postgres >/dev/null 2>&1; then
			listo=true
			break
		fi
		sleep 1
	done
	[ "$listo" = true ] || exit 1
	gzip -dc -- "$respaldo" | sed '/^CREATE ROLE rap_admin;$/d' | \
		docker exec -i "$contenedor" psql -X -v ON_ERROR_STOP=1 -U rap_admin -d postgres \
		> "$directorio/restore.log" 2>&1
	docker exec "$contenedor" psql -X -U rap_admin -d postgres -Atc \
		'SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname;' \
		| tee "$directorio/databases.txt"
	for base in chatwoot_production flows_db flows_db_dev jobbly_db legal_db orquestador_db rap_admin teknoroma_db; do
		docker exec "$contenedor" psql -X -v ON_ERROR_STOP=1 -U rap_admin -d "$base" -Atc \
			"SELECT current_database(), count(*) FROM pg_class WHERE relkind IN ('r','p') AND relnamespace IN (SELECT oid FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema'));" \
			| tee -a "$directorio/tables.txt"
	done
else
	# No TCP listener and no scheduled events during the isolated restore.
	docker create --name "$contenedor" --network none --memory 512m --cpus 0.5 \
		-e MARIADB_ALLOW_EMPTY_ROOT_PASSWORD=1 mariadb:11.4.13 \
		--skip-networking --event-scheduler=OFF > "$directorio/container-id"
	creado=true
	docker start "$contenedor" >/dev/null
	listo=false
	for intento in {1..120}; do
		if docker exec "$contenedor" sh -c 'test "$(cat /proc/1/comm)" = mariadbd' && \
			docker exec "$contenedor" mariadb -u root -e 'SELECT 1;' >/dev/null 2>&1; then
			listo=true
			break
		fi
		sleep 1
	done
	[ "$listo" = true ] || exit 1
	gzip -dc -- "$respaldo" | docker exec -i "$contenedor" mariadb -u root \
		> "$directorio/restore.log" 2>&1
	[ "$(docker exec "$contenedor" mariadb -u root --batch --skip-column-names -e \
		"SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN ('mysql','phpmyadmin','roundcube');")" = 3 ]
	docker exec "$contenedor" mariadb -u root --batch -e \
		"SHOW DATABASES; SELECT TABLE_SCHEMA, COUNT(*) AS tables_count FROM information_schema.TABLES WHERE TABLE_SCHEMA IN ('mysql','phpmyadmin','roundcube') GROUP BY TABLE_SCHEMA;" \
		| tee "$directorio/tables.txt"
fi
echo "Isolated restore completed; application behavior is not tested by this check."
