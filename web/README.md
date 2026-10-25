# Care Data Agent: web

A single-screen chat UI for the agent. React + Vite + TypeScript, plain CSS.

```bash
npm install
npm run dev        # http://localhost:5173 (expects the API on http://localhost:8000)
npm run build      # type-check + production build
```

In development, `/api/*` is proxied to the FastAPI server (`API_URL` overrides the target).

- Citations like `[V205]` in answers become chips; clicking one opens the full record
  in a side panel, with the extracted evidence highlighted.
- "How this was answered" lists the tool calls the agent made.
