<div class="toolbar"><div class="toolbar-inner"><div class="toolbar-buttons">
	<a class="button button-secondary" href="/list/mail/security/"><?= tohtml(_("Mail Security")) ?></a>
	<a class="button button-secondary" href="/list/mail/performance/?period=<?= tohtml($period) ?>"><?= tohtml(_("Refresh")) ?></a>
</div></div></div>
<div class="container"><div class="form-container">
<h1 class="u-mb20"><?= tohtml(_("Webmail performance")) ?></h1>
<?php show_alert_message($_SESSION); ?>
<p class="u-mb20"><?= tohtml(_("Metrics cover the shared www pool, including Roundcube and other applications using it. Samples are collected every minute while monitoring is enabled; seven calendar days are retained.")) ?></p>
<?php if ($monitor !== null) {
	$config = $monitor["config"];
	$state = $monitor["state"];
	$latest = $state["latest"] ?? null;
	$stale = !$latest || $monitor["now"] - $latest["time"] > 150;
	$reasons = ["service_down" => _("PHP-FPM service unavailable"), "probe_failed" => _("Unable to query PHP-FPM metrics"), "capacity_pressure" => _("Sustained webmail capacity pressure")];
?>
<p class="u-mb10"><?= tohtml($config["enabled"] ? _("Monitoring enabled") : _("Monitoring disabled")) ?></p>
<p class="u-mb10"><?= tohtml(!$config["enabled"] ? _("Enable monitoring to begin collecting history.") : ($stale ? _("No recent samples. Check the monitoring task.") : _("Recent sample available."))) ?></p>
<?php if ($latest) { ?>
<p class="u-mb10"><?= tohtml(_("Last sample")) ?>: <?= tohtml(date("Y-m-d H:i:s", $latest["time"])) ?></p>
<?php if ($latest["error"]) { ?>
<p class="u-mb20"><?= tohtml(_("Metrics unavailable")) ?>: <?= tohtml($reasons[$latest["error"]] ?? $latest["error"]) ?></p>
<?php } else { ?>
<table class="table u-mb20"><thead><tr><th><?= tohtml(_("Metric")) ?></th><th><?= tohtml(_("Value")) ?></th></tr></thead><tbody>
<?php foreach (["active processes" => _("Active workers"), "total processes" => _("Total workers"), "listen queue" => _("Waiting requests"), "max listen queue" => _("Peak waiting requests"), "max active processes" => _("Peak active workers"), "max children reached" => _("Worker limit reached"), "accepted conn" => _("Accepted connections")] as $key => $label) { ?>
<tr><td><?= tohtml($label) ?></td><td><?= tohtml((string)$latest["metrics"][$key]) ?></td></tr>
<?php } ?></tbody></table>
<?php } } ?>
<p class="u-mb10"><?= tohtml(_("Counters reset when PHP-FPM reloads or restarts. Worker limit events alone do not prove sustained saturation.")) ?></p>
<?php if (!empty($state["notification_error"])) { ?>
<p class="u-mb10"><?= tohtml(_("The last alert could not be queued. Check Exim.")) ?></p>
<?php } ?>
<?php if (!empty($state["last_alert"])) { ?>
<p class="u-mb10"><?= tohtml(_("Last alert queued")) ?>: <?= tohtml(date("Y-m-d H:i:s", $state["last_alert"])) ?> — <?= tohtml($reasons[$state["last_alert_reason"] ?? ""] ?? "") ?></p>
<?php } ?>
<form method="get" class="u-mb20"><label for="monitor-period" class="form-label"><?= tohtml(_("History period")) ?></label>
<select id="monitor-period" name="period" class="form-select">
<?php foreach (["1" => _("Last hour"), "24" => _("Last 24 hours"), "168" => _("Last 7 days")] as $value => $label) { ?>
<option value="<?= tohtml($value) ?>" <?= $period === (string)$value ? "selected" : "" ?>><?= tohtml($label) ?></option>
<?php } ?></select><button class="button" type="submit"><?= tohtml(_("View")) ?></button></form>
<?php if (empty($monitor["history"])) { ?>
<p class="u-mb20"><?= tohtml(_("History will appear after monitoring collects samples.")) ?></p>
<?php } else { ?>
<div style="position:relative;height:300px" class="u-mb20"><canvas id="webmail-monitor-chart"></canvas></div>
<script id="webmail-monitor-data" type="application/json"><?= json_encode(["history" => $monitor["history"], "active" => _("Active workers"), "queue" => _("Waiting requests")], JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT) ?></script>
<script type="module" src="/js/webmailMonitor.js"></script>
<?php } ?>
<?php if ($read_only !== true) { ?>
<h2 class="u-text-H3 u-mb10"><?= tohtml(_("Monitoring and alerts")) ?></h2>
<form method="post" class="u-mb20">
<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
<label class="form-label"><input type="checkbox" name="enabled" value="yes" <?= $config["enabled"] ? "checked" : "" ?>> <?= tohtml(_("Enable monitoring and email alerts")) ?></label>
<label for="monitor-recipient" class="form-label"><?= tohtml(_("Alert recipient")) ?></label>
<input id="monitor-recipient" class="form-control u-mb10" type="email" name="recipient" required value="<?= tohtml($config["recipient"]) ?>">
<?php foreach (["queue_threshold" => [_('Waiting request threshold'), 1, 10000], "consecutive" => [_('Consecutive minute samples'), 2, 30], "cooldown_minutes" => [_('Minimum minutes between alerts'), 5, 1440]] as $key => $field) { ?>
<label for="monitor-<?= tohtml($key) ?>" class="form-label"><?= tohtml($field[0]) ?></label>
<input id="monitor-<?= tohtml($key) ?>" class="form-control u-mb10" name="<?= tohtml($key) ?>" type="number" required min="<?= $field[1] ?>" max="<?= $field[2] ?>" value="<?= tohtml((string)$config[$key]) ?>">
<?php } ?>
<p class="u-mb10"><?= tohtml(_("Alerts require consecutive samples of queue pressure, worker limit increases, service failure or unavailable metrics. They are queued through Exim; queuing does not confirm remote delivery.")) ?></p>
<button type="submit" class="button"><?= tohtml(_("Save")) ?></button>
</form>
<?php } } ?>
</div></div>
