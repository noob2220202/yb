// PM2 process definitions — starts backend (uvicorn, no venv, plain
// system/user-site Python) and frontend (Next.js `next start`, needs a
// prior `npm run build`) with one command from the repo root:
//
//   pm2 start ecosystem.config.js
//
// See README "상시 실행 (PM2)" for the full setup (installing deps, .env,
// building the frontend once) before this will actually work.
module.exports = {
  apps: [
    {
      name: "yb-backend",
      cwd: "./backend",
      script: "python3",
      // `-m uvicorn` (not the `uvicorn` console script) so this works
      // regardless of whether pip put its scripts on PATH -- only needs
      // python3 itself to be able to import the packages it installed.
      args: ["-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
      interpreter: "none",
    },
    {
      name: "yb-frontend",
      cwd: "./frontend",
      script: "npm",
      args: ["start"],
      interpreter: "none",
      env: {
        PORT: "3000",
      },
    },
  ],
};
