# Versioned mail security configuration

The fork stores the source for two site configuration changes:

- `install/deb/spamassassin/zz-validity-disabled.cf` disables six Validity rules affected by DNS query-limit responses. It does not change the spam threshold or other reputation checks.
- `install/deb/exim/system-notifications.router` redirects only `root`, `postmaster`, and `mailer-daemon` at `$primary_hostname`. The destination is supplied during preparation.

The installed configuration in `/etc` remains specific to the server. Notification addresses, existing relay/SRS configuration, credentials, backups, and generated manifests are not committed to Git. A source commit or package build does not apply these changes to `/etc`. The package includes these preparation assets under `/usr/local/hestia/install`; application remains explicit.

Validity's original query-limit response could trigger both negative reputation rules, incorrectly subtracting five points. The scores here follow the [current upstream defaults](https://github.com/apache/spamassassin/blob/trunk/rules/50_scores.cf). A [zero score disables the corresponding rule](https://spamassassin.apache.org/full/4.0.x/doc/Mail_SpamAssassin_Conf.html). Without an authorized Validity subscription, these rules must not grant reputation based on a quota error.

## Prepare on an existing VPS

### Administration from the panel

After installing a package containing the web settings commands, sign in as an administrator (without impersonating another user), then open **Mail → Mail Security**, or `/list/mail/security/`. The page reads the existing site configuration; it does not require an administrator-owned mail domain.

**Server notifications** displays the existing destination and allows it to be changed. Saving redirects only the three system addresses at Exim's primary hostname. It preserves other routers, relays and SRS macros, validates the candidate and its routing, and reloads Exim. Destinations at the server hostname are rejected to avoid a notification loop. Routing validation does not prove remote mailbox acceptance; check an actual notification after changing the destination.

**Validity reputation checks** reports whether the local correction file contains the six zero scores. If it is missing, an administrator can apply the correction. This validates SpamAssassin and reloads/restarts `spamd`. It does not provide a toggle to enable unlicensed reputation checks. Custom override files and custom notification routers require review in the existing advanced editors and are never overwritten by these controls. File status does not prove that another SpamAssassin configuration file has not overridden those scores; an incoming message report remains the end-to-end verification.

The privileged commands are `v-list-sys-mail-security json`, `v-change-sys-mail-security notifications EMAIL` and `v-change-sys-mail-security validity-disable`. Live paths are fixed, updates are serialized by a lock, and changes are validated before atomic replacement. Configuration/reload failures restore the prior file; a failed recovery is reported as an error. Backups and a change manifest are retained in `/root/hestia-mail-security/change-*`. Successful changes are written to Hestia's action log; failures are written to its event log. Read-only and impersonated sessions cannot apply changes.

### Preparation outside the panel

Run as root from the fork checkout. Set `DESTINO` to an existing monitored mailbox on a different domain from the server hostname. Do not use an address that forwards back to the server's system addresses.

```bash
DESTINO='notificaciones@example.org'
PREPARACION=$(mktemp -d /root/mail-security-XXXXXXXX)

python3 install/common/mail_security_config.py \
  --notification-email "$DESTINO" \
  --output-dir "$PREPARACION"
```

Preparation reads and backs up the current Exim wrapper, its included template, and any existing Validity override. It creates candidates without altering live configuration, sending mail, or reloading services. It preserves relay/SRS macros and all other routers. An existing notification router is accepted only if it has the same supported options; other customizations require manual review. Reusing a nonempty preparation directory is rejected to protect backups.

The supported layout has one direct `.include /etc/exim4/exim4.conf.template` in `/etc/exim4/exim4.conf`. Alternate paths can be passed with `--active-config`, `--template-config`, and `--antispam-config`. Other include layouts fail closed.

Review the changes and validate the effective configuration before applying:

```bash
diff -u "$PREPARACION/template-anterior" "$PREPARACION/template-candidata" || test "$?" -eq 1
exim4 -C "$PREPARACION/config-candidata" -bP primary_hostname
exim4 -bt "$DESTINO"
NOMBRE_SERVIDOR=$(exim4 -C "$PREPARACION/config-candidata" -bP primary_hostname | sed 's/^primary_hostname = //')

for localpart in root postmaster mailer-daemon; do
  exim4 -C "$PREPARACION/config-candidata" -bt "$localpart@$NOMBRE_SERVIDOR"
done

spamassassin --lint --cf="$(cat "$PREPARACION/zz-validity-disabled.cf")"
```

The routing tests must finish at the selected mailbox, with no SMTP hop back to a loopback address. Testing each address in a separate process avoids the duplicate-destination annotation from testing all three together. `-bt` validates routing; it does not prove that a remote mailbox exists or will accept a message.

## Apply the validated candidates

These commands use the default paths. The wrapper stays unchanged and continues to include the original template path. Exim's template replacement is atomic and preserves the existing owner and permissions. The commands refuse to replace configuration changed since preparation.

