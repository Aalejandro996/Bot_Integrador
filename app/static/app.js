/* Surtia™ — comportamiento mínimo de la interfaz (sin dependencias; la CSP no permite scripts en línea).
   Indicadores de carga: barra superior al navegar o enviar un formulario, botón con giro mientras se procesa, aviso si
   el servidor tarda (p. ej. al despertar) y aviso de descarga para los archivos. */
(function () {
  "use strict";

  // ------------------------------------------------------------------ indicadores de carga
  var barra, aviso, tLento, tFin;
  function nodos() {
    if (barra) return;
    barra = document.createElement("div"); barra.className = "carga-barra"; barra.setAttribute("aria-hidden", "true");
    aviso = document.createElement("div"); aviso.className = "carga-aviso"; aviso.setAttribute("role", "status"); aviso.setAttribute("aria-live", "polite");
    document.body.appendChild(barra); document.body.appendChild(aviso);
  }
  function mostrarAviso(texto) { nodos(); aviso.textContent = texto; aviso.classList.add("visible"); }
  function cargando(texto, duracion) {
    nodos();
    clearTimeout(tLento); clearTimeout(tFin);
    barra.classList.remove("fin"); void barra.offsetWidth; barra.classList.add("activa");
    document.body.setAttribute("aria-busy", "true");
    if (texto) mostrarAviso(texto);
    tLento = setTimeout(function () { mostrarAviso("Esto está tardando un poco más de lo normal… seguimos trabajando."); }, 6000);
    if (duracion) tFin = setTimeout(terminar, duracion);
  }
  function terminar() {
    if (!barra) return;
    clearTimeout(tLento); clearTimeout(tFin);
    barra.classList.add("fin");
    setTimeout(function () { barra.classList.remove("activa", "fin"); }, 400);
    aviso.classList.remove("visible");
    document.body.removeAttribute("aria-busy");
    document.querySelectorAll("button.cargando").forEach(function (b) { b.disabled = false; b.classList.remove("cargando"); b.removeAttribute("aria-busy"); });
  }
  window.addEventListener("pageshow", terminar);          // al volver con «atrás» la página no queda bloqueada
  var esDescarga = function (url) { return /\/(exportar|descargar)(\/|$|\?)/.test(url.pathname) || /\.(csv|pdf|xlsx?|zip|ini|exe|sql|py)$/i.test(url.pathname); };

  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-imprimir]")) window.print();
    var menu = e.target.closest("details.menu");
    document.querySelectorAll("details.menu[open]").forEach(function (d) { if (d !== menu) d.removeAttribute("open"); });

    if (e.target.closest("[data-volver]")) {
      if (document.referrer && document.referrer.indexOf(location.origin) === 0 && history.length > 1) { e.preventDefault(); history.back(); }
      return;
    }
    if (e.target.closest("[data-recargar]")) { e.preventDefault(); cargando("Intentando de nuevo…"); location.reload(); return; }

    var a = e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target === "_blank" || a.hasAttribute("download") || a.hasAttribute("data-sin-carga")) return;
    var url; try { url = new URL(a.href, location.href); } catch (err) { return; }
    if (url.origin !== location.origin) return;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;   // ancla en la misma página
    if (esDescarga(url)) { cargando("Preparando la descarga…", 3500); return; }
    cargando();
  });

  // Formularios: confirmación de acciones delicadas, botón con giro y barra de carga
  document.addEventListener("submit", function (e) {
    var f = e.target, msg = f.getAttribute("data-confirmar");
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    if (e.defaultPrevented) return;
    var b = e.submitter || f.querySelector("button:not([type=button])");
    setTimeout(function () { if (b) { b.disabled = true; b.classList.add("cargando"); b.setAttribute("aria-busy", "true"); } }, 0);
    var accion; try { accion = new URL(f.action || location.href, location.href); } catch (err) { accion = location; }
    var archivo = f.querySelector("input[type=file]");
    if (esDescarga(accion)) cargando("Preparando la descarga…", 3500);
    else cargando(archivo && archivo.files && archivo.files.length ? "Subiendo el archivo…" : null);
  });

  // ------------------------------------------------------------------ vincular WhatsApp por QR
  // Consulta el estado cada 3 s, renueva el QR y recarga al quedar vinculado
  var qr = document.querySelector("[data-qr-estado]");
  if (qr) {
    var intentos = 0, texto = qr.querySelector("[data-qr-texto]");
    var consultar = function () {
      if (++intentos > 100) { texto.textContent = "El código venció. Recargue la página para generar uno nuevo."; return; }
      fetch(qr.getAttribute("data-qr-estado"), { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (d && d.estado === "ready") { cargando("¡WhatsApp vinculado! Abriendo…"); window.location.reload(); return; }
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
        .catch(function () { texto.textContent = "Sin conexión con el servidor. Reintentando…"; setTimeout(consultar, 5000); });
    };
    setTimeout(consultar, 2000);
  }

  // Sin conexión a Internet: se avisa en lugar de dejar la página colgada
  window.addEventListener("offline", function () { mostrarAviso("Se perdió la conexión a Internet. Revise su red."); });
  window.addEventListener("online", function () { if (aviso) aviso.classList.remove("visible"); });
})();
