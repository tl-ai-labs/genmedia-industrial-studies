/**
 * GenMedia blind panel — votes in a Google Sheet.
 *
 * This is the whole backend (Sai, 2026-09-11: "simple and free"). Bound to
 * one spreadsheet with two tabs:
 *
 *   key    item | lane | run_id | scenario_id | media | model     (private: the blind)
 *   votes  ts | reviewer | lane | run_id | scenario_id | picked | over | reason
 *
 * The studies console posts a vote as the reviewer saw it (two opaque media
 * ids and a side); THIS script looks the models up in `key` and appends the
 * resolved row. No model name reaches a reviewer: `?action=votes` hands back
 * media ids only. `?action=results` names models — it is for the hidden
 * results page and the study team.
 *
 * Deploy: Extensions → Apps Script → paste → Deploy → New deployment →
 * Web app → Execute as: Me, Who has access: Anyone → copy the URL into the
 * console's VITE_PANEL_URL. Redeploy (New version) after editing this file.
 *
 * Apps Script cannot set an HTTP status, so an error is a 200 whose body is
 * {"error": "..."}; the console treats that as a failure. A POST must be
 * sent with no custom headers (text/plain body) so the browser skips the
 * CORS preflight that Apps Script cannot answer.
 */
var KEY_SHEET = 'key';
var VOTES_SHEET = 'votes';
var VOTE_COLS = ['ts', 'reviewer', 'lane', 'run_id', 'scenario_id', 'picked', 'over', 'reason'];
var REVIEWER_RE = /^[A-Za-z0-9 ._@+-]{1,64}$/;
var REASON_MAX = 500;

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function str_(v) {
  if (v === null || v === undefined) return '';
  if (v instanceof Date) return v.toISOString();
  return String(v);
}

/** The key tab as {itemId: {lane, run_id, scenario_id, media: {mediaId: model}}}. */
function loadKey_() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(KEY_SHEET);
  if (!sh) throw new Error('no "' + KEY_SHEET + '" tab: import key.csv (python -m runner.cli sheet-export)');
  var rows = sh.getDataRange().getValues();
  var items = {};
  for (var i = 1; i < rows.length; i++) {
    var r = rows[i].map(str_);
    if (!r[0]) continue;
    var it = items[r[0]] || (items[r[0]] = { lane: r[1], run_id: r[2], scenario_id: r[3], media: {} });
    it.media[r[4]] = r[5];
  }
  return items;
}

function byScenario_(items) {
  var m = {};
  for (var id in items) {
    var it = items[id];
    m[it.lane + '|' + it.run_id + '|' + it.scenario_id] = id;
  }
  return m;
}

function votesSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(VOTES_SHEET);
  if (!sh) {
    sh = ss.insertSheet(VOTES_SHEET);
    sh.appendRow(VOTE_COLS);
    sh.setFrozenRows(1);
  }
  // Timestamps stay text; Sheets would otherwise turn an ISO string into a date.
  sh.getRange('A:A').setNumberFormat('@');
  return sh;
}

function readVotes_() {
  var rows = votesSheet_().getDataRange().getValues();
  var out = [];
  for (var i = 1; i < rows.length; i++) {
    var r = rows[i];
    if (!str_(r[0])) continue;
    var v = {};
    for (var j = 0; j < VOTE_COLS.length; j++) {
      var s = str_(r[j]);
      v[VOTE_COLS[j]] = s === '' ? null : s;
    }
    out.push(v);
  }
  return out;
}

function doPost(e) {
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    var reviewer = String(body.reviewer || '').trim();
    if (!REVIEWER_RE.test(reviewer)) return json_({ error: 'reviewer id must be 1-64 plain characters' });
    var items = loadKey_();
    var item = items[String(body.item || '')];
    if (!item) return json_({ error: 'unknown item' });
    var left = String(body.left || ''), right = String(body.right || '');
    if (left === right || !(left in item.media) || !(right in item.media)) return json_({ error: "left/right are not this item's pair" });
    var pick = String(body.pick || '');
    if (['left', 'right', 'tie'].indexOf(pick) < 0) return json_({ error: 'pick must be left, right or tie' });
    var reason = String(body.reason || '').trim();
    if (reason.length > REASON_MAX) return json_({ error: 'reason must be at most ' + REASON_MAX + ' characters' });
    var picked = pick === 'tie' ? '' : item.media[pick === 'left' ? left : right];
    var over = pick === 'tie' ? '' : item.media[pick === 'left' ? right : left];
    var row = [new Date().toISOString(), reviewer, item.lane, item.run_id, item.scenario_id, picked, over, reason];
    var lock = LockService.getScriptLock();
    lock.waitLock(10000);
    try { votesSheet_().appendRow(row); } finally { lock.releaseLock(); }
    return json_({ ok: true, scenario_id: item.scenario_id, picked: picked || null });
  } catch (err) {
    return json_({ error: String((err && err.message) || err) });
  }
}

function doGet(e) {
  var p = (e && e.parameter) || {};
  var action = p.action || 'health';
  try {
    if (action === 'health') return json_({ ok: true, items: Object.keys(loadKey_()).length });
    if (action === 'votes') {
      var reviewer = String(p.reviewer || '').trim();
      if (!REVIEWER_RE.test(reviewer)) return json_({ error: 'bad reviewer id' });
      var items = loadKey_(), bySc = byScenario_(items), picks = {};
      readVotes_().forEach(function (v) {
        if (v.reviewer !== reviewer) return;
        var id = bySc[v.lane + '|' + v.run_id + '|' + v.scenario_id];
        if (!id) return;
        var media = null;
        if (v.picked) for (var m in items[id].media) if (items[id].media[m] === v.picked) media = m;
        picks[id] = { media: media, reason: v.reason };          // last row wins
      });
      return json_({ reviewer: reviewer, items: Object.keys(picks), picks: picks });
    }
    if (action === 'results') {
      var last = {};
      readVotes_().forEach(function (v) { last[[v.reviewer, v.lane, v.run_id, v.scenario_id].join('|')] = v; });
      var votes = Object.keys(last).map(function (k) {
        var v = last[k];
        return { ts: v.ts, reviewer: v.reviewer, lane: v.lane, scenario_id: v.scenario_id, picked: v.picked, over: v.over, reason: v.reason };
      }).sort(function (a, b) { return a.ts < b.ts ? 1 : -1; });
      return json_({ votes: votes, total: votes.length });
    }
    return json_({ error: 'no such action' });
  } catch (err) {
    return json_({ error: String((err && err.message) || err) });
  }
}