```bash
(
set -e
: "${PREPARACION:?Set PREPARACION to the reviewed preparation directory}"
PLANTILLA=/etc/exim4/exim4.conf.template
REGLAS=/etc/mail/spamassassin/zz-validity-disabled.cf

cmp -s /etc/exim4/exim4.conf "$PREPARACION/config-anterior"
cmp -s "$PLANTILLA" "$PREPARACION/template-anterior"
if [ -f "$PREPARACION/validity-anterior" ]; then
  cmp -s "$REGLAS" "$PREPARACION/validity-anterior"
else
  test ! -e "$REGLAS"
fi

exim4 -C "$PREPARACION/config-candidata" -bP primary_hostname
spamassassin --lint --cf="$(cat "$PREPARACION/zz-validity-disabled.cf")"

EXIM_CAMBIADO=false
SPAM_CAMBIADO=false
TEMPORAL=$(mktemp /etc/exim4/.mail-security-XXXXXXXX)
trap 'rm -f "$TEMPORAL"' EXIT
restaurar() {
  trap - ERR
  if [ "$EXIM_CAMBIADO" = true ]; then
    cp -p "$PREPARACION/template-anterior" "$PLANTILLA"
  fi
  if [ "$SPAM_CAMBIADO" = true ]; then
    if [ -f "$PREPARACION/validity-anterior" ]; then
      cp -p "$PREPARACION/validity-anterior" "$REGLAS"
    else
      rm -f "$REGLAS"
    fi
  fi
  systemctl reload exim4 || true
  systemctl reload-or-restart spamd || true
  echo "Restored previous configuration; inspect service status."
  exit 1
}
trap restaurar ERR

cp -p "$PLANTILLA" "$TEMPORAL"
cat "$PREPARACION/template-candidata" > "$TEMPORAL"
mv -f "$TEMPORAL" "$PLANTILLA"
EXIM_CAMBIADO=true
SPAM_CAMBIADO=true
install -o root -g root -m 644 "$PREPARACION/zz-validity-disabled.cf" "$REGLAS"

exim4 -bP primary_hostname
spamassassin --lint
systemctl reload exim4
systemctl reload-or-restart spamd
systemctl is-active exim4 dovecot spamd
trap - ERR
echo "Applied. Backups: $PREPARACION"
)
```

This example uses Ubuntu's `spamd.service`. Adjust the service name if the installation uses a different SpamAssassin unit. Reloading/restarting spamd can briefly interrupt its availability. It does not restart Exim or Dovecot. No queued messages are deleted or retried by this procedure.

## Verify and retain

Check one new system notification at the selected destination and an ordinary inbound message at a hosted mailbox. Its spam report should retain other rules and omit the disabled Validity scores. A local GTUBE scan should still trigger the GTUBE rule above the configured threshold; it does not test Exim's per-domain reject/folder policy.

Test actual authenticated sending and external replies, and inspect the recipient's SPF/DKIM/DMARC results. An empty INBOX metadata query is not a representative IMAP performance benchmark.

Keep the preparation directory outside Git. Before future upgrades, verify that the local router and override remain present. Re-run preparation against the current configuration if reapplication is required; never replace the complete Exim configuration with the repository default, because it may discard site relay/SRS customizations. This change does not install update hooks or promise automatic reapplication.

The source and runtime configuration are tracked separately: `/etc/hestiacp/fork-deployment.json` records the installed Hestia package commit, while each preparation directory records hashes of the prior site files in `manifest.json`.

## Recovery check

`test/mail_backup_restore.sh` restores a saved SQL gzip into a new disposable Linux container, without published ports, application networks or production volumes. It limits each container to half a CPU and 1 GiB (PostgreSQL) or 512 MiB (MariaDB), runs the two checks separately, and removes only its own test container and anonymous volumes. Images and private logs remain available. Allow enough disk space for the restored databases and images; the October 8 PostgreSQL dump expands to about 924 MB of SQL, before database/index/WAL overhead.

The current procedure is specific to the saved PostgreSQL 16.13 and MariaDB 11.4.13 dumps. The PostgreSQL initial role is `rap_admin`, matching the dump's `GRANTED BY` clauses. Only its duplicate `CREATE ROLE rap_admin;` is skipped; its attributes, passwords and role grants are restored. Linux locale `en_US.utf8` must be available. The native macOS trial failed on grantor identity and then on the Linux locale and therefore does not count as a completed restore.

Run as root on a Linux host with Docker after copying the saved dumps into a private directory:

```bash
bash test/mail_backup_restore.sh postgresql /root/hestia-restore-dumps/postgresql.sql.gz
bash test/mail_backup_restore.sh mariadb /root/hestia-restore-dumps/mariadb.sql.gz
```

The script stops on SQL errors, checks the expected databases and inventories their tables. This is a SQL recovery check, not an application startup test, mailbox-file recovery or comparison with current live row counts. Actual Linux restore results must be checked before declaring recovery verified. Logs under `/root/hestia-restore-check-*` may contain SQL details and must remain private and outside Git.

## Dependency audit: October 9, 2026

The original lockfile reproduced 14 npm audit findings: 9 high, 2 moderate and 3 low. Compatible lockfile updates plus explicit development-tool overrides for `smol-toml` and `katex` reduced the audit to 5 high findings, all in the development chain `markdownlint-cli2 → micromatch/globby/fast-glob → braces`. The audit with `--omit=dev` reports zero findings after the changes. This npm classification alone is not a proof of runtime exploitability: the normal panel bundles are produced by `build.js`, and several packages classified as production dependencies belong to the documentation/UI dependency tree.

`source-map-js` was the one original finding remaining with `--omit=dev`; it is updated from 1.2.1 to 1.2.2, the [patched version](https://github.com/advisories/GHSA-68fv-2mgg-jv7q). Other resolved findings include `undici`, `brace-expansion`, `js-yaml`, `markdown-it`, `smol-toml` and `katex`. The overrides are tested with the actual Markdown math renderer and TOML parser, and the full panel JS/CSS build passes. Node packages and scripts were installed only in a temporary verification checkout while evaluating the changes.

`braces` 3.0.3 is still the latest published version at audit time and remains [affected](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm). npm proposes a downgrade of `markdownlint-cli2` across its declared version range; this was not applied. Avoid running those development tools on untrusted input and review the upstream patch when available. The dependency correction is source-only until the next package is built and installed; it does not modify the VPS's installed fork5 package.
