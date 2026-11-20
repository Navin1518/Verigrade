/* ===== VERIGRADE – frontend demo logic =====
   All data below is synthetic and stored in memory only. */

// ---------- 1. Demo data ----------
const students = [
  { id: "STU-1001", name: "Aarav Mehta", program: "B.Tech CSE", dept: "Computer Science", email: "aarav.mehta@northbridge.edu" },
  { id: "STU-1002", name: "Diya Sharma", program: "B.Tech ECE", dept: "Electronics", email: "diya.sharma@northbridge.edu" },
  { id: "STU-1003", name: "Rohan Verma", program: "BBA", dept: "Management", email: "rohan.verma@northbridge.edu" },
  { id: "STU-1004", name: "Sara Khan", program: "B.Sc Physics", dept: "Science", email: "sara.khan@northbridge.edu" },
  { id: "STU-1005", name: "Kabir Singh", program: "B.Tech ME", dept: "Mechanical", email: "kabir.singh@northbridge.edu" },
  { id: "STU-1006", name: "Meera Nair", program: "B.Com", dept: "Commerce", email: "meera.nair@northbridge.edu" },
  { id: "STU-1007", name: "Ishaan Gupta", program: "B.Tech CSE", dept: "Computer Science", email: "ishaan.gupta@northbridge.edu" },
  { id: "STU-1008", name: "Priya Patel", program: "BBA", dept: "Management", email: "priya.patel@northbridge.edu" }
];

// "ref" is the verification reference used on the Verify screen
const results = [
  { ref: "VG-2001", studentId: "STU-1001", exam: "Semester 4 Final", subject: "Data Structures", marks: 88, max: 100, date: "2026-06-12", status: "Verified" },
  { ref: "VG-2002", studentId: "STU-1002", exam: "Semester 4 Final", subject: "Digital Circuits", marks: 76, max: 100, date: "2026-06-12", status: "Verified" },
  { ref: "VG-2003", studentId: "STU-1003", exam: "Semester 2 Final", subject: "Marketing", marks: 91, max: 100, date: "2026-06-14", status: "Tampered" },
  { ref: "VG-2004", studentId: "STU-1004", exam: "Semester 6 Final", subject: "Quantum Physics", marks: 64, max: 100, date: "2026-06-15", status: "Pending" },
  { ref: "VG-2005", studentId: "STU-1005", exam: "Semester 4 Final", subject: "Thermodynamics", marks: 72, max: 100, date: "2026-06-15", status: "Verified" },
  { ref: "VG-2006", studentId: "STU-1006", exam: "Semester 2 Final", subject: "Accounting", marks: 83, max: 100, date: "2026-06-16", status: "Verified" },
  { ref: "VG-2007", studentId: "STU-1007", exam: "Semester 4 Final", subject: "Algorithms", marks: 94, max: 100, date: "2026-06-16", status: "Verified" },
  { ref: "VG-2008", studentId: "STU-1008", exam: "Semester 2 Final", subject: "Economics", marks: 69, max: 100, date: "2026-06-17", status: "Pending" },
  { ref: "VG-2009", studentId: "STU-1001", exam: "Semester 4 Final", subject: "Operating Systems", marks: 81, max: 100, date: "2026-06-17", status: "Verified" },
  { ref: "VG-2010", studentId: "STU-1002", exam: "Semester 4 Final", subject: "Signals", marks: 58, max: 100, date: "2026-06-18", status: "Verified" },
  { ref: "VG-2011", studentId: "STU-1007", exam: "Semester 4 Final", subject: "Databases", marks: 90, max: 100, date: "2026-06-18", status: "Pending" },
  { ref: "VG-2012", studentId: "STU-1005", exam: "Semester 4 Final", subject: "Fluid Mechanics", marks: 77, max: 100, date: "2026-06-19", status: "Verified" }
];

