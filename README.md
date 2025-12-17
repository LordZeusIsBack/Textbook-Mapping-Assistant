| Aspect             | Traditional RAG  | Your System     |
| ------------------ | ---------------- | --------------- |
| Output             | Generated answer | Source location |
| Hallucination risk | Medium–High      | Near zero       |
| Explainability     | Weak             | Strong          |
| Academic usage     | Risky            | Ideal           |
| Ethics compliance  | Debated          | Clean           |

# Development: using uv to install & run (corrected)

1. Install uv (if not installed):
   - python -m pip install uv

2. Generate a lockfile from pyproject.toml:
   - uv lock

3. Install / sync the project's environment and dependencies:
   - uv sync

   Note: uv will create or reuse a managed environment. You don't need to activate a shell.

4. Run the FastAPI app inside the uv-managed environment:
   - uv run python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

   Alternatively, you can run uvicorn directly inside the environment:
   - uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000

5. Serve the frontend (open a new terminal, from project root):
   - cd frontend
   - python -m http.server 5500

6. Open the frontend in your browser:
   - http://127.0.0.1:5500/index.html

Note: you can now open the UI directly from the backend server:

- Start backend with uv:
  - uv lock
  - uv sync
  - uv run python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

- Open in browser:
  - http://127.0.0.1:8000/  <-- serves frontend/index.html (and static assets under /static/)
  - Or continue to serve frontend separately (python -m http.server) if you prefer.

## Alternative: use system Python / pip (no uv)

If you prefer not to use uv, install the required packages into your system Python or a virtual environment and run uvicorn directly.

1. (Optional) Create & activate a venv:
   - python -m venv .venv
   - .venv\Scripts\activate    # Windows
   - source .venv/bin/activate # macOS / Linux

2. Ensure pip is up-to-date:
   - python -m pip install --upgrade pip setuptools wheel

3. Install runtime dependencies:
   - python -m pip install fastapi uvicorn python-multipart

4. Start the FastAPI app:
   - python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

5. Serve the frontend (in a new terminal):
   - cd frontend
   - python -m http.server 5500
   - Open: http://127.0.0.1:5500/index.html

Notes:
- This runs the backend using your system/venv Python instead of uv's managed environment.
- Use the uv workflow when you want reproducible dependency management; use the direct pip approach for quick local testing.

Tips / Troubleshooting:
- If you add or change dependencies, run:
  - uv lock
  - uv sync

- To run one-off commands inside the environment (e.g., run tests or a REPL), use:
  - uv run <command>

- If uv is missing a package (e.g., uvicorn), you can add it to pyproject.toml and then:
  - uv lock
  - uv sync

- CORS: main.py enables CORS for development (allow_origins=["*"]). In production restrict origins.
- Uploaded PDFs are saved to the temp_uploads folder in the project root.
