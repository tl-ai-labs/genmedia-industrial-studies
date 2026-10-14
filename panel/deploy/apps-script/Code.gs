/**
 * GenMedia blind panel — votes in a Google Sheet.
 *
 * This is the whole backend (Sai, 2026-09-11: "simple and free"; chosen for
 * the panel on 2026-10-02). Bound to one spreadsheet:
 *
 *   key        item | lane | run_id | scenario_id | media | model     (private: the blind)
 *   votes      ts | reviewer | lane | run_id | scenario_id | picked | over | reason
 *   settings   results_passcode | <the passcode>                      (optional)
 *   reviewers  one name per row in column A                           (optional)
 *
 * The reviewer page posts a vote as the reviewer saw it (two opaque media
 * ids and a side); THIS script looks the models up in `key` and appends the
 * resolved row. No model name reaches a reviewer: a vote is answered with
 * {"ok": true} and nothing else, and `?action=votes` hands back media ids
 * only. The web app's URL is in the reviewer page's bundle, so anything it
 * returns freely is public; the results, which name models and reviewers,
 * are given only for the passcode in `settings`. Without that tab there are
 * none.
 *
 * A reviewer is a name. By default any name may vote, which also means
 * anyone holding the URL can read or overwrite a named reviewer's picks. A
 * `reviewers` tab turns that into a list: only the names in it are served.
 *
 * Deploy: open the script editor FROM THE SHEET (Extensions → Apps Script) →
 * paste → Deploy → New deployment → Web app → Execute as: Me, Who has
 * access: Anyone → copy the URL (the form https://script.google.com/macros/
 * s/<id>/exec) into the dashboard's VITE_PANEL_URL. After editing this file:
 * Deploy → Manage deployments → Edit → Version: New version, which keeps the
 * URL. "New deployment" makes a different URL.
 *
 * Apps Script cannot set an HTTP status, so an error is a 200 whose body is
 * {"error": "..."}; the page treats that as a failure. A POST must be sent
 * with no custom headers (text/plain body) so the browser skips the CORS
 * preflight that Apps Script cannot answer.
 *
 * Do not sort or clear the votes tab in place. To remove test votes, delete
 * rows 2 downward, or delete the whole tab (it is recreated).
 */
var KEY_SHEET = 'key';
var VOTES_SHEET = 'votes';
var SETTINGS_SHEET = 'settings';
var REVIEWERS_SHEET = 'reviewers';
var VOTE_COLS = ['ts', 'reviewer', 'lane', 'run_id', 'scenario_id', 'picked', 'over', 'reason'];
// A letter or digit first: a name can then never begin a formula, and the
// same name is the same reviewer in this script, on the page and in the
// correlation.
var REVIEWER_RE = /^[A-Za-z0-9][A-Za-z0-9 ._@+-]{0,63}$/;
var REASON_MAX = 500;
/** Far above 60 reviewers x 283 pairs: past this, something is flooding. */
var MAX_VOTE_ROWS = 60000;

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function str_(v) {
  if (v === null || v === undefined) return '';
  if (v instanceof Date) return v.toISOString();
  return String(v);
}

function has_(obj, k) {
  return Object.prototype.hasOwnProperty.call(obj, k);
}

/** A name as it is stored and compared: trimmed, one space between words,
 *  lower case. The page sends it this way already; a request made by hand
 *  must not become a second "Asha" beside "asha". */
function reviewer_(v) {
  return String(v || '').trim().replace(/\s+/g, ' ').toLowerCase();
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
    var it = has_(items, r[0]) ? items[r[0]] : (items[r[0]] = { lane: r[1], run_id: r[2], scenario_id: r[3], media: {} });
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

/** May this name vote and read its picks? Yes, unless a `reviewers` tab
 *  exists and the name is not in its first column. */
function allowed_(reviewer) {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(REVIEWERS_SHEET);
  if (!sh) return true;
  var rows = sh.getDataRange().getValues();
  for (var i = 0; i < rows.length; i++) if (reviewer_(str_(rows[i][0])) === reviewer) return true;
  return false;
}

/** The votes tab, made if missing, with its header. Called only when a vote
 *  is being written, under the lock: a read never creates or changes it. */
function votesSheetForWrite_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(VOTES_SHEET);
  if (!sh) sh = ss.insertSheet(VOTES_SHEET);
  // A tab that was cleared, not deleted, has no header: put it back, or the
  // first vote would take its place and be skipped ever after.
  if (sh.getLastRow() === 0) {
    sh.getRange(1, 1, 1, VOTE_COLS.length).setNumberFormat('@').setValues([VOTE_COLS]);
    sh.setFrozenRows(1);
  }
  return sh;
}

/** A reviewer's own words, safe to put in a cell. A value starting with one
 *  of = + - @ would be run as a formula in the study team's Sheet; a leading
 *  apostrophe makes Sheets keep it as the text it is. */
function text_(s) {
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

/** Undo text_ on the way out, whether or not Sheets kept the apostrophe. */
function untext_(s) {
  return /^'[=+\-@]/.test(s) ? s.slice(1) : s;
}

function readVotes_() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(VOTES_SHEET);
  if (!sh) return [];
  var rows = sh.getDataRange().getValues();
  var out = [];
  for (var i = 1; i < rows.length; i++) {
    var r = rows[i];
    if (!str_(r[0])) continue;
    var v = {};
    for (var j = 0; j < VOTE_COLS.length; j++) {
      var s = str_(r[j]);
      v[VOTE_COLS[j]] = s === '' ? null : s;
    }
    if (v.reason) v.reason = untext_(v.reason);
    out.push(v);
  }
  return out;
}

