(function (root, factory) {
  const ledger = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = ledger;
  root.VeriGradeLedger = ledger;
}(typeof globalThis !== "undefined" ? globalThis : window, function () {
  const GENESIS_HASH = "0".repeat(64);
  const STATUS = Object.freeze({
    VERIFIED: "VERIFIED",
    TAMPERED: "TAMPERED / MODIFIED",
    CORRUPTED: "CORRUPTED / INCOMPLETE",
    INVALID: "INVALID / NOT FOUND"
  });
  const HASH_PATTERN = /^[a-f0-9]{64}$/i;
  const REQUIRED_FIELDS = ["id", "rollNo", "name", "exam", "subject", "marks", "hash", "prevHash"];

  function normalizeKey(value) {
    return typeof value === "string" ? value.trim().toLocaleLowerCase() : "";
  }

  function stableValue(value) {
    if (value === null || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
      if (!Number.isFinite(value)) throw new TypeError("Hash input contains a non-finite number.");
      return Object.is(value, -0) ? 0 : value;
    }
    if (Array.isArray(value)) return value.map(stableValue);
    const prototype = value && typeof value === "object" ? Object.getPrototypeOf(value) : null;
    if (typeof value !== "object" || (prototype !== Object.prototype && prototype !== null)) {
      throw new TypeError("Hash input must contain JSON-compatible values only.");
    }
    return Object.keys(value).sort().reduce(function (sorted, key) {
      if (value[key] === undefined) throw new TypeError("Hash input contains an undefined value.");
      sorted[key] = stableValue(value[key]);
      return sorted;
    }, Object.create(null));
  }

  function canonicalRecord(record) {
    const body = Object.create(null);
    Object.keys(record).forEach(function (key) {
      if (key !== "hash") body[key] = record[key];
    });
    return JSON.stringify(stableValue(body));
  }

  // Hash every record field except its own hash; prevHash anchors it to the ledger.
  async function calculateHash(record) {
    if (!globalThis.crypto || !globalThis.crypto.subtle || typeof TextEncoder === "undefined") {
      throw new Error("Web Crypto SHA-256 is unavailable. Use a secure browser context (HTTPS or localhost).");
    }
    const bytes = new TextEncoder().encode(canonicalRecord(record));
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest)).map(function (byte) {
      return byte.toString(16).padStart(2, "0");
    }).join("");
  }

  function validateRecord(record, includeHashes) {
    const issues = [];
    if (!record || typeof record !== "object" || Array.isArray(record)) {
      return ["Record must be an object."];
    }
    REQUIRED_FIELDS.forEach(function (field) {
      if (!(field in record)) issues.push("Missing required field: " + field + ".");
    });
    ["id", "rollNo", "name", "exam", "subject"].forEach(function (field) {
      if (typeof record[field] !== "string" || !record[field].trim()) {
        issues.push("Field " + field + " must be a non-empty string.");
      }
    });
    if (typeof record.marks !== "number" || Number.isNaN(record.marks) || !Number.isFinite(record.marks) || record.marks < 0) {
      issues.push("Field marks must be a finite non-negative number.");
    }
    if (record.max !== undefined && (typeof record.max !== "number" || !Number.isFinite(record.max) || record.max <= 0 || record.marks > record.max)) {
      issues.push("Field max must be positive and at least marks.");
    }
    if (includeHashes) {
      if (typeof record.hash !== "string" || !HASH_PATTERN.test(record.hash)) issues.push("Field hash must be a SHA-256 hex digest.");
      if (typeof record.prevHash !== "string" || !HASH_PATTERN.test(record.prevHash)) issues.push("Field prevHash must be a SHA-256 hex digest.");
    }
    try {
      canonicalRecord(record);
    } catch (error) {
      issues.push(error.message);
    }
    return issues;
  }

  // Build a new immutable ledger block from validated, user-entered fields.
  async function createRecord(fields, prevHash) {
    const record = Object.assign(Object.create(null), fields, { prevHash: prevHash || GENESIS_HASH });
    const issues = validateRecord(Object.assign({}, record, { hash: GENESIS_HASH }), false);
    if (issues.length) throw new TypeError(issues.join(" "));
    if (!HASH_PATTERN.test(record.prevHash)) throw new TypeError("Previous hash is not a valid SHA-256 digest.");
    record.hash = await calculateHash(record);
    return record;
  }

  function duplicateReason(records) {
    const ids = new Set();
    const combinations = new Set();
    for (let index = 0; index < records.length; index += 1) {
      const record = records[index];
      if (!record || typeof record !== "object") continue;
      const id = normalizeKey(record.id);
      if (id && ids.has(id)) return { index: index, reason: "Duplicate record ID." };
      if (id) ids.add(id);
      const key = [record.rollNo, record.subject, record.exam].map(normalizeKey);
      if (key.every(Boolean)) {
        const combination = JSON.stringify(key);
        if (combinations.has(combination)) return { index: index, reason: "Duplicate student, subject, and exam." };
        combinations.add(combination);
      }
    }
    return null;
  }

  // Recompute hashes and links in order, preserving the first block that broke trust.
  async function verifyChain(records) {
    if (!Array.isArray(records)) {
      return { valid: false, entries: [], firstIssue: { status: STATUS.CORRUPTED, reason: "Ledger storage is not an array." } };
    }
    const duplicate = duplicateReason(records);
    const entries = [];
    let expectedPrevHash = GENESIS_HASH;
    let chainTrusted = true;
    for (let index = 0; index < records.length; index += 1) {
      const record = records[index];
      let status = STATUS.VERIFIED;
      let reason = "Hash and chain link match.";
      const issues = validateRecord(record, true);
      if (duplicate && duplicate.index === index) {
        status = STATUS.CORRUPTED;
        reason = duplicate.reason;
      } else if (issues.length) {
        status = STATUS.CORRUPTED;
        reason = issues.join(" ");
      } else if (record.prevHash !== expectedPrevHash) {
        status = STATUS.TAMPERED;
        reason = "Previous hash does not match the preceding record.";
      } else {
        try {
          if (await calculateHash(record) !== record.hash.toLowerCase()) {
            status = STATUS.TAMPERED;
            reason = "Record fields do not match the stored SHA-256 hash.";
          } else if (!chainTrusted) {
            status = STATUS.CORRUPTED;
            reason = "An earlier block is invalid, so this chain link cannot be trusted.";
          }
        } catch (error) {
          status = STATUS.CORRUPTED;
          reason = error.message || "Record hash could not be calculated.";
        }
      }
      entries.push({ index: index, recordId: record && typeof record.id === "string" ? record.id : "", status: status, reason: reason });
      if (status !== STATUS.VERIFIED) chainTrusted = false;
      expectedPrevHash = record && typeof record.hash === "string" && HASH_PATTERN.test(record.hash) ? record.hash.toLowerCase() : null;
    }
    const firstIssue = entries.find(function (entry) { return entry.status !== STATUS.VERIFIED; }) || null;
    return { valid: firstIssue === null, entries: entries, firstIssue: firstIssue };
  }

  function findDuplicate(records, candidate) {
    const candidateId = normalizeKey(candidate.id);
    const rollNo = normalizeKey(candidate.rollNo);
    const subject = normalizeKey(candidate.subject);
    const exam = normalizeKey(candidate.exam);
    for (const record of records) {
      if (candidateId && normalizeKey(record.id) === candidateId) return "A record with this record ID already exists.";
      if (rollNo && subject && exam &&
          normalizeKey(record.rollNo) === rollNo &&
          normalizeKey(record.subject) === subject &&
          normalizeKey(record.exam) === exam) {
        return "A result for this student, subject, and exam already exists.";
      }
    }
    return "";
  }

  function createQrPayload(record) {
    return JSON.stringify({ type: "verigrade-record", version: 1, id: record.id, hash: record.hash });
  }

  function parseLookup(rawInput) {
    if (typeof rawInput !== "string" || !rawInput.trim()) throw new TypeError("Enter a record ID, SHA-256 hash, or QR payload.");
    const raw = rawInput.trim();
    if (raw.charAt(0) !== "{") return { lookup: raw };
    const payload = JSON.parse(raw);
    if (!payload || payload.type !== "verigrade-record" || payload.version !== 1 ||
        typeof payload.id !== "string" || !payload.id.trim() ||
        typeof payload.hash !== "string" || !HASH_PATTERN.test(payload.hash)) {
      throw new TypeError("QR payload is incomplete or has an unsupported format.");
    }
    return { lookup: payload.id.trim(), hash: payload.hash.toLowerCase() };
  }

  // Verify IDs, hashes, and QR JSON only against the locally stored, chained ledger.
  async function verifyLookup(records, rawInput) {
    let parsed;
    try {
      parsed = parseLookup(rawInput);
    } catch (error) {
      return { status: STATUS.INVALID, reason: error.message, record: null };
    }
    if (!Array.isArray(records)) return { status: STATUS.CORRUPTED, reason: "Ledger storage is not an array.", record: null };
    const lookup = parsed.lookup;
    const matches = records.map(function (record, index) {
      const byId = record && typeof record.id === "string" && record.id.toLowerCase() === lookup.toLowerCase();
      const byHash = HASH_PATTERN.test(lookup) && record && typeof record.hash === "string" && record.hash.toLowerCase() === lookup.toLowerCase();
      return byId || byHash ? index : -1;
    }).filter(function (index) { return index >= 0; });
    if (!matches.length) return { status: STATUS.INVALID, reason: "No local ledger record matches this ID, hash, or QR payload.", record: null };
    if (matches.length > 1) return { status: STATUS.CORRUPTED, reason: "Lookup is ambiguous because duplicate records exist.", record: records[matches[0]] };
    const index = matches[0];
    const record = records[index];
    if (parsed.hash && parsed.hash !== String(record.hash).toLowerCase()) {
      return { status: STATUS.TAMPERED, reason: "QR hash does not match the stored record.", record: record };
    }
    const report = await verifyChain(records);
    const entry = report.entries[index];
    return { status: entry ? entry.status : STATUS.CORRUPTED, reason: entry ? entry.reason : "Record integrity could not be checked.", record: record };
  }

  return Object.freeze({
    GENESIS_HASH: GENESIS_HASH,
    STATUS: STATUS,
    calculateHash: calculateHash,
    createRecord: createRecord,
    createQrPayload: createQrPayload,
    duplicateReason: duplicateReason,
    findDuplicate: findDuplicate,
    validateRecord: validateRecord,
    verifyChain: verifyChain,
    verifyLookup: verifyLookup
  });
}));