const corrections = [
  { id: "COR-301", studentId: "STU-1004", subject: "Quantum Physics", original: 64, requested: 68, reason: "Totalling error on question 4.", first: "Pending", second: "Pending", date: "-", status: "Pending" },
  { id: "COR-302", studentId: "STU-1008", subject: "Economics", original: 69, requested: 72, reason: "Re-evaluation requested by student.", first: "Approved", second: "Pending", date: "-", status: "Pending" },
  { id: "COR-303", studentId: "STU-1006", subject: "Accounting", original: 80, requested: 83, reason: "Missed marks on practical component.", first: "Approved", second: "Approved", date: "2026-06-20", status: "Approved" }
];

const auditLogs = [
  { id: "ACT-5001", user: "Dr. Anita Rao", action: "Result published", record: "VG-2012", time: "2026-06-19 10:42", status: "Success" },
  { id: "ACT-5002", user: "Registrar Office", action: "Correction requested", record: "COR-301", time: "2026-06-19 11:05", status: "Success" },
  { id: "ACT-5003", user: "Dr. Anita Rao", action: "Approval completed", record: "COR-303", time: "2026-06-20 09:15", status: "Success" },
  { id: "ACT-5004", user: "Public Verifier", action: "Verification attempted", record: "VG-2003", time: "2026-06-20 12:30", status: "Warning" },
  { id: "ACT-5005", user: "System", action: "Integrity alert generated", record: "VG-2003", time: "2026-06-20 12:31", status: "Alert" },
  { id: "ACT-5006", user: "Public Verifier", action: "Verification attempted", record: "VG-2001", time: "2026-06-21 08:50", status: "Success" }
];

// ---------- 2. Small helpers ----------
const PAGE_SIZE = 5;
let currentPage = 1;
let pendingAction = null;

// Short version of document.getElementById
function $(id) { return document.getElementById(id); }