/**
 * The study team's passcode for the results, from a tab named `settings`:
 * the word `results_passcode` in column A and the passcode beside it in
 * column B. No tab, no row or an empty cell means results are switched off.
 */
function resultsPasscode_() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SETTINGS_SHEET);
  if (!sh) return '';
  var rows = sh.getDataRange().getValues();
  for (var i = 0; i < rows.length; i++) if (str_(rows[i][0]).trim() === 'results_passcode') return str_(rows[i][1]).trim();
  return '';
}

/**
 * Every reviewer's LAST vote per pair, models named — the one answer that
 * names a model, so it is given only for the passcode. It is asked for with a
 * POST so the passcode is never part of a URL.
 */
function results_(passcode) {
  var want = resultsPasscode_();
  if (!want) return json_({ error: 'results are switched off: add a results_passcode row to a tab named settings' });
  if (String(passcode || '') !== want) return json_({ error: 'wrong passcode' });
  var last = {};
  readVotes_().forEach(function (v) {
    var k = [reviewer_(v.reviewer), v.lane, v.run_id, v.scenario_id].join('|');
    // The later vote by its own timestamp, so a sorted tab changes nothing.
    if (!last[k] || String(v.ts) >= String(last[k].ts)) last[k] = v;
  });
  var votes = Object.keys(last).map(function (k) {
    var v = last[k];
    return { ts: v.ts, reviewer: reviewer_(v.reviewer), lane: v.lane, run_id: v.run_id, scenario_id: v.scenario_id, picked: v.picked, over: v.over, reason: v.reason };
  });
  return json_({ votes: votes, total: votes.length });
}

function doPost(e) {
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (body.action === 'results') return results_(body.passcode);
    var reviewer = reviewer_(body.reviewer);
    if (!REVIEWER_RE.test(reviewer)) return json_({ error: 'reviewer id must be 1-64 plain characters' });
    if (!allowed_(reviewer)) return json_({ error: 'unknown reviewer' });
    var items = loadKey_();
    var id = String(body.item || '');
    var item = has_(items, id) ? items[id] : null;
    if (!item) return json_({ error: 'unknown item' });
    var left = String(body.left || ''), right = String(body.right || '');
    if (left === right || !has_(item.media, left) || !has_(item.media, right)) return json_({ error: "left/right are not this item's pair" });
    var pick = String(body.pick || '');
    if (['left', 'right', 'tie'].indexOf(pick) < 0) return json_({ error: 'pick must be left, right or tie' });
    var reason = String(body.reason || '').trim();
    if (reason.length > REASON_MAX) return json_({ error: 'reason must be at most ' + REASON_MAX + ' characters' });
    var picked = pick === 'tie' ? '' : item.media[pick === 'left' ? left : right];
    var over = pick === 'tie' ? '' : item.media[pick === 'left' ? right : left];
    var row = [new Date().toISOString(), reviewer, item.lane, item.run_id, item.scenario_id, picked, over, text_(reason)];
    var lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      var sh = votesSheetForWrite_();
      var at = sh.getLastRow() + 1;
      if (at > MAX_VOTE_ROWS) return json_({ error: 'the panel is closed' });
      if (at > sh.getMaxRows()) sh.insertRowsAfter(sh.getMaxRows(), 500);
      // The row is made plain text BEFORE it is written: Sheets would
      // otherwise turn the ISO timestamp into a date and a reviewer called
      // "007" into the number 7, and neither would be found again.
      sh.getRange(at, 1, 1, VOTE_COLS.length).setNumberFormat('@').setValues([row]);
    } finally { lock.releaseLock(); }
    // Nothing about the vote goes back: the reply is readable in the
    // reviewer's browser, and the picked model's name would unblind the pair.
    return json_({ ok: true });
  } catch (err) {
    // The message can name this spreadsheet's id; the caller is the public.
    console.error(err);
    return json_({ error: 'the vote store had a problem, please try again' });
  }
}

function doGet(e) {
  var p = (e && e.parameter) || {};
  var action = p.action || 'health';
  try {
    if (action === 'health') return json_({ ok: true, items: Object.keys(loadKey_()).length });
    if (action === 'votes') {
      var reviewer = reviewer_(p.reviewer);
      if (!REVIEWER_RE.test(reviewer)) return json_({ error: 'bad reviewer id' });
      if (!allowed_(reviewer)) return json_({ error: 'unknown reviewer' });
      var items = loadKey_(), bySc = byScenario_(items), picks = {}, when = {};
      readVotes_().forEach(function (v) {
        if (reviewer_(v.reviewer) !== reviewer) return;
        var id = bySc[v.lane + '|' + v.run_id + '|' + v.scenario_id];
        if (!id) return;
        // The later vote by its own timestamp, not by where its row sits.
        if (has_(when, id) && String(when[id]) > String(v.ts)) return;
        var media = null;
        if (v.picked) for (var m in items[id].media) if (items[id].media[m] === v.picked) media = m;
        when[id] = v.ts;
        picks[id] = { media: media, reason: v.reason };
      });
      return json_({ reviewer: reviewer, items: Object.keys(picks), picks: picks });
    }
    // No `results` action on a GET: results need the passcode, and a passcode
    // does not belong in a URL. See results_ (asked for with a POST).
    return json_({ error: 'no such action' });
  } catch (err) {
    console.error(err);
    return json_({ error: 'the vote store had a problem, please try again' });
  }
}
