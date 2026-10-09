# Versioned mail security configuration

The fork stores the source for two site configuration changes:

- `install/deb/spamassassin/zz-validity-disabled.cf` disables six Validity rules affected by DNS query-limit responses. It does not change the spam threshold or other reputation checks.
- `install/deb/exim/system-notifications.router` redirects only `root`, `postmaster`, and `mailer-daemon` at `$primary_hostname`. The destination is supplied during preparation.

The installed configuration in `/etc` remains specific to the server. Notification addresses, existing relay/SRS configuration, credentials, backups, and generated manifests are not committed to Git. A source commit or package build does not apply these changes to `/etc`. The package includes these preparation assets under `/usr/local/hestia/install`; application remains explicit.

Validity's original query-limit response could trigger both negative reputation rules, incorrectly subtracting five points. The scores here follow the [current upstream defaults](https://github.com/apache/spamassassin/blob/trunk/rules/50_scores.cf). A [zero score disables the corresponding rule](https://spamassassin.apache.org/full/4.0.x/doc/Mail_SpamAssassin_Conf.html). Without an authorized Validity subscription, these rules must not grant reputation based on a quota error.

## Prepare on an existing VPS

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
