<div class="toolbar">
	<div class="toolbar-inner">
		<div class="toolbar-buttons">
			<a class="button button-secondary button-back js-button-back" href="/list/mail/">
				<i class="fas fa-arrow-left icon-blue"></i><?= tohtml(_("Back")) ?>
			</a>
		</div>
	</div>
</div>

<div class="container">
	<div class="form-container">
		<h1 class="u-mb20"><?= tohtml(_("Mail Security")) ?></h1>
		<?php show_alert_message($_SESSION); ?>
		<div class="alert alert-info u-mb20" role="alert">
			<i class="fas fa-circle-info"></i>
			<div><?= tohtml(_("These settings apply to all mail domains on this server. Domain-specific protection is managed from each mail domain's edit page.")) ?></div>
		</div>

		<h2 class="u-text-H3 u-mb10"><?= tohtml(_("Domain protection")) ?></h2>
		<p class="u-mb10"><?= tohtml(_("Manage spam filtering, spam rejection, antivirus, DKIM, certificates and sending limits for each domain.")) ?></p>
		<a class="button button-secondary u-mb20" href="/list/mail/">
			<i class="fas fa-envelope icon-blue"></i><?= tohtml(_("Mail Domains")) ?>
		</a>
		<a class="button button-secondary u-mb20" href="/list/user/">
			<i class="fas fa-users icon-blue"></i><?= tohtml(_("Users")) ?>
		</a>
		<p class="u-mb20"><?= tohtml(_("Select the domain owner from Users to manage another user's mail domains.")) ?></p>

		<h2 class="u-text-H3 u-mb10"><?= tohtml(_("DNS blocklists (DNSBL)")) ?></h2>
		<p class="u-mb20"><?= tohtml(_("Exim checks these lists when receiving mail. Listed sending IP addresses may be rejected before domain spam filtering. Removing an entry removes that reputation check for all domains.")) ?></p>
		<?php if (!$v_dnsbl_supported) { ?>
			<p class="u-mb20"><?= tohtml(_("DNSBL management requires Exim.")) ?></p>
		<?php } elseif ($v_dnsbl_loaded) { ?>
			<?php if (empty($v_dnsbl_hosts)) { ?>
				<p class="u-mb20"><?= tohtml(_("No DNSBL entries are configured.")) ?></p>
			<?php } else { ?>
				<ul class="values-list u-mb20">
					<?php foreach ($v_dnsbl_hosts as $host) { ?>
						<li class="values-list-item">
							<span class="values-list-label"><?= tohtml($host) ?></span>
							<?php if ($read_only !== true) { ?>
								<form method="post" action="/list/mail/security/" x-data @submit="if (!confirm($el.dataset.confirmMessage)) $event.preventDefault()" data-confirm-message="<?= tohtml(sprintf(_("Remove DNSBL %s for all mail domains?"), $host)) ?>">
									<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
									<input type="hidden" name="action" value="delete">
									<input type="hidden" name="host" value="<?= tohtml($host) ?>">
									<button type="submit" class="button button-secondary">
										<i class="fas fa-trash icon-red"></i><?= tohtml(_("Delete")) ?>
									</button>
								</form>
							<?php } ?>
						</li>
					<?php } ?>
				</ul>
			<?php } ?>
			<?php if ($read_only !== true) { ?>
				<form method="post" action="/list/mail/security/" class="u-mb20">
					<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
					<input type="hidden" name="action" value="add">
					<label for="dnsbl-host" class="form-label"><?= tohtml(_("DNSBL entry")) ?></label>
					<input id="dnsbl-host" class="form-control u-mb10" name="host" type="text" value="<?= tohtml($v_dnsbl_host) ?>" placeholder="dnsbl.example.net" required>
					<p class="u-mb10"><?= tohtml(_("Enter the provider's DNSBL zone, optionally followed by its existing != response-code exclusions. Saving a change reloads the mail service; if reload is unavailable, the service may restart.")) ?></p>
					<button type="submit" class="button">
						<i class="fas fa-circle-plus icon-green"></i><?= tohtml(_("Add DNSBL")) ?>
					</button>
				</form>
			<?php } ?>
		<?php } ?>

		<h2 class="u-text-H3 u-mb10"><?= tohtml(_("Advanced configuration")) ?></h2>
		<p class="u-mb10"><?= tohtml(_("Use the existing service editors for global spam thresholds, sender exceptions and mail rules. Review changes before applying them.")) ?></p>
		<?php if ($read_only !== true) { ?>
		<?php foreach (["MAIL_SYSTEM" => _("Mail Server"), "ANTISPAM_SYSTEM" => _("Spam Filter"), "ANTIVIRUS_SYSTEM" => _("Anti-Virus")] as $setting => $label) { ?>
			<?php if (!empty($_SESSION[$setting])) { ?>
				<a class="button button-secondary u-mb10" href="/edit/server/<?= tohtml(rawurlencode($_SESSION[$setting])) ?>/">
					<i class="fas fa-pencil icon-orange"></i><?= tohtml($label) ?>
				</a>
			<?php } ?>
		<?php } ?>
		<?php } ?>
	</div>
</div>
