/* VeriGrade interface, local persistence, and user actions. */
(function () {
  "use strict";

  const Ledger = window.VeriGradeLedger;
  const API_BASE = "https://web-production-fb17e087.up.railway.app";
  const STORAGE_KEY = "verigrade.state.v1";
  const SCHEMA_VERSION = 1;
  const PAGE_SIZE = 5;
  const DEFAULT_SETTINGS = {
    institution: "Northbridge University",
    contactEmail: "registrar@northbridge.edu",
    administrator: "Dr. Anita Rao",
    role: "Administrator",
    notifyIntegrity: true,
    notifyCorrections: true,
    weeklySummary: false,
    accent: "#C2F23A"
  };
  const ACCENT_COLORS = ["#C2F23A", "#94D52F", "#E6FF68"];
  const LEGACY_ACCENT_MAP = {
    "#168BFF": "#C2F23A",
    "#35D8FF": "#E6FF68",
    "#31D69A": "#94D52F"
  };

  let state = createEmptyState();
  let storageWritable = true;
  let storageIssue = "";
  let storageCanReset = false;
  let integrityReport = { valid: true, entries: [], firstIssue: null };
  let currentPage = 1;
  let pendingAction = null;
  let renderVersion = 0;
  let auditSequence = 0;

  function $(id) { return document.getElementById(id); }

  function createEmptyState() {
    return {
      schemaVersion: SCHEMA_VERSION,
      students: [],
      records: [],
      corrections: [],
      auditLogs: [],
      settings: Object.assign({}, DEFAULT_SETTINGS)
    };
  }

  function esc(value) {
    const node = document.createElement("span");
    const type = typeof value;
    node.textContent = value == null || (type !== "string" && type !== "number" && type !== "boolean") ?
      "" : String(value);
    return node.innerHTML;
  }

  function safeText(value, fallback) {
    return typeof value === "string" && value.trim() ? value : (fallback || "—");
  }

  function normalized(value) {
    return typeof value === "string" ? value.trim().toLocaleLowerCase() : "";
  }

  function safeSearchText(values) {
    return values.filter(function (value) { return typeof value === "string"; }).join(" ").toLocaleLowerCase();
  }

  function refreshIcons() {
    if (window.lucide) window.lucide.createIcons();
  }

  function toast(message, type) {
    const node = document.createElement("div");
    node.className = "toast " + (type || "");
    node.setAttribute("role", type === "bad" ? "alert" : "status");
    node.textContent = message;
    $("toasts").appendChild(node);
    window.setTimeout(function () { node.remove(); }, 5000);
  }

  function loadStoredState() {
    // Keep malformed storage untouched and read-only until the user explicitly resets it.
    let raw;
    try {
      raw = window.localStorage.getItem(STORAGE_KEY);
    } catch (error) {
      storageWritable = false;
      storageIssue = "Browser storage is unavailable. Changes cannot be saved.";
      return;
    }
    if (raw === null) return;

    try {
      const saved = JSON.parse(raw);
      if (!saved || typeof saved !== "object" || Array.isArray(saved) || saved.schemaVersion !== SCHEMA_VERSION) {
        throw new TypeError("Saved data has an unsupported schema.");
      }
      ["students", "records", "corrections", "auditLogs"].forEach(function (key) {
        if (!Array.isArray(saved[key])) throw new TypeError("Saved " + key + " data is not a list.");
      });
      if (!saved.settings || typeof saved.settings !== "object" || Array.isArray(saved.settings)) {
        throw new TypeError("Saved settings are incomplete.");
      }
      state = {
        schemaVersion: SCHEMA_VERSION,
        students: saved.students,
        records: saved.records,
        corrections: saved.corrections,
        auditLogs: saved.auditLogs,
        settings: validateSettings(saved.settings)
      };
      storageIssue = validateAuxiliaryState(state);
      if (storageIssue) {
        storageWritable = false;
        storageCanReset = true;
      }
    } catch (error) {
      state = createEmptyState();
      storageWritable = false;
      storageCanReset = true;
      storageIssue = "Saved data is malformed or has an unsupported schema. It was not overwritten.";
    }
  }

  function validateSettings(saved) {
    const settings = Object.assign({}, DEFAULT_SETTINGS);
    ["institution", "contactEmail", "administrator", "role"].forEach(function (key) {
      if (typeof saved[key] !== "string") throw new TypeError("Saved setting " + key + " is invalid.");
      settings[key] = saved[key];
    });
    ["notifyIntegrity", "notifyCorrections", "weeklySummary"].forEach(function (key) {
      if (typeof saved[key] !== "boolean") throw new TypeError("Saved setting " + key + " is invalid.");
      settings[key] = saved[key];
    });
    if (typeof saved.accent !== "string") {
      throw new TypeError("Saved accent colour is invalid.");
    }
    if (ACCENT_COLORS.indexOf(saved.accent) !== -1) settings.accent = saved.accent;
    else if (Object.prototype.hasOwnProperty.call(LEGACY_ACCENT_MAP, saved.accent)) {
      settings.accent = LEGACY_ACCENT_MAP[saved.accent];
    } else throw new TypeError("Saved accent colour is invalid.");
    return settings;
  }

  function validateAuxiliaryState(saved) {
    const studentIds = new Set();
    for (const student of saved.students) {
      if (!student || typeof student !== "object" || Array.isArray(student) ||
          typeof student.id !== "string" || !student.id.trim() ||
          typeof student.name !== "string" || !student.name.trim() ||
          ["program", "dept", "email"].some(function (field) { return typeof student[field] !== "string"; })) {
        return "Saved student data is incomplete or malformed. The data was not overwritten.";
      }
      const id = normalized(student.id);
      if (studentIds.has(id)) return "Saved student data contains duplicate IDs. The data was not overwritten.";
      studentIds.add(id);
    }

    const correctionIds = new Set();
    for (const correction of saved.corrections) {
      const states = ["Pending", "Approved", "Rejected"];
      if (!correction || typeof correction !== "object" || Array.isArray(correction) ||
          ["id", "recordId", "rollNo", "subject", "reason", "date"].some(function (field) {
            return typeof correction[field] !== "string";
          }) ||
          typeof correction.original !== "number" || !Number.isFinite(correction.original) ||
          typeof correction.requested !== "number" || !Number.isFinite(correction.requested) ||
          states.indexOf(correction.status) === -1 ||
          states.indexOf(correction.first) === -1 || states.indexOf(correction.second) === -1) {
        return "Saved correction data is incomplete or malformed. The data was not overwritten.";
      }
      if (correctionIds.has(correction.id)) return "Saved correction data contains duplicate IDs. The data was not overwritten.";
      correctionIds.add(correction.id);
    }

    const auditIds = new Set();
    for (const entry of saved.auditLogs) {
      if (!entry || typeof entry !== "object" || Array.isArray(entry) ||
          ["id", "user", "action", "record", "time"].some(function (field) {
            return typeof entry[field] !== "string" || !entry[field].trim();
          }) ||
          ["Success", "Warning", "Alert"].indexOf(entry.status) === -1) {
        return "Saved audit log data is incomplete or malformed. The data was not overwritten.";
      }
      if (auditIds.has(entry.id)) return "Saved audit log data contains duplicate IDs. The data was not overwritten.";
      auditIds.add(entry.id);
    }
    return "";
  }

  // Persist a draft before replacing in-memory state, so failed writes never look successful.
  function commitState(update) {
    if (!storageWritable) throw new Error(storageIssue || "Storage is read-only; changes were not saved.");
    const draft = JSON.parse(JSON.stringify(state));
    update(draft);
    draft.schemaVersion = SCHEMA_VERSION;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
    } catch (error) {
      throw new Error("Could not save to browser storage. The change was not applied.");
    }
    state = draft;
    storageIssue = "";
  }

  function makeAuditEntry(action, record, status, user) {
    auditSequence += 1;
    return {
      id: "ACT-" + Date.now().toString(36) + "-" + auditSequence,
      user: user || state.settings.administrator,
      action: action,
      record: record,
      time: new Date().toISOString(),
      status: status
    };
  }

  function appendAudit(draft, action, record, status, user) {
    draft.auditLogs.unshift(makeAuditEntry(action, record, status, user));
  }

  function showStorageNotice() {
    const notice = $("storageNotice");
    notice.hidden = !storageIssue;
    $("storageMessage").textContent = storageIssue;
    $("resetStorageBtn").hidden = !storageCanReset;
  }

  function resetDamagedStorage() {
    openModal("Reset damaged local data", "<p>This removes VeriGrade data stored in this browser and starts with an empty ledger. This cannot be undone.</p>", [
      { label: "Cancel" },
      {
        label: "Reset local data",
        cls: "danger",
        onClick: function () {
          try {
            window.localStorage.removeItem(STORAGE_KEY);
          } catch (error) {
            toast("Browser storage could not be cleared.", "bad");
            return false;
          }
          state = createEmptyState();
          storageWritable = true;
          storageIssue = "";
          storageCanReset = false;
          loadSettingsForm();
          fillDeptFilter();
          showStorageNotice();
          renderAll();
          toast("Local ledger data was reset.", "ok");
        }
      }
    ]);
  }

  function badge(status) {
    const colors = {};
    colors[Ledger.STATUS.VERIFIED] = "ok";
    colors[Ledger.STATUS.TAMPERED] = "bad";
    colors[Ledger.STATUS.CORRUPTED] = "bad";
    colors[Ledger.STATUS.INVALID] = "bad";
    colors.Success = "ok";
    colors.Approved = "ok";
    colors.Pending = "warn";
    colors.Warning = "warn";
    colors.Alert = "bad";
    colors.Rejected = "bad";
    const label = safeText(status, "Unknown");
    return '<span class="badge ' + (colors[label] || "warn") + '" role="status">' + esc(label) + "</span>";
  }

  // Keep the active view and sidebar state in sync for mouse and keyboard navigation.
  function showView(viewId) {
    document.querySelectorAll(".view").forEach(function (view) {
      view.classList.toggle("active", view.id === viewId);
    });
    document.querySelectorAll(".nav-item").forEach(function (button) {
      const active = button.dataset.view === viewId;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    closeMenu();
    window.scrollTo(0, 0);
  }

  function enterApp() {
    $("landingPage").hidden = true;
    $("appShell").hidden = false;
    showView("overview");
  }

  function returnToLanding() {
    closeMenu();
    closeModal();
    $("appShell").hidden = true;
    $("landingPage").hidden = false;
    window.scrollTo(0, 0);
  }

  function openMenu() { $("sidebar").classList.add("open"); }
  function closeMenu() { $("sidebar").classList.remove("open"); }

  // Modal callbacks may be async; returning false keeps the dialog open for validation.
  function openModal(title, bodyHtml, buttons) {
    $("modalTitle").textContent = title;
    $("modalBody").innerHTML = bodyHtml;
    $("modalFoot").innerHTML = "";
    buttons.forEach(function (info) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn " + (info.cls || "");
      button.textContent = info.label;
      button.addEventListener("click", async function () {
        button.disabled = true;
        try {
          const keepOpen = info.onClick ? await info.onClick() === false : false;
          if (keepOpen) button.disabled = false;
          else closeModal();
        } catch (error) {
          toast(error.message || "The action could not be completed.", "bad");
          button.disabled = false;
        }
      });
      $("modalFoot").appendChild(button);
    });
    $("modal").hidden = false;
    refreshIcons();
    const first = $("modalBody").querySelector("input, textarea") || $("modalFoot").lastChild;
    if (first) first.focus();
  }

  function closeModal() { $("modal").hidden = true; }

  function kvRows(pairs) {
    return pairs.map(function (pair) {
      return '<div class="kv"><span>' + esc(pair[0]) + "</span><span>" + pair[1] + "</span></div>";
    }).join("");
  }

  function recordStatus(index) {
    const entry = integrityReport.entries[index];
    return entry ? entry.status : Ledger.STATUS.CORRUPTED;
  }

  function studentName(rollNo) {
    const student = state.students.find(function (item) {
      return item && typeof item === "object" && normalized(item.id) === normalized(rollNo);
    });
    if (student) return safeText(student.name, "Unknown student");
    const record = state.records.find(function (item) {
      return item && typeof item === "object" && normalized(item.rollNo) === normalized(rollNo);
    });
    return record ? safeText(record.name, "Unknown student") : "Unknown student";
  }

  function renderStats() {
    const verified = integrityReport.entries.filter(function (entry) { return entry.status === Ledger.STATUS.VERIFIED; }).length;
    const alerts = integrityReport.entries.filter(function (entry) {
      return entry.status === Ledger.STATUS.TAMPERED || entry.status === Ledger.STATUS.CORRUPTED;
    }).length;
    const pending = state.corrections.filter(function (item) {
      return item && item.status === "Pending";
    }).length;
    const cards = [
      ["users", state.students.length, "Students"],
      ["file-text", state.records.length, "Ledger records"],
      ["badge-check", verified, "Verified records"],
      ["clock", pending, "Pending corrections"],
      ["triangle-alert", alerts, "Integrity issues"]
    ];
    $("stats").innerHTML = cards.map(function (card) {
      return '<div class="card stat"><i data-lucide="' + card[0] + '"></i><strong>' + card[1] + "</strong><span>" + card[2] + "</span></div>";
    }).join("");

    const integrity = state.records.length ? Math.round((verified / state.records.length) * 100) + "%" : "—";
    const ringState = !state.records.length ? "neutral" : (alerts ? "bad" : "ok");
    const message = !state.records.length ? "No records yet." :
      (integrityReport.firstIssue ? "First issue: " + safeText(integrityReport.firstIssue.recordId, "unknown record") : "Every stored block verifies.");
    $("integrity").innerHTML = '<div class="ring ' + ringState + '">' + integrity + '</div><div><strong>' + esc(message) +
      '</strong><p class="muted">SHA-256 chain integrity for this browser.</p></div>';
    $("verifyOverview").innerHTML = [
      ["Verified", verified],
      ["Pending corrections", pending],
      ["Tampered / corrupted", alerts]
    ].map(function (item) {
      return "<div><strong>" + item[1] + "</strong><small>" + esc(item[0]) + "</small></div>";
    }).join("");
  }

  function renderRecent() {
    const rows = state.records.map(function (record, index) {
      return { record: record, index: index };
    }).reverse().slice(0, 5);
    $("recentBody").innerHTML = rows.map(function (item) {
      const record = item.record && typeof item.record === "object" ? item.record : {};
      return "<tr><td>" + esc(safeText(record.name, "Incomplete record")) + "</td><td>" +
        esc(safeText(record.exam)) + "</td><td>" + esc(record.marks) + "/" + esc(record.max) +
        "</td><td>" + badge(recordStatus(item.index)) + "</td></tr>";
    }).join("");
    const entries = state.auditLogs.filter(function (item) { return item && typeof item === "object"; }).slice(0, 5);
    $("activityList").innerHTML = entries.length ? entries.map(function (entry) {
      return '<li><i data-lucide="activity"></i><div>' + esc(safeText(entry.action, "Unknown action")) + " · " +
        esc(safeText(entry.record)) + "<br><small>" + esc(safeText(entry.user)) + ", " +
        esc(safeText(entry.time)) + "</small></div></li>";
    }).join("") : '<li class="empty"><p>No activity recorded yet.</p></li>';
    const pending = state.corrections.filter(function (item) { return item && item.status === "Pending"; });
    $("pendingList").innerHTML = pending.length ? pending.map(function (item) {
      return '<div class="pending-item"><span>' + esc(studentName(item.rollNo)) + " · " +
        esc(item.original) + " → " + esc(item.requested) +
        '</span><button class="btn sm" data-go="corrections">Review</button></div>';
    }).join("") : '<div class="empty"><p>No pending correction requests.</p></div>';
  }

  function fillDeptFilter() {
    const select = $("studentDept");
    const chosen = select.value;
    const departments = Array.from(new Set(state.students.filter(function (student) {
      return student && typeof student === "object" && typeof student.dept === "string";
    }).map(function (student) { return student.dept; }))).sort();
    select.innerHTML = '<option value="">All departments</option>' + departments.map(function (department) {
      return '<option value="' + esc(department) + '">' + esc(department) + "</option>";
    }).join("");
    select.value = chosen;
  }

  function renderStudents() {
    const text = $("studentSearch").value.toLocaleLowerCase();
    const department = $("studentDept").value;
    const list = state.students.filter(function (student) {
      if (!student || typeof student !== "object") return false;
      const haystack = safeSearchText([student.id, student.name, student.email, student.program, student.dept]);
      return haystack.includes(text) && (!department || student.dept === department);
    });
    $("studentBody").innerHTML = list.map(function (student) {
      return "<tr><td>" + esc(safeText(student.id)) + "</td><td>" + esc(safeText(student.name)) +
        "</td><td>" + esc(safeText(student.program)) + "</td><td>" + esc(safeText(student.dept)) +
        "</td><td>" + esc(safeText(student.email)) + '</td><td><button class="btn sm" data-student="' +
        esc(student.id) + '">View details</button></td></tr>';
    }).join("");
    $("studentEmpty").hidden = list.length > 0;
    refreshIcons();
  }

  function showStudent(rollNo) {
    const student = state.students.find(function (item) {
      return item && typeof item === "object" && normalized(item.id) === normalized(rollNo);
    });
    if (!student) {
      toast("Student record was not found.", "bad");
      return;
    }
    const resultCount = state.records.filter(function (record) {
      return record && typeof record === "object" && normalized(record.rollNo) === normalized(rollNo);
    }).length;
    openModal("Student details", kvRows([
      ["Roll no.", esc(student.id)],
      ["Name", esc(student.name)],
      ["Program", esc(student.program)],
      ["Department", esc(student.dept)],
      ["Email", esc(student.email)],
      ["Ledger records", String(resultCount)]
    ]), [{ label: "Close" }]);
  }

  function openAddStudent() {
    const body = '<div class="field"><label for="nId">Student ID / roll no.</label><input id="nId" maxlength="100" required></div>' +
      '<div class="field"><label for="nName">Full name</label><input id="nName" maxlength="160" required></div>' +
      '<div class="field"><label for="nProgram">Program (optional)</label><input id="nProgram" maxlength="160"></div>' +
      '<div class="field"><label for="nDept">Department (optional)</label><input id="nDept" maxlength="160"></div>' +
      '<div class="field"><label for="nEmail">Email (optional)</label><input id="nEmail" type="email" maxlength="254"></div>' +
      '<span class="err" id="nErr" aria-live="polite"></span>';
    openModal("Add student", body, [
      { label: "Cancel" },
      { label: "Add student", cls: "primary", onClick: saveStudent }
    ]);
  }

  function saveStudent() {
    const id = $("nId").value.trim();
    const name = $("nName").value.trim();
    const program = $("nProgram").value.trim();
    const department = $("nDept").value.trim();
    const email = $("nEmail").value.trim();
    if (!id || !name) {
      $("nErr").textContent = "Student ID and name are required.";
      return false;
    }
    if (email && !/^\S+@\S+\.\S+$/.test(email)) {
      $("nErr").textContent = "Enter a valid email address.";
      return false;
    }
    if (state.students.some(function (student) {
      return student && typeof student === "object" && normalized(student.id) === normalized(id);
    })) {
      $("nErr").textContent = "A student with this ID already exists.";
      return false;
    }
    try {
      commitState(function (draft) {
        draft.students.push({ id: id, name: name, program: program, dept: department, email: email });
        appendAudit(draft, "Student added", id, "Success");
      });
      fillDeptFilter();
      renderAll();
      toast(name + " was added to this browser's student list.", "ok");
    } catch (error) {
      toast(error.message, "bad");
      return false;
    }
  }

  function filteredResults() {
    const text = $("ledgerSearch").value.toLocaleLowerCase();
    const status = $("ledgerStatus").value;
    return state.records.map(function (record, index) {
      return { record: record, index: index };
    }).filter(function (item) {
      const record = item.record && typeof item.record === "object" ? item.record : {};
      const haystack = safeSearchText([record.id, record.rollNo, record.name, record.exam, record.subject, record.hash]);
      return haystack.includes(text) && (!status || recordStatus(item.index) === status);
    });
  }

  function renderLedger() {
    const list = filteredResults();
    const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    if (currentPage > pages) currentPage = pages;
    const start = (currentPage - 1) * PAGE_SIZE;
    $("ledgerBody").innerHTML = list.slice(start, start + PAGE_SIZE).map(function (item) {
      const record = item.record && typeof item.record === "object" ? item.record : {};
      const id = safeText(record.id, "");
      const status = recordStatus(item.index);
      return "<tr><td>" + esc(safeText(record.rollNo, "—")) + "</td><td>" + esc(safeText(record.name, "—")) +
        "</td><td>" + esc(safeText(record.exam, "—")) + "</td><td>" + esc(safeText(record.subject, "—")) +
        "</td><td>" + esc(record.marks) + "</td><td>" + esc(record.max) + "</td><td>" +
        esc(safeText(record.timestamp)) + "</td><td>" + badge(status) + '</td><td class="row-actions">' +
        (id ? '<button class="btn sm" data-view-result="' + esc(id) + '">View</button> ' +
          '<button class="btn sm" data-qr="' + esc(id) + '">QR</button> ' +
          '<button class="btn sm" data-download="' + esc(id) + '">Export</button>' : badge(Ledger.STATUS.CORRUPTED)) +
        "</td></tr>";
    }).join("");
    $("ledgerEmpty").hidden = list.length > 0;
    $("pageInfo").textContent = list.length ? "Page " + currentPage + " of " + pages + " · " + list.length + " records" : "0 records";
    $("prevPage").disabled = currentPage <= 1;
    $("nextPage").disabled = currentPage >= pages;
  }

  function findRecord(id) {
    return state.records.find(function (record) {
      return record && typeof record === "object" && record.id === id;
    });
  }

  function recordIndex(id) {
    return state.records.findIndex(function (record) {
      return record && typeof record === "object" && record.id === id;
    });
  }

  function viewResult(id) {
    const record = findRecord(id);
    const index = recordIndex(id);
    if (!record || index < 0) {
      toast("Ledger record was not found.", "bad");
      return;
    }
    const metadata = record.metadata && typeof record.metadata === "object" ?
      JSON.stringify(record.metadata, null, 2) : safeText(record.metadata);
    openModal("Ledger record", kvRows([
      ["Integrity status", badge(recordStatus(index))],
      ["Record ID", esc(record.id)],
      ["Student", esc(record.name) + " (" + esc(record.rollNo) + ")"],
      ["Examination", esc(record.exam)],
      ["Subject", esc(record.subject)],
      ["Marks", esc(record.marks) + " / " + esc(record.max)],
      ["Timestamp", esc(record.timestamp)],
      ["Metadata", esc(metadata)],
      ["Previous hash", '<code class="hash-value">' + esc(record.prevHash) + "</code>"],
      ["SHA-256 hash", '<code class="hash-value">' + esc(record.hash) + "</code>"]
    ]), [
      {
        label: "Show QR",
        onClick: function () {
          showQrForRecord(record.id);
          return false;
        }
      },
      { label: "Export JSON", onClick: function () { downloadRecord(record.id); } },
      {
        label: "Request correction",
        onClick: function () {
          openCorrectionRequest(record.id);
          return false;
        }
      },
      { label: "Close" }
    ]);
  }

  function downloadRecord(id) {
    const record = findRecord(id);
    if (!record) {
      toast("Ledger record was not found.", "bad");
      return;
    }
    try {
      const file = new Blob([JSON.stringify(record, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url;
      link.download = (String(record.id).replace(/[^a-z0-9_-]/gi, "_") || "record") + ".json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      toast("Record JSON exported.", "ok");
    } catch (error) {
      toast("Record export failed: " + error.message, "bad");
    }
  }

  function showQrForRecord(id) {
    const index = recordIndex(id);
    const record = index >= 0 ? state.records[index] : null;
    if (!record) {
      toast("Ledger record was not found.", "bad");
      return;
    }
    const status = recordStatus(index);
    if (status !== Ledger.STATUS.VERIFIED) {
      toast("QR sharing is disabled: this record is not verified.", "bad");
      return;
    }
    const payload = Ledger.createQrPayload(record);
    const body = '<div id="qrCode" class="qr-code" role="img" aria-label="QR code for record ' + esc(record.id) + '"></div>' +
      '<p class="muted">Scan this code, then paste its JSON into Verify Result on a browser with the same saved ledger.</p>' +
      '<label for="qrPayload">QR payload</label><textarea id="qrPayload" rows="3" readonly>' + esc(payload) + "</textarea>";
    openModal("Record verification QR", body, [
      {
        label: "Copy payload",
        onClick: function () {
          const payloadBox = $("qrPayload");
          payloadBox.focus();
          payloadBox.select();
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(payload).then(function () {
              toast("QR payload copied.", "ok");
            }).catch(function () {
              toast("Select and copy the QR payload manually.", "warn");
            });
          } else {
            toast("Select and copy the QR payload manually.", "warn");
          }
          return false;
        }
      },
      {
        label: "Verify here",
        cls: "primary",
        onClick: function () {
          $("vInput").value = payload;
          closeModal();
          showView("verify");
          runVerification();
          return false;
        }
      },
      { label: "Close" }
    ]);
    try {
      if (!window.QRCode) throw new Error("QR generator is unavailable.");
      new window.QRCode($("qrCode"), {
        text: payload,
        width: 190,
        height: 190,
        colorDark: "#080D18",
        colorLight: "#FFFFFF",
        correctLevel: window.QRCode.CorrectLevel.M
      });
    } catch (error) {
      $("qrCode").textContent = "QR image could not be generated. Use the payload below.";
      $("qrCode").setAttribute("role", "status");
    }
  }

  function setFieldError(inputId, message) {
    const input = $(inputId);
    input.parentElement.classList.toggle("invalid", Boolean(message));
    const errorNode = input.parentElement.querySelector(".err");
    if (errorNode) errorNode.textContent = message || "";
    if (message) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }

  function validatePublishForm() {
    let valid = true;
    ["fId", "fName", "fExam", "fSubject", "fMarks", "fMax"].forEach(function (id) {
      const empty = $(id).value.trim() === "";
      setFieldError(id, empty ? "This field is required." : "");
      if (empty) valid = false;
    });
    [["fId", 100], ["fName", 160], ["fExam", 160], ["fSubject", 160]].forEach(function (item) {
      const input = $(item[0]);
      if (input.value.trim().length > item[1]) {
        setFieldError(item[0], "Maximum length is " + item[1] + " characters.");
        valid = false;
      }
    });
    const marks = Number($("fMarks").value);
    const maximum = Number($("fMax").value);
    if ($("fMarks").value !== "" && (!Number.isFinite(marks) || marks < 0)) {
      setFieldError("fMarks", "Enter a finite number of 0 or more.");
      valid = false;
    }
    if ($("fMax").value !== "" && (!Number.isFinite(maximum) || maximum <= 0)) {
      setFieldError("fMax", "Maximum marks must be a positive number.");
      valid = false;
    }
    if (Number.isFinite(marks) && Number.isFinite(maximum) && marks > maximum) {
      setFieldError("fMarks", "Marks cannot be higher than maximum marks.");
      valid = false;
    }
    if ($("fDate").value && Number.isNaN(Date.parse($("fDate").value + "T00:00:00Z"))) {
      setFieldError("fDate", "Enter a valid publication date.");
      valid = false;
    }
    return valid;
  }

  function newRecordId() {
    if (!window.crypto || !window.crypto.getRandomValues) throw new Error("Secure random IDs are unavailable in this browser.");
    if (window.crypto.randomUUID) return "VG-" + window.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return "VG-" + Array.from(bytes).map(function (byte) { return byte.toString(16).padStart(2, "0"); }).join("");
  }

  function getFormMetadata() {
    const metadata = {};
    const course = $("fCourse").value.trim();
    const session = $("fSession").value.trim();
    const additional = $("fMetadata").value.trim();
    if (course) metadata.course = course;
    if (session) metadata.session = session;
    if (additional) metadata.additional = additional;
    return metadata;
  }

  async function publishResult(event) {
    // Validate the existing chain before linking a new user-entered result.
    event.preventDefault();
    if (!validatePublishForm()) {
      toast("Please fix the highlighted fields.", "bad");
      return;
    }
    const button = $("publishBtn");
    button.disabled = true;
    button.textContent = "Hashing and publishing…";
    try {
      const rollNo = $("fId").value.trim();
      const name = $("fName").value.trim();
      const duplicateStudent = state.students.find(function (student) {
        return student && typeof student === "object" && normalized(student.id) === normalized(rollNo);
      });
      if (duplicateStudent && normalized(duplicateStudent.name) !== normalized(name)) {
        throw new Error("This student ID already belongs to a different name.");
      }
      const existingChain = await Ledger.verifyChain(state.records);
      if (!existingChain.valid) {
        throw new Error("Cannot append to an invalid ledger. First issue: " +
          safeText(existingChain.firstIssue.recordId, "unknown record") + " — " + existingChain.firstIssue.reason);
      }
      const fields = {
        id: newRecordId(),
        rollNo: rollNo,
        name: name,
        exam: $("fExam").value.trim(),
        subject: $("fSubject").value.trim(),
        marks: Number($("fMarks").value),
        max: Number($("fMax").value),
        timestamp: $("fDate").value ?
          new Date($("fDate").value + "T00:00:00.000Z").toISOString() : new Date().toISOString(),
        metadata: getFormMetadata()
      };
      const duplicate = Ledger.findDuplicate(state.records, fields);
      if (duplicate) throw new Error(duplicate);
      const response = await fetch(API_BASE + "/api/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          roll_no: rollNo,
          student_name: name,
          subject: fields.subject,
          exam_session: fields.metadata.session || fields.exam || "2025-2026",
          marks: fields.marks
        })
      });
      const serverResult = await response.json();
      if (!response.ok || !serverResult.ok) {
        throw new Error(serverResult.error || "The server could not publish this result.");
      }
      const prevHash = state.records.length ? state.records[state.records.length - 1].hash : Ledger.GENESIS_HASH;
      const record = await Ledger.createRecord(fields, prevHash);
      commitState(function (draft) {
        if (!draft.students.some(function (student) { return normalized(student.id) === normalized(rollNo); })) {
          const course = fields.metadata.course || "";
          draft.students.push({ id: rollNo, name: name, program: course, dept: course, email: "" });
        }
        draft.records.push(record);
        appendAudit(draft, "Result published and hashed", record.id, "Success");
      });
      fillDeptFilter();
      clearPublishForm();
      await renderAll();
      toast("Record " + record.id + " was added to the SHA-256 chain.", "ok");
    } catch (error) {
      toast(error.message || "The record could not be published.", "bad");
    } finally {
      button.disabled = false;
      button.textContent = "Publish result";
    }
  }

  function clearPublishForm() {
    $("publishForm").reset();
    ["fId", "fName", "fExam", "fCourse", "fSubject", "fSession", "fMarks", "fMax", "fDate", "fMetadata"].forEach(function (id) {
      setFieldError(id, "");
    });
  }

  function statusPresentation(status) {
    if (status === Ledger.STATUS.VERIFIED) return { kind: "ok", icon: "shield-check" };
    if (status === Ledger.STATUS.TAMPERED) return { kind: "bad", icon: "shield-alert" };
    if (status === Ledger.STATUS.CORRUPTED) return { kind: "bad", icon: "triangle-alert" };
    return { kind: "bad", icon: "search-x" };
  }

  function renderVerification(result) {
    const presentation = statusPresentation(result.status);
    const record = result.record && typeof result.record === "object" ? result.record : null;
    let html = '<div class="result-box"><div class="result-status ' + presentation.kind + '"><i data-lucide="' +
      presentation.icon + '"></i><span>' + esc(result.status) + "</span></div><p>" + esc(result.reason) + "</p>";
    if (record) {
      html += kvRows([
        ["Record ID", esc(safeText(record.id))],
        ["Student", esc(safeText(record.name)) + " (" + esc(safeText(record.rollNo)) + ")"],
        ["Examination", esc(safeText(record.exam))],
        ["Subject", esc(safeText(record.subject))],
        ["Marks", esc(record.marks) + " / " + esc(record.max)],
        ["SHA-256 hash", '<code class="hash-value">' + esc(record.hash) + "</code>"]
      ]);
    }
    html += '<p class="notice">This verifies only the browser-local hash chain; it is not a server-signed academic credential.</p></div>';
    $("verifyResult").innerHTML = html;
    refreshIcons();
  }

  async function runVerification() {
    // Treat all ID, digest, and pasted QR input as untrusted.
    const button = $("verifyBtn");
    button.disabled = true;
    $("verifyResult").innerHTML = '<div class="empty"><p>Checking the local chain…</p></div>';
    try {
      const result = await Ledger.verifyLookup(state.records, $("vInput").value);
      renderVerification(result);
      try {
        commitState(function (draft) {
          appendAudit(draft, "Record verification attempted", result.record ? result.record.id : "unmatched input",
            result.status === Ledger.STATUS.VERIFIED ? "Success" : "Warning", "Local verifier");
        });
      } catch (error) {
        toast("Verification completed, but its audit event was not saved: " + error.message, "bad");
      }
      await renderAll();
      if (result.status !== Ledger.STATUS.VERIFIED) toast(result.status + ": " + result.reason, "bad");
    } catch (error) {
      const result = { status: Ledger.STATUS.CORRUPTED, reason: error.message || "Verification failed.", record: null };
      renderVerification(result);
      toast(result.reason, "bad");
    } finally {
      button.disabled = false;
    }
  }

  function renderCorrections() {
    const items = state.corrections.filter(function (item) { return item && typeof item === "object"; });
    $("correctionList").innerHTML = items.length ? items.map(function (item) {
      const open = item.status === "Pending";
      return '<div class="card"><div class="corr-top"><div><strong>' + esc(studentName(item.rollNo)) +
        "</strong> <small>" + esc(safeText(item.rollNo)) + " · " + esc(safeText(item.subject)) +
        "</small></div>" + badge(item.status) + "</div>" +
        '<div class="corr-grid"><div><small>Original marks</small>' + esc(item.original) +
        '</div><div><small>Requested marks</small>' + esc(item.requested) +
        '</div><div><small>First approver</small>' + badge(item.first) +
        '</div><div><small>Second approver</small>' + badge(item.second) +
        '</div><div><small>Approval date</small>' + esc(safeText(item.date)) + "</div></div>" +
        '<p class="muted">Reason: ' + esc(safeText(item.reason)) + '</p><br><div class="actions">' +
        '<button class="btn sm primary" data-approve="' + esc(item.id) + '"' + (open ? "" : " disabled") + '>Approve</button>' +
        '<button class="btn sm danger" data-reject="' + esc(item.id) + '"' + (open ? "" : " disabled") + '>Reject</button>' +
        '<button class="btn sm" data-request="' + esc(item.id) + '">View request</button></div></div>';
    }).join("") : '<div class="card empty"><p>No correction requests. Request one from a verified result record.</p></div>';
  }

  function findCorrection(id) {
    return state.corrections.find(function (item) { return item && item.id === id; });
  }

  function viewRequest(id) {
    const correction = findCorrection(id);
    if (!correction) {
      toast("Correction request was not found.", "bad");
      return;
    }
    openModal("Correction request", kvRows([
      ["Student", esc(studentName(correction.rollNo))],
      ["Subject", esc(correction.subject)],
      ["Marks change", esc(correction.original) + " → " + esc(correction.requested)],
      ["Reason", esc(correction.reason)],
      ["First approval", badge(correction.first)],
      ["Second approval", badge(correction.second)],
      ["Status", badge(correction.status)]
    ]), [{ label: "Close" }]);
  }

  function openCorrectionRequest(recordId) {
    const record = findRecord(recordId);
    const index = recordIndex(recordId);
    if (!record || index < 0 || recordStatus(index) !== Ledger.STATUS.VERIFIED) {
      toast("A correction can only be requested for a verified record.", "bad");
      return;
    }
    if (typeof record.max !== "number" || !Number.isFinite(record.max)) {
      toast("A correction requires a numeric maximum-mark value.", "bad");
      return;
    }
    if (state.corrections.some(function (item) {
      return item && item.recordId === recordId && item.status === "Pending";
    })) {
      toast("This record already has a pending correction request.", "warn");
      return;
    }
    const body = '<p>Current marks: <strong>' + esc(record.marks) + "</strong> / " + esc(record.max) + "</p>" +
      '<div class="field"><label for="cRequested">Requested marks</label><input id="cRequested" type="number" min="0" max="' +
      esc(record.max) + '" step="any"></div><div class="field"><label for="cReason">Reason</label><textarea id="cReason" rows="3" maxlength="1000"></textarea></div>' +
      '<span id="cErr" class="err" aria-live="polite"></span>';
    openModal("Request a correction", body, [
      { label: "Cancel" },
      { label: "Submit request", cls: "primary", onClick: function () { return saveCorrectionRequest(record); } }
    ]);
  }

  function saveCorrectionRequest(record) {
    const requested = Number($("cRequested").value);
    const reason = $("cReason").value.trim();
    if ($("cRequested").value === "" || !Number.isFinite(requested) || requested < 0 || requested > record.max) {
      $("cErr").textContent = "Enter a valid mark within the record's maximum.";
      return false;
    }
    if (requested === record.marks) {
      $("cErr").textContent = "Requested marks must differ from the current marks.";
      return false;
    }
    if (!reason) {
      $("cErr").textContent = "A reason is required.";
      return false;
    }
    const correction = {
      id: "COR-" + newRecordId().slice(3),
      recordId: record.id,
      rollNo: record.rollNo,
      subject: record.subject,
      original: record.marks,
      requested: requested,
      reason: reason,
      first: "Pending",
      second: "Pending",
      date: "",
      status: "Pending"
    };
    try {
      commitState(function (draft) {
        draft.corrections.unshift(correction);
        appendAudit(draft, "Correction requested", record.id, "Success");
      });
      renderAll();
      toast("Correction request submitted for two approvals.", "ok");
    } catch (error) {
      $("cErr").textContent = error.message;
      return false;
    }
  }

  function confirmDecision(id, approving) {
    const correction = findCorrection(id);
    if (!correction || correction.status !== "Pending") {
      toast("This correction request is no longer pending.", "warn");
      return;
    }
    openModal(approving ? "Confirm approval" : "Confirm rejection",
      "<p>" + (approving ? "Approve" : "Reject") + " the change from <strong>" +
      esc(correction.original) + "</strong> to <strong>" + esc(correction.requested) +
      "</strong> for " + esc(studentName(correction.rollNo)) + "?</p>" +
      '<p class="notice">This browser-only prototype does not authenticate separate approvers.</p>', [
        { label: "Cancel" },
        {
          label: approving ? "Approve" : "Reject",
          cls: approving ? "primary" : "danger",
          onClick: function () { return applyDecision(id, approving); }
        }
      ]);
  }

  async function rehashChainWithCorrection(recordId, newMarks) {
    // Approved corrections rebuild every descendant hash so links stay consistent.
    const currentReport = await Ledger.verifyChain(state.records);
    if (!currentReport.valid) throw new Error("Cannot approve a correction while the ledger has an integrity issue.");
    let previousHash = Ledger.GENESIS_HASH;
    const rebuilt = [];
    for (const current of state.records) {
      const fields = Object.assign({}, current);
      delete fields.hash;
      delete fields.prevHash;
      if (current.id === recordId) fields.marks = newMarks;
      const record = await Ledger.createRecord(fields, previousHash);
      rebuilt.push(record);
      previousHash = record.hash;
    }
    return rebuilt;
  }

  async function applyDecision(id, approving) {
    const correction = findCorrection(id);
    if (!correction || correction.status !== "Pending") throw new Error("This correction request is no longer pending.");
    if (!approving) {
      commitState(function (draft) {
        const item = draft.corrections.find(function (entry) { return entry.id === id; });
        item.status = "Rejected";
        if (item.first === "Pending") item.first = "Rejected";
        else item.second = "Rejected";
        appendAudit(draft, "Correction rejected", item.recordId, "Warning");
      });
      renderAll();
      toast(id + " was rejected.", "warn");
      return;
    }
    if (correction.first === "Pending") {
      commitState(function (draft) {
        const item = draft.corrections.find(function (entry) { return entry.id === id; });
        item.first = "Approved";
        appendAudit(draft, "First correction approval recorded", item.recordId, "Success");
      });
      renderAll();
      toast("First approval recorded. A second approval is required.", "ok");
      return;
    }
    const updatedRecords = await rehashChainWithCorrection(correction.recordId, correction.requested);
    commitState(function (draft) {
      const item = draft.corrections.find(function (entry) { return entry.id === id; });
      item.second = "Approved";
      item.status = "Approved";
      item.date = new Date().toISOString();
      draft.records = updatedRecords;
      appendAudit(draft, "Correction approved and chain rebuilt", item.recordId, "Success");
    });
    await renderAll();
    toast(id + " was approved; the affected chain was rehashed.", "ok");
  }

  function renderAudit() {
    const text = $("auditSearch").value.toLocaleLowerCase();
    const status = $("auditStatus").value;
    const list = state.auditLogs.filter(function (entry) {
      if (!entry || typeof entry !== "object") return false;
      return safeSearchText([entry.user, entry.action, entry.record, entry.id]).includes(text) &&
        (!status || entry.status === status);
    });
    $("auditBody").innerHTML = list.map(function (entry) {
      return "<tr><td>" + esc(safeText(entry.id)) + "</td><td>" + esc(safeText(entry.user)) +
        "</td><td>" + esc(safeText(entry.action)) + "</td><td>" + esc(safeText(entry.record)) +
        "</td><td>" + esc(safeText(entry.time)) + "</td><td>" + badge(entry.status) + "</td></tr>";
    }).join("");
    $("auditEmpty").hidden = list.length > 0;
  }

  // Re-audit before rendering status badges so storage edits are visible immediately.
  async function renderAll() {
    const version = ++renderVersion;
    const report = await Ledger.verifyChain(state.records);
    if (version !== renderVersion) return;
    integrityReport = report;
    showStorageNotice();
    renderStats();
    renderRecent();
    renderStudents();
    renderLedger();
    renderCorrections();
    renderAudit();
    const hasIssues = integrityReport.entries.some(function (entry) { return entry.status !== Ledger.STATUS.VERIFIED; });
    const hasPending = state.corrections.some(function (item) { return item && item.status === "Pending"; });
    $("bellBtn").querySelector(".dot").hidden =
      !(state.settings.notifyIntegrity && hasIssues) && !(state.settings.notifyCorrections && hasPending);
    refreshIcons();
  }

  function loadSettingsForm() {
    $("sInst").value = state.settings.institution;
    $("sEmail").value = state.settings.contactEmail;
    $("sAdmin").value = state.settings.administrator;
    $("sRole").value = state.settings.role;
    $("notifyIntegrity").checked = state.settings.notifyIntegrity;
    $("notifyCorrections").checked = state.settings.notifyCorrections;
    $("weeklySummary").checked = state.settings.weeklySummary;
    $("sAccent").value = state.settings.accent;
    document.documentElement.style.setProperty("--accent", state.settings.accent);
    $("brandInstitution").textContent = state.settings.institution;
    $("brandAdmin").textContent = state.settings.administrator;
    $("brandRole").textContent = state.settings.role;
  }

  function saveSettings() {
    const email = $("sEmail").value.trim();
    const values = {
      institution: $("sInst").value.trim(),
      contactEmail: email,
      administrator: $("sAdmin").value.trim(),
      role: $("sRole").value.trim(),
      notifyIntegrity: $("notifyIntegrity").checked,
      notifyCorrections: $("notifyCorrections").checked,
      weeklySummary: $("weeklySummary").checked,
      accent: $("sAccent").value
    };
    if (!values.institution || !values.administrator || !values.role ||
        (email && !/^\S+@\S+\.\S+$/.test(email)) || ACCENT_COLORS.indexOf(values.accent) === -1) {
      toast("Enter valid institution, administrator, role, email, and accent settings.", "bad");
      return;
    }
    try {
      commitState(function (draft) {
        draft.settings = values;
        appendAudit(draft, "Settings saved", "Application settings", "Success");
      });
      loadSettingsForm();
      renderAll();
      toast("Settings saved in this browser.", "ok");
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  function showNotifications() {
    const integrityIssues = integrityReport.entries.filter(function (entry) {
      return entry.status !== Ledger.STATUS.VERIFIED;
    });
    const pending = state.corrections.filter(function (item) { return item && item.status === "Pending"; });
    const notices = [];
    if (state.settings.notifyIntegrity && integrityIssues.length) {
      notices.push(integrityIssues.length + " ledger record(s) need integrity review.");
    }
    if (state.settings.notifyCorrections && pending.length) {
      notices.push(pending.length + " correction request(s) are pending.");
    }
    if (!notices.length) notices.push("No new integrity alerts or correction requests.");
    openModal("Notifications", "<ul>" + notices.map(function (item) { return "<li>" + esc(item) + "</li>"; }).join("") + "</ul>", [
      { label: "Close" }
    ]);
    $("bellBtn").querySelector(".dot").hidden = true;
  }

  function setupEvents() {
    $("landingOpenBtn").addEventListener("click", enterApp);
    $("getStartedBtn").addEventListener("click", enterApp);
    $("landingFinalBtn").addEventListener("click", enterApp);
    $("landingReturnBtn").addEventListener("click", returnToLanding);
    $("nav").addEventListener("click", function (event) {
      const button = event.target.closest("[data-view]");
      if (button) showView(button.dataset.view);
    });
    document.addEventListener("click", function (event) {
      const target = event.target.closest("[data-go],[data-student],[data-view-result],[data-download],[data-qr],[data-approve],[data-reject],[data-request]");
      if (!target) return;
      if (target.dataset.go) showView(target.dataset.go);
      else if (target.dataset.student) showStudent(target.dataset.student);
      else if (target.dataset.viewResult) viewResult(target.dataset.viewResult);
      else if (target.dataset.download) downloadRecord(target.dataset.download);
      else if (target.dataset.qr) showQrForRecord(target.dataset.qr);
      else if (target.dataset.approve) confirmDecision(target.dataset.approve, true);
      else if (target.dataset.reject) confirmDecision(target.dataset.reject, false);
      else if (target.dataset.request) viewRequest(target.dataset.request);
    });
    $("menuBtn").addEventListener("click", openMenu);
    $("backdrop").addEventListener("click", closeMenu);
    $("bellBtn").addEventListener("click", showNotifications);
    $("globalSearch").addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      $("ledgerSearch").value = event.target.value;
      currentPage = 1;
      renderLedger();
      showView("ledger");
    });
    $("studentSearch").addEventListener("input", renderStudents);
    $("studentDept").addEventListener("change", renderStudents);
    $("addStudentBtn").addEventListener("click", openAddStudent);
    $("ledgerSearch").addEventListener("input", function () { currentPage = 1; renderLedger(); });
    $("ledgerStatus").addEventListener("change", function () { currentPage = 1; renderLedger(); });
    $("prevPage").addEventListener("click", function () { currentPage -= 1; renderLedger(); });
    $("nextPage").addEventListener("click", function () { currentPage += 1; renderLedger(); });
    $("auditSearch").addEventListener("input", renderAudit);
    $("auditStatus").addEventListener("change", renderAudit);
    $("publishForm").addEventListener("submit", publishResult);
    $("clearBtn").addEventListener("click", function () {
      clearPublishForm();
      toast("Form cleared.");
    });
    $("verifyBtn").addEventListener("click", runVerification);
    $("saveSettings").addEventListener("click", saveSettings);
    $("sAccent").addEventListener("change", function (event) {
      document.documentElement.style.setProperty("--accent", event.target.value);
      toast("Preview updated. Save changes to keep this accent.", "warn");
    });
    $("resetStorageBtn").addEventListener("click", resetDamagedStorage);
    $("modalClose").addEventListener("click", closeModal);
    $("modal").addEventListener("click", function (event) {
      if (event.target === $("modal")) closeModal();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeModal();
        closeMenu();
      }
    });
  }

  loadStoredState();
  setupEvents();
  loadSettingsForm();
  fillDeptFilter();
  showStorageNotice();
  renderAll().catch(function (error) {
    toast("The ledger could not be audited: " + error.message, "bad");
  });
}());
