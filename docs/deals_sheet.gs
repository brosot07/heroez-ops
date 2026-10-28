/**
 * Heroez Ops → Google Sheet: receives the deal pipeline and rewrites the "Deals" tab.
 *
 * Setup (once, by the sheet's owner):
 *   1. Open the Google Sheet that ops uses → Extensions → Apps Script.
 *   2. Replace the code with this file. Change TOKEN to a long random secret.
 *   3. Deploy → New deployment → type "Web app" → Execute as: Me → Who has access: Anyone → Deploy.
 *   4. Copy the web app URL and the TOKEN into Heroez Ops → Pengaturan → section 8 (Deal).
 * The app pushes key events immediately and a full refresh every morning. Columns you add to the
 * right of the app's columns are kept (e.g. your own "Ops notes").
 */
var TOKEN = "change-me-to-a-long-random-secret";
var SHEET_NAME = "Deals";

function doPost(e) {
  var body = JSON.parse(e.postData.contents || "{}");
  if (body.token !== TOKEN) {
    return ContentService.createTextOutput("error: bad token");
  }
  var rows = body.rows || [];
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  var width = rows.length ? rows[0].length : 0;
  if (width) {
    // Keep columns to the right of the app's columns (ops notes etc.), matched by deal ID.
    var old = sh.getDataRange().getValues();
    var extraByID = {};
    for (var i = 1; i < old.length; i++) {
      if (old[i].length > width) extraByID[String(old[i][0])] = old[i].slice(width);
    }
    var extraWidth = old.length ? Math.max(0, old[0].length - width) : 0;
    for (var r = 0; r < rows.length; r++) {
      var extra = r === 0 ? (old.length ? old[0].slice(width) : []) : (extraByID[String(rows[r][0])] || []);
      while (extra.length < extraWidth) extra.push("");
      rows[r] = rows[r].concat(extra);
    }
    sh.clearContents();
    sh.getRange(1, 1, rows.length, width + extraWidth).setValues(rows);
    sh.getRange(1, 1, 1, width + extraWidth).setFontWeight("bold");
    sh.setFrozenRows(1);
  }
  return ContentService.createTextOutput("ok " + (rows.length - 1) + " rows " + new Date().toISOString());
}

function doGet() {
  return ContentService.createTextOutput("Heroez Ops deals endpoint is alive");
}
