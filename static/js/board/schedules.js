/* Board module: schedules. */

function _boardScheduleCount() {
  var scheds = (state && state.schedules) || {};
  var count = 0;
  for (var id in scheds) count++;
  return count;
}

function boardToggleSchedules() {
  _boardPrepareViewChange(true);
  _boardShowSchedules = !_boardShowSchedules;
  renderBoard();
}

function _renderSchedulesView() {
  var scheds = (state && state.schedules) || {};
  var html = '<div class="board-cards" id="board-cards">';

  // Add schedule button
  html += '<div class="board-add-task" onclick="openScheduleModal()">'
    + '<button class="board-add-btn">'
    + '+ Add schedule</button></div>';

  var list = [];
  for (var id in scheds) list.push(scheds[id]);
  list.sort(function(a, b) {
    return (a.name || '').localeCompare(b.name || '');
  });

  if (!list.length) {
    html += '<div class="board-empty">No schedules</div>';
  }

  for (var i = 0; i < list.length; i++) {
    var s = list[i];
    var enabled = s.enabled !== false;
    var cls = 'board-card board-schedule-card' + (enabled ? '' : ' dimmed');
    var trigger = s.cron_expr || s.scheduled_at || '';
    var triggerLabel = s.cron_expr ? 'cron' : 'one-shot';

    html += '<div class="' + cls + '" data-schedule-id="' + esc(s.id) + '">';

    // Header
    html += '<div class="board-card-header">';
    html += '<span class="board-card-title">' + esc(s.name || '') + '</span>';
    html += '<span class="board-card-slug">' + esc(s.slug || '') + '</span>';
    html += '</div>';

    // Trigger info
    html += '<div class="board-schedule-trigger">';
    html += '<span class="board-schedule-type">' + esc(triggerLabel) + '</span> ';
    html += '<code>' + esc(trigger) + '</code>';
    if (s.timezone) html += ' <span class="board-schedule-tz">(' + esc(s.timezone) + ')</span>';
    html += '</div>';

    // Task template
    if (s.task_template) {
      html += '<div class="board-schedule-template">' + esc(s.task_template) + '</div>';
    }

    // Action badge
    if (s.action_name) {
      html += '<div class="board-card-action">' + esc(s.action_name) + '</div>';
    }

    // Status row
    html += '<div class="board-schedule-status">';
    if (s.next_run_at && enabled) {
      html += '<span class="board-schedule-next">Next: ' + _schedFormatTime(s.next_run_at) + '</span>';
    }
    if (s.run_count) {
      html += '<span class="board-schedule-runs">' + s.run_count + ' run' + (s.run_count === 1 ? '' : 's') + '</span>';
    }
    html += '</div>';

    // Actions row
    html += '<div class="board-schedule-actions">';
    html += '<button class="board-schedule-action-btn" onclick="scheduleToggleEnabled(\'' + esc(s.id) + '\')">'
      + (enabled ? 'Disable' : 'Enable') + '</button>';
    html += '<button class="board-schedule-action-btn" onclick="scheduleRunNow(\'' + esc(s.id) + '\')">Run now</button>';
    html += '<button class="board-schedule-action-btn" onclick="openScheduleModal(\'' + esc(s.id) + '\')">Edit</button>';
    html += '<button class="board-schedule-action-btn board-schedule-delete-btn" onclick="scheduleDelete(\'' + esc(s.id) + '\')">Delete</button>';
    html += '</div>';

    html += '</div>';
  }

  html += '</div>';
  return html;
}

function _schedFormatTime(iso) {
  if (!iso) return '';
  try {
    var d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  } catch(e) {
    return iso;
  }
}

function scheduleToggleEnabled(sid) {
  var s = (state.schedules || {})[sid];
  if (!s) return;
  send({ cmd: s.enabled !== false ? 'schedule_disable' : 'schedule_enable', id: sid });
}

function scheduleRunNow(sid) {
  send({ cmd: 'schedule_run', id: sid });
}

function scheduleDelete(sid) {
  showConfirm('Delete this schedule?', function() {
    send({ cmd: 'schedule_remove', id: sid });
  });
}
