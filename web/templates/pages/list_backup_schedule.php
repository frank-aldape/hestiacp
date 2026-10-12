<div class="toolbar"><div class="toolbar-inner"><div class="toolbar-buttons">
<a class="button button-secondary" href="/list/backup/"><?= tohtml(_("Backups")) ?></a>
</div></div></div>
<div class="container"><div class="form-container">
<h1 class="u-mb20"><?= tohtml(_("Backup scheduling")) ?></h1>
<?php show_alert_message($_SESSION); ?>
<p class="u-mb20"><?= tohtml(_("Choose automatic backup frequency for each Hestia user. Manual backups remain available through Create Backup in the user's context.")) ?></p>
<?php if ($schedules !== null) {
$frequencies = ["manual" => _("Manual only"), "daily" => _("Daily"), "weekly" => _("Weekly"), "monthly" => _("Monthly")];
$weekdays = [_('Sunday'), _('Monday'), _('Tuesday'), _('Wednesday'), _('Thursday'), _('Friday'), _('Saturday')];
$statuses = ["queued" => _("Queued"), "already_scheduled" => _("Already scheduled"), "failed" => _("Failed"), "dispatching" => _("Dispatch attempted")];
?>
<p class="u-mb20"><?= tohtml(_("Schedule timezone")) ?>: <?= tohtml($schedules["timezone"]) ?></p>
<?php if (!$schedules["supported"]) { ?>
<p class="u-mb20"><?= tohtml(_("The current backup cron is custom or unsupported. Review it before using this scheduler.")) ?></p>
<?php } else { ?>
<?php if (!$schedules["managed"]) { ?>
<p class="u-mb20"><?= tohtml(_("The first save replaces the standard global backup task with per-user scheduling. Other users keep the current frequency until you change them.")) ?></p>
<?php } elseif (!$schedules["dispatcher_installed"]) { ?>
<p class="u-mb20"><?= tohtml(_("The backup scheduler task is missing. Save a schedule to restore it.")) ?></p>
<?php } ?>
<form method="get" action="/list/backup/schedule/" class="u-mb20">
<label class="form-label" for="backup-user-selector"><?= tohtml(_("Select a user to configure backups")) ?></label>
<select class="form-select u-mb10" name="backup_user" id="backup-user-selector" required>
<option value=""><?= tohtml(_("Choose a user")) ?></option>
<?php foreach ($schedules["users"] as $item) { ?>
<option value="<?= tohtml($item["user"]) ?>" <?= $selected_backup_user === $item["user"] ? "selected" : "" ?>><?= tohtml($item["user"] . " — " . $frequencies[$item["policy"]["frequency"]]) ?></option>
<?php } ?>
</select>
<button type="submit" class="button"><?= tohtml(_("Configure")) ?></button>
</form>
<?php if ($backup_account !== null) {
$account = $backup_account;
$policy = $account["policy"];
?>
<h2 class="u-text-H3 u-mb10"><?= tohtml($account["user"]) ?> — <?= tohtml($frequencies[$policy["frequency"]]) ?></h2>
<p class="u-mb10"><?= tohtml(_("Configured backup retention")) ?>: <?= tohtml($account["retention"] ?: "0") ?></p>
<?php if ($account["suspended"] || in_array($account["retention"], ["0", ""], true)) { ?>
<p class="u-mb10"><?= tohtml(_("Automatic backups are skipped for suspended users or users with backups disabled in their package.")) ?></p>
<?php } ?>
<?php if ($account["last_attempt"]) { ?>
<p class="u-mb10"><?= tohtml(_("Last scheduling attempt")) ?>: <?= tohtml($account["last_attempt"]["slot"]) ?> — <?= tohtml($statuses[$account["last_attempt"]["status"]] ?? $account["last_attempt"]["status"]) ?></p>
<?php } ?>
<?php if ($read_only !== true) { ?>
<form method="post" class="u-mb20" x-data="{ frequency: '<?= tohtml($policy["frequency"]) ?>' }">
<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
<input type="hidden" name="backup_user" value="<?= tohtml($account["user"]) ?>">
<label class="form-label" for="frequency-<?= tohtml($account["user"]) ?>"><?= tohtml(_("Frequency")) ?></label>
<select class="form-select u-mb10" name="frequency" x-model="frequency" id="frequency-<?= tohtml($account["user"]) ?>">
<?php foreach ($frequencies as $value => $label) { ?>
<option value="<?= tohtml($value) ?>" <?= $policy["frequency"] === $value ? "selected" : "" ?>><?= tohtml($label) ?></option>
<?php } ?></select>
<div x-show="frequency !== 'manual'">
<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px">
<?php foreach (["hour" => [_('Hour'), 0, 23], "minute" => [_('Minute'), 0, 59]] as $key => $field) { ?>
<div><label class="form-label" for="<?= tohtml($key . '-' . $account["user"]) ?>"><?= tohtml($field[0]) ?></label>
<input class="form-control u-mb10" type="number" required name="<?= tohtml($key) ?>" min="<?= $field[1] ?>" max="<?= $field[2] ?>" id="<?= tohtml($key . '-' . $account["user"]) ?>" value="<?= tohtml((string)$policy[$key]) ?>">
</div><?php } ?></div>
<div x-show="frequency === 'monthly'">
<label class="form-label" for="monthly-day"><?= tohtml(_("Monthly day (1–28)")) ?></label>
<input class="form-control u-mb10" type="number" required name="day" id="monthly-day" min="1" max="28" value="<?= tohtml((string)$policy["day"]) ?>">
</div>
<div x-show="frequency === 'weekly'">
<label class="form-label" for="weekday-<?= tohtml($account["user"]) ?>"><?= tohtml(_("Weekly day")) ?></label>
<select class="form-select u-mb10" name="weekday" id="weekday-<?= tohtml($account["user"]) ?>">
<?php foreach ($weekdays as $value => $label) { ?>
<option value="<?= $value ?>" <?= $policy["weekday"] === $value ? "selected" : "" ?>><?= tohtml($label) ?></option>
<?php } ?></select>
</div></div>
<p class="u-mb10"><?= tohtml(_("Hour and minute apply to automatic schedules. Weekly day and monthly day apply only to their respective frequencies. Existing backups and queued jobs are retained.")) ?></p>
<button class="button" type="submit"><?= tohtml(_("Save")) ?></button>
</form>
<?php } else { ?>
<p class="u-mb20"><?= tohtml(sprintf(_("Scheduled time: %02d:%02d"), $policy["hour"], $policy["minute"])) ?></p>
<?php } } } } ?>
</div></div>
