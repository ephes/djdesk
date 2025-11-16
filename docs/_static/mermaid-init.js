document.addEventListener('DOMContentLoaded', () => {
  if (typeof mermaid === 'undefined') {
    return;
  }
  mermaid.initialize({
    startOnLoad: true,
    theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'default'
  });
});
