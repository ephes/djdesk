Contributing to the Docs
========================

1. Ensure dependencies are installed:

   .. code-block:: bash

      uv pip install -r docs/requirements.txt

2. Build once:

   .. code-block:: bash

      just docs-html

3. Live preview:

   .. code-block:: bash

      just docs-serve

4. Capture screenshots using feature flags as described in
   ``specs/2025-11-16_app_contents.md`` and drop them under
   ``docs/_static/app-shots/<stage>/``.

5. Reference new pages in ``index.rst`` so RTD picks them up.
