(function () {
  var box = document.getElementById("share-location");
  var lat = document.getElementById("lat");
  var lng = document.getElementById("lng");
  var note = document.getElementById("loc-status");
  var form = document.getElementById("scan-form");

  if (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("button[type=submit]");
      if (btn) { btn.disabled = true; }
    });
  }
  if (!box || !lat || !lng || !note) { return; }

  if (!navigator.geolocation) {
    var wrapper = box.closest("label");
    if (wrapper) { wrapper.hidden = true; }
    return;
  }
  if (lat.value && lng.value) {
    box.checked = true;
    note.textContent = "Location added (approximate).";
  }
  box.addEventListener("change", function () {
    if (!box.checked) {
      lat.value = "";
      lng.value = "";
      note.textContent = "";
      return;
    }
    note.textContent = "Getting your location...";
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        lat.value = pos.coords.latitude.toFixed(3);
        lng.value = pos.coords.longitude.toFixed(3);
        note.textContent = "Location added (approximate).";
      },
      function () {
        box.checked = false;
        lat.value = "";
        lng.value = "";
        note.textContent = "Could not get your location. You can still send your message.";
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 60000 }
    );
  });
})();
