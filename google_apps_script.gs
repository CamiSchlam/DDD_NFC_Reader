// Paste this into the Sheet's Extensions > Apps Script editor, then:
// Deploy > New deployment > type "Web app" > Execute as: Me > Who has access: Anyone.
// Copy the resulting .../exec URL into device_secrets.py as SHEET_URL, and set
// SHARED_SECRET below to match SHEET_SECRET in device_secrets.py.

var SHARED_SECRET = "HeelMoeilijkWachtwoord";

function doPost(e) {
  var body = JSON.parse(e.postData.contents);

  if (body.secret !== SHARED_SECRET) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: "bad secret" }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  sheet.appendRow([
    new Date(),
    body.worker || "",
    body.event || "",
    body.box || "",
    body.elapsed_s !== undefined ? body.elapsed_s : "",
    body.status || "",
  ]);

  return ContentService
    .createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