// Escape text so user input cannot inject HTML
function esc(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function studentName(studentId) {
  const student = students.find(function (s) { return s.id === studentId; });
  return student ? student.name : "Unknown";
}

// Coloured badge for any status word
function badge(status) {
  const colours = { Verified: "ok", Success: "ok", Approved: "ok", Pending: "warn", Warning: "warn", Tampered: "bad", Alert: "bad", Rejected: "bad" };
  return '<span class="badge ' + (colours[status] || "warn") + '">' + status + "</span>";
}

function refreshIcons() { if (window.lucide) lucide.createIcons(); }

// Small pop-up message. type: ok | bad | warn | (blank = info)
function toast(message, type) {
  const el = document.createElement("div");
  el.className = "toast " + (type || "");
  el.textContent = message;
  $("toasts").appendChild(el);
  setTimeout(function () { el.remove(); }, 3500);
}

function addAudit(action, record, status) {
  const count = 5001 + auditLogs.length;
  const now = new Date().toISOString().slice(0, 16).replace("T", " ");
  auditLogs.unshift({ id: "ACT-" + count, user: "Dr. Anita Rao", action: action, record: record, time: now, status: status });
}

// ---------- 3. Navigation ----------
function showView(viewId) {
  document.querySelectorAll(".view").forEach(function (v) { v.classList.toggle("active", v.id === viewId); });
  document.querySelectorAll(".nav-item").forEach(function (b) {
    const active = b.dataset.view === viewId;
    b.classList.toggle("active", active);
    if (active) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  closeMenu();
  window.scrollTo(0, 0);
}

function openMenu() { $("sidebar").classList.add("open"); }
function closeMenu() { $("sidebar").classList.remove("open"); }

// ---------- 4. Modal ----------
// buttons: [{ label, cls, onClick }]  – onClick returns false to keep the modal open
function openModal(title, bodyHtml, buttons) {
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML = bodyHtml;
  $("modalFoot").innerHTML = "";
  buttons.forEach(function (info) {
    const btn = document.createElement("button");
    btn.className = "btn " + (info.cls || "");
    btn.textContent = info.label;
    btn.addEventListener("click", function () {
      const keepOpen = info.onClick ? info.onClick() === false : false;
      if (!keepOpen) closeModal();
    });
    $("modalFoot").appendChild(btn);
  });
  $("modal").hidden = false;
  refreshIcons();
  const first = $("modalBody").querySelector("input") || $("modalFoot").lastChild;
  if (first) first.focus();
}

function closeModal() { $("modal").hidden = true; }

// Rows of label/value pairs used in several modals
function kvRows(pairs) {
  return pairs.map(function (p) { return '<div class="kv"><span>' + p[0] + "</span><span>" + p[1] + "</span></div>"; }).join("");
}

// ---------- 5. Overview ----------
function renderStats() {
  const published = results.filter(function (r) { return r.status === "Verified"; }).length;
  const pendingApprovals = corrections.filter(function (c) { return c.status === "Pending"; }).length;
  const alerts = results.filter(function (r) { return r.status === "Tampered"; }).length;
  const cards = [
    ["users", students.length, "Total Students"],
    ["file-text", results.length, "Total Results"],
    ["badge-check", published, "Published Results"],
    ["clock", pendingApprovals, "Pending Approvals"],
    ["triangle-alert", alerts, "Integrity Alerts"]
  ];
  $("stats").innerHTML = cards.map(function (c) {
    return '<div class="card stat"><i data-lucide="' + c[0] + '"></i><strong>' + c[1] + "</strong><span>" + c[2] + "</span></div>";
  }).join("");

  // Integrity ring and verification overview
  const percent = Math.round(((results.length - alerts) / results.length) * 100);
  $("integrity").innerHTML = '<div class="ring">' + percent + '%</div><div><strong>' + (alerts ? alerts + " record needs review" : "Ledger looks healthy") + "</strong><p class=\"muted\">Based on demo ledger statuses.</p></div>";
  const pending = results.filter(function (r) { return r.status === "Pending"; }).length;
  $("verifyOverview").innerHTML = [["Verified", published], ["Pending", pending], ["Tampered", alerts]].map(function (x) {
    return "<div><strong>" + x[1] + "</strong><small>" + x[0] + "</small></div>";
  }).join("");
}

function renderRecent() {
  $("recentBody").innerHTML = results.slice().reverse().slice(0, 5).map(function (r) {
    return "<tr><td>" + esc(studentName(r.studentId)) + "</td><td>" + esc(r.exam) + "</td><td>" + r.marks + "/" + r.max + "</td><td>" + badge(r.status) + "</td></tr>";
  }).join("");
  $("activityList").innerHTML = auditLogs.slice(0, 5).map(function (a) {
    return '<li><i data-lucide="activity"></i><div>' + esc(a.action) + " · " + esc(a.record) + "<br><small>" + esc(a.user) + ", " + a.time + "</small></div></li>";
  }).join("");
  const pending = corrections.filter(function (c) { return c.status === "Pending"; });
  $("pendingList").innerHTML = pending.length ? pending.map(function (c) {
    return '<div class="pending-item"><span>' + esc(studentName(c.studentId)) + " · " + c.original + " → " + c.requested + '</span><button class="btn sm" data-go="corrections">Review</button></div>';
  }).join("") : '<div class="empty"><p>No pending corrections.</p></div>';
}

// ---------- 6. Students ----------
function fillDeptFilter() {
  const select = $("studentDept");
  const chosen = select.value;
  const depts = Array.from(new Set(students.map(function (s) { return s.dept; }))).sort();
  select.innerHTML = '<option value="">All departments</option>' + depts.map(function (d) { return "<option>" + esc(d) + "</option>"; }).join("");
  select.value = chosen;
}

function renderStudents() {
  const text = $("studentSearch").value.toLowerCase();
  const dept = $("studentDept").value;
  const list = students.filter(function (s) {
    const haystack = (s.id + s.name + s.email).toLowerCase();
    return haystack.includes(text) && (!dept || s.dept === dept);
  });
  $("studentBody").innerHTML = list.map(function (s) {
    return "<tr><td>" + esc(s.id) + "</td><td>" + esc(s.name) + "</td><td>" + esc(s.program) + "</td><td>" + esc(s.dept) + "</td><td>" + esc(s.email) + '</td><td><button class="btn sm" data-student="' + esc(s.id) + '">View details</button></td></tr>';
  }).join("");
  $("studentEmpty").hidden = list.length > 0;
  refreshIcons();
}

function showStudent(studentId) {
  const s = students.find(function (x) { return x.id === studentId; });
  const count = results.filter(function (r) { return r.studentId === studentId; }).length;
  openModal("Student details", kvRows([["Student ID", esc(s.id)], ["Name", esc(s.name)], ["Program", esc(s.program)], ["Department", esc(s.dept)], ["Email", esc(s.email)], ["Results on ledger", count]]), [{ label: "Close" }]);
}

function openAddStudent() {
  const body = '<div class="field"><label for="nName">Full name</label><input id="nName"></div>' +
    '<div class="field"><label for="nProgram">Program</label><input id="nProgram" placeholder="e.g. B.Tech CSE"></div>' +
    '<div class="field"><label for="nDept">Department</label><input id="nDept" placeholder="e.g. Computer Science"></div>' +
    '<div class="field"><label for="nEmail">Email</label><input id="nEmail" type="email"></div>' +
    '<span class="err" id="nErr"></span>';
  openModal("Add student", body, [{ label: "Cancel" }, { label: "Add student", cls: "primary", onClick: saveStudent }]);
}

function saveStudent() {
  const name = $("nName").value.trim();
  const program = $("nProgram").value.trim();
  const dept = $("nDept").value.trim();
  const email = $("nEmail").value.trim();
  if (!name || !program || !dept || !/^\S+@\S+\.\S+$/.test(email)) {
    $("nErr").textContent = "Fill in every field and enter a valid email address.";
    return false; // keep modal open
  }
  const id = "STU-" + (1001 + students.length);
  students.push({ id: id, name: name, program: program, dept: dept, email: email });
  addAudit("Student added", id, "Success");
  fillDeptFilter();
  renderAll();
  toast(name + " was added as " + id + ".", "ok");
}

// ---------- 7. Results ledger ----------
function filteredResults() {
  const text = $("ledgerSearch").value.toLowerCase();
  const status = $("ledgerStatus").value;
  return results.filter(function (r) {
    const haystack = (r.studentId + studentName(r.studentId) + r.exam + r.subject + r.ref).toLowerCase();
    return haystack.includes(text) && (!status || r.status === status);
  });
}

function renderLedger() {
  const list = filteredResults();
  const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
  if (currentPage > pages) currentPage = pages;
  const start = (currentPage - 1) * PAGE_SIZE;
  $("ledgerBody").innerHTML = list.slice(start, start + PAGE_SIZE).map(function (r) {
    return "<tr><td>" + esc(r.studentId) + "</td><td>" + esc(studentName(r.studentId)) + "</td><td>" + esc(r.exam) + "</td><td>" + esc(r.subject) + "</td><td>" + r.marks + "</td><td>" + r.max + "</td><td>" + r.date + "</td><td>" + badge(r.status) +
      '</td><td><button class="btn sm" data-view-result="' + r.ref + '">View</button> <button class="btn sm" data-download="' + r.ref + '">Download</button></td></tr>';
  }).join("");
  $("ledgerEmpty").hidden = list.length > 0;
  $("pageInfo").textContent = list.length ? "Page " + currentPage + " of " + pages + " · " + list.length + " records" : "0 records";
  $("prevPage").disabled = currentPage <= 1;
  $("nextPage").disabled = currentPage >= pages;
}

function viewResult(ref) {
  const r = results.find(function (x) { return x.ref === ref; });
  openModal("Result details", kvRows([["Reference", r.ref], ["Student", esc(studentName(r.studentId)) + " (" + esc(r.studentId) + ")"], ["Examination", esc(r.exam)], ["Subject", esc(r.subject)], ["Marks", r.marks + " / " + r.max], ["Published", r.date], ["Ledger status", badge(r.status)]]), [{ label: "Close" }]);
}

function downloadMarksheet(button, ref) {
  button.disabled = true;
  button.textContent = "Preparing…";
  setTimeout(function () {
    button.disabled = false;
    button.textContent = "Download";
    toast("Marksheet for " + ref + " is ready (demo action, no file is created).", "ok");
  }, 800);
}

// ---------- 8. Publish result ----------
function setFieldError(inputId, message) {
  const input = $(inputId);
  input.parentElement.classList.toggle("invalid", Boolean(message));
  input.parentElement.querySelector(".err").textContent = message || "";
}

function validatePublishForm() {
  let valid = true;
  ["fId", "fName", "fExam", "fCourse", "fSubject", "fSession", "fDate"].forEach(function (id) {
    const empty = $(id).value.trim() === "";
    setFieldError(id, empty ? "This field is required." : "");
    if (empty) valid = false;
  });
  if ($("fId").value.trim() && !/^STU-\d{3,}$/i.test($("fId").value.trim())) {
    setFieldError("fId", "Use the format STU-1001."); valid = false;
  }
  const marks = Number($("fMarks").value);
  const max = Number($("fMax").value);
  if ($("fMarks").value === "" || marks < 0) { setFieldError("fMarks", "Enter marks of 0 or more."); valid = false; } else { setFieldError("fMarks", ""); }
  if ($("fMax").value === "" || max < 1) { setFieldError("fMax", "Maximum marks must be at least 1."); valid = false; } else { setFieldError("fMax", ""); }
  if (valid && marks > max) { setFieldError("fMarks", "Marks cannot be higher than maximum marks."); valid = false; }
  return valid;
}

function publishResult(event) {
  event.preventDefault();
  if (!validatePublishForm()) { toast("Please fix the highlighted fields.", "bad"); return; }
  const button = $("publishBtn");
  button.disabled = true;
  button.textContent = "Publishing…";
  setTimeout(function () {
    const ref = "VG-" + (2001 + results.length);
    results.push({ ref: ref, studentId: $("fId").value.trim().toUpperCase(), exam: $("fExam").value.trim(), subject: $("fSubject").value.trim(), marks: Number($("fMarks").value), max: Number($("fMax").value), date: $("fDate").value, status: "Pending" });
    addAudit("Result published", ref, "Success");
    renderAll();
    clearPublishForm();
    button.disabled = false;
    button.textContent = "Publish result";
    toast("Published " + ref + " to the demo ledger.", "ok");
  }, 700);
}

function clearPublishForm() {
  $("publishForm").reset();
  ["fId", "fName", "fExam", "fCourse", "fSubject", "fSession", "fMarks", "fMax", "fDate"].forEach(function (id) { setFieldError(id, ""); });
}

// ---------- 9. Verify result (simulated) ----------
function runVerification() {
  const id = $("vId").value.trim().toUpperCase();
  const ref = $("vRef").value.trim().toUpperCase();
  if (!id || !ref) { toast("Enter both a student ID and a verification reference.", "warn"); return; }
  const button = $("verifyBtn");
  button.disabled = true;
  $("verifyResult").innerHTML = '<div class="empty"><p>Checking demo ledger…</p></div>';
  setTimeout(function () {
    button.disabled = false;
    const record = results.find(function (r) { return r.ref === ref && r.studentId === id; });
    let kind = "bad", icon = "search-x", title = "Record not found", note = "No record matches this ID and reference. Check the details and try again.";
    if (record && record.status === "Verified") { kind = "ok"; icon = "shield-check"; title = "Verified"; note = "This record matches the demo ledger."; }
    if (record && record.status === "Tampered") { kind = "bad"; icon = "shield-alert"; title = "Tampered"; note = "This record was flagged as changed in the demo ledger."; }
    if (record && record.status === "Pending") { kind = "warn"; icon = "shield-question"; title = "Invalid record"; note = "This record is still pending and cannot be confirmed yet."; }
    let html = '<div class="result-box"><div class="result-status ' + kind + '"><i data-lucide="' + icon + '"></i>' + title + "</div><p class=\"muted\">" + note + "</p>";
    if (record) html += kvRows([["Student", esc(studentName(record.studentId))], ["Examination", esc(record.exam)], ["Subject", esc(record.subject)], ["Marks", record.marks + " / " + record.max], ["Record status", badge(record.status)]]);
    html += '<p class="notice">Simulated demo result. No cryptographic check was performed.</p></div>';
    $("verifyResult").innerHTML = html;
    addAudit("Verification attempted", ref, kind === "ok" ? "Success" : "Warning");
    renderAll();
    refreshIcons();
  }, 800);
}

// ---------- 10. Corrections & approvals ----------
function renderCorrections() {
  $("correctionList").innerHTML = corrections.map(function (c) {
    const open = c.status === "Pending";
    return '<div class="card"><div class="corr-top"><div><strong>' + esc(studentName(c.studentId)) + "</strong> <small>" + c.studentId + " · " + esc(c.subject) + "</small></div>" + badge(c.status) + "</div>" +
      '<div class="corr-grid"><div><small>Original marks</small>' + c.original + "</div><div><small>Requested marks</small>" + c.requested + "</div><div><small>First approver</small>" + badge(c.first) + "</div><div><small>Second approver</small>" + badge(c.second) + "</div><div><small>Approval date</small>" + c.date + "</div></div>" +
      '<p class="muted">Reason: ' + esc(c.reason) + '</p><br><div class="actions"><button class="btn sm primary" data-approve="' + c.id + '"' + (open ? "" : " disabled") + '>Approve</button><button class="btn sm danger" data-reject="' + c.id + '"' + (open ? "" : " disabled") + '>Reject</button><button class="btn sm" data-request="' + c.id + '">View request</button></div></div>';
  }).join("");
}

function findCorrection(id) { return corrections.find(function (c) { return c.id === id; }); }

function viewRequest(id) {
  const c = findCorrection(id);
  openModal("Correction request " + c.id, kvRows([["Student", esc(studentName(c.studentId))], ["Subject", esc(c.subject)], ["Marks change", c.original + " → " + c.requested], ["Reason", esc(c.reason)], ["First approver", badge(c.first)], ["Second approver", badge(c.second)], ["Status", badge(c.status)]]), [{ label: "Close" }]);
}

function confirmDecision(id, decision) {
  const c = findCorrection(id);
  const approving = decision === "approve";
  openModal(approving ? "Confirm approval" : "Confirm rejection", "<p>" + (approving ? "Approve" : "Reject") + " the change from <strong>" + c.original + "</strong> to <strong>" + c.requested + "</strong> for " + esc(studentName(c.studentId)) + "?</p><p class=\"muted\">This only changes the demo data.</p>", [
    { label: "Cancel" },
    { label: approving ? "Approve" : "Reject", cls: approving ? "primary" : "danger", onClick: function () { applyDecision(c, approving); } }
  ]);
}

function applyDecision(c, approving) {
  if (!approving) {
    c.status = "Rejected";
    if (c.first === "Pending") c.first = "Rejected"; else c.second = "Rejected";
    addAudit("Correction rejected", c.id, "Warning");
    toast(c.id + " was rejected.", "warn");
  } else if (c.first === "Pending") {
    c.first = "Approved";
    addAudit("First approval completed", c.id, "Success");
    toast("First approval recorded. One more approver is needed.", "ok");
  } else {
    c.second = "Approved";
    c.status = "Approved";
    c.date = new Date().toISOString().slice(0, 10);
    addAudit("Approval completed", c.id, "Success");
    toast(c.id + " is fully approved.", "ok");
  }
  renderAll();
}

// ---------- 11. Audit logs ----------
function renderAudit() {
  const text = $("auditSearch").value.toLowerCase();
  const status = $("auditStatus").value;
  const list = auditLogs.filter(function (a) {
    return (a.user + a.action + a.record + a.id).toLowerCase().includes(text) && (!status || a.status === status);
  });
  $("auditBody").innerHTML = list.map(function (a) {
    return "<tr><td>" + a.id + "</td><td>" + esc(a.user) + "</td><td>" + esc(a.action) + "</td><td>" + esc(a.record) + "</td><td>" + a.time + "</td><td>" + badge(a.status) + "</td></tr>";
  }).join("");
  $("auditEmpty").hidden = list.length > 0;
}

// ---------- 12. Render everything and wire up events ----------
function renderAll() {
  renderStats(); renderRecent(); renderStudents(); renderLedger(); renderCorrections(); renderAudit();
  refreshIcons();
}

function setupEvents() {
  // Sidebar, mobile menu and any button with data-go
  $("nav").addEventListener("click", function (e) { const b = e.target.closest("[data-view]"); if (b) showView(b.dataset.view); });
  document.addEventListener("click", function (e) {
    const go = e.target.closest("[data-go]");
    if (go) showView(go.dataset.go);
    const stu = e.target.closest("[data-student]");
    if (stu) showStudent(stu.dataset.student);
    const view = e.target.closest("[data-view-result]");
    if (view) viewResult(view.dataset.viewResult);
    const dl = e.target.closest("[data-download]");
    if (dl) downloadMarksheet(dl, dl.dataset.download);
    const ap = e.target.closest("[data-approve]");
    if (ap) confirmDecision(ap.dataset.approve, "approve");
    const rj = e.target.closest("[data-reject]");
    if (rj) confirmDecision(rj.dataset.reject, "reject");
    const rq = e.target.closest("[data-request]");
    if (rq) viewRequest(rq.dataset.request);
  });
  $("menuBtn").addEventListener("click", openMenu);
  $("backdrop").addEventListener("click", closeMenu);

  // Top bar
  $("bellBtn").addEventListener("click", function () {
    const alerts = results.filter(function (r) { return r.status === "Tampered"; }).length;
    toast("You have " + alerts + " integrity alert(s) and " + corrections.filter(function (c) { return c.status === "Pending"; }).length + " pending approval(s).", "warn");
    document.querySelector(".dot").hidden = true;
  });
  $("globalSearch").addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    $("ledgerSearch").value = e.target.value;
    currentPage = 1;
    renderLedger();
    showView("ledger");
  });

  // Students, ledger and audit filters
  $("studentSearch").addEventListener("input", renderStudents);
  $("studentDept").addEventListener("change", renderStudents);
  $("addStudentBtn").addEventListener("click", openAddStudent);
  $("ledgerSearch").addEventListener("input", function () { currentPage = 1; renderLedger(); });
  $("ledgerStatus").addEventListener("change", function () { currentPage = 1; renderLedger(); });
  $("prevPage").addEventListener("click", function () { currentPage--; renderLedger(); });
  $("nextPage").addEventListener("click", function () { currentPage++; renderLedger(); });
  $("auditSearch").addEventListener("input", renderAudit);
  $("auditStatus").addEventListener("change", renderAudit);

  // Forms and verification
  $("publishForm").addEventListener("submit", publishResult);
  $("clearBtn").addEventListener("click", function () { clearPublishForm(); toast("Form cleared."); });
  $("verifyBtn").addEventListener("click", runVerification);

  // Settings
  $("saveSettings").addEventListener("click", function () { toast("Settings saved (demo only).", "ok"); });
  $("sAccent").addEventListener("change", function (e) { document.documentElement.style.setProperty("--blue", e.target.value); toast("Accent colour updated."); });

  // Modal
  $("modalClose").addEventListener("click", closeModal);
  $("modal").addEventListener("click", function (e) { if (e.target === $("modal")) closeModal(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeModal(); closeMenu(); } });
}

fillDeptFilter();
setupEvents();
renderAll();