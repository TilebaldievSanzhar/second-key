// Тема до первой отрисовки: явный выбор человека, иначе — системная настройка (её применяет CSS).
// Отдельный файл, а не inline-скрипт: CSP разрешает только скрипты с нашего домена.
(function () {
  try {
    var t = localStorage.getItem("sk-theme");
    if (t === "light" || t === "dark") {
      document.documentElement.dataset.theme = t;
      var metas = document.querySelectorAll('meta[name="theme-color"]');
      for (var i = 0; i < metas.length; i++) metas[i].setAttribute("content", t === "dark" ? "#121413" : "#f6f5f1");
    }
  } catch (e) {
    /* хранилище недоступно — остаётся системная тема */
  }
})();
