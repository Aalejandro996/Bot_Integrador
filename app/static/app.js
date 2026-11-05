/* Pedyra™ — comportamiento mínimo de la interfaz (sin dependencias; la CSP no permite scripts en línea). */
(function () {
  "use strict";

  // Confirmación antes de acciones delicadas (desvincular, renovar clave…)
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute("data-confirmar");
    if (msg && !window.confirm(msg)) e.preventDefault();
    var b = e.target.querySelector("button:not([type=button])");
    if (!e.defaultPrevented && b) setTimeout(function () { b.disabled = true; b.classList.add("cargando"); }, 0);
  });

  // Imprimir documentos
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-imprimir]")) window.print();
    var menu = e.target.closest("details.menu");
    document.querySelectorAll("details.menu[open]").forEach(function (d) { if (d !== menu) d.removeAttribute("open"); });
  });

  // Vincular WhatsApp por QR: consulta el estado cada 3 s, renueva el QR y recarga al quedar vinculado
  var qr = document.querySelector("[data-qr-estado]");
  if (qr) {
    var intentos = 0, texto = qr.querySelector("[data-qr-texto]");
    var consultar = function () {
      if (++intentos > 100) { texto.textContent = "El código venció. Recargue la página para generar uno nuevo."; return; }
      fetch(qr.getAttribute("data-qr-estado"), { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (d && d.estado === "ready") { window.location.reload(); return; }
          if (d && d.qr) {
            var img = qr.querySelector("[data-qr-img]");
            if (img.tagName !== "IMG") {
              var n = document.createElement("img"); n.width = n.height = 264; n.alt = "Código QR para vincular el WhatsApp de la empresa";
              n.setAttribute("data-qr-img", ""); img.replaceWith(n); img = n;
            }
            if (img.getAttribute("src") !== d.qr) img.src = d.qr;
          }
          if (d && d.texto) texto.textContent = d.estado === "qr_ready" ? "Esperando el escaneo…" : d.texto;
          setTimeout(consultar, 3000);
        })
        .catch(function () { setTimeout(consultar, 5000); });
    };
    setTimeout(consultar, 2000);
  }
})();
