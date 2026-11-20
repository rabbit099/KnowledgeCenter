// Upload form improvements (the form works without this script, ADR-032): drag and drop onto
// the drop zone, the list of chosen files, and the limits checked before anything is sent.
// No inline handlers, no eval; reattached after HTMX swaps the form (error message).
(function () {
  "use strict";

  function setup(form) {
    if (!form || form.dataset.enhanced) return;
    form.dataset.enhanced = "1";
    var input = form.querySelector('input[type="file"]');
    var zone = form.querySelector(".dropzone");
    var list = form.querySelector(".upload-files");
    var check = form.querySelector(".upload-check");
    var button = form.querySelector('button[type="submit"]');
    var maxFiles = Number(form.dataset.maxFiles);
    var maxFile = Number(form.dataset.maxFileMb) * 1024 * 1024;
    var maxTotal = Number(form.dataset.maxTotalMb) * 1024 * 1024;

    function refresh() {
      var files = Array.prototype.slice.call(input.files || []);
      var total = 0;
      var big = [];
      list.textContent = "";
      files.forEach(function (file) {
        total += file.size;
        var item = document.createElement("li");
        item.textContent = file.name + " (" + Math.ceil(file.size / 1024) + " KB)";
        list.appendChild(item);
        if (file.size > maxFile) big.push(file.name);
      });
      list.hidden = files.length === 0;
      var problems = [];
      if (files.length > maxFiles) problems.push(form.dataset.msgFiles);
      if (total > maxTotal) problems.push(form.dataset.msgTotal);
      if (big.length) problems.push(form.dataset.msgFile + " " + big.join(", "));
      check.textContent = problems.join(" ");
      check.hidden = problems.length === 0;
      // Too many files or too large together: the server would refuse the whole form.
      button.disabled = files.length > maxFiles || total > maxTotal;
    }

    input.addEventListener("change", refresh);
    if (zone && typeof DataTransfer !== "undefined") {
      zone.hidden = false;
      ["dragenter", "dragover"].forEach(function (name) {
        zone.addEventListener(name, function (event) {
          event.preventDefault();
          zone.classList.add("dragover");
        });
      });
      ["dragleave", "drop"].forEach(function (name) {
        zone.addEventListener(name, function () { zone.classList.remove("dragover"); });
      });
      zone.addEventListener("drop", function (event) {
        event.preventDefault();
        var merged = new DataTransfer();
        Array.prototype.forEach.call(input.files || [], function (f) { merged.items.add(f); });
        Array.prototype.forEach.call(event.dataTransfer.files, function (f) { merged.items.add(f); });
        input.files = merged.files;
        refresh();
      });
    }
    refresh();
  }

  function setupAll() { setup(document.getElementById("upload-form")); }
  document.addEventListener("DOMContentLoaded", setupAll);
  document.addEventListener("htmx:afterSwap", setupAll);
})();
