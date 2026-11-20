const test = require("node:test");
const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");

globalThis.crypto = webcrypto;

const ledger = require("../ledger.js");

async function makeRecord(id, marks, prevHash) {
  return ledger.createRecord({
    id: id,
    rollNo: "ROLL-1",
    name: "Student Example",
    subject: id === "VG-test-2" ? "Physics" : "Mathematics",
    exam: "Term 1",
    marks: marks,
    max: 100,
    timestamp: "2026-11-20T10:00:00.000Z",
    metadata: { session: "2026-27" }
  }, prevHash);
}

test("SHA-256 records form a valid chain and accept zero marks", async function () {
  const first = await makeRecord("VG-test-1", 0);
  const second = await makeRecord("VG-test-2", 75, first.hash);
  const report = await ledger.verifyChain([first, second]);

  assert.equal(first.prevHash, ledger.GENESIS_HASH);
  assert.match(first.hash, /^[a-f0-9]{64}$/);
  assert.equal(report.valid, true);
  assert.deepEqual(report.entries.map(function (entry) { return entry.status; }), [
    ledger.STATUS.VERIFIED,
    ledger.STATUS.VERIFIED
  ]);
});

test("field edits and broken links are identified at the first changed block", async function () {
  const first = await makeRecord("VG-test-1", 75);
  const second = await makeRecord("VG-test-2", 81, first.hash);
  first.marks = 99;

  const report = await ledger.verifyChain([first, second]);
  assert.equal(report.entries[0].status, ledger.STATUS.TAMPERED);
  assert.match(report.entries[0].reason, /SHA-256 hash/);
  assert.equal(report.entries[1].status, ledger.STATUS.CORRUPTED);
});

test("metadata changes are covered by the record hash", async function () {
  const record = await makeRecord("VG-test-1", 75);
  record.metadata.session = "altered-session";
  const report = await ledger.verifyChain([record]);

  assert.equal(report.entries[0].status, ledger.STATUS.TAMPERED);
});

test("incomplete records and duplicate records are flagged", async function () {
  const first = await makeRecord("VG-test-1", 75);
  const incomplete = { id: "VG-broken", rollNo: "ROLL-2", name: "Broken", marks: "75" };
  const report = await ledger.verifyChain([first, incomplete]);
  assert.equal(report.entries[1].status, ledger.STATUS.CORRUPTED);
  assert.match(report.entries[1].reason, /subject|marks|hash|prevHash/);

  const duplicateBusiness = await ledger.createRecord({
    id: "VG-test-2",
    rollNo: first.rollNo,
    name: "Student Example",
    subject: first.subject,
    exam: first.exam,
    marks: 80
  }, first.hash);
  const duplicateReport = await ledger.verifyChain([first, duplicateBusiness]);
  assert.equal(duplicateReport.entries[1].status, ledger.STATUS.CORRUPTED);
  assert.equal(
    ledger.findDuplicate([first], duplicateBusiness),
    "A result for this student, subject, and exam already exists."
  );

  const duplicateId = await ledger.createRecord({
    id: first.id,
    rollNo: "ROLL-2",
    name: "Another Student",
    subject: "Physics",
    exam: "Term 2",
    marks: 80
  }, first.hash);
  const duplicateIdReport = await ledger.verifyChain([first, duplicateId]);
  assert.equal(duplicateIdReport.entries[1].status, ledger.STATUS.CORRUPTED);
  assert.equal(ledger.findDuplicate([first], duplicateId), "A record with this record ID already exists.");
});

test("lookup verifies IDs, hashes, QR payloads, and malformed input safely", async function () {
  const record = await makeRecord("VG-test-1", 88);
  const qr = ledger.createQrPayload(record);

  assert.equal((await ledger.verifyLookup([record], record.id)).status, ledger.STATUS.VERIFIED);
  assert.equal((await ledger.verifyLookup([record], record.hash)).status, ledger.STATUS.VERIFIED);
  assert.equal((await ledger.verifyLookup([record], qr)).status, ledger.STATUS.VERIFIED);
  const alteredQr = JSON.stringify({ type: "verigrade-record", version: 1, id: record.id, hash: "f".repeat(64) });
  assert.equal((await ledger.verifyLookup([record], alteredQr)).status, ledger.STATUS.TAMPERED);
  assert.equal((await ledger.verifyLookup([record], "{not-json")).status, ledger.STATUS.INVALID);
  assert.equal((await ledger.verifyLookup([record], "UNKNOWN")).status, ledger.STATUS.INVALID);
  assert.equal((await ledger.verifyLookup([record], "")).status, ledger.STATUS.INVALID);
});
