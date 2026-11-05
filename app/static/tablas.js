/* Surtia™ — tablas: búsqueda, filtros por columna, rango de fechas, orden por columna, paginación y exportación a CSV.
   Mejora progresiva: sin JavaScript la tabla se ve completa. El estado (búsqueda, filtros, página) se recuerda por pestaña. */
(function () {
  "use strict";
  var usoIcono = document.querySelector("svg.i use");
  var SPRITE = usoIcono ? usoIcono.getAttribute("href").split("#")[0] : "/static/iconos.svg";
  var ico = function (n) { return '<svg class="i" aria-hidden="true"><use href="' + SPRITE + "#" + n + '"></use></svg>'; };
  var norm = function (t) { return (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim(); };
  var numero = function (t) {           // «1.880,00», «Bs 45,50», «USD 3,10», «12» → número; lo demás → null
    var s = (t || "").replace(/^\s*(bs\.?|usd|us\$|eur|ves)\s*/i, "").replace(/\s*(bs|usd|%)\s*$/i, "").trim();
    if (!/^-?[\d.]*\d(,\d+)?$/.test(s)) return null;
    return parseFloat(s.replace(/\./g, "").replace(",", "."));
  };
  var FECHA = /^\d{4}-\d{2}-\d{2}/;
  var guardar = function (k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* sin almacenamiento */ } };
  var leer = function (k) { try { return JSON.parse(sessionStorage.getItem(k) || "null"); } catch (e) { return null; } };
  var el = function (tag, cls, html) { var n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };

  document.querySelectorAll(".tabla-wrap[data-tabla]").forEach(function (wrap, iTabla) {
    var tabla = wrap.querySelector("table"), tbody = tabla.tBodies[0];
    var filas = Array.prototype.filter.call(tbody.rows, function (r) { return !r.querySelector(".vacio"); });
    if (!filas.length) return;
    var ths = Array.prototype.slice.call(tabla.tHead.rows[0].cells);
    var textos = filas.map(function (r) { return Array.prototype.map.call(r.cells, function (c) { return c.textContent.replace(/\s+/g, " ").trim(); }); });
    var datos = filas.map(function (r, i) { return { tr: r, t: textos[i], n: norm(textos[i].join(" ")), orden: i }; });
    var simple = wrap.hasAttribute("data-tabla-simple");
    var clave = "tabla:" + location.pathname + ":" + iTabla;
    var est = leer(clave) || { q: "", f: {}, desde: "", hasta: "", col: -1, dir: 1, pag: 1, tam: 25 };

    // columnas con pocos valores distintos → filtro desplegable; columna de fechas → rango Desde / Hasta
    var filtros = [], colFecha = -1;
    ths.forEach(function (th, j) {
      var vals = textos.map(function (t) { return t[j] || ""; });
      if (colFecha < 0 && vals.filter(function (v) { return FECHA.test(v); }).length >= Math.max(1, vals.length * 0.6)) { colFecha = j; return; }
      if (th.classList.contains("n") || j === 0) return;
      var distintos = Array.from(new Set(vals.filter(function (v) { return v && v !== "—"; }))).sort(function (a, b) { return a.localeCompare(b, "es"); });
      if (distintos.length >= 2 && distintos.length <= 12 && distintos.length < vals.length) filtros.push({ j: j, nombre: th.textContent.trim(), vals: distintos });
    });

    if (colFecha >= 0) filas.forEach(function (r) { if (r.cells[colFecha]) r.cells[colFecha].classList.add("fecha"); });
    // ---------------------------------------------------------- barra de herramientas
    var herr = el("div", "tabla-herr");
    var buscar = el("label", "tabla-buscar", ico("buscar") + '<input type="search" placeholder="Buscar en ' + filas.length + ' registros…" aria-label="Buscar en la tabla">');
    var inpQ = buscar.querySelector("input"); inpQ.value = est.q || "";
    herr.appendChild(buscar);
    var selects = filtros.map(function (f) {
      var s = el("select", "tabla-filtro"); s.setAttribute("aria-label", "Filtrar por " + f.nombre);
      s.innerHTML = '<option value="">' + f.nombre + ": todos</option>" + f.vals.map(function (v) { return "<option></option>"; }).join("");
      f.vals.forEach(function (v, k) { s.options[k + 1].value = v; s.options[k + 1].textContent = v; });
      s.value = (est.f || {})[f.j] || ""; s.dataset.col = f.j; herr.appendChild(s); return s;
    });
    var desde, hasta;
    if (colFecha >= 0 && filas.length > 5) {
      var rango = el("div", "tabla-rango", ico("reloj") + '<span>Desde</span><input type="date" aria-label="Desde"><span>Hasta</span><input type="date" aria-label="Hasta">');
      desde = rango.querySelectorAll("input")[0]; hasta = rango.querySelectorAll("input")[1];
      desde.value = est.desde || ""; hasta.value = est.hasta || ""; herr.appendChild(rango);
    }
    var acc = el("div", "tabla-acc");
    var limpiar = el("button", "btn sec btn-peq", ico("recargar") + "Limpiar"); limpiar.type = "button";
    var csv = el("button", "btn sec btn-peq", ico("descarga") + "Excel (CSV)"); csv.type = "button";
    acc.appendChild(limpiar); acc.appendChild(csv); herr.appendChild(acc);
    if (!simple) wrap.parentNode.insertBefore(herr, wrap);

    // ---------------------------------------------------------- pie: conteo, tamaño de página y páginas
    var pie = el("div", "tabla-pie");
    var conteo = el("span", "tabla-conteo"); conteo.setAttribute("aria-live", "polite");
    var tam = el("select", "tabla-tam", [10, 25, 50, 100].map(function (n) { return '<option value="' + n + '">' + n + " por página</option>"; }).join(""));
    tam.setAttribute("aria-label", "Registros por página"); tam.value = String(est.tam || 25);
    var pags = el("nav", "tabla-pags"); pags.setAttribute("aria-label", "Páginas");
    pie.appendChild(conteo); pie.appendChild(tam); pie.appendChild(pags);
    if (!simple) wrap.parentNode.insertBefore(pie, wrap.nextSibling);
    var sinRes = el("tr", "tabla-sin", '<td colspan="' + ths.length + '" class="vacio">No hay resultados con estos filtros. <button type="button" class="enlace-btn">Limpiar filtros</button></td>');

    // ---------------------------------------------------------- orden por columna
    ths.forEach(function (th, j) {
      th.classList.add("ordenable"); th.tabIndex = 0; th.setAttribute("role", "columnheader");
      th.insertAdjacentHTML("beforeend", '<span class="orden-ico" aria-hidden="true"></span>');
      var ordenar = function () { est.dir = est.col === j ? -est.dir : 1; est.col = j; est.pag = 1; pintar(); };
      th.addEventListener("click", ordenar);
      th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ordenar(); } });
    });

    var visibles = [];
    function pintar() {
      var q = norm(inpQ.value);
      est.q = inpQ.value; est.f = {}; selects.forEach(function (s) { if (s.value) est.f[s.dataset.col] = s.value; });
      est.desde = desde ? desde.value : ""; est.hasta = hasta ? hasta.value : ""; est.tam = parseInt(tam.value, 10) || 25;
      var palabras = q.split(/\s+/).filter(Boolean);
      visibles = datos.filter(function (d) {
        if (palabras.some(function (p) { return d.n.indexOf(p) < 0; })) return false;
        for (var c in est.f) if (d.t[c] !== est.f[c]) return false;
        if (colFecha >= 0) { var f = (d.t[colFecha] || "").slice(0, 10); if (est.desde && f < est.desde) return false; if (est.hasta && f > est.hasta) return false; }
        return true;
      });
      if (est.col >= 0) {
        var j = est.col, num = visibles.every(function (d) { var v = d.t[j]; return !v || v === "—" || numero(v) !== null; });
        visibles.sort(function (a, b) {
          var x = a.t[j] || "", y = b.t[j] || "";
          var r = num ? (numero(x) || 0) - (numero(y) || 0) : x.localeCompare(y, "es", { numeric: true, sensitivity: "base" });
          return (r || a.orden - b.orden) * est.dir;
        });
      } else visibles.sort(function (a, b) { return a.orden - b.orden; });
      ths.forEach(function (th, k) { th.setAttribute("aria-sort", k === est.col ? (est.dir > 0 ? "ascending" : "descending") : "none"); });

      var total = visibles.length, porPag = simple ? total || 1 : est.tam, nPags = Math.max(1, Math.ceil(total / porPag));
      est.pag = Math.min(Math.max(1, est.pag || 1), nPags);
      var ini = (est.pag - 1) * porPag, fin = Math.min(total, ini + porPag);
      var frag = document.createDocumentFragment();
      visibles.slice(ini, fin).forEach(function (d) { frag.appendChild(d.tr); });
      while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
      tbody.appendChild(total ? frag : sinRes);
      var filtrado = total !== datos.length;
      conteo.innerHTML = total ? "Mostrando <b>" + (ini + 1) + "–" + fin + "</b> de <b>" + total + "</b>" + (filtrado ? " (filtrados de " + datos.length + ")" : "") : "Sin resultados";
      limpiar.hidden = !(q || Object.keys(est.f).length || est.desde || est.hasta || est.col >= 0);
      herr.classList.toggle("con-filtros", filtrado);

      pags.innerHTML = "";
      if (nPags > 1) {
        var boton = function (txt, p, etiqueta, actual) {
          var b = el("button", "pag" + (actual ? " actual" : ""), txt); b.type = "button"; b.disabled = p < 1 || p > nPags || actual;
          b.setAttribute("aria-label", etiqueta); if (actual) b.setAttribute("aria-current", "page");
          b.addEventListener("click", function () { est.pag = p; pintar(); wrap.scrollIntoView({ block: "nearest", behavior: "smooth" }); });
          pags.appendChild(b);
        };
        boton("‹", est.pag - 1, "Página anterior");
        var vistas = [1, est.pag - 1, est.pag, est.pag + 1, nPags].filter(function (p, i, a) { return p >= 1 && p <= nPags && a.indexOf(p) === i; }).sort(function (a, b) { return a - b; });
        vistas.forEach(function (p, i) {
          if (i && p - vistas[i - 1] > 1) pags.appendChild(el("span", "pag-sep", "…"));
          boton(String(p), p, "Página " + p, p === est.pag);
        });
        boton("›", est.pag + 1, "Página siguiente");
      }
      pie.hidden = simple || total <= 10 && nPags === 1 && !filtrado;
      guardar(clave, est);
    }

    var tq;
    inpQ.addEventListener("input", function () { clearTimeout(tq); tq = setTimeout(function () { est.pag = 1; pintar(); }, 120); });
    selects.concat(desde ? [desde, hasta] : []).forEach(function (s) { s.addEventListener("change", function () { est.pag = 1; pintar(); }); });
    tam.addEventListener("change", function () { est.pag = 1; pintar(); });
    var reiniciar = function () { inpQ.value = ""; selects.forEach(function (s) { s.value = ""; }); if (desde) { desde.value = hasta.value = ""; } est.col = -1; est.pag = 1; pintar(); inpQ.focus(); };
    limpiar.addEventListener("click", reiniciar);
    sinRes.querySelector("button").addEventListener("click", reiniciar);

    // exportación de lo filtrado (todas las páginas), separada por «;» para Excel en español y a prueba de fórmulas
    csv.addEventListener("click", function () {
      var celda = function (v) { v = String(v == null ? "" : v); if (/^[=+\-@\t\r]/.test(v)) v = "'" + v; return '"' + v.replace(/"/g, '""') + '"'; };
      var lineas = [ths.map(function (th) { return celda(th.textContent.trim()); }).join(";")].concat(visibles.map(function (d) { return d.t.map(celda).join(";"); }));
      var blob = new Blob(["﻿" + lineas.join("\r\n")], { type: "text/csv;charset=utf-8" });
      var a = document.createElement("a"); a.href = URL.createObjectURL(blob);
      a.download = (wrap.getAttribute("data-tabla") || "tabla").toLowerCase().normalize("NFD").replace(/[^\w]+/g, "-").replace(/^-|-$/g, "") + ".csv";
      a.setAttribute("data-sin-carga", ""); document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
    });
    pintar();
  });
})();
