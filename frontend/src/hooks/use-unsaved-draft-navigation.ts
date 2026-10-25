import { useEffect } from 'react';
import { useRouter } from 'next/router';

/** Preserve local source through link navigation, history navigation, and unload. */
export function useUnsavedDraftNavigation(dirty: boolean) {
  const router = useRouter();
  useEffect(() => {
    if (!dirty) return;
    const message =
      'Discard unsaved draft source changes? They have not been saved to Reqlica.';
    function beforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = '';
    }
    function beforeLink(event: MouseEvent) {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const anchor =
        event.target instanceof Element
          ? event.target.closest('a[href]')
          : null;
      if (
        !(anchor instanceof HTMLAnchorElement) ||
        anchor.download ||
        (anchor.target && anchor.target !== '_self')
      )
        return;
      const url = new URL(anchor.href, window.location.href);
      if (
        url.origin !== window.location.origin ||
        !['http:', 'https:'].includes(url.protocol)
      )
        return;
      if (
        url.pathname === window.location.pathname &&
        url.search === window.location.search
      )
        return;
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopPropagation();
      }
    }
    const currentState = window.history.state;
    const currentUrl = router.asPath;
    router.beforePopState(() => {
      if (window.confirm(message)) return true;
      // The browser already moved its URL before notifying the Pages Router.
      window.history.pushState(currentState, '', currentUrl);
      return false;
    });
    window.addEventListener('beforeunload', beforeUnload);
    document.addEventListener('click', beforeLink, true);
    return () => {
      window.removeEventListener('beforeunload', beforeUnload);
      document.removeEventListener('click', beforeLink, true);
      router.beforePopState(() => true);
    };
  }, [dirty, router]);
}
