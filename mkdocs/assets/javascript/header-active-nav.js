/**
 * Keep the custom header navigation active state synchronised with
 * MkDocs Material instant navigation.
 *
 * Top-level items may be section roots (e.g. /getting-started/) while the
 * current page is a child (e.g. /getting-started/quick-start/). Those parents
 * stay active via prefix matching. Home / site-root stays exact-match only so
 * it does not light up on every nested page.
 */

(() => {
  "use strict";

  const { onDocumentReady, onInstantNavigation } =
    window.LupaxaPageLifecycle;

  const normalisePath = (value) => {
    // Resolve relative hrefs (./, ..) against the current page, not origin.
    const url = new URL(value, window.location.href);

    const path = url.pathname
      .replace(/\/index\.html$/, "/")
      .replace(/\/+$/, "");

    return path || "/";
  };

  const updateActiveNavigation = () => {
    const currentPath = normalisePath(window.location.href);
    const links = Array.from(
      document.querySelectorAll(".lupaxa-header__nav-link"),
    );

    if (!links.length) {
      return;
    }

    // Home is the first top-level item (site convention).
    const homePath = normalisePath(links[0].href);

    links.forEach((link) => {
      const item = link.closest(".lupaxa-header__nav-item");

      if (!item) {
        return;
      }

      const linkPath = normalisePath(link.href);
      const isHome = linkPath === homePath;
      const isActive = isHome
        ? currentPath === linkPath
        : currentPath === linkPath ||
          currentPath.startsWith(`${linkPath}/`);

      item.classList.toggle(
        "lupaxa-header__nav-item--active",
        isActive,
      );

      if (isActive) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    });
  };

  const scheduleUpdate = () => {
    requestAnimationFrame(updateActiveNavigation);
  };

  onDocumentReady(updateActiveNavigation);
  onInstantNavigation(updateActiveNavigation);

  window.addEventListener("popstate", scheduleUpdate);
})();
